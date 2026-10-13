"""Media provider abstraction, Wikimedia Commons CC/Public-Domain fetcher, Authorized Media scanner, and 15 GB SHA-256 LRU cache."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Any, Sequence

import httpx
from sqlmodel import select

from localshorts.config import (
    AUTHORIZED_MEDIA_DIR,
    BACKGROUNDS_DIR,
    MEDIA_CACHE_DIR,
    OUTPUT_DIR,
    TEMP_CACHE_DIR,
    ensure_directories,
)
from localshorts.database import get_session
from localshorts.logging_config import get_logger
from localshorts.models.db_models import MediaCacheEntry, utc_now_iso
from localshorts.models.schemas import ScenePlanItemSchema
from localshorts.security import sanitize_filename, validate_external_url

logger = get_logger("media")

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_LICENSE_KEYWORDS = ("cc0", "public domain", "pd-", "cc-by", "cc by", "creative commons")


def compute_sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get_directory_size_bytes(directory: Path) -> int:
    if not directory.exists():
        return 0
    total = 0
    for root, _dirs, files in os.walk(directory):
        for f in files:
            fp = Path(root) / f
            try:
                total += fp.stat().st_size
            except OSError:
                pass
    return total


def get_storage_and_cache_stats(cache_limit_gb: float = 15.0) -> dict[str, Any]:
    """Return current cache size, temp size, output size, and free disk space."""
    ensure_directories()
    media_bytes = get_directory_size_bytes(MEDIA_CACHE_DIR)
    temp_bytes = get_directory_size_bytes(TEMP_CACHE_DIR)
    output_bytes = get_directory_size_bytes(OUTPUT_DIR)
    free_bytes = shutil.disk_usage(OUTPUT_DIR).free
    return {
        "media_cache_mb": round(media_bytes / (1024 * 1024), 2),
        "temp_cache_mb": round(temp_bytes / (1024 * 1024), 2),
        "total_cache_mb": round((media_bytes + temp_bytes) / (1024 * 1024), 2),
        "total_cache_gb": round((media_bytes + temp_bytes) / (1024**3), 3),
        "cache_limit_gb": cache_limit_gb,
        "output_size_mb": round(output_bytes / (1024 * 1024), 2),
        "free_disk_gb": round(free_bytes / (1024**3), 2),
    }


def enforce_media_cache_limit(limit_gb: float = 15.0) -> dict[str, int]:
    """
    Evict oldest unused cached media files (LRU by last_used_at ASC) when cache/media
    exceeds limit_gb. Never touches output/ or assets/authorized_media/.
    """
    ensure_directories()
    max_bytes = int(limit_gb * (1024**3))
    current_bytes = get_directory_size_bytes(MEDIA_CACHE_DIR)
    removed_count = 0
    freed_bytes = 0

    if current_bytes <= max_bytes:
        return {"removed_files": 0, "freed_bytes": 0, "current_bytes": current_bytes}

    with get_session() as session:
        stmt = select(MediaCacheEntry).order_by(MediaCacheEntry.last_used_at.asc())
        entries = list(session.exec(stmt).all())
        for entry in entries:
            if current_bytes - freed_bytes <= max_bytes:
                break
            p = Path(entry.local_path)
            if p.exists() and p.resolve().is_relative_to(MEDIA_CACHE_DIR.resolve()):
                try:
                    sz = p.stat().st_size
                    p.unlink()
                    freed_bytes += sz
                    removed_count += 1
                except OSError:
                    pass
            session.delete(entry)
        session.commit()

    logger.info("Media cache LRU eviction removed %d files (%d bytes)", removed_count, freed_bytes)
    return {
        "removed_files": removed_count,
        "freed_bytes": freed_bytes,
        "current_bytes": max(0, current_bytes - freed_bytes),
    }


def clean_temporary_cache(include_media_cache: bool = False) -> dict[str, Any]:
    """
    Clean temporary intermediate files (cache/temp/*) and optionally cached external media.
    NEVER deletes output/ videos, SQLite DB, settings, scripts, or source manifests.
    """
    ensure_directories()
    freed = 0
    removed = 0

    target_dirs = [TEMP_CACHE_DIR]
    if include_media_cache:
        target_dirs.append(MEDIA_CACHE_DIR)

    for tdir in target_dirs:
        if not tdir.exists():
            continue
        for item in tdir.iterdir():
            if item.name == ".gitkeep":
                continue
            try:
                if item.is_dir():
                    freed += get_directory_size_bytes(item)
                    shutil.rmtree(item, ignore_errors=True)
                    removed += 1
                else:
                    freed += item.stat().st_size
                    item.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                pass

    if include_media_cache:
        with get_session() as session:
            for entry in session.exec(select(MediaCacheEntry)).all():
                session.delete(entry)
            session.commit()

    return {
        "removed_items": removed,
        "freed_mb": round(freed / (1024 * 1024), 2),
    }


class MediaService:
    """Resolves licensed/local visual assets for scenes with SHA-256 caching and procedural fallback."""

    def find_authorized_local_media(
        self,
        query: str,
        custom_dir: str = "",
    ) -> tuple[str, str] | None:
        """Search assets/authorized_media/ and optional custom_dir for matching user-owned images."""
        search_dirs = [AUTHORIZED_MEDIA_DIR, BACKGROUNDS_DIR]
        if custom_dir:
            cp = Path(custom_dir)
            if cp.exists() and cp.is_dir():
                search_dirs.insert(0, cp)

        query_tokens = [t.lower() for t in re.findall(r"[a-zA-Z0-9]+", query) if len(t) >= 3]
        all_files: list[Path] = []
        for sdir in search_dirs:
            if not sdir.exists():
                continue
            for fp in sdir.iterdir():
                if fp.is_file() and fp.suffix.lower() in ALLOWED_IMAGE_EXTS:
                    all_files.append(fp)

        if not all_files:
            return None

        for fp in all_files:
            stem_lower = fp.stem.lower()
            if any(tok in stem_lower for tok in query_tokens):
                return (str(fp), f"User Authorized Media ({fp.name})")

        return None

    async def fetch_wikimedia_image(
        self,
        query: str,
        cache_limit_gb: float = 15.0,
    ) -> dict[str, str] | None:
        """
        Query Wikimedia Commons API for permissively licensed (CC0 / Public Domain / CC-BY / CC-BY-SA)
        images matching `query`, caching locally by SHA-256.
        """
        clean_q = re.sub(r"[^a-zA-Z0-9\s-]", " ", query or "").strip()
        if not clean_q:
            return None

        # Check local SQLite cache first
        with get_session() as session:
            cached = session.exec(
                select(MediaCacheEntry).where(MediaCacheEntry.query_term == clean_q.lower())
            ).first()
            if cached and Path(cached.local_path).exists():
                cached.last_used_at = utc_now_iso()
                session.add(cached)
                session.commit()
                return {
                    "local_path": cached.local_path,
                    "attribution": cached.attribution,
                    "license": cached.license_type,
                    "source_url": cached.source_url,
                    "provider": cached.provider,
                }

        api_url = "https://commons.wikimedia.org/w/api.php"
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"filetype:bitmap {clean_q}",
            "gsrnamespace": "6",
            "gsrlimit": "5",
            "prop": "imageinfo",
            "iiprop": "url|extmetadata|dimensions",
            "iiurlwidth": "1080",
        }
        headers = {
            "User-Agent": "LocalShortsStudio/0.1.0 (https://localhost; local-first educational research)"
        }

        try:
            async with httpx.AsyncClient(timeout=8.0, headers=headers, follow_redirects=True) as client:
                resp = await client.get(api_url, params=params)
                if resp.status_code != 200:
                    return None
                pages = resp.json().get("query", {}).get("pages", {})
                for _page_id, page in pages.items():
                    ii_list = page.get("imageinfo", [])
                    if not ii_list:
                        continue
                    ii = ii_list[0]
                    ext = ii.get("extmetadata", {})
                    lic_short = str(ext.get("LicenseShortName", {}).get("value", "CC-BY-SA")).strip()
                    if not any(k in lic_short.lower() for k in ALLOWED_LICENSE_KEYWORDS):
                        continue

                    img_url = str(ii.get("thumburl") or ii.get("url") or "")
                    if not img_url:
                        continue
                    validate_external_url(img_url)

                    artist_raw = str(ext.get("Artist", {}).get("value", "Wikimedia Contributor"))
                    artist = re.sub(r"<[^>]+>", "", artist_raw).strip()[:60] or "Wikimedia Commons"

                    img_resp = await client.get(img_url)
                    if img_resp.status_code != 200 or len(img_resp.content) < 5_000:
                        continue
                    if len(img_resp.content) > 20 * 1024 * 1024:
                        continue

                    sha = compute_sha256_bytes(img_resp.content)
                    subdir = MEDIA_CACHE_DIR / sha[:2]
                    subdir.mkdir(parents=True, exist_ok=True)
                    ext_suffix = Path(sanitize_filename(img_url)).suffix or ".jpg"
                    if ext_suffix not in ALLOWED_IMAGE_EXTS:
                        ext_suffix = ".jpg"
                    local_file = subdir / f"{sha}{ext_suffix}"
                    local_file.write_bytes(img_resp.content)

                    attribution = f"Wikimedia Commons / {artist} ({lic_short})"
                    with get_session() as session:
                        existing = session.get(MediaCacheEntry, sha)
                        if not existing:
                            session.add(
                                MediaCacheEntry(
                                    sha256=sha,
                                    provider="wikimedia",
                                    source_url=img_url,
                                    query_term=clean_q.lower(),
                                    title=str(page.get("title", clean_q))[:120],
                                    license_type=lic_short,
                                    attribution=attribution,
                                    local_path=str(local_file),
                                    media_type="image",
                                    size_bytes=len(img_resp.content),
                                )
                            )
                            session.commit()

                    enforce_media_cache_limit(limit_gb=cache_limit_gb)
                    return {
                        "local_path": str(local_file),
                        "attribution": attribution,
                        "license": lic_short,
                        "source_url": img_url,
                        "provider": "wikimedia",
                    }
        except Exception as exc:
            logger.debug("Wikimedia Commons lookup skipped for '%s': %s", clean_q, exc)

        return None

    async def enrich_scenes_with_media(
        self,
        scenes: Sequence[ScenePlanItemSchema],
        enable_wikimedia: bool = True,
        authorized_media_dir: str = "",
        cache_limit_gb: float = 15.0,
    ) -> tuple[list[ScenePlanItemSchema], list[dict[str, str]]]:
        """
        Attach local Authorized Media or Wikimedia Commons licensed stills to `media_composite`
        scenes while keeping procedural graphics as the primary/fallback engine.
        Returns (updated_scenes, media_attributions).
        """
        attributions: list[dict[str, str]] = []
        for scene in scenes:
            # First check user-owned Authorized Media
            local_match = self.find_authorized_local_media(scene.visual_query, custom_dir=authorized_media_dir)
            if local_match:
                scene.media_asset_path, scene.media_attribution = local_match
                attributions.append(
                    {
                        "scene_index": str(scene.scene_index),
                        "provider": "authorized_local",
                        "path": scene.media_asset_path,
                        "attribution": scene.media_attribution,
                        "license": "User Authorized",
                    }
                )
                continue

            if enable_wikimedia and scene.visual_type == "media_composite":
                wiki_asset = await self.fetch_wikimedia_image(
                    scene.visual_query,
                    cache_limit_gb=cache_limit_gb,
                )
                if wiki_asset:
                    scene.media_asset_path = wiki_asset["local_path"]
                    scene.media_attribution = wiki_asset["attribution"]
                    attributions.append(
                        {
                            "scene_index": str(scene.scene_index),
                            "provider": wiki_asset["provider"],
                            "source_url": wiki_asset["source_url"],
                            "attribution": wiki_asset["attribution"],
                            "license": wiki_asset["license"],
                        }
                    )

        return list(scenes), attributions

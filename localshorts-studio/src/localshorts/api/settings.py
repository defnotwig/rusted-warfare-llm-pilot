"""Settings, Watched Topics, Cache Cleaner, and Windows Startup configuration endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from localshorts.database import get_app_settings, get_session
from localshorts.models.db_models import AppSettings, WatchedTopic, utc_now_iso
from localshorts.services.media import clean_temporary_cache, get_storage_and_cache_stats
from localshorts.services.scheduler import set_windows_startup

router = APIRouter(prefix="/api/settings", tags=["settings"])


class UpdateSettingsRequest(BaseModel):
    channel_name: str | None = None
    channel_niche: str | None = None
    automation_mode: str | None = None
    ollama_base_url: str | None = None
    ollama_model: str | None = None
    ollama_num_ctx: int | None = None
    unload_model_after_gen: bool | None = None
    tts_provider: str | None = None
    tts_voice: str | None = None
    tts_speed: float | None = None
    use_whisper_alignment: bool | None = None
    visual_preset: str | None = None
    caption_preset: str | None = None
    enable_wikimedia: bool | None = None
    enable_sfx: bool | None = None
    enable_background_music: bool | None = None
    prefer_nvenc: bool | None = None
    target_duration_s: int | None = None
    quality_threshold: int | None = None
    source_confidence_threshold: int | None = None
    originality_threshold: float | None = None
    cache_limit_gb: float | None = None
    min_free_disk_gb: float | None = None
    daily_candidate_limit: int | None = None
    schedule_enabled: bool | None = None
    schedule_hours_json: str | None = None
    battery_override: bool | None = None
    lan_access_enabled: bool | None = None
    windows_startup_enabled: bool | None = None
    custom_rss_urls_json: str | None = None
    authorized_media_dir: str | None = None
    youtube_api_key: str | None = None
    pixabay_api_key: str | None = None
    coverr_api_key: str | None = None


class CreateWatchedTopicRequest(BaseModel):
    name: str
    category: str = "creator"
    youtube_channel_id: str = ""
    search_query: str = ""


class CleanCacheRequest(BaseModel):
    include_media_cache: bool = False


def _sanitize_settings_response(cfg: AppSettings) -> dict[str, Any]:
    data = cfg.model_dump()
    # Mask optional API keys in GET responses while indicating whether they are configured
    data["has_youtube_api_key"] = bool(cfg.youtube_api_key)
    data["has_pixabay_api_key"] = bool(cfg.pixabay_api_key)
    data["has_coverr_api_key"] = bool(cfg.coverr_api_key)
    data["youtube_api_key"] = ""
    data["pixabay_api_key"] = ""
    data["coverr_api_key"] = ""
    return data


@router.get("")
async def get_settings_endpoint() -> dict[str, Any]:
    cfg = get_app_settings()
    return {
        "settings": _sanitize_settings_response(cfg),
        "storage": get_storage_and_cache_stats(cache_limit_gb=cfg.cache_limit_gb),
    }


@router.post("")
async def update_settings_endpoint(payload: UpdateSettingsRequest) -> dict[str, Any]:
    updates = payload.model_dump(exclude_none=True)
    with get_session() as session:
        cfg = session.get(AppSettings, 1)
        if not cfg:
            cfg = AppSettings(id=1)

        for key, val in updates.items():
            if key in ("youtube_api_key", "pixabay_api_key", "coverr_api_key") and val == "":
                continue
            if hasattr(cfg, key):
                setattr(cfg, key, val)

        if "windows_startup_enabled" in updates:
            set_windows_startup(bool(updates["windows_startup_enabled"]))

        cfg.updated_at = utc_now_iso()
        session.add(cfg)
        session.commit()
        session.refresh(cfg)

    return {
        "status": "updated",
        "settings": _sanitize_settings_response(cfg),
    }


@router.get("/topics")
async def list_watched_topics() -> list[dict[str, Any]]:
    with get_session() as session:
        topics = list(session.exec(select(WatchedTopic).order_by(WatchedTopic.id.asc())).all())
    return [t.model_dump() for t in topics]


@router.post("/topics")
async def add_watched_topic(payload: CreateWatchedTopicRequest) -> dict[str, Any]:
    with get_session() as session:
        topic = WatchedTopic(
            name=payload.name.strip(),
            category=payload.category.strip(),
            youtube_channel_id=payload.youtube_channel_id.strip(),
            search_query=(payload.search_query or payload.name).strip(),
            enabled=True,
        )
        session.add(topic)
        session.commit()
        session.refresh(topic)
        return topic.model_dump()


@router.post("/topics/{topic_id}/toggle")
async def toggle_watched_topic(topic_id: int) -> dict[str, Any]:
    with get_session() as session:
        topic = session.get(WatchedTopic, topic_id)
        if not topic:
            raise HTTPException(status_code=404, detail="Watched topic not found")
        topic.enabled = not topic.enabled
        session.add(topic)
        session.commit()
        session.refresh(topic)
        return topic.model_dump()


@router.delete("/topics/{topic_id}")
async def delete_watched_topic(topic_id: int) -> dict[str, Any]:
    with get_session() as session:
        topic = session.get(WatchedTopic, topic_id)
        if not topic:
            raise HTTPException(status_code=404, detail="Watched topic not found")
        session.delete(topic)
        session.commit()
    return {"deleted": True, "id": topic_id}


@router.post("/cache/clean")
async def clean_cache_endpoint(payload: CleanCacheRequest) -> dict[str, Any]:
    res = clean_temporary_cache(include_media_cache=payload.include_media_cache)
    cfg = get_app_settings()
    return {
        "cleaned": res,
        "storage": get_storage_and_cache_stats(cache_limit_gb=cfg.cache_limit_gb),
    }

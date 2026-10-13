"""Export bundler, YouTube Shorts metadata builder, and Ready-to-Upload folder manager."""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from localshorts.config import OUTPUT_DIR, READY_TO_UPLOAD_DIR, ensure_directories
from localshorts.logging_config import get_logger
from localshorts.models.schemas import (
    ContentQualityBreakdown,
    EvidenceBundleSchema,
    ScenePlanItemSchema,
    ScriptGenerationSchema,
    TechnicalQCReport,
)
from localshorts.security import resolve_safe_path, run_subprocess_safe, sanitize_slug

logger = get_logger("export")

SUGGESTED_POSTING_SLOTS = [
    "11:30 AM Local (Midday Scroll Peak)",
    "04:30 PM Local (After-School / Commute Peak)",
    "08:00 PM Local (Prime Evening Shorts Peak)",
]


def generate_hashtags(topic: str, creator: str, script_obj: ScriptGenerationSchema) -> list[str]:
    """Generate 3 to 5 clean, non-spammy hashtags relevant to the story."""
    tags: list[str] = []
    for raw in [creator, topic, *(script_obj.visual_search_terms or [])[:3]]:
        clean = re.sub(r"[^a-zA-Z0-9]", "", raw or "")
        if len(clean) >= 3:
            tag = f"#{clean}"
            if tag.lower() not in {t.lower() for t in tags}:
                tags.append(tag)
        if len(tags) >= 4:
            break

    if "#Shorts" not in tags:
        tags.append("#Shorts")
    while len(tags) < 3:
        for fallback in ("#CreatorNews", "#TechExplained", "#InternetCulture"):
            if fallback not in tags:
                tags.append(fallback)
                break
    return tags[:5]


def allocate_job_output_dir(topic: str, job_id: str, created_at_iso: str = "") -> Path:
    """Create and return `output/YYYY-MM-DD/<slug>-<id>/`."""
    ensure_directories()
    date_prefix = (created_at_iso or datetime.now(timezone.utc).strftime("%Y-%m-%d"))[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_prefix):
        date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    slug = sanitize_slug(topic, max_length=48)
    short_id = re.sub(r"[^a-zA-Z0-9]", "", job_id)[:6] or "001"
    folder_name = f"{slug}-{short_id}"

    day_dir = resolve_safe_path(OUTPUT_DIR, date_prefix)
    job_dir = resolve_safe_path(day_dir, folder_name)
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def write_job_export_bundle(
    job_dir: Path,
    job_id: str,
    topic: str,
    creator: str,
    script_obj: ScriptGenerationSchema,
    evidence: EvidenceBundleSchema,
    scenes: Sequence[ScenePlanItemSchema],
    tech_qc: TechnicalQCReport,
    quality: ContentQualityBreakdown,
    media_attributions: Sequence[dict[str, str]],
    encoder_used: str,
    promote_to_ready: bool = True,
) -> dict[str, Any]:
    """
    Write metadata.json, sources.json, source_manifest.json, script.json, script.txt,
    scene_plan.json, and qc.json into `job_dir`, and copy video.mp4 to
    `output/ready-to-upload/` when quality & technical gates pass.
    """
    ensure_directories()
    job_dir.mkdir(parents=True, exist_ok=True)

    hashtags = generate_hashtags(topic=topic, creator=creator, script_obj=script_obj)
    slot = SUGGESTED_POSTING_SLOTS[hash(job_id) % len(SUGGESTED_POSTING_SLOTS)]

    source_disclosure = (
        "Sources: Verified public feeds & official creator channels."
        if evidence.confidence >= 70
        else "Note: Developing story (Needs Editorial Review before publishing)."
    )
    full_description = (
        f"{script_obj.description.strip()}\n\n"
        f"💬 {script_obj.comment_question.strip()}\n\n"
        f"{source_disclosure}\n"
        f"{' '.join(hashtags)}"
    ).strip()

    metadata_payload = {
        "job_id": job_id,
        "topic": topic,
        "creator": creator,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "title_options": script_obj.title_options[:3],
        "primary_title": script_obj.title_options[0] if script_obj.title_options else topic,
        "description": full_description,
        "hashtags": hashtags,
        "comment_question": script_obj.comment_question,
        "suggested_category": "Entertainment & Science/Tech",
        "suggested_posting_slot": slot,
        "source_confidence": evidence.confidence,
        "review_status": evidence.review_status,
        "quality_score": quality.total,
        "quality_breakdown": quality.model_dump(),
        "technical_qc": tech_qc.model_dump(),
        "encoder_used": encoder_used,
        "duration_seconds": tech_qc.duration_s,
    }

    sources_payload = {
        "story_id": evidence.story_id,
        "headline": evidence.headline,
        "confidence": evidence.confidence,
        "review_status": evidence.review_status,
        "sources": [s.model_dump() for s in evidence.sources],
        "claims": evidence.claims,
        "contradictions": evidence.contradictions,
        "media_attributions": list(media_attributions),
    }

    (job_dir / "metadata.json").write_text(json.dumps(metadata_payload, indent=2), encoding="utf-8")
    (job_dir / "sources.json").write_text(json.dumps(sources_payload, indent=2), encoding="utf-8")
    (job_dir / "source_manifest.json").write_text(json.dumps(sources_payload, indent=2), encoding="utf-8")
    (job_dir / "script.json").write_text(script_obj.model_dump_json(indent=2), encoding="utf-8")
    (job_dir / "script.txt").write_text(script_obj.script + "\n", encoding="utf-8")
    (job_dir / "scene_plan.json").write_text(
        json.dumps([s.model_dump() for s in scenes], indent=2),
        encoding="utf-8",
    )
    (job_dir / "qc.json").write_text(
        json.dumps(
            {
                "technical_qc": tech_qc.model_dump(),
                "content_quality": quality.model_dump(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    video_mp4 = job_dir / "video.mp4"
    ready_path_str = ""
    if promote_to_ready and video_mp4.exists() and tech_qc.passed and quality.passed_threshold:
        READY_TO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        ready_filename = f"{job_dir.parent.name}_{job_dir.name}.mp4"
        ready_target = resolve_safe_path(READY_TO_UPLOAD_DIR, ready_filename)
        shutil.copy2(video_mp4, ready_target)
        ready_path_str = str(ready_target)
        logger.info("Promoted verified Short to Ready-to-Upload: %s", ready_target)

    return {
        "metadata": metadata_payload,
        "ready_video_path": ready_path_str,
    }


def open_folder_in_explorer(folder_path: Path) -> bool:
    """Open an approved output directory in Windows Explorer safely."""
    resolved = resolve_safe_path(OUTPUT_DIR, folder_path)
    if not resolved.exists():
        return False
    if os.name == "nt":
        try:
            run_subprocess_safe(["explorer.exe", str(resolved)], timeout=5.0)
            return True
        except Exception:
            return False
    return False

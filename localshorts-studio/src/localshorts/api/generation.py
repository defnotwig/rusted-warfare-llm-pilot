"""Job creation, sequential batch generation, per-stage regeneration, script approval, and queue control endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select

from localshorts.database import get_app_settings, get_session
from localshorts.models.db_models import ScriptRecord, ShortJob, TrendItem, WatchedTopic
from localshorts.models.schemas import ScriptGenerationSchema
from localshorts.services.trends import discover_and_rank_trends
from localshorts.worker.queue import worker_queue

router = APIRouter(prefix="/api", tags=["generation"])


class CreateJobRequest(BaseModel):
    topic: str = Field(..., min_length=3)
    headline: str = ""
    source_urls: list[str] = Field(default_factory=list)
    mode: str | None = None
    trend_item_id: str = ""
    model: str | None = None
    voice_id: str | None = None
    tts_speed: float | None = None
    visual_preset: str | None = None
    caption_preset: str | None = None
    target_duration_s: int = 24
    stop_after_script: bool = False


class BatchGenerateRequest(BaseModel):
    count: int = Field(default=3, ge=1, le=10)
    mode: str | None = None


class ApproveScriptRequest(BaseModel):
    script_text: str | None = None
    hook: str | None = None
    title: str | None = None


class RegenerateStageRequest(BaseModel):
    stage: str  # script, tts, voice, captions, visuals, scene_plan, render
    voice_id: str | None = None
    visual_preset: str | None = None
    caption_preset: str | None = None


def serialize_job(job: ShortJob) -> dict[str, Any]:
    try:
        completed = json.loads(job.completed_stages_json or "[]")
    except Exception:
        completed = []
    try:
        q_breakdown = json.loads(job.quality_breakdown_json or "{}")
    except Exception:
        q_breakdown = {}
    try:
        tech_qc = json.loads(job.technical_qc_json or "{}")
    except Exception:
        tech_qc = {}

    return {
        "id": job.id,
        "slug": job.slug,
        "topic": job.topic,
        "headline": job.headline,
        "mode": job.mode,
        "state": job.state,
        "progress_pct": job.progress_pct,
        "stage_message": job.stage_message,
        "priority": job.priority,
        "trend_item_id": job.trend_item_id,
        "model_used": job.model_used,
        "voice_id": job.voice_id,
        "tts_speed": job.tts_speed,
        "visual_preset": job.visual_preset,
        "caption_preset": job.caption_preset,
        "target_duration_s": job.target_duration_s,
        "duration_actual_s": job.duration_actual_s,
        "file_size_bytes": job.file_size_bytes,
        "file_size_mb": round(job.file_size_bytes / (1024 * 1024), 2) if job.file_size_bytes else 0.0,
        "completed_stages": completed,
        "failed_stage": job.failed_stage,
        "error_summary": job.error_summary,
        "error_trace": job.error_trace,
        "retry_count": job.retry_count,
        "source_confidence": job.source_confidence,
        "originality_similarity": job.originality_similarity,
        "technical_qc_passed": job.technical_qc_passed,
        "technical_qc": tech_qc,
        "quality_score_total": job.quality_score_total,
        "quality_breakdown": q_breakdown,
        "encoder_used": job.encoder_used,
        "output_dir": job.output_dir,
        "video_path": job.video_path,
        "ready_video_path": job.ready_video_path,
        "is_test_video": job.is_test_video,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "completed_at": job.completed_at,
    }


@router.get("/jobs")
async def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    with get_session() as session:
        stmt = select(ShortJob).order_by(ShortJob.created_at.desc()).limit(limit)
        jobs = list(session.exec(stmt).all())
    return [serialize_job(j) for j in jobs]


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    with get_session() as session:
        job = session.get(ShortJob, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        data = serialize_job(job)
        if job.script_id:
            s_rec = session.get(ScriptRecord, job.script_id)
            if s_rec:
                data["script_text"] = s_rec.script_text
                data["hook"] = s_rec.hook
                data["titles"] = json.loads(s_rec.title_options_json or "[]")
        return data


@router.post("/jobs")
async def create_job(payload: CreateJobRequest) -> dict[str, Any]:
    """Enqueue a manual or trend-backed Short generation job."""
    job = worker_queue.enqueue_job(
        topic=payload.topic,
        headline=payload.headline or payload.topic,
        mode=payload.mode,
        trend_item_id=payload.trend_item_id,
        model=payload.model,
        voice_id=payload.voice_id,
        tts_speed=payload.tts_speed,
        visual_preset=payload.visual_preset,
        caption_preset=payload.caption_preset,
        target_duration_s=payload.target_duration_s,
        stop_after_script=payload.stop_after_script,
    )
    return serialize_job(job)


@router.post("/jobs/sample")
async def create_sample_smoke_test_job() -> dict[str, Any]:
    """Enqueue the official local AI installation smoke-test Short."""
    topic = "Why local AI can run without sending your prompt to the cloud."
    job = worker_queue.enqueue_job(
        topic=topic,
        headline=topic,
        mode="SEMI_AUTO",
        visual_preset="Tech Explainer",
        caption_preset="Bold",
        target_duration_s=14,
        priority=25,
        is_test_video=True,
    )
    return serialize_job(job)


@router.post("/jobs/batch")
async def create_batch_jobs(payload: BatchGenerateRequest) -> dict[str, Any]:
    """
    Enqueue a sequential batch of 1, 3, 5, or 10 candidate Shorts from top discovered trends
    (scanning feeds first if no fresh candidates are present).
    """
    cfg = get_app_settings()
    count = min(10, max(1, payload.count))

    with get_session() as session:
        candidates = list(
            session.exec(
                select(TrendItem)
                .where(TrendItem.status == "NEW")
                .order_by(TrendItem.trend_score.desc())
                .limit(count)
            ).all()
        )

    if len(candidates) < count:
        await discover_and_rank_trends(confidence_threshold=cfg.source_confidence_threshold)
        with get_session() as session:
            candidates = list(
                session.exec(
                    select(TrendItem)
                    .where(TrendItem.status == "NEW")
                    .order_by(TrendItem.trend_score.desc())
                    .limit(count)
                ).all()
            )

    queued_ids: list[str] = []
    for item in candidates[:count]:
        job = worker_queue.enqueue_job(
            topic=item.topic or item.creator or item.headline,
            headline=item.headline,
            mode=payload.mode or cfg.automation_mode,
            trend_item_id=item.id,
        )
        queued_ids.append(job.id)

    # If still fewer than requested (e.g. offline), fill remaining from watched topics
    if len(queued_ids) < count:
        with get_session() as session:
            topics = list(session.exec(select(WatchedTopic).where(WatchedTopic.enabled == True)).all())  # noqa: E712
        for idx in range(count - len(queued_ids)):
            wt = topics[idx % len(topics)] if topics else None
            t_name = wt.name if wt else f"Local AI Breakthrough #{idx + 1}"
            job = worker_queue.enqueue_job(
                topic=t_name,
                headline=f"{t_name}: Latest Viral Moment & Breakdown",
                mode=payload.mode or cfg.automation_mode,
            )
            queued_ids.append(job.id)

    return {"queued_count": len(queued_ids), "job_ids": queued_ids}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job_endpoint(job_id: str) -> dict[str, Any]:
    ok = worker_queue.cancel_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "cancelled", "job_id": job_id}


@router.post("/jobs/{job_id}/retry")
async def retry_job_endpoint(job_id: str, from_failed_stage: bool = True) -> dict[str, Any]:
    job = worker_queue.retry_job(job_id, from_failed_stage=from_failed_stage)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return serialize_job(job)


@router.post("/jobs/{job_id}/approve-script")
async def approve_script_and_render(job_id: str, payload: ApproveScriptRequest) -> dict[str, Any]:
    """Approve (and optionally edit) a script in ASSISTED mode and continue from TTS to READY."""
    with get_session() as session:
        job = session.get(ShortJob, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if payload.script_text and job.output_dir:
            script_file = Path(job.output_dir) / "script.json"
            if script_file.exists():
                s_obj = ScriptGenerationSchema.model_validate_json(script_file.read_text(encoding="utf-8"))
                s_obj.script = payload.script_text.strip()
                if payload.hook:
                    s_obj.hook = payload.hook.strip()
                if payload.title:
                    s_obj.title_options = [payload.title.strip(), *s_obj.title_options[:2]]
                script_file.write_text(s_obj.model_dump_json(indent=2), encoding="utf-8")
                (Path(job.output_dir) / "script.txt").write_text(s_obj.script + "\n", encoding="utf-8")

    updated = worker_queue.request_stage_regeneration(job_id=job_id, stage_name="tts")
    if not updated:
        raise HTTPException(status_code=404, detail="Job not found")
    return serialize_job(updated)


@router.post("/jobs/{job_id}/regenerate-stage")
async def regenerate_stage_endpoint(job_id: str, payload: RegenerateStageRequest) -> dict[str, Any]:
    """Regenerate a single stage (and its downstream stages) without repeating earlier stages."""
    job = worker_queue.request_stage_regeneration(
        job_id=job_id,
        stage_name=payload.stage,
        new_voice_id=payload.voice_id,
        new_visual_preset=payload.visual_preset,
        new_caption_preset=payload.caption_preset,
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return serialize_job(job)


@router.post("/queue/pause")
async def pause_queue() -> dict[str, Any]:
    worker_queue.pause()
    return {"paused": True}


@router.post("/queue/resume")
async def resume_queue() -> dict[str, Any]:
    worker_queue.resume()
    return {"paused": False}

"""Library endpoints: video streaming, thumbnail serving, metadata/source inspection, folder opening, and deletion."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import select

from localshorts.api.generation import serialize_job
from localshorts.config import OUTPUT_DIR, READY_TO_UPLOAD_DIR
from localshorts.database import get_session
from localshorts.models.db_models import ShortJob
from localshorts.security import resolve_safe_path
from localshorts.services.export import open_folder_in_explorer

router = APIRouter(prefix="/api/library", tags=["library"])


@router.get("")
async def list_library_items() -> list[dict[str, Any]]:
    """Return rendered Shorts (READY or NEEDS_REVIEW with video file)."""
    with get_session() as session:
        stmt = (
            select(ShortJob)
            .where(ShortJob.state.in_(["READY", "NEEDS_REVIEW"]))  # type: ignore[arg-type]
            .order_by(ShortJob.created_at.desc())
        )
        jobs = list(session.exec(stmt).all())

    results: list[dict[str, Any]] = []
    for job in jobs:
        item = serialize_job(job)
        if job.output_dir:
            meta_file = Path(job.output_dir) / "metadata.json"
            if meta_file.exists():
                try:
                    item["export_metadata"] = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    item["export_metadata"] = {}
        results.append(item)
    return results


@router.get("/{job_id}/video")
async def stream_job_video(job_id: str) -> FileResponse:
    with get_session() as session:
        job = session.get(ShortJob, job_id)
        if not job or not job.video_path:
            raise HTTPException(status_code=404, detail="Video not found")
        video_file = resolve_safe_path(OUTPUT_DIR, job.video_path)
        if not video_file.exists():
            raise HTTPException(status_code=404, detail="Video file missing on disk")
        return FileResponse(str(video_file), media_type="video/mp4", filename=f"{job.slug}.mp4")


@router.get("/{job_id}/thumbnail")
async def get_job_thumbnail(job_id: str) -> FileResponse:
    with get_session() as session:
        job = session.get(ShortJob, job_id)
        if not job or not job.thumbnail_path:
            raise HTTPException(status_code=404, detail="Thumbnail not found")
        thumb_file = resolve_safe_path(OUTPUT_DIR, job.thumbnail_path)
        if not thumb_file.exists():
            raise HTTPException(status_code=404, detail="Thumbnail missing on disk")
        return FileResponse(str(thumb_file), media_type="image/jpeg")


@router.get("/{job_id}/metadata")
async def get_job_metadata_and_sources(job_id: str) -> dict[str, Any]:
    with get_session() as session:
        job = session.get(ShortJob, job_id)
        if not job or not job.output_dir:
            raise HTTPException(status_code=404, detail="Job output not found")
        job_dir = resolve_safe_path(OUTPUT_DIR, job.output_dir)

    meta_data = {}
    sources_data = {}
    script_data = {}
    if (job_dir / "metadata.json").exists():
        meta_data = json.loads((job_dir / "metadata.json").read_text(encoding="utf-8"))
    if (job_dir / "sources.json").exists():
        sources_data = json.loads((job_dir / "sources.json").read_text(encoding="utf-8"))
    if (job_dir / "script.json").exists():
        script_data = json.loads((job_dir / "script.json").read_text(encoding="utf-8"))

    return {
        "job_id": job_id,
        "output_dir": str(job_dir),
        "metadata": meta_data,
        "sources": sources_data,
        "script": script_data,
    }


@router.post("/{job_id}/open-folder")
async def open_job_folder(job_id: str) -> dict[str, Any]:
    with get_session() as session:
        job = session.get(ShortJob, job_id)
        if not job or not job.output_dir:
            raise HTTPException(status_code=404, detail="Job folder not found")
        folder = resolve_safe_path(OUTPUT_DIR, job.output_dir)
    opened = open_folder_in_explorer(folder)
    return {"opened": opened, "path": str(folder)}


@router.post("/open-ready-folder")
async def open_ready_to_upload_folder() -> dict[str, Any]:
    READY_TO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    opened = open_folder_in_explorer(READY_TO_UPLOAD_DIR)
    return {"opened": opened, "path": str(READY_TO_UPLOAD_DIR)}


@router.delete("/{job_id}")
async def delete_library_job(job_id: str) -> dict[str, Any]:
    """Delete a job and its exported artifacts when explicitly requested by the user."""
    with get_session() as session:
        job = session.get(ShortJob, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        out_dir = job.output_dir
        ready_vid = job.ready_video_path
        session.delete(job)
        session.commit()

    if ready_vid:
        try:
            rp = resolve_safe_path(OUTPUT_DIR, ready_vid)
            rp.unlink(missing_ok=True)
        except Exception:
            pass

    if out_dir:
        try:
            dp = resolve_safe_path(OUTPUT_DIR, out_dir)
            if dp.exists() and dp != OUTPUT_DIR.resolve():
                shutil.rmtree(dp, ignore_errors=True)
        except Exception:
            pass

    return {"deleted": True, "job_id": job_id}

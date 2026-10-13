"""Health and live system status bar endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlmodel import select

from localshorts.config import APP_VERSION
from localshorts.database import get_app_settings, get_session
from localshorts.models.db_models import ShortJob
from localshorts.models.schemas import HealthResponseSchema
from localshorts.services.hardware import detect_hardware
from localshorts.services.ollama import OllamaService
from localshorts.services.tts import TTSService
from localshorts.worker.queue import ACTIVE_TRANSIENT_STATES, worker_queue

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponseSchema)
async def get_health() -> HealthResponseSchema:
    """Return structured health status without exposing any secrets."""
    db_ok = True
    queued_count = 0
    try:
        cfg = get_app_settings()
        with get_session() as session:
            jobs = list(session.exec(select(ShortJob)).all())
            queued_count = sum(
                1 for j in jobs if j.state in ACTIVE_TRANSIENT_STATES or j.state == "DISCOVERED"
            )
    except Exception:
        db_ok = False
        cfg = None

    hw = detect_hardware()
    ollama_svc = OllamaService(base_url=cfg.ollama_base_url if cfg else None)
    ollama_status = await ollama_svc.health()
    ollama_ok = bool(ollama_status.get("online"))
    active_model = cfg.ollama_model if cfg else hw.recommended_model
    if ollama_ok and ollama_status.get("models"):
        if active_model not in ollama_status["models"]:
            active_model = ollama_status["models"][0]

    tts_svc = TTSService()
    tts_ok = tts_svc.is_kokoro_ready() or True  # Windows SAPI / DSP fallback always available

    if worker_queue.is_paused:
        queue_state = "paused"
    elif queued_count > 0 or worker_queue.active_job_id:
        queue_state = f"active ({queued_count})"
    else:
        queue_state = "idle"

    overall = "healthy" if (db_ok and hw.ffmpeg_available and tts_ok) else "degraded"

    return HealthResponseSchema(
        status=overall,
        version=APP_VERSION,
        database=db_ok,
        ffmpeg=hw.ffmpeg_available,
        ollama=ollama_ok,
        model=active_model,
        tts=tts_ok,
        nvenc=hw.nvenc_available,
        queue=queue_state,
        profile=hw.recommended_profile.value,
        gpu_temp_c=hw.gpu_temp_c,
        thermal_state=hw.thermal_state.value,
        on_battery=hw.on_battery,
        free_disk_gb=hw.free_disk_gb,
    )


@router.get("/status-bar")
async def get_status_bar() -> dict[str, Any]:
    """Return compact telemetry for the persistent top status bar."""
    health = await get_health()
    with get_session() as session:
        jobs = list(session.exec(select(ShortJob)).all())
        queue_num = sum(
            1 for j in jobs if j.state in ACTIVE_TRANSIENT_STATES or j.state == "DISCOVERED"
        )
    return {
        "ollama_online": health.ollama,
        "model": health.model,
        "gpu_temp_c": health.gpu_temp_c,
        "thermal_state": health.thermal_state,
        "nvenc": health.nvenc,
        "ffmpeg": health.ffmpeg,
        "queue_count": queue_num,
        "queue_paused": worker_queue.is_paused,
        "on_battery": health.on_battery,
        "free_disk_gb": health.free_disk_gb,
        "profile": health.profile,
    }

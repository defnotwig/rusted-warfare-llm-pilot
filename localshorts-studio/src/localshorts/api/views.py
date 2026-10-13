"""Jinja2 server-rendered UI views for LocalShorts Studio."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select

from localshorts.api.generation import serialize_job
from localshorts.api.health import get_health
from localshorts.config import APP_VERSION, AUTHORIZED_MEDIA_DIR, LOGS_DIR, TEMPLATES_DIR
from localshorts.database import get_app_settings, get_session
from localshorts.models.db_models import ShortJob, TrendItem, WatchedTopic
from localshorts.models.schemas import CaptionPreset, VisualPreset
from localshorts.services.hardware import detect_hardware
from localshorts.services.media import get_storage_and_cache_stats
from localshorts.services.ollama import OllamaService
from localshorts.services.scheduler import count_jobs_created_today
from localshorts.services.tts import AVAILABLE_VOICES

router = APIRouter(tags=["views"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

PAGE_TITLES = {
    "wizard": "First-Run Hardware & Pipeline Wizard",
    "dashboard": "Studio Production Dashboard",
    "research": "Trend Discovery & Source Confidence",
    "create": "Create New Short",
    "queue": "Sequential Production Queue",
    "library": "Output Library & Ready-to-Upload",
    "settings": "Studio Settings & Watchlist",
    "diagnostics": "System Diagnostics & Telemetry",
}


def _enrich_job_for_template(j_dict: dict[str, Any]) -> dict[str, Any]:
    """Provide convenient template aliases on serialized jobs."""
    j_dict["topic_title"] = j_dict.get("headline") or j_dict.get("topic") or j_dict.get("slug", "")
    j_dict["quality_score"] = j_dict.get("quality_score_total", 0)
    j_dict["duration_sec"] = j_dict.get("duration_actual_s") or j_dict.get("target_duration_s", 0.0)
    j_dict["final_video_path"] = j_dict.get("video_path", "")
    j_dict["error_message"] = j_dict.get("error_summary", "")
    j_dict.setdefault("script_text", j_dict.get("headline", ""))
    return j_dict


def _enrich_trend_for_template(t: TrendItem) -> dict[str, Any]:
    try:
        reasons = json.loads(t.reasons_json or "[]")
    except Exception:
        reasons = []
    conf = t.source_confidence or 70
    label = "HIGH" if conf >= 75 else ("MEDIUM" if conf >= 50 else "LOW")
    return {
        "id": t.id,
        "provider": t.provider,
        "topic": t.topic,
        "category": t.topic or t.provider,
        "creator": t.creator,
        "headline": t.headline,
        "canonical_title": t.headline,
        "summary": " · ".join(reasons) if reasons else t.headline,
        "url": t.url,
        "published_at": t.published_at,
        "trend_score": t.trend_score,
        "velocity_score": min(100, int(round(t.outlier_multiplier * 50))),
        "source_confidence": conf,
        "confidence_label": label,
        "outlier_multiplier": t.outlier_multiplier,
        "reasons": reasons,
        "sources_count": t.sources_count,
        "source_count": t.sources_count,
        "status": t.status,
    }


async def _common_context(request: Request, active_page: str) -> dict[str, Any]:
    cfg = get_app_settings()
    hw = detect_hardware()
    health = await get_health()
    today_counts = count_jobs_created_today()
    storage = get_storage_and_cache_stats(cache_limit_gb=cfg.cache_limit_gb)

    hw_dict = hw.to_dict()
    hw_dict["physical_cores"] = hw.cores_physical
    hw_dict["logical_threads"] = hw.cores_logical
    hw_dict["gpu_model"] = hw.gpu_name
    hw_dict["ram_total_gb"] = round(hw.ram_total_mb / 1024.0, 1)
    hw_dict["ram_free_gb"] = round(hw.ram_free_mb / 1024.0, 1)
    hw_dict["disk_free_gb"] = hw.free_disk_gb
    hw_dict["nvidia_driver_version"] = hw.nvidia_driver
    hw_dict["gpu_temperature_c"] = hw.gpu_temp_c
    hw_dict["on_ac_power"] = not hw.on_battery
    hw_dict["ollama_service_running"] = health.ollama
    hw_dict["ollama_models"] = [health.model] if health.model else []

    with get_session() as session:
        topics = list(session.exec(select(WatchedTopic).order_by(WatchedTopic.id.asc())).all())
    watched_keywords = [t.name for t in topics if t.enabled]

    # Attach template-friendly aliases onto cfg copy/dict
    cfg_dict = cfg.model_dump()
    cfg_dict["daily_target_shorts"] = cfg.daily_candidate_limit
    cfg_dict["allow_battery_batch"] = cfg.battery_override
    cfg_dict["tts_engine"] = cfg.tts_provider
    cfg_dict["default_visual_preset"] = cfg.visual_preset
    cfg_dict["default_caption_preset"] = cfg.caption_preset
    cfg_dict["niche_name"] = cfg.channel_niche
    cfg_dict["ollama_context"] = cfg.ollama_num_ctx
    cfg_dict["auto_start_windows"] = cfg.windows_startup_enabled
    cfg_dict["pexels_api_key_set"] = bool(cfg.coverr_api_key)
    cfg_dict["pixabay_api_key_set"] = bool(cfg.pixabay_api_key)

    return {
        "request": request,
        "active_page": active_page,
        "active_tab": active_page,
        "page_title": PAGE_TITLES.get(active_page, "LocalShorts Studio"),
        "app_version": APP_VERSION,
        "version": APP_VERSION,
        "settings": cfg_dict,
        "hardware": hw_dict,
        "hw": hw_dict,
        "health": health.model_dump(),
        "today": today_counts,
        "daily_generated": today_counts.get("total_today", 0),
        "ready_count": today_counts.get("ready_today", 0),
        "storage": storage,
        "cache_stats": {
            "total_mb": storage.get("media_cache_mb", 0.0),
            "entry_count": storage.get("media_cache_files", 0),
            "limit_gb": cfg.cache_limit_gb,
        },
        "voices": AVAILABLE_VOICES,
        "visual_presets": [p.value for p in VisualPreset],
        "caption_presets": [p.value for p in CaptionPreset],
        "watched_keywords": watched_keywords,
    }


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_view(request: Request) -> HTMLResponse:
    ctx = await _common_context(request, "dashboard")
    with get_session() as session:
        recent_jobs = list(
            session.exec(select(ShortJob).order_by(ShortJob.created_at.desc()).limit(8)).all()
        )
        trends = list(
            session.exec(select(TrendItem).order_by(TrendItem.trend_score.desc()).limit(8)).all()
        )
    ctx["recent_jobs"] = [_enrich_job_for_template(serialize_job(j)) for j in recent_jobs]
    ctx["top_trends"] = [_enrich_trend_for_template(t) for t in trends]
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@router.get("/wizard", response_class=HTMLResponse)
async def wizard_view(request: Request) -> HTMLResponse:
    ctx = await _common_context(request, "wizard")
    return templates.TemplateResponse(request, "wizard.html", ctx)


@router.get("/research", response_class=HTMLResponse)
@router.get("/trends", response_class=HTMLResponse)
async def research_view(request: Request) -> HTMLResponse:
    ctx = await _common_context(request, "research")
    with get_session() as session:
        trends = list(
            session.exec(
                select(TrendItem)
                .where(TrendItem.status != "IGNORED")
                .order_by(TrendItem.trend_score.desc())
                .limit(40)
            ).all()
        )
    ctx["trends"] = [_enrich_trend_for_template(t) for t in trends]
    ctx["research_briefs"] = []
    return templates.TemplateResponse(request, "research.html", ctx)


@router.get("/create", response_class=HTMLResponse)
async def create_view(request: Request) -> HTMLResponse:
    ctx = await _common_context(request, "create")
    ollama = OllamaService(base_url=ctx["settings"]["ollama_base_url"])
    models_list = await ollama.list_models()
    installed_names = [m.get("name", "") for m in models_list if m.get("name")]
    if ctx["settings"]["ollama_model"] not in installed_names:
        installed_names.insert(0, ctx["settings"]["ollama_model"])
    ctx["installed_models"] = installed_names
    with get_session() as session:
        trends = list(
            session.exec(select(TrendItem).order_by(TrendItem.trend_score.desc()).limit(25)).all()
        )
    ctx["trends"] = [_enrich_trend_for_template(t) for t in trends]
    return templates.TemplateResponse(request, "create.html", ctx)


@router.get("/queue", response_class=HTMLResponse)
async def queue_view(request: Request) -> HTMLResponse:
    ctx = await _common_context(request, "queue")
    with get_session() as session:
        jobs = list(
            session.exec(select(ShortJob).order_by(ShortJob.created_at.desc()).limit(50)).all()
        )
    ctx["jobs"] = [_enrich_job_for_template(serialize_job(j)) for j in jobs]
    return templates.TemplateResponse(request, "queue.html", ctx)


@router.get("/library", response_class=HTMLResponse)
async def library_view(request: Request) -> HTMLResponse:
    ctx = await _common_context(request, "library")
    with get_session() as session:
        jobs = list(
            session.exec(
                select(ShortJob)
                .where(ShortJob.state.in_(["READY", "NEEDS_REVIEW"]))  # type: ignore[arg-type]
                .order_by(ShortJob.created_at.desc())
            ).all()
        )
    items = [_enrich_job_for_template(serialize_job(j)) for j in jobs]
    ctx["library_items"] = items
    ctx["jobs"] = items
    return templates.TemplateResponse(request, "library.html", ctx)


@router.get("/settings", response_class=HTMLResponse)
async def settings_view(request: Request) -> HTMLResponse:
    ctx = await _common_context(request, "settings")
    with get_session() as session:
        topics = list(session.exec(select(WatchedTopic).order_by(WatchedTopic.id.asc())).all())
    ctx["watched_topics"] = [t.model_dump() for t in topics]
    ctx["authorized_media"] = (
        [str(p.name) for p in AUTHORIZED_MEDIA_DIR.rglob("*") if p.is_file() and p.name != ".gitkeep"]
        if AUTHORIZED_MEDIA_DIR.exists()
        else []
    )
    return templates.TemplateResponse(request, "settings.html", ctx)


@router.get("/diagnostics", response_class=HTMLResponse)
async def diagnostics_view(request: Request) -> HTMLResponse:
    ctx = await _common_context(request, "diagnostics")
    log_file = LOGS_DIR / "app.jsonl"
    log_tail: list[str] = []
    if log_file.exists():
        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            log_tail = lines[-40:]
        except Exception:
            log_tail = []
    ctx["log_tail"] = log_tail
    return templates.TemplateResponse(request, "diagnostics.html", ctx)

"""Trend discovery and Research-Only mode endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from localshorts.database import get_app_settings, get_session
from localshorts.models.db_models import TrendItem
from localshorts.services.trends import discover_and_rank_trends
from localshorts.worker.queue import worker_queue

router = APIRouter(prefix="/api/trends", tags=["trends"])


class TrendStatusUpdate(BaseModel):
    status: str  # NEW, SAVED, IGNORED


class GenerateSelectedRequest(BaseModel):
    trend_ids: list[str]
    mode: str | None = None


def _serialize_trend(item: TrendItem) -> dict[str, Any]:
    try:
        reasons = json.loads(item.reasons_json or "[]")
    except Exception:
        reasons = []
    try:
        signals = json.loads(item.signals_json or "{}")
    except Exception:
        signals = {}
    return {
        "id": item.id,
        "provider": item.provider,
        "topic": item.topic,
        "creator": item.creator,
        "headline": item.headline,
        "url": item.url,
        "published_at": item.published_at,
        "trend_score": item.trend_score,
        "source_confidence": item.source_confidence,
        "outlier_multiplier": item.outlier_multiplier,
        "reasons": reasons,
        "signals": signals,
        "sources_count": item.sources_count,
        "status": item.status,
        "discovered_at": item.discovered_at,
    }


@router.get("")
async def list_trends(status: str | None = None) -> list[dict[str, Any]]:
    """Return discovered trend candidates ordered by trend_score DESC."""
    with get_session() as session:
        stmt = select(TrendItem).order_by(TrendItem.trend_score.desc())
        items = list(session.exec(stmt).all())
    if status:
        items = [i for i in items if i.status.upper() == status.upper()]
    else:
        items = [i for i in items if i.status != "IGNORED"]
    return [_serialize_trend(i) for i in items]


@router.post("/scan")
async def scan_trends_research_only() -> dict[str, Any]:
    """
    Execute Research-Only discovery across all public trend feeds:
    fetches feeds, normalizes stories, clusters duplicates, calculates Trend Score,
    Source Confidence, and Outlier Multiplier, and stops before script generation.
    """
    cfg = get_app_settings()
    try:
        custom_urls = json.loads(cfg.custom_rss_urls_json or "[]")
    except Exception:
        custom_urls = []

    ranked = await discover_and_rank_trends(
        custom_rss_urls=custom_urls,
        confidence_threshold=cfg.source_confidence_threshold,
    )
    return {
        "status": "completed",
        "count": len(ranked),
        "candidates": [r.model_dump() for r in ranked],
    }


@router.post("/{trend_id}/status")
async def update_trend_status(trend_id: str, payload: TrendStatusUpdate) -> dict[str, Any]:
    with get_session() as session:
        item = session.get(TrendItem, trend_id)
        if not item:
            raise HTTPException(status_code=404, detail="Trend item not found")
        item.status = payload.status.upper()
        session.add(item)
        session.commit()
        session.refresh(item)
        return _serialize_trend(item)


@router.post("/generate-selected")
async def generate_selected_trends(payload: GenerateSelectedRequest) -> dict[str, Any]:
    """Enqueue one or more user-selected trend candidates from Research-Only view."""
    queued_jobs: list[str] = []
    with get_session() as session:
        for tid in payload.trend_ids:
            t_item = session.get(TrendItem, tid)
            if not t_item:
                continue
            job = worker_queue.enqueue_job(
                topic=t_item.topic or t_item.creator or t_item.headline,
                headline=t_item.headline,
                mode=payload.mode,
                trend_item_id=t_item.id,
            )
            queued_jobs.append(job.id)
    return {"queued_count": len(queued_jobs), "job_ids": queued_jobs}

"""FastAPI application entrypoint and lifespan manager for LocalShorts Studio."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from localshorts.api import generation, health, library, system, trends, views
from localshorts.api import settings as settings_api
from localshorts.config import APP_VERSION, STATIC_DIR, ensure_directories, settings
from localshorts.database import get_app_settings, init_db
from localshorts.logging_config import get_logger, setup_logging
from localshorts.services.audio_sfx import ensure_sfx_assets
from localshorts.services.hardware import persist_hardware_snapshot
from localshorts.services.scheduler import SchedulerService
from localshorts.worker.queue import worker_queue

logger = get_logger("main")


async def _scheduled_batch_callback(count: int) -> None:
    await generation.create_batch_jobs(generation.BatchGenerateRequest(count=count))


scheduler_service = SchedulerService(batch_trigger_cb=_scheduled_batch_callback)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialize SQLite, SFX assets, hardware snapshot, worker queue, and scheduler on startup."""
    setup_logging()
    ensure_directories()
    init_db()
    ensure_sfx_assets()
    persist_hardware_snapshot()
    worker_queue.start()
    scheduler_service.start()
    logger.info("LocalShorts Studio v%s started at http://%s:%d", APP_VERSION, settings.host, settings.port)
    try:
        yield
    finally:
        await scheduler_service.stop()
        await worker_queue.stop()
        logger.info("LocalShorts Studio stopped cleanly.")


app = FastAPI(
    title="LocalShorts Studio — Free Local AI Shorts Factory",
    version=APP_VERSION,
    lifespan=lifespan,
)

STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(health.router)
app.include_router(trends.router)
app.include_router(generation.router)
app.include_router(library.router)
app.include_router(settings_api.router)
app.include_router(system.router)
app.include_router(views.router)


def run_cli() -> None:
    """Start the local web application bound to 127.0.0.1 by default (or 0.0.0.0 if LAN mode enabled)."""
    init_db()
    cfg = get_app_settings()
    bind_host = "0.0.0.0" if cfg.lan_access_enabled else settings.host
    uvicorn.run(
        "localshorts.main:app",
        host=bind_host,
        port=settings.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    run_cli()

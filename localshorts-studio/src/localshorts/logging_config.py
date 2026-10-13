"""Structured rotating logging configuration for LocalShorts Studio."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from localshorts.config import FFMPEG_LOGS_DIR, LOGS_DIR, ensure_directories

_LOGGING_INITIALIZED = False


def setup_logging(level: int = logging.INFO) -> None:
    """Configure rotating file handlers for app.log, worker.log, and setup.log."""
    global _LOGGING_INITIALIZED
    if _LOGGING_INITIALIZED:
        return

    ensure_directories()
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    root_logger = logging.getLogger("localshorts")
    root_logger.setLevel(level)
    root_logger.propagate = False

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    root_logger.addHandler(console_handler)

    # App rotating file handler (10 MB x 5 backups)
    app_handler = RotatingFileHandler(
        LOGS_DIR / "app.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    app_handler.setFormatter(fmt)
    root_logger.addHandler(app_handler)

    # Dedicated worker logger handler
    worker_logger = logging.getLogger("localshorts.worker")
    worker_handler = RotatingFileHandler(
        LOGS_DIR / "worker.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    worker_handler.setFormatter(fmt)
    worker_logger.addHandler(worker_handler)

    _LOGGING_INITIALIZED = True


def get_logger(name: str) -> logging.Logger:
    """Return a named child logger under localshorts."""
    setup_logging()
    if not name.startswith("localshorts"):
        name = f"localshorts.{name}"
    return logging.getLogger(name)


def get_ffmpeg_log_path(job_id: str) -> Path:
    """Return the dedicated FFmpeg log path for a job."""
    ensure_directories()
    safe_id = "".join(ch for ch in job_id if ch.isalnum() or ch in ("-", "_"))[:64] or "job"
    return FFMPEG_LOGS_DIR / f"{safe_id}.log"

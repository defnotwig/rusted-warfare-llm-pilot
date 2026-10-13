"""Laptop-aware catch-up scheduler and user-level Windows Startup registry manager."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any, Callable, Coroutine

from sqlmodel import select

from localshorts.config import PROJECT_ROOT
from localshorts.database import get_app_settings, get_session
from localshorts.logging_config import get_logger
from localshorts.models.db_models import AppSettings, ShortJob
from localshorts.services.hardware import detect_hardware, should_pause_for_battery

logger = get_logger("scheduler")

WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_APP_VALUE_NAME = "LocalShortsStudio"


def set_windows_startup(enabled: bool) -> bool:
    """
    Configure user-level Windows sign-in startup via HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.
    Requires zero Administrator rights. Default is OFF.
    """
    if os.name != "nt":
        return False
    try:
        import winreg

        launcher_bat = (PROJECT_ROOT / "START_LOCAL_SHORTS.bat").resolve()
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            WINDOWS_RUN_KEY,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        ) as key:
            if enabled:
                winreg.SetValueEx(key, WINDOWS_APP_VALUE_NAME, 0, winreg.REG_SZ, f'"{launcher_bat}"')
            else:
                try:
                    winreg.DeleteValue(key, WINDOWS_APP_VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as exc:
        logger.warning("Could not update HKCU Run startup setting: %s", exc)
        return False


def get_windows_startup_status() -> bool:
    """Check whether LocalShortsStudio is registered in HKCU Run."""
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            WINDOWS_RUN_KEY,
            0,
            winreg.KEY_QUERY_VALUE,
        ) as key:
            val, _ = winreg.QueryValueEx(key, WINDOWS_APP_VALUE_NAME)
            return bool(val)
    except Exception:
        return False


def count_jobs_created_today() -> dict[str, int]:
    """Return today's funnel metrics (generated, passed_factual, passed_originality, ready, failed, queued)."""
    today_prefix = datetime.now().strftime("%Y-%m-%d")
    with get_session() as session:
        all_jobs = list(session.exec(select(ShortJob)).all())

    today_jobs = [j for j in all_jobs if (j.created_at or "").startswith(today_prefix) and not j.is_test_video]
    generated = len(today_jobs)
    passed_factual = sum(1 for j in today_jobs if j.source_confidence >= 70)
    passed_orig = sum(1 for j in today_jobs if j.originality_similarity <= 0.78 and j.script_id is not None)
    ready = sum(1 for j in today_jobs if j.state == "READY")
    failed = sum(1 for j in today_jobs if j.state == "FAILED")
    queued = sum(
        1
        for j in all_jobs
        if j.state
        not in ("READY", "FAILED", "CANCELLED", "NEEDS_REVIEW")
    )

    return {
        "generated_today": generated,
        "passed_factual_today": passed_factual,
        "passed_originality_today": passed_orig,
        "ready_today": ready,
        "failed_today": failed,
        "queued_active": queued,
    }


class SchedulerService:
    """
    Laptop-aware scheduler that checks configured daily slots and supports catch-up
    generation after startup without requiring the laptop to stay awake 24/7.
    """

    def __init__(self, batch_trigger_cb: Callable[[int], Coroutine[Any, Any, Any]]) -> None:
        self._batch_trigger_cb = batch_trigger_cb
        self._task: asyncio.Task[None] | None = None
        self._running = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        await asyncio.sleep(8.0)
        while self._running:
            try:
                await self.evaluate_schedule_tick()
            except Exception as exc:
                logger.debug("Scheduler tick notice: %s", exc)
            await asyncio.sleep(60.0)

    async def evaluate_schedule_tick(self) -> int:
        """
        Check if schedule_enabled is True and how many slots have elapsed today vs jobs generated today.
        Returns number of catch-up jobs triggered.
        """
        cfg = get_app_settings()
        if not cfg.schedule_enabled:
            return 0

        hw = detect_hardware()
        if should_pause_for_battery(hw.on_battery, cfg.battery_override, is_batch=True):
            logger.info("Scheduled batch skipped: running on battery without override.")
            return 0

        if hw.free_disk_gb < cfg.min_free_disk_gb:
            logger.warning("Scheduled batch skipped: low disk space (%.1f GB)", hw.free_disk_gb)
            return 0

        now = datetime.now()
        current_hm = now.strftime("%H:%M")
        try:
            slots: list[str] = json.loads(cfg.schedule_hours_json or "[]")
        except Exception:
            slots = []

        # Count daytime/elapsed slots up to current local HH:MM
        due_slots = [s for s in slots if "04:00" <= s <= current_hm]
        counts = count_jobs_created_today()
        already_generated = counts["generated_today"] + counts["queued_active"]
        target_so_far = min(cfg.daily_candidate_limit, max(1, len(due_slots)))

        needed = max(0, target_so_far - already_generated)
        if needed > 0:
            to_enqueue = min(needed, cfg.daily_candidate_limit - already_generated, 3)
            if to_enqueue > 0:
                logger.info("Scheduler triggering %d catch-up candidate job(s)", to_enqueue)
                await self._batch_trigger_cb(to_enqueue)
                with get_session() as session:
                    db_cfg = session.get(AppSettings, 1)
                    if db_cfg:
                        db_cfg.last_scheduled_date = now.strftime("%Y-%m-%d %H:%M")
                        session.add(db_cfg)
                        session.commit()
                return to_enqueue
        return 0

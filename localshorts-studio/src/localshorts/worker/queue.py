"""Single-concurrency sequential background worker queue with crash recovery, pause/resume, and safe cancellation."""

from __future__ import annotations

import asyncio
import json
import uuid

from sqlmodel import select

from localshorts.database import get_app_settings, get_session
from localshorts.logging_config import get_logger
from localshorts.models.db_models import ShortJob, TrendItem, utc_now_iso
from localshorts.models.schemas import JobState
from localshorts.security import sanitize_slug
from localshorts.services.hardware import detect_hardware, should_pause_for_battery
from localshorts.worker.jobs import ORDERED_PIPELINE_STAGES, JobPipelineExecutor

logger = get_logger("worker.queue")

ACTIVE_TRANSIENT_STATES = {
    JobState.RESEARCHING.value,
    JobState.RESEARCHED.value,
    JobState.SCRIPTING.value,
    JobState.TTS.value,
    JobState.CAPTIONS.value,
    JobState.MEDIA.value,
    JobState.SCENE_PLANNING.value,
    JobState.RENDERING.value,
    JobState.TECHNICAL_QC.value,
    JobState.QUALITY_CHECK.value,
}


class WorkerQueue:
    """
    Manages sequential execution (concurrency = 1) of ShortJob items, crash recovery,
    stage regeneration, and cancellation.
    """

    def __init__(self) -> None:
        self.executor = JobPipelineExecutor()
        self._lock = asyncio.Lock()
        self._queue_event = asyncio.Event()
        self._worker_task: asyncio.Task[None] | None = None
        self._running = False
        self._paused = False
        self._active_job_id: str | None = None
        self._cancelled_jobs: set[str] = set()
        self._pending_stage_overrides: dict[str, tuple[JobState, bool]] = {}

    @property
    def active_job_id(self) -> str | None:
        return self._active_job_id

    @property
    def is_paused(self) -> bool:
        return self._paused

    def start(self) -> None:
        """Start the background worker loop and recover any interrupted jobs from SQLite."""
        self.recover_interrupted_jobs()
        if self._worker_task is None or self._worker_task.done():
            self._running = True
            self._worker_task = asyncio.create_task(self._worker_loop())
            self._queue_event.set()

    async def stop(self) -> None:
        self._running = False
        self._queue_event.set()
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    def pause(self) -> None:
        self._paused = True
        logger.info("Worker queue paused")

    def resume(self) -> None:
        self._paused = False
        self._queue_event.set()
        logger.info("Worker queue resumed")

    def recover_interrupted_jobs(self) -> int:
        """
        Inspect SQLite on startup for jobs left in transient states by a previous crash
        and re-queue them from their next incomplete stage.
        """
        recovered = 0
        with get_session() as session:
            stmt = select(ShortJob).where(ShortJob.state.in_(list(ACTIVE_TRANSIENT_STATES)))  # type: ignore[arg-type]
            interrupted = list(session.exec(stmt).all())
            for job in interrupted:
                try:
                    completed = json.loads(job.completed_stages_json or "[]")
                except Exception:
                    completed = []

                resume_stage = JobState.RESEARCHING
                for st in ORDERED_PIPELINE_STAGES:
                    if st.value not in completed:
                        resume_stage = st
                        break

                job.state = JobState.DISCOVERED.value
                job.stage_message = f"Recovered after restart (resuming at {resume_stage.value})"
                job.updated_at = utc_now_iso()
                session.add(job)
                self._pending_stage_overrides[job.id] = (resume_stage, False)
                recovered += 1
            session.commit()

        if recovered > 0:
            logger.info("Recovered %d interrupted job(s) from SQLite", recovered)
        return recovered

    def enqueue_job(
        self,
        topic: str,
        headline: str = "",
        mode: str | None = None,
        trend_item_id: str = "",
        model: str | None = None,
        voice_id: str | None = None,
        tts_speed: float | None = None,
        visual_preset: str | None = None,
        caption_preset: str | None = None,
        target_duration_s: int | None = None,
        priority: int = 10,
        is_test_video: bool = False,
        stop_after_script: bool = False,
    ) -> ShortJob:
        """Create and persist a new ShortJob and notify the sequential worker."""
        cfg = get_app_settings()
        job_id = "job_" + uuid.uuid4().hex[:10]
        chosen_mode = mode or cfg.automation_mode
        if chosen_mode == "ASSISTED":
            stop_after_script = True

        job = ShortJob(
            id=job_id,
            slug=sanitize_slug(headline or topic),
            topic=topic.strip(),
            headline=(headline or topic).strip(),
            mode=chosen_mode,
            state=JobState.DISCOVERED.value,
            progress_pct=5,
            stage_message="Queued for processing",
            priority=priority,
            trend_item_id=trend_item_id,
            model_used=model or cfg.ollama_model,
            voice_id=voice_id or cfg.tts_voice,
            tts_speed=tts_speed if tts_speed is not None else cfg.tts_speed,
            visual_preset=visual_preset or cfg.visual_preset,
            caption_preset=caption_preset or cfg.caption_preset,
            target_duration_s=target_duration_s or cfg.target_duration_s,
            is_test_video=is_test_video,
        )

        with get_session() as session:
            session.add(job)
            if trend_item_id:
                t_item = session.get(TrendItem, trend_item_id)
                if t_item:
                    t_item.status = "QUEUED"
                    session.add(t_item)
            session.commit()
            session.refresh(job)

        self._pending_stage_overrides[job_id] = (JobState.RESEARCHING, stop_after_script)
        self._queue_event.set()
        return job

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a queued or running job and terminate any active FFmpeg child process."""
        self._cancelled_jobs.add(job_id)
        self.executor.renderer.cancel_job_process(job_id)

        with get_session() as session:
            job = session.get(ShortJob, job_id)
            if not job:
                return False
            job.state = JobState.CANCELLED.value
            job.stage_message = "Cancelled by user"
            job.updated_at = utc_now_iso()
            session.add(job)
            session.commit()
        return True

    def retry_job(self, job_id: str, from_failed_stage: bool = True) -> ShortJob | None:
        """Retry a failed, cancelled, or review-paused job from its failed stage or from the beginning."""
        self._cancelled_jobs.discard(job_id)
        with get_session() as session:
            job = session.get(ShortJob, job_id)
            if not job:
                return None

            start_stage = JobState.RESEARCHING
            if from_failed_stage and job.failed_stage:
                try:
                    start_stage = JobState(job.failed_stage)
                except ValueError:
                    start_stage = JobState.RESEARCHING

            job.state = JobState.DISCOVERED.value
            job.stage_message = f"Queued to retry from {start_stage.value}"
            job.retry_count += 1
            job.error_summary = ""
            job.updated_at = utc_now_iso()
            session.add(job)
            session.commit()
            session.refresh(job)

        self._pending_stage_overrides[job_id] = (start_stage, False)
        self._queue_event.set()
        return job

    def request_stage_regeneration(
        self,
        job_id: str,
        stage_name: str,
        new_voice_id: str | None = None,
        new_visual_preset: str | None = None,
        new_caption_preset: str | None = None,
    ) -> ShortJob | None:
        """
        Schedule per-stage regeneration for an existing job without repeating upstream stages:
        - script -> SCRIPTING
        - tts / voice -> TTS
        - captions -> CAPTIONS
        - visuals -> MEDIA
        - scene_plan -> SCENE_PLANNING
        - render -> RENDERING
        """
        stage_map = {
            "script": JobState.SCRIPTING,
            "tts": JobState.TTS,
            "voice": JobState.TTS,
            "captions": JobState.CAPTIONS,
            "visuals": JobState.MEDIA,
            "scene_plan": JobState.SCENE_PLANNING,
            "render": JobState.RENDERING,
        }
        target_stage = stage_map.get(stage_name.lower().strip(), JobState.RENDERING)
        self._cancelled_jobs.discard(job_id)

        with get_session() as session:
            job = session.get(ShortJob, job_id)
            if not job:
                return None
            if new_voice_id:
                job.voice_id = new_voice_id
            if new_visual_preset:
                job.visual_preset = new_visual_preset
            if new_caption_preset:
                job.caption_preset = new_caption_preset
            job.state = JobState.DISCOVERED.value
            job.stage_message = f"Queued for stage regeneration ({target_stage.value})"
            job.updated_at = utc_now_iso()
            session.add(job)
            session.commit()
            session.refresh(job)

        self._pending_stage_overrides[job_id] = (target_stage, False)
        self._queue_event.set()
        return job

    async def process_one_job_now(
        self,
        job_id: str,
        start_stage: JobState = JobState.RESEARCHING,
        stop_after_script: bool = False,
    ) -> ShortJob:
        """Directly execute a specific job under the single-concurrency lock."""
        async with self._lock:
            self._active_job_id = job_id
            try:
                return await self.executor.execute_job(
                    job_id=job_id,
                    start_from_stage=start_stage,
                    stop_after_script=stop_after_script,
                    is_cancelled_cb=lambda: job_id in self._cancelled_jobs,
                )
            finally:
                self._active_job_id = None

    async def _worker_loop(self) -> None:
        while self._running:
            await self._queue_event.wait()
            if not self._running:
                break

            if self._paused:
                await asyncio.sleep(2.0)
                continue

            with get_session() as session:
                stmt = (
                    select(ShortJob)
                    .where(ShortJob.state == JobState.DISCOVERED.value)
                    .order_by(ShortJob.priority.desc(), ShortJob.created_at.asc())
                )
                pending_jobs = list(session.exec(stmt).all())

            if not pending_jobs:
                self._queue_event.clear()
                continue

            cfg = get_app_settings()
            hw = detect_hardware()

            # Check minimum disk space (default 10 GB)
            if hw.free_disk_gb < cfg.min_free_disk_gb:
                logger.warning(
                    "Low disk space (%.1f GB free < %.1f GB minimum); pausing queue",
                    hw.free_disk_gb,
                    cfg.min_free_disk_gb,
                )
                self._paused = True
                continue

            # Check battery guard when multiple jobs are queued
            if len(pending_jobs) > 1 and should_pause_for_battery(
                hw.on_battery, cfg.battery_override, is_batch=True
            ):
                logger.info("Running on battery — batch generation paused by default.")
                await asyncio.sleep(5.0)
                continue

            next_job = pending_jobs[0]
            start_stage, stop_after_script = self._pending_stage_overrides.pop(
                next_job.id,
                (JobState.RESEARCHING, next_job.mode == "ASSISTED"),
            )
            await self.process_one_job_now(
                job_id=next_job.id,
                start_stage=start_stage,
                stop_after_script=stop_after_script,
            )


worker_queue = WorkerQueue()

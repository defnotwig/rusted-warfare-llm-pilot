"""Unit tests for WorkerQueue persistence, crash recovery, and per-stage regeneration."""

from __future__ import annotations

import json
from pathlib import Path

from localshorts.database import get_session, init_db, reset_engine_for_tests
from localshorts.models.db_models import ShortJob
from localshorts.models.schemas import JobState
from localshorts.worker.queue import WorkerQueue


def test_queue_crash_recovery_and_stage_regeneration(tmp_path: Path) -> None:
    test_db = tmp_path / "test_queue.db"
    reset_engine_for_tests(test_db)
    init_db(test_db)

    wq = WorkerQueue()
    job = wq.enqueue_job(
        topic="Test Queue Recovery Topic",
        headline="Test Queue Recovery Headline",
        visual_preset="Tech Explainer",
    )

    # Simulate an interrupted crash while in RENDERING after completing upstream stages
    with get_session(test_db) as session:
        db_job = session.get(ShortJob, job.id)
        assert db_job is not None
        db_job.state = JobState.RENDERING.value
        db_job.completed_stages_json = json.dumps(
            [
                JobState.RESEARCHING.value,
                JobState.RESEARCHED.value,
                JobState.SCRIPTING.value,
                JobState.TTS.value,
                JobState.CAPTIONS.value,
                JobState.MEDIA.value,
                JobState.SCENE_PLANNING.value,
            ]
        )
        session.add(db_job)
        session.commit()

    recovered_count = wq.recover_interrupted_jobs()
    assert recovered_count == 1
    resume_stage, stop_after_script = wq._pending_stage_overrides[job.id]
    assert resume_stage == JobState.RENDERING
    assert stop_after_script is False

    # Test per-stage regeneration request (e.g. regenerate captions only)
    regen_job = wq.request_stage_regeneration(job.id, stage_name="captions", new_caption_preset="High Energy")
    assert regen_job is not None
    assert regen_job.caption_preset == "High Energy"
    target_stage, _ = wq._pending_stage_overrides[job.id]
    assert target_stage == JobState.CAPTIONS

"""16-stage job execution pipeline, per-stage regeneration, diagnostic render, and smoke-test builder."""

from __future__ import annotations

import asyncio
import json
import traceback
from pathlib import Path
from typing import Any, Callable

from localshorts.config import TEMP_CACHE_DIR
from localshorts.database import get_app_settings, get_session
from localshorts.logging_config import get_logger
from localshorts.models.db_models import ScriptRecord, ShortJob, TrendItem, utc_now_iso
from localshorts.models.schemas import (
    STAGE_PROGRESS_MAP,
    CaptionCardSchema,
    EvidenceBundleSchema,
    JobState,
    ScenePlanItemSchema,
    ScriptGenerationSchema,
    ThermalState,
)
from localshorts.services.captions import generate_captions_for_job
from localshorts.services.export import allocate_job_output_dir, write_job_export_bundle
from localshorts.services.hardware import classify_thermal_state, query_nvidia_gpu
from localshorts.services.media import MediaService
from localshorts.services.ollama import OllamaService
from localshorts.services.procedural import render_all_scene_plates
from localshorts.services.quality import run_technical_qc, score_content_quality
from localshorts.services.render import RenderService
from localshorts.services.research import build_evidence_bundle
from localshorts.services.scene_planner import build_scene_plan
from localshorts.services.scripting import ScriptingService, count_spoken_words
from localshorts.services.tts import ClauseTiming, TTSService

logger = get_logger("worker.jobs")

ORDERED_PIPELINE_STAGES: list[JobState] = [
    JobState.RESEARCHING,
    JobState.SCRIPTING,
    JobState.TTS,
    JobState.CAPTIONS,
    JobState.MEDIA,
    JobState.SCENE_PLANNING,
    JobState.RENDERING,
    JobState.TECHNICAL_QC,
    JobState.QUALITY_CHECK,
]


def _update_job_state(
    job_id: str,
    state: JobState,
    message: str | None = None,
    completed_stage: JobState | None = None,
    **extra_fields: Any,
) -> ShortJob:
    """Persist state transition, progress percentage, and completed stage list to SQLite."""
    pct, default_msg = STAGE_PROGRESS_MAP.get(state, (10, state.value))
    with get_session() as session:
        job = session.get(ShortJob, job_id)
        if not job:
            raise ValueError(f"ShortJob {job_id} not found")
        job.state = state.value
        if state not in (JobState.FAILED, JobState.CANCELLED):
            job.progress_pct = pct
        job.stage_message = message or default_msg
        job.updated_at = utc_now_iso()

        if completed_stage is not None:
            try:
                completed = json.loads(job.completed_stages_json or "[]")
            except Exception:
                completed = []
            if completed_stage.value not in completed:
                completed.append(completed_stage.value)
            job.completed_stages_json = json.dumps(completed)

        for k, v in extra_fields.items():
            if hasattr(job, k):
                setattr(job, k, v)

        session.add(job)
        session.commit()
        session.refresh(job)
        return job


async def _enforce_thermal_guard(job_id: str) -> None:
    """Check GPU temperature before GPU-heavy stages; throttle at 80-85C, pause above 85C."""
    gpu_info = query_nvidia_gpu()
    temp_c = gpu_info.get("gpu_temp_c")
    t_state = classify_thermal_state(temp_c)

    if t_state == ThermalState.WARM:
        logger.warning("GPU temp is %s°C (80-85°C); inserting 10s thermal cooldown for job %s", temp_c, job_id)
        _update_job_state(
            job_id,
            JobState.SCRIPTING,
            message=f"Thermal cooldown ({temp_c}°C — waiting 10s)",
        )
        await asyncio.sleep(10.0)
    elif t_state == ThermalState.HOT:
        logger.warning("GPU temp is %s°C (>85°C); pausing GPU stage until temperature < 80°C", temp_c)
        for _ in range(18):
            _update_job_state(
                job_id,
                JobState.SCRIPTING,
                message=f"Paused: GPU temperature {temp_c}°C (>85°C safety limit)",
            )
            await asyncio.sleep(5.0)
            gpu_info = query_nvidia_gpu()
            temp_c = gpu_info.get("gpu_temp_c")
            if classify_thermal_state(temp_c) == ThermalState.NORMAL:
                break


class JobPipelineExecutor:
    """Executes all stages for a ShortJob and supports granular per-stage regeneration."""

    def __init__(self, render_service: RenderService | None = None) -> None:
        self.ollama = OllamaService()
        self.scripting = ScriptingService(ollama=self.ollama)
        self.tts = TTSService()
        self.media = MediaService()
        self.renderer = render_service or RenderService()

    async def execute_job(
        self,
        job_id: str,
        start_from_stage: JobState = JobState.RESEARCHING,
        stop_after_script: bool = False,
        is_cancelled_cb: Callable[[], bool] | None = None,
    ) -> ShortJob:
        """
        Execute a ShortJob sequentially starting from `start_from_stage`, reusing
        any persisted artifacts from earlier stages.
        """
        cfg = get_app_settings()
        with get_session() as session:
            job = session.get(ShortJob, job_id)
            if not job:
                raise ValueError(f"Job '{job_id}' does not exist")
            topic = job.topic
            headline = job.headline or job.topic
            trend_item_id = job.trend_item_id
            mode = job.mode
            model_name = job.model_used or cfg.ollama_model
            voice_id = job.voice_id or cfg.tts_voice
            tts_speed = job.tts_speed or cfg.tts_speed
            visual_preset = job.visual_preset or cfg.visual_preset
            caption_preset = job.caption_preset or cfg.caption_preset
            target_dur = job.target_duration_s or cfg.target_duration_s
            is_test = job.is_test_video
            existing_out_dir = job.output_dir

        job_dir = (
            Path(existing_out_dir)
            if existing_out_dir and Path(existing_out_dir).exists()
            else allocate_job_output_dir(topic=topic, job_id=job_id)
        )
        _update_job_state(job_id, start_from_stage, output_dir=str(job_dir), failed_stage="", error_summary="")

        start_idx = (
            ORDERED_PIPELINE_STAGES.index(start_from_stage)
            if start_from_stage in ORDERED_PIPELINE_STAGES
            else 0
        )
        current_stage = start_from_stage

        try:
            # --- STAGE 1: RESEARCHING & SOURCE CONFIDENCE ---
            evidence_file = job_dir / "sources.json"
            evidence: EvidenceBundleSchema | None = None

            if start_idx <= ORDERED_PIPELINE_STAGES.index(JobState.RESEARCHING) or not evidence_file.exists():
                current_stage = JobState.RESEARCHING
                _update_job_state(job_id, JobState.RESEARCHING, "10% Researching sources")
                if is_cancelled_cb and is_cancelled_cb():
                    raise asyncio.CancelledError("Cancelled during research")

                if trend_item_id:
                    with get_session() as session:
                        t_item = session.get(TrendItem, trend_item_id)
                        if t_item and t_item.evidence_bundle_json:
                            try:
                                evidence = EvidenceBundleSchema.model_validate_json(t_item.evidence_bundle_json)
                            except Exception:
                                evidence = None

                if evidence is None:
                    evidence = build_evidence_bundle(
                        topic=topic,
                        headline=headline,
                        creator="",
                        raw_sources=None,
                        confidence_threshold=cfg.source_confidence_threshold,
                    )

                evidence_file.write_text(evidence.model_dump_json(indent=2), encoding="utf-8")
                (job_dir / "source_manifest.json").write_text(evidence.model_dump_json(indent=2), encoding="utf-8")

                _update_job_state(
                    job_id,
                    JobState.RESEARCHED,
                    "16% Evidence bundle verified",
                    completed_stage=JobState.RESEARCHING,
                    source_confidence=evidence.confidence,
                )

                # Enforce Source Confidence gate in AUTO_FACTORY mode
                if (
                    mode == "AUTO_FACTORY"
                    and not is_test
                    and evidence.confidence < cfg.source_confidence_threshold
                ):
                    return _update_job_state(
                        job_id,
                        JobState.NEEDS_REVIEW,
                        f"Needs Review: Source confidence ({evidence.confidence}) < {cfg.source_confidence_threshold}",
                    )
            else:
                evidence = EvidenceBundleSchema.model_validate_json(evidence_file.read_text(encoding="utf-8"))

            # --- STAGE 2: SCRIPTING & ORIGINALITY ---
            script_file = job_dir / "script.json"
            script_obj: ScriptGenerationSchema | None = None
            orig_sim = 0.0

            if start_idx <= ORDERED_PIPELINE_STAGES.index(JobState.SCRIPTING) or not script_file.exists():
                current_stage = JobState.SCRIPTING
                await _enforce_thermal_guard(job_id)
                _update_job_state(job_id, JobState.SCRIPTING, "22% Writing script")
                if is_cancelled_cb and is_cancelled_cb():
                    raise asyncio.CancelledError("Cancelled during scripting")

                script_obj, orig_report = await self.scripting.generate_script(
                    topic=topic,
                    evidence=evidence,
                    model=model_name,
                    num_ctx=cfg.ollama_num_ctx,
                    target_duration_s=target_dur,
                    originality_threshold=cfg.originality_threshold,
                    job_id=job_id,
                    is_smoke_test=is_test,
                )
                orig_sim = float(orig_report.get("max_similarity", 0.0))

                # Unload Ollama model from GPU VRAM immediately after script generation
                if cfg.unload_model_after_gen and not is_test:
                    await self.ollama.unload_model(model_name)

                script_file.write_text(script_obj.model_dump_json(indent=2), encoding="utf-8")
                (job_dir / "script.txt").write_text(script_obj.script + "\n", encoding="utf-8")

                with get_session() as session:
                    s_rec = ScriptRecord(
                        job_id=job_id,
                        story_id=evidence.story_id,
                        event_summary=script_obj.event_summary,
                        hook=script_obj.hook,
                        script_text=script_obj.script,
                        word_count=count_spoken_words(script_obj.script),
                        visual_search_terms_json=json.dumps(script_obj.visual_search_terms),
                        caption_highlights_json=json.dumps(script_obj.caption_highlights),
                        title_options_json=json.dumps(script_obj.title_options),
                        description=script_obj.description,
                        comment_question=script_obj.comment_question,
                        source_claims_json=json.dumps(script_obj.source_claims),
                        originality_similarity=orig_sim,
                        matched_script_id=orig_report.get("matched_script_id"),
                    )
                    session.add(s_rec)
                    session.commit()
                    session.refresh(s_rec)
                    script_db_id = s_rec.id

                _update_job_state(
                    job_id,
                    JobState.SCRIPT_READY,
                    "28% Script verified & originality checked",
                    completed_stage=JobState.SCRIPTING,
                    script_id=script_db_id,
                    originality_similarity=orig_sim,
                    headline=script_obj.title_options[0] if script_obj.title_options else headline,
                )

                if mode == "AUTO_FACTORY" and not is_test and not orig_report.get("passed", True):
                    return _update_job_state(
                        job_id,
                        JobState.NEEDS_REVIEW,
                        f"Needs Review: Similarity ({orig_sim:.2f}) exceeds originality gate ({cfg.originality_threshold})",
                    )

                if stop_after_script:
                    return _update_job_state(
                        job_id,
                        JobState.SCRIPT_READY,
                        "Script ready — awaiting user review & render approval",
                    )
            else:
                script_obj = ScriptGenerationSchema.model_validate_json(script_file.read_text(encoding="utf-8"))

            # --- STAGE 3: LOCAL CPU TTS ---
            narration_wav = job_dir / "narration.wav"
            clauses_file = job_dir / "clauses.json"
            clauses: list[ClauseTiming] = []

            if (
                start_idx <= ORDERED_PIPELINE_STAGES.index(JobState.TTS)
                or not narration_wav.exists()
                or not clauses_file.exists()
            ):
                current_stage = JobState.TTS
                _update_job_state(job_id, JobState.TTS, "34% Generating narration")
                if is_cancelled_cb and is_cancelled_cb():
                    raise asyncio.CancelledError("Cancelled during TTS")

                tts_res = await asyncio.to_thread(
                    self.tts.synthesize_script,
                    script_text=script_obj.script,
                    output_wav_path=narration_wav,
                    voice=voice_id,
                    speed=tts_speed,
                    preferred_provider=cfg.tts_provider,
                )
                clauses = tts_res.clauses
                clauses_file.write_text(
                    json.dumps(
                        [
                            {
                                "index": c.index,
                                "text": c.text,
                                "start": c.start,
                                "end": c.end,
                                "duration": c.duration,
                            }
                            for c in clauses
                        ],
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                _update_job_state(
                    job_id,
                    JobState.TTS,
                    "40% Narration audio synthesized",
                    completed_stage=JobState.TTS,
                    duration_actual_s=tts_res.duration_seconds,
                )
            else:
                raw_clauses = json.loads(clauses_file.read_text(encoding="utf-8"))
                clauses = [ClauseTiming(**c) for c in raw_clauses]

            # --- STAGE 4: CAPTIONS ---
            captions_ass = job_dir / "captions.ass"
            cards_file = job_dir / "caption_cards.json"
            caption_cards: list[CaptionCardSchema] = []

            if (
                start_idx <= ORDERED_PIPELINE_STAGES.index(JobState.CAPTIONS)
                or not captions_ass.exists()
                or not cards_file.exists()
            ):
                current_stage = JobState.CAPTIONS
                _update_job_state(job_id, JobState.CAPTIONS, "46% Preparing captions")
                if is_cancelled_cb and is_cancelled_cb():
                    raise asyncio.CancelledError("Cancelled during captioning")

                caption_cards, captions_ass, _srt = generate_captions_for_job(
                    clauses=clauses,
                    caption_highlights=script_obj.caption_highlights,
                    output_dir=job_dir,
                    preset=caption_preset,
                )
                cards_file.write_text(
                    json.dumps([c.model_dump() for c in caption_cards], indent=2),
                    encoding="utf-8",
                )
                _update_job_state(
                    job_id,
                    JobState.CAPTIONS,
                    "52% Captions generated",
                    completed_stage=JobState.CAPTIONS,
                )
            else:
                raw_cards = json.loads(cards_file.read_text(encoding="utf-8"))
                caption_cards = [CaptionCardSchema.model_validate(c) for c in raw_cards]

            # --- STAGE 5 & 6: SCENE PLANNING & VISUAL MEDIA ---
            scene_plan_file = job_dir / "scene_plan.json"
            plates_dir = job_dir / "scenes"
            scenes: list[ScenePlanItemSchema] = []
            media_attributions: list[dict[str, str]] = []

            if (
                start_idx <= ORDERED_PIPELINE_STAGES.index(JobState.SCENE_PLANNING)
                or not scene_plan_file.exists()
                or not plates_dir.exists()
            ):
                current_stage = JobState.MEDIA
                _update_job_state(job_id, JobState.MEDIA, "61% Preparing visuals")
                if is_cancelled_cb and is_cancelled_cb():
                    raise asyncio.CancelledError("Cancelled during visual assembly")

                scenes = build_scene_plan(clauses=clauses, script_obj=script_obj, evidence=evidence)
                scenes, media_attributions = await self.media.enrich_scenes_with_media(
                    scenes=scenes,
                    enable_wikimedia=(cfg.enable_wikimedia and not is_test),
                    authorized_media_dir=cfg.authorized_media_dir,
                    cache_limit_gb=cfg.cache_limit_gb,
                )

                current_stage = JobState.SCENE_PLANNING
                _update_job_state(
                    job_id,
                    JobState.SCENE_PLANNING,
                    "68% Rendering 1080x1920 visual plates",
                    completed_stage=JobState.MEDIA,
                )
                scenes = await asyncio.to_thread(
                    render_all_scene_plates,
                    scenes=scenes,
                    plates_dir=plates_dir,
                    preset=visual_preset,
                    topic_title=topic,
                    evidence=evidence,
                )
                scene_plan_file.write_text(
                    json.dumps([s.model_dump() for s in scenes], indent=2),
                    encoding="utf-8",
                )
                _update_job_state(
                    job_id,
                    JobState.SCENE_PLANNING,
                    "72% Scene timeline & plates ready",
                    completed_stage=JobState.SCENE_PLANNING,
                )
            else:
                raw_scenes = json.loads(scene_plan_file.read_text(encoding="utf-8"))
                scenes = [ScenePlanItemSchema.model_validate(s) for s in raw_scenes]
                # Ensure all plates exist on disk
                if any(not s.rendered_plate_path or not Path(s.rendered_plate_path).exists() for s in scenes):
                    scenes = await asyncio.to_thread(
                        render_all_scene_plates,
                        scenes=scenes,
                        plates_dir=plates_dir,
                        preset=visual_preset,
                        topic_title=topic,
                        evidence=evidence,
                    )

            # --- STAGE 7: FFMPEG / NVENC RENDERING ---
            video_mp4 = job_dir / "video.mp4"
            encoder_used = "h264_nvenc"

            if start_idx <= ORDERED_PIPELINE_STAGES.index(JobState.RENDERING) or not video_mp4.exists():
                current_stage = JobState.RENDERING
                await _enforce_thermal_guard(job_id)
                _update_job_state(job_id, JobState.RENDERING, "76% Rendering")
                if is_cancelled_cb and is_cancelled_cb():
                    raise asyncio.CancelledError("Cancelled before FFmpeg encode")

                render_res = await self.renderer.render_short(
                    job_id=job_id,
                    scenes=scenes,
                    narration_wav=narration_wav,
                    captions_ass=captions_ass,
                    output_dir=job_dir,
                    prefer_nvenc=cfg.prefer_nvenc,
                    enable_sfx=cfg.enable_sfx,
                    enable_music=cfg.enable_background_music,
                    is_cancelled_cb=is_cancelled_cb,
                )
                encoder_used = str(render_res.get("encoder_used", "libx264"))
                _update_job_state(
                    job_id,
                    JobState.RENDERING,
                    f"85% Video encoded ({encoder_used})",
                    completed_stage=JobState.RENDERING,
                    encoder_used=encoder_used,
                    video_path=str(video_mp4),
                    thumbnail_path=str(render_res.get("thumbnail_path", "")),
                )

            # --- STAGE 8 & 9: TECHNICAL QC & CONTENT QUALITY SCORING ---
            current_stage = JobState.TECHNICAL_QC
            _update_job_state(job_id, JobState.TECHNICAL_QC, "92% Quality checks")
            tech_qc = run_technical_qc(
                video_path=video_mp4,
                narration_wav_path=narration_wav,
                captions_ass_path=captions_ass,
            )
            if not tech_qc.passed:
                err_msg = "Technical QC failed: " + "; ".join(tech_qc.errors)
                return _update_job_state(
                    job_id,
                    JobState.FAILED,
                    err_msg,
                    failed_stage=JobState.TECHNICAL_QC.value,
                    error_summary=err_msg,
                    technical_qc_passed=False,
                    technical_qc_json=tech_qc.model_dump_json(),
                )

            current_stage = JobState.QUALITY_CHECK
            quality = score_content_quality(
                script_obj=script_obj,
                originality_similarity=orig_sim,
                source_confidence=evidence.confidence,
                scenes=scenes,
                caption_cards=caption_cards,
                duration_s=tech_qc.duration_s,
                threshold=cfg.quality_threshold,
            )

            export_info = write_job_export_bundle(
                job_dir=job_dir,
                job_id=job_id,
                topic=topic,
                creator=evidence.creator,
                script_obj=script_obj,
                evidence=evidence,
                scenes=scenes,
                tech_qc=tech_qc,
                quality=quality,
                media_attributions=media_attributions,
                encoder_used=encoder_used,
                promote_to_ready=(quality.passed_threshold or mode != "AUTO_FACTORY"),
            )

            final_state = (
                JobState.READY
                if (quality.passed_threshold or mode != "AUTO_FACTORY" or is_test)
                else JobState.NEEDS_REVIEW
            )
            final_msg = (
                "100% Ready"
                if final_state == JobState.READY
                else f"Needs Review: Content quality score ({quality.total}/100) < {cfg.quality_threshold}"
            )

            return _update_job_state(
                job_id,
                final_state,
                final_msg,
                completed_stage=JobState.QUALITY_CHECK,
                technical_qc_passed=True,
                technical_qc_json=tech_qc.model_dump_json(),
                quality_score_total=quality.total,
                quality_breakdown_json=quality.model_dump_json(),
                duration_actual_s=tech_qc.duration_s,
                file_size_bytes=tech_qc.file_size_bytes,
                video_path=str(video_mp4),
                ready_video_path=str(export_info.get("ready_video_path", "")),
                completed_at=utc_now_iso(),
            )

        except asyncio.CancelledError:
            self.renderer.cancel_job_process(job_id)
            return _update_job_state(
                job_id,
                JobState.CANCELLED,
                "Cancelled by user",
                failed_stage=current_stage.value,
            )
        except Exception as exc:
            tb = traceback.format_exc()
            logger.error("Job %s failed at stage %s: %s\n%s", job_id, current_stage.value, exc, tb)
            return _update_job_state(
                job_id,
                JobState.FAILED,
                f"Failed at {current_stage.value}: {exc}",
                failed_stage=current_stage.value,
                error_summary=str(exc)[:300],
                error_trace=tb[:4000],
            )


async def render_diagnostic_video(output_dir: Path | None = None) -> dict[str, Any]:
    """
    Render a fast 4-second 1080x1920 diagnostic Short testing:
    Local TTS + Open-Font ASS Caption + Procedural Visual Card + FFmpeg/NVENC + ffprobe QC.
    """
    diag_dir = output_dir or (TEMP_CACHE_DIR / "diagnostic_render")
    diag_dir.mkdir(parents=True, exist_ok=True)

    tts = TTSService()
    wav_path = diag_dir / "diag_narration.wav"
    tts_res = await asyncio.to_thread(
        tts.synthesize_preview,
        output_wav_path=wav_path,
        voice="af_heart",
        speed=1.1,
        sample_text="LocalShorts Studio diagnostic check passed. Hardware video rendering is ready.",
    )

    cards, ass_path, _srt = generate_captions_for_job(
        clauses=tts_res.clauses,
        caption_highlights=["DIAGNOSTIC", "READY"],
        output_dir=diag_dir,
        preset="Bold",
    )

    scene = ScenePlanItemSchema(
        scene_index=0,
        start=0.0,
        end=max(3.0, min(5.0, tts_res.duration_seconds)),
        duration=max(3.0, min(5.0, tts_res.duration_seconds)),
        purpose="hook",
        spoken_text="LocalShorts Studio diagnostic check passed. Hardware video rendering is ready.",
        visual_type="stat_callout",
        visual_query="local ai diagnostic",
        caption_emphasis=["SYSTEM READY"],
        motion="punch_zoom_in",
        zoom_start=1.0,
        zoom_end=1.06,
        sfx="impact",
    )
    scenes = await asyncio.to_thread(
        render_all_scene_plates,
        scenes=[scene],
        plates_dir=diag_dir / "plates",
        preset="Tech Explainer",
        topic_title="Hardware Diagnostic",
    )

    renderer = RenderService()
    render_res = await renderer.render_short(
        job_id="diag_test",
        scenes=scenes,
        narration_wav=wav_path,
        captions_ass=ass_path,
        output_dir=diag_dir,
        prefer_nvenc=True,
        enable_sfx=True,
    )

    video_mp4 = Path(str(render_res["video_path"]))
    qc = run_technical_qc(
        video_path=video_mp4,
        narration_wav_path=wav_path,
        captions_ass_path=ass_path,
        min_duration_s=2.0,
    )
    return {
        "passed": qc.passed,
        "video_path": str(video_mp4),
        "encoder_used": render_res["encoder_used"],
        "duration_s": qc.duration_s,
        "tts_provider": tts_res.provider_used,
        "qc": qc.model_dump(),
    }

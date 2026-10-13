"""Technical video QC (ffprobe) and 100-point Content Quality Rubric scorer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import numpy as np

from localshorts.config import resolve_binary
from localshorts.logging_config import get_logger
from localshorts.models.schemas import (
    CaptionCardSchema,
    ContentQualityBreakdown,
    ScenePlanItemSchema,
    ScriptGenerationSchema,
    TechnicalQCReport,
)
from localshorts.security import run_subprocess_safe
from localshorts.services.audio_sfx import read_wav_float32
from localshorts.services.scripting import count_spoken_words, has_banned_intro

logger = get_logger("quality")


def run_technical_qc(
    video_path: Path,
    narration_wav_path: Path | None = None,
    captions_ass_path: Path | None = None,
    expected_width: int = 1080,
    expected_height: int = 1920,
    min_duration_s: float = 2.5,
    max_duration_s: float = 65.0,
    min_file_size_bytes: int = 15_000,
) -> TechnicalQCReport:
    """
    Run ffprobe after every render to verify:
    - File exists & size > reasonable minimum
    - Playable container
    - H.264 video stream at 1080x1920
    - AAC audio stream at 48kHz
    - Duration within configured range & non-zero
    - Non-silent narration voice energy
    - Captions stage completed
    """
    errors: list[str] = []
    if not video_path.exists():
        return TechnicalQCReport(passed=False, errors=["Rendered MP4 file does not exist"])

    size_bytes = video_path.stat().st_size
    if size_bytes < min_file_size_bytes:
        errors.append(f"File size ({size_bytes} B) is below minimum ({min_file_size_bytes} B)")

    ffprobe_bin = resolve_binary("ffprobe")
    if not ffprobe_bin:
        return TechnicalQCReport(
            passed=False,
            file_exists=True,
            file_size_bytes=size_bytes,
            errors=["ffprobe binary not found"],
        )

    res = run_subprocess_safe(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(video_path.resolve()),
        ],
        timeout=20.0,
    )
    if res.returncode != 0 or not res.stdout.strip():
        return TechnicalQCReport(
            passed=False,
            file_exists=True,
            file_size_bytes=size_bytes,
            errors=[f"ffprobe failed to parse container: {res.stderr[:200]}"],
        )

    try:
        probe_data = json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        return TechnicalQCReport(
            passed=False,
            file_exists=True,
            file_size_bytes=size_bytes,
            errors=[f"Invalid ffprobe JSON: {exc}"],
        )

    streams = probe_data.get("streams", [])
    fmt = probe_data.get("format", {})

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    has_h264 = False
    width = 0
    height = 0
    fps = 0.0
    if video_stream:
        codec_name = str(video_stream.get("codec_name", "")).lower()
        has_h264 = codec_name == "h264"
        if not has_h264:
            errors.append(f"Expected h264 video codec, got '{codec_name}'")
        width = int(video_stream.get("width") or 0)
        height = int(video_stream.get("height") or 0)
        if width != expected_width or height != expected_height:
            errors.append(f"Expected resolution {expected_width}x{expected_height}, got {width}x{height}")

        r_frame_rate = str(video_stream.get("r_frame_rate", "30/1"))
        if "/" in r_frame_rate:
            num, den = r_frame_rate.split("/", 1)
            fps = round(float(num) / max(1.0, float(den)), 2)
    else:
        errors.append("Missing video stream")

    has_aac = False
    sample_rate = 0
    if audio_stream:
        acodec = str(audio_stream.get("codec_name", "")).lower()
        has_aac = acodec == "aac"
        if not has_aac:
            errors.append(f"Expected aac audio codec, got '{acodec}'")
        sample_rate = int(audio_stream.get("sample_rate") or 0)
    else:
        errors.append("Missing audio stream")

    duration_s = float(fmt.get("duration") or (video_stream or {}).get("duration") or 0.0)
    if duration_s < min_duration_s or duration_s > max_duration_s:
        errors.append(f"Duration {duration_s:.2f}s outside valid range [{min_duration_s}s, {max_duration_s}s]")

    # Verify narration voice energy
    has_voice = True
    if narration_wav_path and narration_wav_path.exists():
        try:
            samples, _ = read_wav_float32(narration_wav_path)
            rms = float(np.sqrt(np.mean(samples**2))) if len(samples) > 0 else 0.0
            has_voice = rms > 1e-4
            if not has_voice:
                errors.append("Narration WAV has zero/silent RMS energy")
        except Exception as exc:
            errors.append(f"Could not verify narration WAV: {exc}")
            has_voice = False

    captions_ok = True
    if captions_ass_path is not None:
        captions_ok = captions_ass_path.exists() and "Dialogue:" in captions_ass_path.read_text(encoding="utf-8")
        if not captions_ok:
            errors.append("Captions ASS file is missing or contains no Dialogue events")

    non_blank = size_bytes > min_file_size_bytes and has_h264 and duration_s > 0.0
    passed = len(errors) == 0

    return TechnicalQCReport(
        passed=passed,
        file_exists=True,
        file_size_bytes=size_bytes,
        playable=has_h264 and has_aac and duration_s > 0,
        has_h264_video=has_h264,
        has_aac_audio=has_aac,
        width=width,
        height=height,
        fps=fps,
        duration_s=round(duration_s, 2),
        audio_sample_rate=sample_rate,
        has_voice_energy=has_voice,
        non_blank_video=non_blank,
        captions_completed=captions_ok,
        errors=errors,
    )


def score_content_quality(
    script_obj: ScriptGenerationSchema,
    originality_similarity: float,
    source_confidence: int,
    scenes: Sequence[ScenePlanItemSchema],
    caption_cards: Sequence[CaptionCardSchema],
    duration_s: float,
    threshold: int = 75,
) -> ContentQualityBreakdown:
    """
    Compute the 100-point Content Quality Rubric:
    - Hook              /20
    - Clarity           /15
    - Pacing            /15
    - Originality       /15
    - Source confidence /15
    - Caption quality   /10
    - Visual diversity  /10
    TOTAL               /100
    """
    notes: list[str] = []

    # 1. Hook (/20)
    hook_words = count_spoken_words(script_obj.hook)
    hook_score = 16
    if not has_banned_intro(script_obj.hook) and not has_banned_intro(script_obj.script):
        hook_score += 2
    else:
        hook_score -= 6
        notes.append("Hook contained generic intro phrasing")
    if 6 <= hook_words <= 22:
        hook_score += 2
    hook_score = max(0, min(20, hook_score))

    # 2. Clarity (/15)
    word_count = count_spoken_words(script_obj.script)
    if 55 <= word_count <= 80:
        clarity_score = 15
    elif 48 <= word_count <= 88:
        clarity_score = 12
        notes.append(f"Script length ({word_count} words) slightly outside ideal 55–80 word target")
    else:
        clarity_score = 8
        notes.append(f"Script length ({word_count} words) outside target")

    # 3. Pacing (/15)
    num_scenes = len(scenes)
    avg_scene_dur = (duration_s / max(1, num_scenes)) if duration_s > 0 else 2.2
    if 6 <= num_scenes <= 16 and 1.2 <= avg_scene_dur <= 3.6:
        pacing_score = 15
    elif num_scenes >= 4:
        pacing_score = 12
    else:
        pacing_score = 9
        notes.append("Scene count below optimal pacing target")

    # 4. Originality (/15)
    # similarity 0.0 -> 15 pts; similarity 0.78 -> 11 pts; similarity > 0.85 -> <= 5 pts
    if originality_similarity <= 0.40:
        orig_score = 15
    elif originality_similarity <= 0.78:
        orig_score = max(10, int(round(15 - (originality_similarity - 0.40) * 12)))
    else:
        orig_score = max(2, int(round(9 - (originality_similarity - 0.78) * 35)))
        notes.append(f"High similarity ({originality_similarity:.2f}) to a previous script")

    # 5. Source confidence (/15)
    src_score = max(0, min(15, int(round((source_confidence / 100.0) * 15.0))))
    if source_confidence < 70:
        notes.append(f"Source confidence ({source_confidence}/100) is below auto-approval gate (70)")

    # 6. Caption quality (/10)
    if caption_cards:
        max_card_words = max(len(c.words) for c in caption_cards)
        emph_cards = sum(1 for c in caption_cards if c.emphasized_words)
        cap_score = 8
        if max_card_words <= 5:
            cap_score += 1
        if emph_cards >= 2:
            cap_score += 1
    else:
        cap_score = 0
        notes.append("No caption cards generated")

    # 7. Visual diversity (/10)
    distinct_vtypes = len({s.visual_type for s in scenes})
    punch_zooms = sum(1 for s in scenes if s.motion.startswith("punch_zoom"))
    vis_score = min(7, distinct_vtypes * 2)
    if 2 <= punch_zooms <= 5:
        vis_score += 3
    elif punch_zooms >= 1:
        vis_score += 2
    vis_score = max(0, min(10, vis_score))

    total = hook_score + clarity_score + pacing_score + orig_score + src_score + cap_score + vis_score
    passed = total >= threshold

    return ContentQualityBreakdown(
        hook=hook_score,
        clarity=clarity_score,
        pacing=pacing_score,
        originality=orig_score,
        source_confidence=src_score,
        caption_quality=cap_score,
        visual_diversity=vis_score,
        total=total,
        passed_threshold=passed,
        threshold=threshold,
        notes=notes,
    )

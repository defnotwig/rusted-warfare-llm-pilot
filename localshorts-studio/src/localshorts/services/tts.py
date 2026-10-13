"""Local CPU TTS provider abstraction: Kokoro ONNX (primary) -> Piper ONNX (fallback) -> Windows SAPI5 (offline fallback)."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from localshorts.config import RUNTIME_DIR, resolve_binary
from localshorts.logging_config import get_logger
from localshorts.security import run_subprocess_safe
from localshorts.services.audio_sfx import (
    SAMPLE_RATE,
    _write_wav_mono_or_stereo,
    read_wav_float32,
    resample_linear,
)

logger = get_logger("tts")

KOKORO_MODEL_PATH = RUNTIME_DIR / "models" / "kokoro" / "kokoro-v1.0.onnx"
KOKORO_VOICES_PATH = RUNTIME_DIR / "models" / "kokoro" / "voices-v1.0.bin"

AVAILABLE_VOICES: list[dict[str, str]] = [
    {"id": "af_heart", "name": "Heart (Neutral US Female)", "provider": "kokoro", "lang": "en-us"},
    {"id": "am_adam", "name": "Adam (Neutral US Male)", "provider": "kokoro", "lang": "en-us"},
    {"id": "af_bella", "name": "Bella (Energetic US Female)", "provider": "kokoro", "lang": "en-us"},
    {"id": "am_michael", "name": "Michael (Documentary US Male)", "provider": "kokoro", "lang": "en-us"},
    {"id": "bf_emma", "name": "Emma (Clear UK Female)", "provider": "kokoro", "lang": "en-gb"},
    {"id": "bm_george", "name": "George (Narrator UK Male)", "provider": "kokoro", "lang": "en-gb"},
    {"id": "sapi_default", "name": "Windows SAPI Narrator (Offline)", "provider": "sapi", "lang": "en-us"},
]


@dataclass
class ClauseTiming:
    index: int
    text: str
    start: float
    end: float
    duration: float


@dataclass
class TTSSynthesisResult:
    wav_path: Path
    duration_seconds: float
    provider_used: str
    voice_used: str
    clauses: list[ClauseTiming]


def split_script_into_clauses(script_text: str) -> list[str]:
    """
    Split a 55-80 word narration script into 8-15 natural spoken clauses
    suitable for both breath-paced TTS and scene planning.
    """
    cleaned = re.sub(r"\s+", " ", (script_text or "").strip())
    if not cleaned:
        return ["Local AI processes every frame directly on your own computer."]

    raw_sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    clauses: list[str] = []

    for sent in raw_sentences:
        sent = sent.strip()
        if not sent:
            continue
        words = sent.split()
        if len(words) <= 10:
            clauses.append(sent)
        else:
            # Split longer sentences at commas, conjunctions, or ~8-word chunks
            sub_parts = re.split(r"(?<=[,;—:])\s+|\s+(?=(?:because|while|instead|combined|until|where|when|after)\b)", sent)
            for part in sub_parts:
                part_words = part.strip().split()
                if len(part_words) <= 11:
                    if part.strip():
                        clauses.append(part.strip())
                else:
                    mid = len(part_words) // 2
                    clauses.append(" ".join(part_words[:mid]))
                    clauses.append(" ".join(part_words[mid:]))

    return [c for c in clauses if c.strip()]


class TTSService:
    """Manages local CPU speech synthesis across Kokoro ONNX, Piper, and Windows SAPI5."""

    def __init__(self) -> None:
        self._kokoro_instance: Any = None

    def is_kokoro_ready(self) -> bool:
        return KOKORO_MODEL_PATH.exists() and KOKORO_VOICES_PATH.exists()

    def _get_kokoro(self) -> Any:
        if self._kokoro_instance is not None:
            return self._kokoro_instance
        if not self.is_kokoro_ready():
            return None
        try:
            # Force CPU execution so we never contend with Ollama or NVENC for 4GB VRAM
            os.environ.setdefault("ONNX_PROVIDER", "CPUExecutionProvider")
            from kokoro_onnx import Kokoro

            self._kokoro_instance = Kokoro(str(KOKORO_MODEL_PATH), str(KOKORO_VOICES_PATH))
            return self._kokoro_instance
        except Exception as exc:
            logger.warning("Failed to initialize Kokoro ONNX on CPU: %s", exc)
            return None

    def _synthesize_clause_kokoro(self, text: str, voice: str, speed: float) -> np.ndarray | None:
        kokoro = self._get_kokoro()
        if kokoro is None:
            return None
        try:
            valid_voices = {v["id"] for v in AVAILABLE_VOICES if v["provider"] == "kokoro"}
            chosen_voice = voice if voice in valid_voices else "af_heart"
            lang = "en-gb" if chosen_voice.startswith("b") else "en-us"
            samples, sr = kokoro.create(
                text,
                voice=chosen_voice,
                speed=float(min(max(speed, 0.75), 1.4)),
                lang=lang,
            )
            arr = np.asarray(samples, dtype=np.float32)
            return resample_linear(arr, int(sr), SAMPLE_RATE)
        except Exception as exc:
            logger.warning("Kokoro synthesis failed for clause (%s): %s", text[:30], exc)
            return None

    def _synthesize_clause_piper(self, text: str) -> np.ndarray | None:
        piper_bin = resolve_binary("piper")
        piper_model = RUNTIME_DIR / "models" / "piper" / "en_US-lessac-medium.onnx"
        if not piper_bin or not piper_model.exists():
            return None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            proc = run_subprocess_safe(
                [piper_bin, "--model", str(piper_model), "--output_file", str(tmp_path)],
                timeout=20.0,
            )
            if proc.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 100:
                samples, sr = read_wav_float32(tmp_path)
                tmp_path.unlink(missing_ok=True)
                return resample_linear(samples, sr, SAMPLE_RATE)
            tmp_path.unlink(missing_ok=True)
        except Exception as exc:
            logger.debug("Piper fallback unavailable: %s", exc)
        return None

    def _synthesize_clause_sapi(self, text: str, speed: float) -> np.ndarray | None:
        """Synthesize speech using built-in Windows SAPI5 (System.Speech.Synthesis)."""
        if os.name != "nt":
            return None
        tmp_path = Path(tempfile.mktemp(suffix=".wav"))
        try:
            # Map speed (0.8 - 1.3) to SAPI Rate (-3 to +4)
            sapi_rate = int(round((speed - 1.0) * 10.0))
            sapi_rate = max(-5, min(5, sapi_rate))
            safe_text = text.replace("'", " ").replace('"', " ").replace("`", " ")
            ps_script = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.Rate = {sapi_rate}; "
                f"$s.SetOutputToWaveFile('{tmp_path}'); "
                f"$s.Speak('{safe_text}'); "
                "$s.Dispose();"
            )
            res = run_subprocess_safe(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                timeout=20.0,
            )
            if res.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 200:
                samples, sr = read_wav_float32(tmp_path)
                tmp_path.unlink(missing_ok=True)
                if len(samples) > 100 and float(np.max(np.abs(samples))) > 1e-4:
                    return resample_linear(samples, sr, SAMPLE_RATE)
        except Exception as exc:
            logger.debug("Windows SAPI synthesis warning: %s", exc)
        finally:
            tmp_path.unlink(missing_ok=True)
        return None

    def _synthesize_clause_dsp_emergency(self, text: str, speed: float) -> np.ndarray:
        """
        Last-resort harmonic formant vocal cadence synthesizer if both ONNX models
        and Windows audio devices are stripped in a headless container.
        """
        words = max(1, len(text.split()))
        dur = max(1.1, min(3.8, (words / (2.7 * max(0.8, speed)))))
        n_samples = int(SAMPLE_RATE * dur)
        t = np.linspace(0.0, dur, n_samples, endpoint=False)
        f0 = 135.0 + 18.0 * np.sin(2.0 * np.pi * 2.5 * t)
        signal = (
            0.50 * np.sin(2.0 * np.pi * f0 * t)
            + 0.25 * np.sin(2.0 * np.pi * (f0 * 2.0) * t)
            + 0.15 * np.sin(2.0 * np.pi * (f0 * 3.0) * t)
        )
        syllable_mod = 0.55 + 0.45 * np.sin(2.0 * np.pi * 4.2 * t)
        env = np.minimum(1.0, t / 0.04) * np.minimum(1.0, (dur - t) / 0.06)
        return (signal * syllable_mod * env * 0.45).astype(np.float32)

    def synthesize_script(
        self,
        script_text: str,
        output_wav_path: Path,
        voice: str = "af_heart",
        speed: float = 1.05,
        preferred_provider: str = "kokoro",
    ) -> TTSSynthesisResult:
        """
        Synthesize full script clause-by-clause on CPU, producing narration.wav and
        exact per-clause acoustic timestamps.
        """
        clauses_text = split_script_into_clauses(script_text)
        pause_samples = np.zeros(int(SAMPLE_RATE * 0.11), dtype=np.float32)

        all_chunks: list[np.ndarray] = []
        clause_timings: list[ClauseTiming] = []
        cursor_s = 0.0
        provider_used = "kokoro-onnx"

        for idx, clause in enumerate(clauses_text):
            audio: np.ndarray | None = None
            if preferred_provider != "sapi" and voice != "sapi_default":
                audio = self._synthesize_clause_kokoro(clause, voice=voice, speed=speed)
                if audio is not None:
                    provider_used = "kokoro-onnx"
                else:
                    audio = self._synthesize_clause_piper(clause)
                    if audio is not None:
                        provider_used = "piper-onnx"

            if audio is None:
                audio = self._synthesize_clause_sapi(clause, speed=speed)
                if audio is not None:
                    provider_used = "windows-sapi"
                else:
                    audio = self._synthesize_clause_dsp_emergency(clause, speed=speed)
                    provider_used = "local-dsp-fallback"

            # Trim excessive leading/trailing silence while keeping natural attack/release
            non_silent = np.where(np.abs(audio) > 0.004)[0]
            if len(non_silent) > 10:
                pad = int(SAMPLE_RATE * 0.03)
                start_i = max(0, int(non_silent[0]) - pad)
                end_i = min(len(audio), int(non_silent[-1]) + pad)
                audio = audio[start_i:end_i]

            clause_dur = round(len(audio) / float(SAMPLE_RATE), 3)
            start_s = round(cursor_s, 3)
            end_s = round(start_s + clause_dur, 3)

            clause_timings.append(
                ClauseTiming(
                    index=idx,
                    text=clause,
                    start=start_s,
                    end=end_s,
                    duration=clause_dur,
                )
            )
            all_chunks.append(audio)
            if idx < len(clauses_text) - 1:
                all_chunks.append(pause_samples)
                cursor_s = end_s + (len(pause_samples) / float(SAMPLE_RATE))
            else:
                cursor_s = end_s

        full_audio = np.concatenate(all_chunks) if all_chunks else np.zeros(SAMPLE_RATE, dtype=np.float32)
        _write_wav_mono_or_stereo(output_wav_path, full_audio, SAMPLE_RATE)

        total_dur = round(len(full_audio) / float(SAMPLE_RATE), 3)
        logger.info(
            "Synthesized %d clauses (%.2fs) using %s [voice=%s] -> %s",
            len(clause_timings),
            total_dur,
            provider_used,
            voice,
            output_wav_path,
        )
        return TTSSynthesisResult(
            wav_path=output_wav_path,
            duration_seconds=total_dur,
            provider_used=provider_used,
            voice_used=voice,
            clauses=clause_timings,
        )

    def synthesize_preview(
        self,
        output_wav_path: Path,
        voice: str = "af_heart",
        speed: float = 1.05,
        sample_text: str = "LocalShorts Studio is ready to produce high-retention vertical videos on your laptop.",
    ) -> TTSSynthesisResult:
        """Generate a short 2-4 second voice preview for Settings and First-Run Wizard."""
        return self.synthesize_script(
            script_text=sample_text,
            output_wav_path=output_wav_path,
            voice=voice,
            speed=speed,
        )

"""Royalty-free CC0 DSP sound effect synthesizer and narration-first audio mixer."""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Sequence

import numpy as np

from localshorts.config import MUSIC_DIR, SFX_DIR, ensure_directories
from localshorts.logging_config import get_logger
from localshorts.models.schemas import ScenePlanItemSchema

logger = get_logger("audio_sfx")

SAMPLE_RATE = 48000


def _write_wav_mono_or_stereo(path: Path, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> Path:
    """Write float32 [-1.0, 1.0] samples to a 16-bit PCM stereo WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(samples, -0.95, 0.95)
    if clipped.ndim == 1:
        stereo = np.column_stack([clipped, clipped])
    else:
        stereo = clipped
    pcm16 = (stereo * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())
    return path


def read_wav_float32(path: Path) -> tuple[np.ndarray, int]:
    """Read a 16-bit PCM WAV file into float32 mono samples and return (samples, sample_rate)."""
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        n_channels = wf.getnchannels()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if n_channels > 1:
        pcm = pcm.reshape(-1, n_channels).mean(axis=1)
    return pcm, sr


def resample_linear(samples: np.ndarray, orig_sr: int, target_sr: int = SAMPLE_RATE) -> np.ndarray:
    if orig_sr == target_sr or len(samples) == 0:
        return samples
    duration = len(samples) / float(orig_sr)
    target_len = max(1, int(round(duration * target_sr)))
    x_old = np.linspace(0.0, 1.0, len(samples), endpoint=False)
    x_new = np.linspace(0.0, 1.0, target_len, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float32)


def ensure_sfx_assets() -> dict[str, Path]:
    """
    Deterministically synthesize 5 clean, royalty-free CC0 sound effects into assets/sfx/:
    impact.wav, whoosh.wav, pop.wav, click.wav, riser.wav.
    """
    ensure_directories()
    rng = np.random.default_rng(42)
    sfx_paths: dict[str, Path] = {}

    # 1. impact.wav (0.45s sub-bass punch sweep + crisp transient)
    impact_path = SFX_DIR / "impact.wav"
    if not impact_path.exists():
        t = np.linspace(0, 0.45, int(SAMPLE_RATE * 0.45), endpoint=False)
        freq = 135.0 * np.exp(-t * 9.0) + 42.0
        phase = 2.0 * np.pi * np.cumsum(freq) / SAMPLE_RATE
        env = np.exp(-t * 8.5)
        transient = rng.normal(0, 0.25, len(t)) * np.exp(-t * 45.0)
        sig = (0.75 * np.sin(phase) * env + transient) * 0.65
        _write_wav_mono_or_stereo(impact_path, sig.astype(np.float32))
    sfx_paths["impact"] = impact_path

    # 2. whoosh.wav (0.32s filtered air sweep)
    whoosh_path = SFX_DIR / "whoosh.wav"
    if not whoosh_path.exists():
        t = np.linspace(0, 0.32, int(SAMPLE_RATE * 0.32), endpoint=False)
        noise = rng.normal(0, 1.0, len(t))
        # Moving average lowpass + amplitude bell
        kernel = np.hanning(36)
        kernel /= kernel.sum()
        filtered = np.convolve(noise, kernel, mode="same")
        tone = np.sin(2.0 * np.pi * (220.0 + 380.0 * np.sin(np.pi * t / 0.32)) * t)
        env = np.sin(np.pi * t / 0.32) ** 1.8
        sig = (0.7 * filtered + 0.3 * tone) * env * 0.45
        _write_wav_mono_or_stereo(whoosh_path, sig.astype(np.float32))
    sfx_paths["whoosh"] = whoosh_path

    # 3. pop.wav (0.12s upward pitch bubble pop)
    pop_path = SFX_DIR / "pop.wav"
    if not pop_path.exists():
        t = np.linspace(0, 0.12, int(SAMPLE_RATE * 0.12), endpoint=False)
        freq = 280.0 + 520.0 * (t / 0.12)
        phase = 2.0 * np.pi * np.cumsum(freq) / SAMPLE_RATE
        env = np.sin(np.pi * t / 0.12) * np.exp(-t * 14.0)
        sig = np.sin(phase) * env * 0.55
        _write_wav_mono_or_stereo(pop_path, sig.astype(np.float32))
    sfx_paths["pop"] = pop_path

    # 4. click.wav (0.06s crisp mechanical/UI tick)
    click_path = SFX_DIR / "click.wav"
    if not click_path.exists():
        t = np.linspace(0, 0.06, int(SAMPLE_RATE * 0.06), endpoint=False)
        sig = (np.sin(2.0 * np.pi * 1400.0 * t) + 0.5 * np.sin(2.0 * np.pi * 2800.0 * t)) * np.exp(
            -t * 70.0
        ) * 0.45
        _write_wav_mono_or_stereo(click_path, sig.astype(np.float32))
    sfx_paths["click"] = click_path

    # 5. riser.wav (0.65s subtle tension swell)
    riser_path = SFX_DIR / "riser.wav"
    if not riser_path.exists():
        t = np.linspace(0, 0.65, int(SAMPLE_RATE * 0.65), endpoint=False)
        freq = 180.0 + 480.0 * ((t / 0.65) ** 2)
        phase = 2.0 * np.pi * np.cumsum(freq) / SAMPLE_RATE
        env = ((t / 0.65) ** 1.6) * np.exp(-max(0.0, 1.0) * (1.0 - t / 0.65) * 0.2)
        fade_out = np.minimum(1.0, (0.65 - t) / 0.04)
        sig = np.sin(phase) * env * fade_out * 0.40
        _write_wav_mono_or_stereo(riser_path, sig.astype(np.float32))
    sfx_paths["riser"] = riser_path

    return sfx_paths


def mix_narration_and_sfx(
    narration_wav: Path,
    output_wav: Path,
    scenes: Sequence[ScenePlanItemSchema] | None = None,
    enable_sfx: bool = True,
    enable_music: bool = False,
) -> Path:
    """
    Create the mastered 48kHz stereo audio track:
    - Peak-normalized clean voice narration
    - Subtle transition SFX (-18 dB relative to voice)
    - Optional user-supplied background music from assets/music/ with automatic voice ducking
    - Soft limiter & 30ms fade-in/out to prevent any clipping
    """
    sfx_map = ensure_sfx_assets()
    voice_samples, sr = read_wav_float32(narration_wav)
    voice = resample_linear(voice_samples, sr, SAMPLE_RATE)

    if len(voice) == 0:
        voice = np.zeros(int(SAMPLE_RATE * 5.0), dtype=np.float32)

    # Normalize voice peak to -1.5 dBFS (0.84)
    peak = float(np.max(np.abs(voice)))
    if peak > 1e-4:
        voice = voice * (0.84 / peak)

    mix = voice.copy()

    # Layer subtle SFX on scene transitions (approx -18 dB = 0.125 gain)
    if enable_sfx and scenes:
        for scene in scenes:
            sfx_name = (scene.sfx or "").strip().lower()
            if sfx_name and sfx_name in sfx_map:
                sfx_file = sfx_map[sfx_name]
                if sfx_file.exists():
                    sfx_samples, sfx_sr = read_wav_float32(sfx_file)
                    sfx_resampled = resample_linear(sfx_samples, sfx_sr, SAMPLE_RATE) * 0.13
                    start_idx = max(0, int(scene.start * SAMPLE_RATE))
                    end_idx = min(len(mix), start_idx + len(sfx_resampled))
                    seg_len = end_idx - start_idx
                    if seg_len > 0:
                        mix[start_idx:end_idx] += sfx_resampled[:seg_len]

    # Optional user-licensed background music with automatic narration ducking
    if enable_music and MUSIC_DIR.exists():
        music_files = list(MUSIC_DIR.glob("*.wav"))
        if music_files:
            try:
                mus_samples, mus_sr = read_wav_float32(music_files[0])
                mus = resample_linear(mus_samples, mus_sr, SAMPLE_RATE)
                if len(mus) > 0:
                    reps = (len(mix) // len(mus)) + 1
                    mus_tiled = np.tile(mus, reps)[: len(mix)]
                    # Compute voice activity envelope for ducking
                    win = int(SAMPLE_RATE * 0.08)
                    voice_env = np.convolve(np.abs(voice), np.ones(win) / max(1, win), mode="same")
                    duck_gain = np.where(voice_env > 0.03, 0.05, 0.14).astype(np.float32)
                    mix += mus_tiled * duck_gain
            except Exception as exc:
                logger.warning("Could not mix background music: %s", exc)

    # Apply 30ms fade-in and fade-out
    fade_samples = min(int(SAMPLE_RATE * 0.03), len(mix) // 4)
    if fade_samples > 1:
        fade_in = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
        fade_out = np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
        mix[:fade_samples] *= fade_in
        mix[-fade_samples:] *= fade_out

    # Soft tanh limiter at 0.89 ceiling so final track never clips
    mix = np.tanh(mix * 1.05) * 0.89

    return _write_wav_mono_or_stereo(output_wav, mix, SAMPLE_RATE)

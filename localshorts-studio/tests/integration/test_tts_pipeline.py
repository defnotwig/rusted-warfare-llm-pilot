"""Integration test for local CPU TTS synthesis and CC0 SFX audio mastering."""

from __future__ import annotations

from pathlib import Path

from localshorts.services.audio_sfx import (
    ensure_sfx_assets,
    mix_narration_and_sfx,
    read_wav_float32,
)
from localshorts.services.tts import TTSService


def test_local_tts_and_audio_mixer(tmp_path: Path) -> None:
    sfx_map = ensure_sfx_assets()
    assert len(sfx_map) == 5
    for name in ("impact", "whoosh", "pop", "click", "riser"):
        assert sfx_map[name].exists()
        assert sfx_map[name].stat().st_size > 1000

    tts = TTSService()
    narration_wav = tmp_path / "narration.wav"
    res = tts.synthesize_script(
        script_text=(
            "Most AI tools send every word you type straight to a remote cloud server. "
            "Local AI runs compact models directly on your laptop GPU with zero monthly fees."
        ),
        output_wav_path=narration_wav,
        voice="af_heart",
        speed=1.05,
    )
    assert res.wav_path.exists()
    assert res.duration_seconds > 2.0
    assert len(res.clauses) >= 2

    mixed_wav = tmp_path / "mixed.wav"
    mix_narration_and_sfx(
        narration_wav=narration_wav,
        output_wav=mixed_wav,
        scenes=[],
        enable_sfx=True,
    )
    assert mixed_wav.exists()
    samples, sr = read_wav_float32(mixed_wav)
    assert sr == 48000
    assert len(samples) > 48000

"""Integration test for real FFmpeg 1080x1920 vertical rendering and ffprobe Technical QC."""

from __future__ import annotations

import pytest

from localshorts.worker.jobs import render_diagnostic_video


@pytest.mark.asyncio
async def test_live_ffmpeg_render_and_ffprobe_qc() -> None:
    report = await render_diagnostic_video()
    assert report.get("passed") is True, f"Diagnostic render failed: {report}"
    qc = report.get("qc", {})
    assert qc.get("width") == 1080
    assert qc.get("height") == 1920
    assert qc.get("has_h264_video") is True
    assert qc.get("has_aac_audio") is True
    assert report.get("duration_s", 0.0) >= 2.5
    assert report.get("encoder_used") in ("h264_nvenc", "libx264")

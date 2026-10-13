"""Unit tests for FFmpeg scene zoompan filter builder, final mux command, and NVENC fallback args."""

from __future__ import annotations

from pathlib import Path

from localshorts.models.schemas import ScenePlanItemSchema
from localshorts.services.nvenc import get_video_encoder_args
from localshorts.services.render import build_final_mux_command, build_scene_zoompan_filter


def test_build_scene_zoompan_filter_produces_1080x1920_yuv420p() -> None:
    scene = ScenePlanItemSchema(
        scene_index=1,
        start=0.0,
        end=2.5,
        duration=2.5,
        purpose="hook",
        spoken_text="4GB VRAM runs local AI models privately.",
        visual_type="stat_callout",
        visual_query="4GB VRAM",
        motion="punch_zoom_in",
        zoom_start=1.0,
        zoom_end=1.08,
        sfx="impact",
    )
    vf = build_scene_zoompan_filter(scene, fps=30)
    assert "s=1080x1920:fps=30" in vf
    assert "format=yuv420p" in vf
    assert "zoompan=" in vf


def test_build_final_mux_command_avoids_shell_interpolation(tmp_path: Path) -> None:
    enc_name, enc_args = get_video_encoder_args(prefer_nvenc=False)
    assert enc_name == "libx264"
    cmd = build_final_mux_command(
        ffmpeg_bin="ffmpeg.exe",
        concat_list_path=tmp_path / "concat.txt",
        mixed_audio_path=tmp_path / "mixed.wav",
        ass_filename="captions.ass",
        fonts_rel_dir="fonts",
        output_mp4_path=tmp_path / "video.mp4",
        encoder_args=enc_args,
        total_duration_s=18.5,
    )
    assert isinstance(cmd, list)
    assert "-c:v" in cmd
    assert "libx264" in cmd
    assert "-c:a" in cmd
    assert "aac" in cmd
    assert "+faststart" in cmd

"""NVENC hardware encoder detection and FFmpeg encoder argument builder."""

from __future__ import annotations

from localshorts.config import resolve_binary
from localshorts.logging_config import get_logger
from localshorts.security import run_subprocess_safe

logger = get_logger("nvenc")

_CACHED_NVENC_AVAILABLE: bool | None = None


def probe_nvenc_support(force_refresh: bool = False) -> bool:
    """
    Execute an actual 0.1-second FFmpeg null-encode probe to verify h264_nvenc works
    with the current GPU and NVIDIA driver.
    """
    global _CACHED_NVENC_AVAILABLE
    if _CACHED_NVENC_AVAILABLE is not None and not force_refresh:
        return _CACHED_NVENC_AVAILABLE

    ffmpeg_bin = resolve_binary("ffmpeg")
    if not ffmpeg_bin:
        _CACHED_NVENC_AVAILABLE = False
        return False

    try:
        res = run_subprocess_safe(
            [
                ffmpeg_bin,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=256x256:d=0.1:r=30",
                "-c:v",
                "h264_nvenc",
                "-f",
                "null",
                "-",
            ],
            timeout=12.0,
        )
        _CACHED_NVENC_AVAILABLE = res.returncode == 0
    except Exception as exc:
        logger.warning("NVENC probe failed (%s); falling back to libx264", exc)
        _CACHED_NVENC_AVAILABLE = False

    return _CACHED_NVENC_AVAILABLE


def get_video_encoder_args(prefer_nvenc: bool = True, force_cpu: bool = False) -> tuple[str, list[str]]:
    """
    Return (encoder_name, ffmpeg_video_codec_args) tuned for 1080x1920 30fps Shorts.
    Uses balanced quality settings on h264_nvenc and clean fallback to libx264.
    """
    if not force_cpu and prefer_nvenc and probe_nvenc_support():
        return (
            "h264_nvenc",
            [
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p4",
                "-rc",
                "vbr",
                "-cq",
                "23",
                "-b:v",
                "6M",
                "-maxrate",
                "10M",
                "-bufsize",
                "12M",
                "-pix_fmt",
                "yuv420p",
                "-r",
                "30",
            ],
        )

    return (
        "libx264",
        [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "21",
            "-maxrate",
            "8M",
            "-bufsize",
            "12M",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
        ],
    )

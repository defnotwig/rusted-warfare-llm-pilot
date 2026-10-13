"""FFmpeg scene-based editing engine with punch-zooms, ASS subtitle burn-in, audio mastering, and NVENC/libx264 encoding."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Callable, Sequence

from localshorts.config import FONTS_DIR, TEMP_CACHE_DIR, resolve_binary
from localshorts.logging_config import get_ffmpeg_log_path, get_logger
from localshorts.models.schemas import ScenePlanItemSchema
from localshorts.services.audio_sfx import mix_narration_and_sfx
from localshorts.services.nvenc import get_video_encoder_args

logger = get_logger("render")


def build_scene_zoompan_filter(scene: ScenePlanItemSchema, fps: int = 30) -> str:
    """
    Build an FFmpeg video filter string for a single scene (duration_s @ 30fps, 1080x1920)
    applying subtle punch-zooms (1.00 -> 1.05/1.08/1.12) or smooth drift without jitter.
    """
    frames = max(15, int(round(scene.duration * fps)))
    z0 = scene.zoom_start
    z1 = scene.zoom_end
    delta = max(0.01, z1 - z0)

    if scene.motion.startswith("punch_zoom"):
        # Smooth ease-in punch zoom centered on upper-middle focal area
        z_expr = f"min({z0:.3f}+{delta:.4f}*(on/{frames}),{z1:.3f})"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih*0.42-(ih/zoom*0.42)"
    elif scene.motion == "slow_pan_up":
        z_expr = f"{max(1.04, z1):.3f}"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = f"(ih-(ih/zoom))*(1.0-on/{frames})"
    else:
        z_expr = f"min(1.000+0.030*(on/{frames}),1.030)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"

    return (
        f"scale=1200:2134:force_original_aspect_ratio=increase,crop=1200:2134,"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s=1080x1920:fps={fps},"
        f"format=yuv420p"
    )


def build_final_mux_command(
    ffmpeg_bin: str,
    concat_list_path: Path,
    mixed_audio_path: Path,
    ass_filename: str,
    fonts_rel_dir: str,
    output_mp4_path: Path,
    encoder_args: Sequence[str],
    total_duration_s: float,
) -> list[str]:
    """
    Build the argument array for final FFmpeg composition (concatenated scenes + mastered WAV + ASS burn-in).
    Runs with cwd set to the job working directory so relative paths avoid Windows drive-letter colon issues.
    """
    vf_filter = f"ass=filename={ass_filename}:fontsdir={fonts_rel_dir},format=yuv420p"
    return [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_path),
        "-i",
        str(mixed_audio_path),
        "-vf",
        vf_filter,
        *encoder_args,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-t",
        f"{total_duration_s:.3f}",
        "-movflags",
        "+faststart",
        str(output_mp4_path),
    ]


class RenderService:
    """Executes scene-level motion rendering, audio mixing, subtitle burn-in, and NVENC/CPU encoding."""

    def __init__(self) -> None:
        self._active_processes: dict[str, asyncio.subprocess.Process] = {}

    def cancel_job_process(self, job_id: str) -> bool:
        """Terminate an active FFmpeg child process safely if the user cancels the job."""
        proc = self._active_processes.pop(job_id, None)
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                return True
            except Exception as exc:
                logger.warning("Error terminating FFmpeg process for %s: %s", job_id, exc)
        return False

    async def _run_ffmpeg_logged(
        self,
        job_id: str,
        args: list[str],
        cwd: Path,
        log_file: Path,
        timeout: float = 240.0,
    ) -> tuple[int, str]:
        import subprocess

        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )
        self._active_processes[job_id] = proc
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise TimeoutError(f"FFmpeg timed out after {timeout}s")
        finally:
            self._active_processes.pop(job_id, None)

        combined_log = (
            f"CMD: {' '.join(args)}\n"
            f"EXIT: {proc.returncode}\n"
            f"STDOUT:\n{stdout_b.decode('utf-8', errors='replace')}\n"
            f"STDERR:\n{stderr_b.decode('utf-8', errors='replace')}\n"
        )
        with log_file.open("a", encoding="utf-8") as lf:
            lf.write(combined_log + "\n" + ("=" * 60) + "\n")

        return proc.returncode or 0, stderr_b.decode("utf-8", errors="replace")

    async def render_short(
        self,
        job_id: str,
        scenes: Sequence[ScenePlanItemSchema],
        narration_wav: Path,
        captions_ass: Path,
        output_dir: Path,
        prefer_nvenc: bool = True,
        enable_sfx: bool = True,
        enable_music: bool = False,
        is_cancelled_cb: Callable[[], bool] | None = None,
    ) -> dict[str, str | float]:
        """
        Render all scenes, mix audio, burn in ASS captions, extract thumbnail.jpg,
        and encode video.mp4 using h264_nvenc (with automatic fallback to libx264).
        """
        ffmpeg_bin = resolve_binary("ffmpeg")
        if not ffmpeg_bin:
            raise RuntimeError("FFmpeg executable not found. Run START_LOCAL_SHORTS.bat to install FFmpeg.")

        output_dir.mkdir(parents=True, exist_ok=True)
        temp_job_dir = TEMP_CACHE_DIR / job_id
        temp_job_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg_log = get_ffmpeg_log_path(job_id)
        render_log_copy = output_dir / "render.log"

        try:
            # 1. Master audio track (narration + subtle DSP SFX + optional ducked music)
            mixed_wav = temp_job_dir / "narration_mixed.wav"
            mix_narration_and_sfx(
                narration_wav=narration_wav,
                output_wav=mixed_wav,
                scenes=scenes,
                enable_sfx=enable_sfx,
                enable_music=enable_music,
            )

            # 2. Render each scene clip with its motion/zoompan profile
            scene_clip_paths: list[Path] = []
            for scene in scenes:
                if is_cancelled_cb and is_cancelled_cb():
                    raise asyncio.CancelledError("Job cancelled during scene rendering")

                plate_path = Path(scene.rendered_plate_path)
                if not plate_path.exists():
                    raise FileNotFoundError(f"Scene plate missing: {plate_path}")

                clip_path = temp_job_dir / f"scene_clip_{scene.scene_index:02d}.mp4"
                vf_zoom = build_scene_zoompan_filter(scene, fps=30)
                scene_args = [
                    ffmpeg_bin,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-loop",
                    "1",
                    "-i",
                    str(plate_path.resolve()),
                    "-vf",
                    vf_zoom,
                    "-t",
                    f"{scene.duration:.3f}",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                    "-r",
                    "30",
                    "-an",
                    str(clip_path.resolve()),
                ]
                rc, err = await self._run_ffmpeg_logged(
                    job_id=job_id,
                    args=scene_args,
                    cwd=temp_job_dir,
                    log_file=ffmpeg_log,
                )
                if rc != 0 or not clip_path.exists():
                    raise RuntimeError(f"Scene {scene.scene_index} render failed: {err[:300]}")
                scene_clip_paths.append(clip_path)

            if is_cancelled_cb and is_cancelled_cb():
                raise asyncio.CancelledError("Job cancelled before final encode")

            # 3. Write concat demuxer list
            concat_txt = temp_job_dir / "concat_list.txt"
            concat_lines = [f"file '{p.resolve().as_posix()}'" for p in scene_clip_paths]
            concat_txt.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

            # Copy captions.ass and fonts into temp_job_dir so relative filter paths never break on Windows drive colons
            local_ass = temp_job_dir / "captions.ass"
            shutil.copy2(captions_ass, local_ass)
            local_fonts_dir = temp_job_dir / "fonts"
            if FONTS_DIR.exists():
                shutil.copytree(FONTS_DIR, local_fonts_dir, dirs_exist_ok=True)
            else:
                local_fonts_dir.mkdir(parents=True, exist_ok=True)

            total_duration = sum(s.duration for s in scenes)
            final_mp4 = output_dir / "video.mp4"

            encoder_name, encoder_args = get_video_encoder_args(prefer_nvenc=prefer_nvenc, force_cpu=False)
            mux_cmd = build_final_mux_command(
                ffmpeg_bin=ffmpeg_bin,
                concat_list_path=concat_txt.resolve(),
                mixed_audio_path=mixed_wav.resolve(),
                ass_filename="captions.ass",
                fonts_rel_dir="fonts",
                output_mp4_path=final_mp4.resolve(),
                encoder_args=encoder_args,
                total_duration_s=total_duration,
            )

            rc, err = await self._run_ffmpeg_logged(
                job_id=job_id,
                args=mux_cmd,
                cwd=temp_job_dir,
                log_file=ffmpeg_log,
            )

            # Automatic libx264 fallback if h264_nvenc failed at runtime
            if (rc != 0 or not final_mp4.exists()) and encoder_name == "h264_nvenc":
                logger.warning("h264_nvenc encode failed (%s); retrying with libx264 CPU fallback...", err[:200])
                encoder_name, encoder_args = get_video_encoder_args(prefer_nvenc=False, force_cpu=True)
                mux_cmd = build_final_mux_command(
                    ffmpeg_bin=ffmpeg_bin,
                    concat_list_path=concat_txt.resolve(),
                    mixed_audio_path=mixed_wav.resolve(),
                    ass_filename="captions.ass",
                    fonts_rel_dir="fonts",
                    output_mp4_path=final_mp4.resolve(),
                    encoder_args=encoder_args,
                    total_duration_s=total_duration,
                )
                rc, err = await self._run_ffmpeg_logged(
                    job_id=job_id,
                    args=mux_cmd,
                    cwd=temp_job_dir,
                    log_file=ffmpeg_log,
                )
                encoder_name = "libx264 (fallback)"

            if rc != 0 or not final_mp4.exists():
                raise RuntimeError(f"Final video encode failed: {err[:400]}")

            # 4. Extract thumbnail.jpg at t=0.8s
            thumb_jpg = output_dir / "thumbnail.jpg"
            thumb_cmd = [
                ffmpeg_bin,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                "0.5",
                "-i",
                str(final_mp4.resolve()),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(thumb_jpg.resolve()),
            ]
            await self._run_ffmpeg_logged(
                job_id=job_id,
                args=thumb_cmd,
                cwd=temp_job_dir,
                log_file=ffmpeg_log,
            )

            if ffmpeg_log.exists():
                shutil.copy2(ffmpeg_log, render_log_copy)

            return {
                "video_path": str(final_mp4),
                "thumbnail_path": str(thumb_jpg) if thumb_jpg.exists() else "",
                "encoder_used": encoder_name,
                "duration_s": round(total_duration, 2),
            }
        finally:
            # Clean up temporary intermediate scene clips
            shutil.rmtree(temp_job_dir, ignore_errors=True)

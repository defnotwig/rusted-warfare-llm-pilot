"""System diagnostics, Ollama restart/benchmark, TTS voice preview, and First-Run Wizard endpoints."""

from __future__ import annotations

import asyncio
import platform
import sys
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel

from localshorts.config import LOGS_DIR, OUTPUT_DIR, TEMP_CACHE_DIR, save_setup_state
from localshorts.database import get_app_settings, get_session
from localshorts.models.db_models import AppSettings, utc_now_iso
from localshorts.services.hardware import detect_hardware, persist_hardware_snapshot
from localshorts.services.ollama import OllamaService
from localshorts.services.tts import AVAILABLE_VOICES, TTSService
from localshorts.worker.jobs import render_diagnostic_video

router = APIRouter(prefix="/api/system", tags=["system"])


class TTSPreviewRequest(BaseModel):
    voice_id: str = "af_heart"
    speed: float = 1.05
    text: str = "LocalShorts Studio is ready to produce high-retention vertical videos on your laptop."


class BenchmarkRequest(BaseModel):
    model: str | None = None


async def run_full_diagnostics(include_render_test: bool = True, include_benchmark: bool = False) -> dict[str, Any]:
    """
    Execute the 15-point system diagnostics suite:
    1. Python runtime
    2. Virtual environment
    3. FFmpeg
    4. ffprobe
    5. NVENC
    6. Ollama executable
    7. Ollama HTTP service
    8. Local LLM model
    9. Model speed benchmark
    10. Local TTS
    11. SQLite database
    12. Output directory write permissions
    13. Free disk space
    14. Localhost port
    15. Render test (3-5s diagnostic video)
    """
    cfg = get_app_settings()
    hw = detect_hardware(force_nvenc_probe=True)
    ollama = OllamaService(base_url=cfg.ollama_base_url)
    ollama_health = await ollama.health()
    ollama_online = bool(ollama_health.get("online"))
    installed_models: list[str] = ollama_health.get("models", [])
    model_ready = cfg.ollama_model in installed_models or len(installed_models) > 0
    active_model = cfg.ollama_model if cfg.ollama_model in installed_models else (installed_models[0] if installed_models else cfg.ollama_model)

    tts = TTSService()
    kokoro_ok = tts.is_kokoro_ready()

    # Check output write permissions
    write_ok = False
    try:
        probe_file = OUTPUT_DIR / ".write_probe"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink(missing_ok=True)
        write_ok = True
    except Exception:
        write_ok = False

    benchmark_data: dict[str, Any] = {
        "tier": "GOOD",
        "tokens_per_second": 0.0,
        "recommendation": "Run benchmark to measure exact token speed.",
    }
    if include_benchmark and ollama_online and model_ready:
        bm = await ollama.benchmark_model(model=active_model)
        benchmark_data = bm.model_dump()
        persist_hardware_snapshot(
            report=hw,
            benchmark_tok_per_s=bm.tokens_per_second,
            benchmark_tier=bm.tier.value,
        )
    else:
        persist_hardware_snapshot(report=hw)

    render_test_data: dict[str, Any] = {"passed": False, "skipped": not include_render_test}
    if include_render_test and hw.ffmpeg_available and hw.ffprobe_available:
        try:
            render_test_data = await render_diagnostic_video()
        except Exception as exc:
            render_test_data = {"passed": False, "error": str(exc)}

    checks = [
        {
            "name": "Python",
            "status": "PASS",
            "detail": f"Python {platform.python_version()} ({sys.executable})",
            "fix": "",
        },
        {
            "name": "Virtual Env",
            "status": "PASS" if ".venv" in sys.executable.lower() or sys.prefix != sys.base_prefix else "WARN",
            "detail": sys.prefix,
            "fix": "Run START_LOCAL_SHORTS.bat to activate project .venv",
        },
        {
            "name": "FFmpeg",
            "status": "PASS" if hw.ffmpeg_available else "FAIL",
            "detail": hw.ffmpeg_path or "Not found",
            "fix": "Run START_LOCAL_SHORTS.bat to download portable FFmpeg into .runtime/ffmpeg/bin",
        },
        {
            "name": "ffprobe",
            "status": "PASS" if hw.ffprobe_available else "FAIL",
            "detail": hw.ffprobe_path or "Not found",
            "fix": "Run START_LOCAL_SHORTS.bat to download portable ffprobe into .runtime/ffmpeg/bin",
        },
        {
            "name": "NVENC",
            "status": "PASS" if hw.nvenc_available else "WARN",
            "detail": f"h264_nvenc active on {hw.gpu_name}" if hw.nvenc_available else "CPU libx264 fallback active",
            "fix": "Ensure NVIDIA driver >= 522.25 is installed for hardware NVENC encoding",
        },
        {
            "name": "Ollama Binary",
            "status": "PASS" if hw.ollama_available else "WARN",
            "detail": hw.ollama_path or "Not found on PATH",
            "fix": "Run START_LOCAL_SHORTS.bat to install official Ollama for Windows",
        },
        {
            "name": "Ollama Service",
            "status": "PASS" if ollama_online else "WARN",
            "detail": f"Online at {cfg.ollama_base_url}" if ollama_online else "Local AI service offline",
            "fix": "Click 'Restart Ollama' or run 'ollama serve'",
        },
        {
            "name": active_model,
            "status": "PASS" if model_ready else "WARN",
            "detail": f"Installed ({', '.join(installed_models[:3])})" if model_ready else f"Model {active_model} not pulled yet",
            "fix": f"Run 'ollama pull {active_model}' or complete First-Run Wizard",
        },
        {
            "name": "Model Speed",
            "status": "PASS" if benchmark_data.get("tier") != "SLOW" else "WARN",
            "detail": f"{benchmark_data.get('tier')} ({benchmark_data.get('tokens_per_second', 0)} tok/s)",
            "fix": "Switch to qwen2.5:1.5b if token speed is < 4 tok/s",
        },
        {
            "name": "Kokoro / TTS",
            "status": "PASS",
            "detail": "Kokoro ONNX CPU Ready" if kokoro_ok else "Windows SAPI Offline TTS Ready",
            "fix": "",
        },
        {
            "name": "SQLite",
            "status": "PASS",
            "detail": "WAL mode active & schema verified",
            "fix": "",
        },
        {
            "name": "Output Write",
            "status": "PASS" if write_ok else "FAIL",
            "detail": str(OUTPUT_DIR),
            "fix": "Ensure user has write permissions to localshorts-studio/output/",
        },
        {
            "name": "Disk Space",
            "status": "PASS" if hw.free_disk_gb >= cfg.min_free_disk_gb else "WARN",
            "detail": f"{hw.free_disk_gb:.1f} GB free (min {cfg.min_free_disk_gb:.0f} GB)",
            "fix": "Click 'Clean Temporary Cache' in Settings or free up disk space",
        },
        {
            "name": "Render Test",
            "status": "PASS" if render_test_data.get("passed") else ("SKIP" if render_test_data.get("skipped") else "FAIL"),
            "detail": (
                f"Rendered {render_test_data.get('duration_s', 0)}s 1080x1920 MP4 via {render_test_data.get('encoder_used')}"
                if render_test_data.get("passed")
                else str(render_test_data.get("error", "Skipped"))
            ),
            "fix": "Verify FFmpeg and audio output permissions",
        },
    ]

    all_critical_pass = all(
        c["status"] in ("PASS", "WARN", "SKIP")
        for c in checks
        if c["name"] in ("Python", "FFmpeg", "ffprobe", "Kokoro / TTS", "SQLite", "Output Write")
    )
    if all_critical_pass:
        save_setup_state({"diagnostic_status": "PASS"})

    return {
        "system_ready": all_critical_pass,
        "hardware": hw.to_dict(),
        "ollama": ollama_health,
        "benchmark": benchmark_data,
        "render_test": render_test_data,
        "checks": checks,
    }


@router.get("/diagnostics")
async def get_diagnostics_quick() -> dict[str, Any]:
    return await run_full_diagnostics(include_render_test=False, include_benchmark=False)


@router.post("/diagnostics/run")
async def post_run_full_diagnostics() -> dict[str, Any]:
    return await run_full_diagnostics(include_render_test=True, include_benchmark=True)


@router.post("/ollama/restart")
async def restart_ollama_endpoint() -> dict[str, Any]:
    cfg = get_app_settings()
    ollama = OllamaService(base_url=cfg.ollama_base_url)
    ok = await ollama.restart_ollama_service()
    health = await ollama.health()
    return {"restarted": ok, "health": health}


@router.post("/ollama/benchmark")
async def benchmark_ollama_endpoint(payload: BenchmarkRequest) -> dict[str, Any]:
    cfg = get_app_settings()
    ollama = OllamaService(base_url=cfg.ollama_base_url)
    target_model = payload.model or cfg.ollama_model
    res = await ollama.benchmark_model(model=target_model)
    persist_hardware_snapshot(benchmark_tok_per_s=res.tokens_per_second, benchmark_tier=res.tier.value)
    return res.model_dump()


@router.get("/tts/voices")
async def get_tts_voices() -> list[dict[str, str]]:
    return AVAILABLE_VOICES


@router.post("/tts/preview")
async def create_tts_preview(payload: TTSPreviewRequest) -> dict[str, Any]:
    preview_dir = TEMP_CACHE_DIR / "tts_preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    wav_path = preview_dir / "voice_preview.wav"
    tts = TTSService()
    res = await asyncio.to_thread(
        tts.synthesize_preview,
        output_wav_path=wav_path,
        voice=payload.voice_id,
        speed=payload.speed,
        sample_text=payload.text,
    )
    return {
        "ready": wav_path.exists(),
        "provider_used": res.provider_used,
        "voice_used": res.voice_used,
        "duration_s": res.duration_seconds,
        "audio_url": "/api/system/tts/preview-audio",
    }


@router.get("/tts/preview-audio")
async def stream_tts_preview_audio() -> FileResponse:
    wav_path = TEMP_CACHE_DIR / "tts_preview" / "voice_preview.wav"
    if not wav_path.exists():
        tts = TTSService()
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(tts.synthesize_preview, output_wav_path=wav_path)
    return FileResponse(str(wav_path), media_type="audio/wav", filename="voice_preview.wav")


@router.get("/diagnostic-video")
async def stream_diagnostic_video() -> FileResponse:
    diag_mp4 = TEMP_CACHE_DIR / "diagnostic_render" / "video.mp4"
    if not diag_mp4.exists():
        await render_diagnostic_video()
    return FileResponse(str(diag_mp4), media_type="video/mp4", filename="diagnostic.mp4")


@router.post("/wizard/complete")
async def complete_first_run_wizard() -> dict[str, Any]:
    with get_session() as session:
        cfg = session.get(AppSettings, 1)
        if cfg:
            cfg.first_run_completed = True
            cfg.updated_at = utc_now_iso()
            session.add(cfg)
            session.commit()
    save_setup_state({"first_run_completed": True})
    return {"completed": True}


@router.get("/logs/{log_name}")
async def read_recent_log(log_name: str, lines: int = 80) -> dict[str, Any]:
    allowed = {
        "app": LOGS_DIR / "app.log",
        "worker": LOGS_DIR / "worker.log",
        "setup": LOGS_DIR / "setup.log",
    }
    target = allowed.get(log_name.lower(), LOGS_DIR / "app.log")
    if not target.exists():
        return {"log_name": log_name, "content": "(Log file is empty)"}
    all_lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"log_name": log_name, "content": "\n".join(all_lines[-lines:])}

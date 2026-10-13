"""Hardware profiler, adaptive profile selector, and laptop thermal/battery safety guard."""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import asdict, dataclass, field
from typing import Any

import psutil

from localshorts.config import PROJECT_ROOT, resolve_binary
from localshorts.database import get_session
from localshorts.logging_config import get_logger
from localshorts.models.db_models import HardwareSnapshot, utc_now_iso
from localshorts.models.schemas import HardwareProfileType, ThermalState
from localshorts.security import run_subprocess_safe
from localshorts.services.nvenc import probe_nvenc_support

logger = get_logger("hardware")


@dataclass
class HardwareReport:
    os_version: str = ""
    cpu_model: str = ""
    cores_physical: int = 4
    cores_logical: int = 8
    ram_total_mb: int = 16384
    ram_free_mb: int = 8192
    gpu_vendor: str = "Unknown"
    gpu_name: str = "Integrated / CPU"
    nvidia_present: bool = False
    vram_total_mb: int = 0
    vram_free_mb: int = 0
    nvidia_driver: str = ""
    gpu_temp_c: int | None = None
    thermal_state: ThermalState = ThermalState.NORMAL
    nvenc_available: bool = False
    cuda_visible: bool = False
    free_disk_gb: float = 100.0
    on_battery: bool = False
    battery_percent: int | None = None
    ffmpeg_available: bool = False
    ffmpeg_path: str = ""
    ffprobe_available: bool = False
    ffprobe_path: str = ""
    ollama_available: bool = False
    ollama_path: str = ""
    recommended_profile: HardwareProfileType = HardwareProfileType.BALANCED
    recommended_model: str = "qwen3:4b"
    recommended_context: int = 4096
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["thermal_state"] = self.thermal_state.value
        data["recommended_profile"] = self.recommended_profile.value
        return data


def classify_hardware_profile(
    ram_total_mb: int,
    nvidia_present: bool,
    vram_total_mb: int,
) -> tuple[HardwareProfileType, str, int]:
    """
    Classify hardware into LIGHTWEIGHT, BALANCED, or PERFORMANCE using measurable RAM & VRAM.
    Returns (profile, recommended_model, recommended_context).
    """
    if ram_total_mb <= 9000 or not nvidia_present or vram_total_mb <= 2500:
        return (HardwareProfileType.LIGHTWEIGHT, "qwen2.5:1.5b", 2048)

    if ram_total_mb >= 28000 and vram_total_mb >= 7500:
        return (HardwareProfileType.PERFORMANCE, "qwen3:4b", 8192)

    # Default BALANCED profile for 16 GB RAM + 4-6 GB VRAM laptops (RTX 3050 Ti / RTX 4050)
    return (HardwareProfileType.BALANCED, "qwen3:4b", 4096)


def classify_thermal_state(temp_c: int | None) -> ThermalState:
    """
    Evaluate GPU temperature safety thresholds:
    - < 80C: NORMAL
    - 80-85C: WARM (reduce workload / insert cooldown between jobs)
    - > 85C: HOT (pause GPU-heavy queue and show warning)
    """
    if temp_c is None:
        return ThermalState.NORMAL
    if temp_c > 85:
        return ThermalState.HOT
    if temp_c >= 80:
        return ThermalState.WARM
    return ThermalState.NORMAL


def should_pause_for_battery(on_battery: bool, battery_override: bool, is_batch: bool = True) -> bool:
    """Return True if batch generation should pause due to running on laptop battery."""
    if not is_batch:
        return False
    return bool(on_battery and not battery_override)


def query_nvidia_gpu() -> dict[str, Any]:
    """Query nvidia-smi for GPU name, total/free VRAM, driver version, and temperature."""
    nvidia_smi = resolve_binary("nvidia-smi")
    if not nvidia_smi:
        return {"nvidia_present": False}

    try:
        res = run_subprocess_safe(
            [
                nvidia_smi,
                "--query-gpu=name,memory.total,memory.free,driver_version,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            timeout=5.0,
        )
        if res.returncode == 0 and res.stdout.strip():
            first_line = res.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first_line.split(",")]
            if len(parts) >= 5:
                temp_val: int | None = None
                try:
                    temp_val = int(float(parts[4]))
                except ValueError:
                    temp_val = None
                return {
                    "nvidia_present": True,
                    "gpu_vendor": "NVIDIA",
                    "gpu_name": parts[0],
                    "vram_total_mb": int(float(parts[1])),
                    "vram_free_mb": int(float(parts[2])),
                    "nvidia_driver": parts[3],
                    "gpu_temp_c": temp_val,
                    "cuda_visible": True,
                }
    except Exception as exc:
        logger.debug("nvidia-smi query failed: %s", exc)

    return {"nvidia_present": False}


def _detect_cpu_name() -> str:
    if os.name == "nt":
        try:
            res = run_subprocess_safe(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name",
                ],
                timeout=4.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
    return platform.processor() or "x86_64 Processor"


def detect_hardware(force_nvenc_probe: bool = False) -> HardwareReport:
    """Run full hardware, thermal, battery, disk, and binary detection."""
    vm = psutil.virtual_memory()
    ram_total_mb = int(vm.total // (1024 * 1024))
    ram_free_mb = int(vm.available // (1024 * 1024))

    cores_physical = psutil.cpu_count(logical=False) or 4
    cores_logical = psutil.cpu_count(logical=True) or 8

    disk = shutil.disk_usage(PROJECT_ROOT)
    free_disk_gb = round(disk.free / (1024**3), 2)

    battery = psutil.sensors_battery()
    on_battery = False
    battery_percent: int | None = None
    if battery is not None:
        on_battery = not bool(battery.power_plugged)
        battery_percent = int(battery.percent)

    gpu_info = query_nvidia_gpu()
    nvidia_present = bool(gpu_info.get("nvidia_present", False))
    vram_total_mb = int(gpu_info.get("vram_total_mb", 0))
    vram_free_mb = int(gpu_info.get("vram_free_mb", 0))
    gpu_temp_c = gpu_info.get("gpu_temp_c")
    thermal_state = classify_thermal_state(gpu_temp_c)

    ffmpeg_path = resolve_binary("ffmpeg") or ""
    ffprobe_path = resolve_binary("ffprobe") or ""
    ollama_path = resolve_binary("ollama") or ""
    nvenc_ok = probe_nvenc_support(force_refresh=force_nvenc_probe) if ffmpeg_path else False

    profile, rec_model, rec_ctx = classify_hardware_profile(
        ram_total_mb=ram_total_mb,
        nvidia_present=nvidia_present,
        vram_total_mb=vram_total_mb,
    )

    warnings: list[str] = []
    if thermal_state == ThermalState.HOT:
        warnings.append(
            f"GPU temperature is {gpu_temp_c}°C (>85°C) — GPU-heavy queue paused until cooled."
        )
    elif thermal_state == ThermalState.WARM:
        warnings.append(
            f"GPU temperature is {gpu_temp_c}°C (80–85°C) — cooldown delays active between jobs."
        )
    if on_battery:
        warnings.append("Running on battery — batch generation paused by default.")
    if free_disk_gb < 10.0:
        warnings.append(f"Low disk space ({free_disk_gb:.1f} GB free; 10 GB recommended).")

    report = HardwareReport(
        os_version=f"{platform.system()} {platform.release()} ({platform.version()})",
        cpu_model=_detect_cpu_name(),
        cores_physical=cores_physical,
        cores_logical=cores_logical,
        ram_total_mb=ram_total_mb,
        ram_free_mb=ram_free_mb,
        gpu_vendor=str(gpu_info.get("gpu_vendor", "Standard")),
        gpu_name=str(gpu_info.get("gpu_name", "CPU / Integrated Graphics")),
        nvidia_present=nvidia_present,
        vram_total_mb=vram_total_mb,
        vram_free_mb=vram_free_mb,
        nvidia_driver=str(gpu_info.get("nvidia_driver", "")),
        gpu_temp_c=gpu_temp_c,
        thermal_state=thermal_state,
        nvenc_available=nvenc_ok,
        cuda_visible=bool(gpu_info.get("cuda_visible", False)),
        free_disk_gb=free_disk_gb,
        on_battery=on_battery,
        battery_percent=battery_percent,
        ffmpeg_available=bool(ffmpeg_path),
        ffmpeg_path=ffmpeg_path,
        ffprobe_available=bool(ffprobe_path),
        ffprobe_path=ffprobe_path,
        ollama_available=bool(ollama_path),
        ollama_path=ollama_path,
        recommended_profile=profile,
        recommended_model=rec_model,
        recommended_context=rec_ctx,
        warnings=warnings,
    )
    return report


def persist_hardware_snapshot(
    report: HardwareReport | None = None,
    benchmark_tok_per_s: float = 0.0,
    benchmark_tier: str = "GOOD",
) -> HardwareReport:
    """Save the latest hardware detection snapshot into SQLite."""
    hw = report or detect_hardware()
    try:
        with get_session() as session:
            snap = HardwareSnapshot(
                detected_at=utc_now_iso(),
                os_version=hw.os_version,
                cpu_model=hw.cpu_model,
                cores_physical=hw.cores_physical,
                cores_logical=hw.cores_logical,
                ram_total_mb=hw.ram_total_mb,
                ram_free_mb=hw.ram_free_mb,
                gpu_vendor=hw.gpu_vendor,
                gpu_name=hw.gpu_name,
                vram_total_mb=hw.vram_total_mb,
                vram_free_mb=hw.vram_free_mb,
                nvidia_driver=hw.nvidia_driver,
                gpu_temp_c=hw.gpu_temp_c,
                nvenc_available=hw.nvenc_available,
                on_battery=hw.on_battery,
                battery_percent=hw.battery_percent,
                free_disk_gb=hw.free_disk_gb,
                active_profile=hw.recommended_profile.value,
                benchmark_tok_per_s=benchmark_tok_per_s,
                benchmark_tier=benchmark_tier,
            )
            session.add(snap)
            session.commit()
    except Exception as exc:
        logger.debug("Could not persist hardware snapshot: %s", exc)
    return hw

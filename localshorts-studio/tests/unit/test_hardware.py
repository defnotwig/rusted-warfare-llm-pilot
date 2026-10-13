"""Unit tests for Hardware Profiler, thermal thresholds, and battery safety."""

from __future__ import annotations

from localshorts.models.schemas import HardwareProfileType, ThermalState
from localshorts.services.hardware import (
    classify_hardware_profile,
    classify_thermal_state,
    detect_hardware,
    should_pause_for_battery,
)


def test_classify_hardware_profile_lightweight() -> None:
    prof, model, ctx = classify_hardware_profile(ram_total_mb=8192, nvidia_present=False, vram_total_mb=0)
    assert prof == HardwareProfileType.LIGHTWEIGHT
    assert model == "qwen2.5:1.5b"
    assert ctx == 2048


def test_classify_hardware_profile_balanced_target_laptop() -> None:
    # Target Acer Predator Helios 300 PH315-54: 16 GB RAM, RTX 3050 Ti 4 GB VRAM
    prof, model, ctx = classify_hardware_profile(ram_total_mb=16384, nvidia_present=True, vram_total_mb=4096)
    assert prof == HardwareProfileType.BALANCED
    assert model == "qwen3:4b"
    assert ctx == 4096


def test_classify_hardware_profile_performance() -> None:
    prof, model, ctx = classify_hardware_profile(ram_total_mb=32768, nvidia_present=True, vram_total_mb=8192)
    assert prof == HardwareProfileType.PERFORMANCE
    assert model == "qwen3:4b"
    assert ctx == 8192


def test_classify_thermal_state_thresholds() -> None:
    assert classify_thermal_state(None) == ThermalState.NORMAL
    assert classify_thermal_state(65) == ThermalState.NORMAL
    assert classify_thermal_state(79) == ThermalState.NORMAL
    assert classify_thermal_state(80) == ThermalState.WARM
    assert classify_thermal_state(85) == ThermalState.WARM
    assert classify_thermal_state(86) == ThermalState.HOT
    assert classify_thermal_state(92) == ThermalState.HOT


def test_battery_pause_guard() -> None:
    assert should_pause_for_battery(on_battery=False, battery_override=False, is_batch=True) is False
    assert should_pause_for_battery(on_battery=True, battery_override=False, is_batch=True) is True
    assert should_pause_for_battery(on_battery=True, battery_override=True, is_batch=True) is False
    assert should_pause_for_battery(on_battery=True, battery_override=False, is_batch=False) is False


def test_live_hardware_detector_executes() -> None:
    hw = detect_hardware(force_nvenc_probe=False)
    assert hw.cores_physical >= 1
    assert hw.cores_logical >= 1
    assert hw.ram_total_mb > 1000
    assert hw.recommended_profile in (
        HardwareProfileType.LIGHTWEIGHT,
        HardwareProfileType.BALANCED,
        HardwareProfileType.PERFORMANCE,
    )

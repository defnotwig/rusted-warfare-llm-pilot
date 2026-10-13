"""CLI entry point for LocalShorts Studio automated system diagnostics."""

from __future__ import annotations

import asyncio
import sys

from localshorts.api.system import run_full_diagnostics
from localshorts.config import FONTS_DIR, SFX_DIR, ensure_directories
from localshorts.database import init_db
from localshorts.services.audio_sfx import ensure_sfx_assets


async def _run_async() -> int:
    print("=" * 78)
    print("  LocalShorts Studio v0.1.0 — Automated 15-Point System Diagnostics")
    print("=" * 78)

    ensure_directories()
    init_db()
    sfx = ensure_sfx_assets()
    report = await run_full_diagnostics(include_render_test=True, include_benchmark=False)
    hw = report["hardware"]

    print(f"  Host OS        : {hw['os_version']}")
    print(f"  CPU            : {hw['cpu_model']} ({hw['cores_physical']}C/{hw['cores_logical']}T)")
    print(f"  Memory         : {hw['ram_total_mb']} MB Total / {hw['ram_free_mb']} MB Free")
    print(f"  GPU & VRAM     : {hw['gpu_name']} ({hw['vram_total_mb']} MB VRAM, Driver {hw['nvidia_driver'] or 'N/A'})")
    print(f"  Thermal State  : {hw['gpu_temp_c'] or 'N/A'} C ({hw['thermal_state']})")
    print(f"  Power Status   : {'Battery' if hw['on_battery'] else 'AC Power'}")
    print(f"  Profile        : {hw['recommended_profile']} (Model: {hw['recommended_model']}, Ctx: {hw['recommended_context']})")
    print("-" * 78)

    failed = 0
    for chk in report["checks"]:
        status = chk["status"]
        name = chk["name"]
        detail = chk["detail"]
        print(f"[{status:4s}] {name:20s} : {detail}")
        if status == "FAIL":
            failed += 1

    sfx_ok = len(sfx) >= 5
    fonts_ok = any(FONTS_DIR.glob("*.ttf"))
    print(f"[{'PASS' if sfx_ok else 'FAIL':4s}] {'CC0 SFX Assets':20s} : {len(sfx)} WAV files verified in {SFX_DIR}")
    print(f"[{'PASS' if fonts_ok else 'FAIL':4s}] {'OFL Font Assets':20s} : Verified in {FONTS_DIR}")
    if not sfx_ok or not fonts_ok:
        failed += 1

    print("=" * 78)
    if failed == 0 and report.get("system_ready"):
        print("STATUS: SYSTEM READY (Exit Code 0)")
        return 0
    print(f"STATUS: {failed} CRITICAL CHECK(S) FAILED (Exit Code 1)")
    return 1


def run_cli_diagnostics() -> int:
    return asyncio.run(_run_async())


if __name__ == "__main__":
    sys.exit(run_cli_diagnostics())

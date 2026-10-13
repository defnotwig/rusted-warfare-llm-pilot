"""Configuration and runtime path resolution for LocalShorts Studio."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root: .../localshorts-studio
PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
PROJECT_ROOT = SRC_DIR.parent

VERSION_FILE = PROJECT_ROOT / "VERSION"
APP_VERSION = VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else "0.1.0"

DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = PROJECT_ROOT / "cache"
MEDIA_CACHE_DIR = CACHE_DIR / "media"
TEMP_CACHE_DIR = CACHE_DIR / "temp"
OUTPUT_DIR = PROJECT_ROOT / "output"
READY_TO_UPLOAD_DIR = OUTPUT_DIR / "ready-to-upload"
LOGS_DIR = PROJECT_ROOT / "logs"
FFMPEG_LOGS_DIR = LOGS_DIR / "ffmpeg"
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
SETUP_STATE_PATH = RUNTIME_DIR / "setup-state.json"

ASSETS_DIR = PROJECT_ROOT / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
SFX_DIR = ASSETS_DIR / "sfx"
MUSIC_DIR = ASSETS_DIR / "music"
BACKGROUNDS_DIR = ASSETS_DIR / "backgrounds"
AUTHORIZED_MEDIA_DIR = ASSETS_DIR / "authorized_media"

UI_DIR = PACKAGE_DIR / "ui"
TEMPLATES_DIR = UI_DIR / "templates"
STATIC_DIR = UI_DIR / "static"


def ensure_directories() -> None:
    """Create all required application directories if missing."""
    for directory in (
        DATA_DIR,
        CACHE_DIR,
        MEDIA_CACHE_DIR,
        TEMP_CACHE_DIR,
        OUTPUT_DIR,
        READY_TO_UPLOAD_DIR,
        LOGS_DIR,
        FFMPEG_LOGS_DIR,
        RUNTIME_DIR,
        RUNTIME_DIR / "bin",
        RUNTIME_DIR / "ffmpeg" / "bin",
        RUNTIME_DIR / "models" / "kokoro",
        FONTS_DIR,
        SFX_DIR,
        MUSIC_DIR,
        BACKGROUNDS_DIR,
        AUTHORIZED_MEDIA_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def load_setup_state() -> dict[str, Any]:
    """Read .runtime/setup-state.json if present."""
    if SETUP_STATE_PATH.exists():
        try:
            return json.loads(SETUP_STATE_PATH.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}
    return {}


def save_setup_state(updates: dict[str, Any]) -> dict[str, Any]:
    """Update and persist .runtime/setup-state.json."""
    ensure_directories()
    state = load_setup_state()
    state.update(updates)
    SETUP_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def resolve_binary(name: str) -> str | None:
    """Resolve ffmpeg, ffprobe, or ollama from .runtime, local AppData, or system PATH."""
    exe_name = f"{name}.exe" if os.name == "nt" and not name.endswith(".exe") else name
    candidates: list[Path] = [
        RUNTIME_DIR / "ffmpeg" / "bin" / exe_name,
        RUNTIME_DIR / "bin" / exe_name,
    ]
    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            candidates.append(Path(local_appdata) / "Programs" / "Ollama" / exe_name)
        if name.lower().startswith("nvidia-smi"):
            sys_root = os.environ.get("SystemRoot", r"C:\Windows")
            candidates.append(Path(sys_root) / "System32" / "nvidia-smi.exe")

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate)

    which_path = shutil.which(name)
    if which_path:
        return which_path
    return None


class Settings(BaseSettings):
    """Runtime environment settings."""

    model_config = SettingsConfigDict(
        env_prefix="LOCALSHORTS_",
        env_file=str(PROJECT_ROOT / ".env"),
        extra="ignore",
    )

    app_name: str = "LocalShorts Studio"
    app_subtitle: str = "Free Local AI Shorts Factory"
    version: str = APP_VERSION
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8765)
    ollama_base_url: str = Field(default="http://127.0.0.1:11434")
    default_model: str = Field(default="qwen3:4b")
    fallback_models: list[str] = Field(default_factory=lambda: ["gemma3:4b", "qwen2.5:1.5b"])
    db_path: Path = Field(default=DATA_DIR / "localshorts.db")
    max_cache_gb: float = Field(default=15.0)
    min_free_disk_gb: float = Field(default=10.0)


settings = Settings()
ensure_directories()

"""Unit tests for slug sanitization, Windows reserved names, path containment, and SSRF protection."""

from __future__ import annotations

import pytest

from localshorts.config import OUTPUT_DIR
from localshorts.security import resolve_safe_path, sanitize_slug, validate_external_url


def test_sanitize_slug_handles_punctuation_and_reserved_names() -> None:
    assert sanitize_slug("MrBeast's $1,000,000 Challenge?!") == "mrbeast-1000000-challenge"
    assert sanitize_slug("CON") == "video-con"
    assert sanitize_slug("NUL") == "video-nul"
    assert sanitize_slug("   ") == "short-project"


def test_resolve_safe_path_blocks_traversal() -> None:
    valid = resolve_safe_path(OUTPUT_DIR, "2026-10-13/sample/video.mp4")
    assert valid.is_relative_to(OUTPUT_DIR.resolve())

    with pytest.raises(ValueError, match="Path traversal blocked"):
        resolve_safe_path(OUTPUT_DIR, "../../Windows/System32/cmd.exe")


def test_validate_external_url_blocks_ssrf_and_invalid_schemes() -> None:
    with pytest.raises(ValueError):
        validate_external_url("file:///C:/Windows/win.ini")

    with pytest.raises(ValueError):
        validate_external_url("http://127.0.0.1:11434/api/tags", allow_localhost=False)

    with pytest.raises(ValueError):
        validate_external_url("http://169.254.169.254/latest/meta-data")

    assert validate_external_url("http://127.0.0.1:11434/api/tags", allow_localhost=True) == "http://127.0.0.1:11434/api/tags"

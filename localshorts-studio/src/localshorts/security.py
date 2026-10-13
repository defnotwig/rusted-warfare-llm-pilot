"""Security utilities: filename sanitization, path containment, URL validation, and safe subprocess execution."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import subprocess
import unicodedata
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

from localshorts.config import (
    ASSETS_DIR,
    CACHE_DIR,
    DATA_DIR,
    LOGS_DIR,
    OUTPUT_DIR,
    PROJECT_ROOT,
    RUNTIME_DIR,
)

WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

APPROVED_ROOTS = (
    PROJECT_ROOT.resolve(),
    DATA_DIR.resolve(),
    CACHE_DIR.resolve(),
    OUTPUT_DIR.resolve(),
    LOGS_DIR.resolve(),
    RUNTIME_DIR.resolve(),
    ASSETS_DIR.resolve(),
)


def sanitize_slug(text: str, max_length: int = 64, default: str = "short-project") -> str:
    """
    Convert arbitrary titles into deterministic, Windows-safe ASCII slugs.
    Example: "MrBeast's $1,000,000 Challenge?!" -> "mrbeast-1000000-challenge"
    """
    if not text or not text.strip():
        return default

    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()

    # Remove possessive "'s" or "’s" so "MrBeast's" -> "mrbeast"
    normalized = re.sub(r"'s\b", "", normalized)
    # Remove commas inside numbers: "1,000,000" -> "1000000"
    normalized = re.sub(r"(?<=\d),(?=\d)", "", normalized)
    # Strip currency symbols and apostrophes cleanly
    normalized = re.sub(r"[$'\"`]", "", normalized)
    # Replace remaining non-alphanumeric runs with a single hyphen
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")

    if not slug:
        return default

    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")

    if slug in WINDOWS_RESERVED_NAMES:
        slug = f"video-{slug}"

    return slug or default


def sanitize_filename(filename: str, default: str = "file.bin") -> str:
    """Sanitize a filename while preserving a safe extension."""
    raw = Path(filename).name
    stem = Path(raw).stem
    suffix = re.sub(r"[^a-zA-Z0-9.]", "", Path(raw).suffix.lower())[:10]
    safe_stem = sanitize_slug(stem, max_length=60, default="file")
    return f"{safe_stem}{suffix}" if suffix else (safe_stem or default)


def resolve_safe_path(base_dir: Path, candidate: str | Path) -> Path:
    """
    Resolve a candidate path and ensure it stays strictly inside base_dir.
    Raises ValueError on directory traversal or unapproved root escape.
    """
    resolved_base = base_dir.resolve()
    cand_path = Path(candidate)
    if not cand_path.is_absolute():
        resolved_target = (resolved_base / cand_path).resolve()
    else:
        resolved_target = cand_path.resolve()

    if not resolved_target.is_relative_to(resolved_base):
        raise ValueError(f"Path traversal blocked: '{candidate}' escapes '{resolved_base}'")

    if not any(resolved_target.is_relative_to(root) for root in APPROVED_ROOTS):
        raise ValueError(f"Path '{resolved_target}' is outside approved project directories")

    return resolved_target


def validate_external_url(url: str, allow_localhost: bool = False) -> str:
    """
    Validate an HTTP/HTTPS URL and block SSRF attempts against private/loopback networks
    unless allow_localhost=True (used only for local Ollama endpoint).
    """
    if not url or not isinstance(url, str):
        raise ValueError("URL must be a non-empty string")

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: '{parsed.scheme}'")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("URL is missing hostname")

    if not allow_localhost:
        if hostname in ("localhost", "0.0.0.0", "127.0.0.1", "::1") or hostname.endswith(".local"):
            raise ValueError(f"Localhost/internal hostname blocked: {hostname}")

        try:
            ip = ipaddress.ip_address(hostname)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise ValueError(f"Non-public IP literal blocked: {hostname}")
        except ValueError as exc:
            if "blocked" in str(exc):
                raise
            # Hostname is a domain name; check DNS resolution if online
            try:
                addr_info = socket.getaddrinfo(hostname, None)
                for entry in addr_info:
                    resolved_ip = ipaddress.ip_address(entry[4][0])
                    if (
                        resolved_ip.is_private
                        or resolved_ip.is_loopback
                        or resolved_ip.is_link_local
                    ):
                        raise ValueError(f"Domain resolves to private IP: {hostname}")
            except socket.gaierror:
                # Offline or DNS unresolvable right now; allow syntactic valid domain to fail cleanly at HTTP layer
                pass

    return url.strip()


def run_subprocess_safe(
    args: Sequence[str],
    cwd: Path | None = None,
    timeout: float = 120.0,
) -> subprocess.CompletedProcess[str]:
    """
    Execute an external binary safely using an argument list (never shell=True).
    """
    if not args or not isinstance(args, (list, tuple)):
        raise ValueError("Subprocess arguments must be a non-empty list/tuple")

    str_args = [str(a) for a in args]
    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

    return subprocess.run(
        str_args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
        creationflags=creationflags,
    )


async def run_async_subprocess_safe(
    args: Sequence[str],
    cwd: Path | None = None,
    timeout: float = 300.0,
) -> tuple[int, str, str]:
    """
    Execute an external binary asynchronously without shell interpolation.
    Returns (returncode, stdout, stderr).
    """
    if not args or not isinstance(args, (list, tuple)):
        raise ValueError("Subprocess arguments must be a non-empty list/tuple")

    str_args = [str(a) for a in args]
    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

    proc = await asyncio.create_subprocess_exec(
        *str_args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=creationflags,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise TimeoutError(f"Command timed out after {timeout}s: {str_args[0]}")

    return (
        proc.returncode or 0,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )

"""Centralized version — reads from VERSION file (single source of truth)."""

from pathlib import Path


def _read_version() -> str:
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "VERSION",  # repo root (local dev)
        Path("/app/VERSION"),  # Docker container
    ]
    for p in candidates:
        try:
            return p.read_text().strip()
        except (FileNotFoundError, PermissionError):
            continue
    return "0.0.0"


APP_VERSION: str = _read_version()

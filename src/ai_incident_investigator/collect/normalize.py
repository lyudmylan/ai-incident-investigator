"""Shared normalization rules for collected data (docs/collection_sources.md)."""

import re
from typing import Literal

LogLevel = Literal["DEBUG", "INFO", "WARN", "ERROR", "FATAL"]

_LEVEL_MAP: dict[str, LogLevel] = {
    "debug": "DEBUG",
    "info": "INFO",
    "warning": "WARN",
    "warn": "WARN",
    "error": "ERROR",
    "fatal": "FATAL",
    "critical": "FATAL",
}

LEVEL_TOKEN = re.compile(r"\b(debug|info|warn|warning|error|fatal|critical)\b", re.IGNORECASE)


def normalize_level(raw: str | None) -> LogLevel:
    """Source level label -> contract level; unknown or absent -> INFO."""
    return _LEVEL_MAP.get((raw or "").lower(), "INFO")


def level_from_text(line: str) -> LogLevel:
    """Best-effort level from a raw log line: first level token wins."""
    match = LEVEL_TOKEN.search(line)
    return normalize_level(match.group(1)) if match else "INFO"

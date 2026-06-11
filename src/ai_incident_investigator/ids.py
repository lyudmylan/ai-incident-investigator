"""Deterministic, content-derived identifiers.

Ids must be stable across runs on the same package so reports are diffable
and golden-file tests stay meaningful. Never use counters or randomness.
"""

import hashlib

_SEPARATOR = "\x1f"


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256(_SEPARATOR.join(parts).encode()).hexdigest()[:10]
    return f"{prefix}_{digest}"

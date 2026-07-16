"""Shared vocabulary and config-guardrail primitives used across contracts."""

from collections.abc import Iterator
from enum import StrEnum
from typing import Any, Literal

CheckResult = Literal["pass", "warning", "blocked"]
"""Safety-check outcome, shared by the report contract and the critic's
response schema so the two can never drift apart."""

SECRET_KEY_MARKERS = ("token", "secret", "password", "api_key", "apikey")
"""Key substrings that mark a config value as credential-shaped. Shared by
every config loader (collect and executor) so the pasted-credential
guardrail cannot drift between the read-side and write-side configs."""


def looks_like_credential_key(key: str) -> bool:
    """True for keys that should hold env-var REFERENCES, never values."""
    lowered = key.lower()
    return any(marker in lowered for marker in SECRET_KEY_MARKERS) and not lowered.endswith("_env")


def _leaves(location: str, key: str, value: Any) -> Iterator[tuple[str, str, Any]]:
    if isinstance(value, dict):
        yield from config_leaves(value, location)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(f"{location}[{index}]", key, item)
    else:
        yield location, key, value


def config_leaves(node: dict[str, Any], where: str = "") -> Iterator[tuple[str, str, Any]]:
    """Yield (location, key, value) for every scalar leaf of a parsed config,
    descending through nested tables AND arrays (TOML [[tables]] included), so
    a guardrail scan cannot be dodged by nesting."""
    for key, value in node.items():
        location = f"{where}.{key}" if where else key
        yield from _leaves(location, key, value)


class SeverityLevel(StrEnum):
    """Severity classification; rules live in docs/assumptions.md."""

    SEV1 = "SEV-1"
    SEV2 = "SEV-2"
    SEV3 = "SEV-3"
    SEV4 = "SEV-4"


class Confidence(StrEnum):
    """Confidence label; the rubric behind it lives in docs/assumptions.md."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Source(StrEnum):
    """Where a piece of information originated inside the incident package."""

    ALERT = "alert"
    METRICS = "metrics"
    LOGS = "logs"
    TRACES = "traces"
    DEPLOYS = "deploys"
    TOPOLOGY = "topology"
    RUNBOOK = "runbook"

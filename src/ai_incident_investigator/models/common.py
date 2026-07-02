"""Shared vocabulary used by both the incident package and the report contracts."""

from enum import StrEnum
from typing import Literal

CheckResult = Literal["pass", "warning", "blocked"]
"""Safety-check outcome, shared by the report contract and the critic's
response schema so the two can never drift apart."""


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

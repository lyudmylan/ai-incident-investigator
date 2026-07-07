"""Deterministic severity cross-check (issue #45, from the first live run)."""

from datetime import UTC, datetime, timedelta

from ai_incident_investigator.agents.ranker import RANKER_PROMPT
from ai_incident_investigator.agents.triage import TRIAGE_PROMPT
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.models.package import (
    Alert,
    IncidentPackage,
    MetricPoint,
    MetricSeries,
    MetricsFile,
)
from ai_incident_investigator.models.report import SafetyCheck, SeverityAssessment
from ai_incident_investigator.pipeline import initial_state
from ai_incident_investigator.safety import _severity_ceiling, lint_state
from ai_incident_investigator.state import InvestigationState, StateUpdate, apply_update
from helpers import EXAMPLE_DIR

T0 = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)


def _series(signal: str, values: list[float], baseline: float, unit: str) -> MetricSeries:
    return MetricSeries(
        service="svc",
        signal=signal,
        baseline=baseline,
        unit=unit,
        points=[
            MetricPoint(timestamp=T0 + timedelta(minutes=5 * i), value=v)
            for i, v in enumerate(values)
        ],
    )


def _state(metrics: MetricsFile | None, claimed: str | None) -> InvestigationState:
    package = IncidentPackage(
        incident_id="synthetic",
        alert=Alert(id="a", title="t", service="svc", triggered_at=T0 + timedelta(minutes=30)),
        metrics=metrics,
    )
    state = InvestigationState(
        package=package,
        window=initial_state(load_package(EXAMPLE_DIR)).window,  # any valid window
        timeline=[],
    )
    if claimed is None:
        return state
    severity = SeverityAssessment.model_validate(
        {"level": claimed, "explanation": "test", "confidence": "high"}
    )
    return apply_update(state, StateUpdate(severity=severity))


def _ceiling_check(state: InvestigationState) -> SafetyCheck:
    return {c.check: c for c in lint_state(state)}["severity_not_above_numeric_ceiling"]


ERROR_30 = MetricsFile(series=[_series("error_rate_pct", [0.3, 30.0], 0.3, "%")])
ERROR_5 = MetricsFile(series=[_series("error_rate_pct", [0.3, 4.8], 0.3, "%")])
LATENCY_7X = MetricsFile(series=[_series("p95_latency_ms", [450, 3200], 450, "ms")])
FLAT = MetricsFile(series=[_series("p95_latency_ms", [450, 460], 450, "ms")])
UNRECOGNIZED = MetricsFile(series=[_series("queue_depth", [12, 545], 10, "msgs")])


def test_ceiling_derivation_follows_the_documented_bands() -> None:
    assert _severity_ceiling(_state(ERROR_30, None))[0] == "SEV-1"  # type: ignore[index]
    assert _severity_ceiling(_state(ERROR_5, None))[0] == "SEV-2"  # type: ignore[index]
    assert _severity_ceiling(_state(LATENCY_7X, None))[0] == "SEV-2"  # type: ignore[index]
    assert _severity_ceiling(_state(FLAT, None))[0] == "SEV-3"  # type: ignore[index]
    assert _severity_ceiling(_state(UNRECOGNIZED, None)) is None
    assert _severity_ceiling(_state(None, None)) is None


def test_overstated_severity_is_flagged_the_haiku_case() -> None:
    check = _ceiling_check(_state(ERROR_5, "SEV-1"))
    assert check.result == "warning"
    assert "claimed SEV-1" in (check.detail or "")
    assert "at most SEV-2" in (check.detail or "")
    assert "max error rate 4.8%" in (check.detail or "")


def test_claim_at_or_below_the_ceiling_passes() -> None:
    at_ceiling = _ceiling_check(_state(LATENCY_7X, "SEV-2"))
    assert at_ceiling.result == "pass"
    assert "within the numeric ceiling" in (at_ceiling.detail or "")

    # a justified downgrade (error_rate_spike's non-critical flow) stays legal
    below = _ceiling_check(_state(ERROR_5, "SEV-3"))
    assert below.result == "pass"


def test_honest_passes_when_nothing_is_checkable() -> None:
    assert _ceiling_check(_state(ERROR_5, None)).detail == "no severity assessed"
    no_signals = _ceiling_check(_state(UNRECOGNIZED, "SEV-1"))
    assert no_signals.result == "pass"
    assert "no numeric signals" in (no_signals.detail or "")


def test_prompts_carry_the_issue_45_guidance() -> None:
    triage = " ".join(TRIAGE_PROMPT.split())
    assert "inside the 1-25% SEV-2 band" in triage
    assert "deterministic linter cross-checks" in triage
    ranker = " ".join(RANKER_PROMPT.split())
    assert "at most 8 per hypothesis" in ranker

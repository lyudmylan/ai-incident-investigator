"""Deterministic recovery verification plan (docs/assumptions.md, v3 rules).

The report already knows which series deviated and what "recovered" means -
turning that into a watch-plan is code, not an LLM call (Principle 4). The
builder depends on nothing but the package and window, so it survives any
combination of LLM agent failures.

Derivation rules (all documented):
- watched signals = the metric series that deviated in the window, each
  with its baseline and the recovery rule spelled out
- watch duration = twice the observed deviation duration, minimum 30
  minutes (recovery should be observed at least as long as the disruption)
- log patterns that should stop = recurring (>= 2 occurrences) ERROR/FATAL
  message shapes in the window, digit runs normalized to N, top 5
- re-alert condition mirrors the original alert threshold when present,
  else names the alert itself
- mode: confirm_sustained_recovery when the window already ended (recovery
  observed in-window), else watch_for_recovery
"""

import re
from datetime import datetime
from typing import Literal

from ai_incident_investigator.graph import FunctionAgent
from ai_incident_investigator.models.package import IncidentPackage, MetricSeries
from ai_incident_investigator.models.report import (
    IncidentWindow,
    ReasoningStep,
    RecoveryVerificationPlan,
    WatchedSignal,
)
from ai_incident_investigator.state import InvestigationState, StateUpdate
from ai_incident_investigator.window import (
    RECOVERY_POINTS,
    RECOVERY_TOLERANCE,
    is_anomalous,
)

RECOVERY_BUILDER_NAME = "recovery_builder"

MIN_WATCH_MINUTES = 30
MAX_LOG_PATTERNS = 5
_DIGITS = re.compile(r"\d+")
_SPACES = re.compile(r"\s+")


def normalize_pattern(message: str) -> str:
    return _SPACES.sub(" ", _DIGITS.sub("N", message)).strip()


def _watch_minutes(series: MetricSeries) -> int:
    anomalous = [p.timestamp for p in series.points if is_anomalous(p.value, series.baseline)]
    if not anomalous:
        return MIN_WATCH_MINUTES
    duration_minutes = int((max(anomalous) - min(anomalous)).total_seconds() // 60)
    return max(MIN_WATCH_MINUTES, 2 * duration_minutes)


def _watched_signal(series: MetricSeries) -> WatchedSignal:
    unit = f" {series.unit}" if series.unit else ""
    tolerance_pct = int(RECOVERY_TOLERANCE * 100)
    return WatchedSignal(
        service=series.service,
        signal=series.signal,
        baseline=series.baseline,
        recovered_when=(
            f"within {tolerance_pct}% of baseline {series.baseline:g}{unit} "
            f"for >= {RECOVERY_POINTS} consecutive points"
        ),
        watch_minutes=_watch_minutes(series),
    )


def _log_patterns(package: IncidentPackage, start: datetime, end: datetime | None) -> list[str]:
    counts: dict[str, int] = {}
    for record in package.logs:
        if record.level not in ("ERROR", "FATAL"):
            continue
        if record.timestamp < start or (end is not None and record.timestamp > end):
            continue
        pattern = normalize_pattern(record.message)
        counts[pattern] = counts.get(pattern, 0) + 1
    recurring = [(count, pattern) for pattern, count in counts.items() if count >= 2]
    recurring.sort(key=lambda item: (-item[0], item[1]))
    return [pattern for _, pattern in recurring[:MAX_LOG_PATTERNS]]


def _re_alert_condition(package: IncidentPackage) -> str:
    alert = package.alert
    if alert.signal and alert.threshold is not None:
        return (
            f"{alert.signal} on {alert.service} crosses the alert "
            f"threshold {alert.threshold:g} again"
        )
    return f"the original alert '{alert.title}' fires again"


def build_recovery_verification(
    package: IncidentPackage, window: IncidentWindow
) -> tuple[RecoveryVerificationPlan | None, str]:
    """The plan plus a trace summary explaining what was derived from what."""
    if package.metrics is None:
        return None, "no metrics in the package; no verification plan derivable"

    deviated = [
        series
        for series in package.metrics.series
        if any(is_anomalous(point.value, series.baseline) for point in series.points)
    ]
    if not deviated:
        return None, "no metric series deviated from baseline; nothing to watch"

    signals = sorted(
        (_watched_signal(series) for series in deviated),
        key=lambda s: (s.service, s.signal),
    )
    patterns = _log_patterns(package, window.start, window.end)
    mode: Literal["watch_for_recovery", "confirm_sustained_recovery"] = (
        "confirm_sustained_recovery" if window.end is not None else "watch_for_recovery"
    )
    plan = RecoveryVerificationPlan(
        mode=mode,
        signals=signals,
        log_patterns_should_stop=patterns,
        re_alert_condition=_re_alert_condition(package),
    )
    summary = (
        f"derived from {len(signals)} deviated series and {len(patterns)} recurring "
        f"error pattern(s); mode={mode} per the incident window rule"
    )
    return plan, summary


def make_recovery_builder(depends_on: frozenset[str] = frozenset()) -> FunctionAgent:
    def run(state: InvestigationState) -> StateUpdate:
        plan, summary = build_recovery_verification(state.package, state.window)
        return StateUpdate(
            recovery_verification=plan,
            reasoning_trace=[ReasoningStep(stage=RECOVERY_BUILDER_NAME, summary=summary)],
        )

    return FunctionAgent(name=RECOVERY_BUILDER_NAME, run=run, depends_on=depends_on)

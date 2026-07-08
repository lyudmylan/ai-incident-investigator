"""Incident window detection and metric deviation rules.

All thresholds here are documented in docs/assumptions.md; agents apply these
results, they do not re-derive them.
"""

from datetime import datetime, timedelta

from ai_incident_investigator.models.package import IncidentPackage, MetricSeries
from ai_incident_investigator.models.report import IncidentWindow

DEFAULT_LOOKBACK = timedelta(minutes=30)
DEVIATION_RATIO = 2.0
RECOVERY_TOLERANCE = 0.10
RECOVERY_POINTS = 3


def is_anomalous(value: float, baseline: float) -> bool:
    """A value is anomalous at >= 2x baseline or <= 0.5x baseline.

    With a zero baseline any nonzero value is anomalous.
    """
    if baseline == 0:
        return value != 0
    ratio = value / baseline
    return ratio >= DEVIATION_RATIO or ratio <= 1 / DEVIATION_RATIO


def _is_recovered(value: float, baseline: float) -> bool:
    if baseline == 0:
        return value == 0
    return abs(value - baseline) <= RECOVERY_TOLERANCE * abs(baseline)


def recovery_start(series: MetricSeries) -> datetime | None:
    """Start of the trailing recovered run, if the series ends recovered."""
    points = sorted(series.points, key=lambda p: p.timestamp)
    trailing: list[datetime] = []
    for point in reversed(points):
        if _is_recovered(point.value, series.baseline):
            trailing.append(point.timestamp)
        else:
            break
    if len(trailing) >= RECOVERY_POINTS:
        return trailing[-1]
    return None


def incident_window(
    package: IncidentPackage, lookback: timedelta = DEFAULT_LOOKBACK
) -> IncidentWindow:
    """Apply the documented window rule (docs/assumptions.md).

    Start: alert.triggered_at minus the lookback.
    End: the start of sustained recovery when every deviated metric series
    ends recovered; otherwise None (ongoing as of the latest data point).
    """
    triggered_at = package.alert.triggered_at
    start = triggered_at - lookback
    minutes = int(lookback.total_seconds() // 60)
    rule = f"alert.triggered_at ({triggered_at.isoformat()}) minus {minutes}m lookback"

    if package.metrics is None:
        return IncidentWindow(
            start=start,
            end=None,
            rule=f"{rule}; no metrics available to detect recovery -> ongoing",
        )

    deviated = [
        series
        for series in package.metrics.series
        if any(is_anomalous(point.value, series.baseline) for point in series.points)
    ]
    if not deviated:
        return IncidentWindow(
            start=start,
            end=None,
            rule=f"{rule}; no metric series deviated from baseline -> ongoing",
        )

    recovery_starts = [recovery_start(series) for series in deviated]
    if all(recovery is not None for recovery in recovery_starts):
        end = max(recovery for recovery in recovery_starts if recovery is not None)
        return IncidentWindow(
            start=start,
            end=end,
            rule=f"{rule}; end = start of sustained recovery across all deviated series",
        )
    return IncidentWindow(
        start=start,
        end=None,
        rule=f"{rule}; deviated series not recovered -> ongoing",
    )

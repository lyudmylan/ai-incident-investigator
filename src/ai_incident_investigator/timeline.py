"""Deterministic timeline builder.

Merges notable events from every package source into one ordered timeline.
Inclusion rules (documented in docs/assumptions.md):

- the alert trigger
- every deploy / config change / feature flag flip
- log records at WARN, ERROR, or FATAL (INFO/DEBUG are evidence, not events)
- per metric series: the first point that deviates >= 2x from baseline
- root trace spans with status "error"
"""

from datetime import datetime

from ai_incident_investigator.ids import stable_id
from ai_incident_investigator.models.common import Source
from ai_incident_investigator.models.package import IncidentPackage
from ai_incident_investigator.models.report import TimelineEntry
from ai_incident_investigator.window import DEVIATION_RATIO, is_anomalous


def _entry(
    timestamp: datetime, source: Source, service: str | None, description: str
) -> TimelineEntry:
    return TimelineEntry(
        id=stable_id("timeline", source.value, timestamp.isoformat(), service or "", description),
        timestamp=timestamp,
        source=source,
        service=service,
        description=description,
    )


def build_timeline(package: IncidentPackage) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []

    alert = package.alert
    entries.append(
        _entry(alert.triggered_at, Source.ALERT, alert.service, f"Alert fired: {alert.title}")
    )

    for deploy in package.deploys.deploys if package.deploys else []:
        description = f"{deploy.change_type} {deploy.version} on {deploy.service}"
        if deploy.description:
            description += f": {deploy.description}"
        entries.append(_entry(deploy.deployed_at, Source.DEPLOYS, deploy.service, description))

    for record in package.logs:
        if record.level in ("WARN", "ERROR", "FATAL"):
            entries.append(
                _entry(
                    record.timestamp,
                    Source.LOGS,
                    record.service,
                    f"{record.level}: {record.message}",
                )
            )

    for series in package.metrics.series if package.metrics else []:
        for point in sorted(series.points, key=lambda p: p.timestamp):
            if is_anomalous(point.value, series.baseline):
                unit = f" {series.unit}" if series.unit else ""
                entries.append(
                    _entry(
                        point.timestamp,
                        Source.METRICS,
                        series.service,
                        f"{series.signal} first deviated >={DEVIATION_RATIO}x from baseline "
                        f"({series.baseline}{unit} -> {point.value}{unit})",
                    )
                )
                break

    for span in package.traces.spans if package.traces else []:
        if span.parent_span_id is None and span.status == "error":
            entries.append(
                _entry(
                    span.start_time,
                    Source.TRACES,
                    span.service,
                    f"Trace {span.trace_id}: {span.operation} failed "
                    f"after {span.duration_ms:.0f}ms",
                )
            )

    entries.sort(key=lambda e: (e.timestamp, e.source.value, e.id))
    return entries

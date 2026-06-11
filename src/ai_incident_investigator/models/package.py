"""Incident package input contract.

One model per input file. `alert.json` is the only required file: its trigger
time anchors the incident window (docs/assumptions.md). Every other file is
optional; absence becomes a `missing_data` entry in the report, never a crash.

All timestamps must be timezone-aware (UTC recommended).
"""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class PackageModel(BaseModel):
    """Base for all input models: unknown fields are rejected early."""

    model_config = ConfigDict(extra="forbid")


class Alert(PackageModel):
    """alert.json — the monitoring alert that opened the incident. Required."""

    id: str
    title: str
    service: str = Field(description="Service the alert fired on")
    triggered_at: AwareDatetime = Field(description="Anchors the incident window")
    severity: str | None = Field(default=None, description="Severity as reported by monitoring")
    description: str | None = None
    signal: str | None = Field(default=None, description="Signal name, e.g. p95_latency_ms")
    threshold: float | None = None
    observed_value: float | None = None


class MetricPoint(PackageModel):
    timestamp: AwareDatetime
    value: float


class MetricSeries(PackageModel):
    service: str
    signal: str = Field(description="Signal name, e.g. p95_latency_ms, error_rate_pct")
    baseline: float = Field(
        description="Required pre-incident baseline; without it 'abnormal' is undefined offline"
    )
    unit: str | None = None
    points: list[MetricPoint] = Field(min_length=1)


class MetricsFile(PackageModel):
    """metrics.json — metric snapshots covering the incident window."""

    series: list[MetricSeries] = Field(min_length=1)


class LogRecord(PackageModel):
    """One line of logs.jsonl — the preferred, structured log format.

    logs.txt is accepted as a best-effort fallback; the loader parses it into
    this same shape and reports unparseable lines as missing data.
    """

    timestamp: AwareDatetime
    service: str
    level: Literal["DEBUG", "INFO", "WARN", "ERROR", "FATAL"]
    message: str


class Span(PackageModel):
    trace_id: str
    span_id: str
    parent_span_id: str | None = Field(default=None, description="None for a root span")
    service: str
    operation: str
    start_time: AwareDatetime
    duration_ms: float = Field(ge=0)
    status: Literal["ok", "error"] = "ok"


class TracesFile(PackageModel):
    """traces.json — distributed trace spans from the incident window."""

    spans: list[Span] = Field(min_length=1)


class Deploy(PackageModel):
    id: str
    service: str
    version: str = Field(description="Release identifier, e.g. 2026.06.01-1420")
    deployed_at: AwareDatetime
    change_type: Literal["deploy", "config", "feature_flag"] = "deploy"
    description: str | None = None


class DeploysFile(PackageModel):
    """deploys.json — recent deploys, config changes, and flag flips."""

    deploys: list[Deploy] = Field(min_length=1)


class ServiceNode(PackageModel):
    name: str
    kind: Literal["service", "database", "queue", "cache", "third_party"] = "service"
    depends_on: list[str] = Field(default_factory=list)


class TopologyFile(PackageModel):
    """topology.json — the service dependency graph."""

    services: list[ServiceNode] = Field(min_length=1)


class IncidentPackage(PackageModel):
    """A fully loaded incident package, as assembled by the loader.

    runbook.md is free-form Markdown and is carried verbatim.
    """

    incident_id: str = Field(description="Derived from the package directory name")
    alert: Alert
    metrics: MetricsFile | None = None
    logs: list[LogRecord] = Field(default_factory=list)
    traces: TracesFile | None = None
    deploys: DeploysFile | None = None
    topology: TopologyFile | None = None
    runbook: str | None = None

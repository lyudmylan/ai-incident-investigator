"""Prometheus-like metrics adapter: query_range in, metrics.json out.

Wire subset: GET {base_url}/api/v1/query_range with query/start/end/step.
Mapping and span rules are documented in docs/collection_sources.md; the
baseline rule additionally lives in docs/assumptions.md next to the
deviation rule it feeds.

One configured query = one package series. Each query makes a single
query_range call covering the baseline span plus the incident window;
baseline = median of the pre-incident samples. Per-query problems (HTTP
error, empty or ambiguous result, no pre-incident samples) skip that series
with a note in the collection report; the adapter only fails outright when
no series could be collected at all.
"""

import math
import statistics
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_incident_investigator.collect.adapter import CollectionContext, PackageContribution
from ai_incident_investigator.collect.config import CollectError, SourcesConfig
from ai_incident_investigator.collect.http import (
    EnvBearerAuth,
    HTTPClient,
    HTTPClientError,
    HTTPRequest,
    raise_for_status,
)
from ai_incident_investigator.models.package import MetricPoint, MetricSeries, MetricsFile

SECTION = "prometheus"

BASELINE_SPAN = timedelta(hours=2)
BASELINE_MARGIN = timedelta(minutes=15)


class PromQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    service: str
    signal: str
    query: str
    unit: str | None = None


class PromConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str
    token_env: str | None = None
    step_seconds: int = 300
    post_minutes: int = 30
    queries: list[PromQuery] = Field(min_length=1)


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PromSeriesResult(_WireModel):
    metric: dict[str, str] = Field(default_factory=dict)
    values: list[tuple[float, str]] = Field(default_factory=list)


class PromData(_WireModel):
    resultType: str = ""  # Prometheus wire field name
    result: list[PromSeriesResult] = Field(default_factory=list)


class PromResponse(_WireModel):
    status: str
    data: PromData | None = None
    error: str | None = None
    errorType: str | None = None  # Prometheus wire field name


class Spans(BaseModel):
    """The documented time spans, derived once per collection run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    baseline_start: datetime
    baseline_end: datetime
    window_start: datetime
    window_end: datetime


def compute_spans(context: CollectionContext, post_minutes: int) -> Spans:
    """docs/assumptions.md, 'Collected metric baselines': the baseline span is
    a fixed 2h ending margin-before-the-lookback so incident run-up cannot
    contaminate it; points cover the window plus a post-anchor span."""
    window_start = context.anchor_time - context.lookback
    baseline_end = window_start - BASELINE_MARGIN
    return Spans(
        baseline_start=baseline_end - BASELINE_SPAN,
        baseline_end=baseline_end,
        window_start=window_start,
        window_end=context.anchor_time + timedelta(minutes=post_minutes),
    )


def median_baseline(samples: list[float]) -> float:
    return float(statistics.median(samples))


class PrometheusMetricsAdapter:
    def __init__(self, http: HTTPClient, config: PromConfig) -> None:
        self._http = http
        self._config = config
        self._auth = (
            EnvBearerAuth(env_var=config.token_env) if config.token_env is not None else None
        )

    @property
    def name(self) -> str:
        return "prometheus"

    def _query_range(self, query: str, spans: Spans) -> PromSeriesResult:
        request = HTTPRequest(
            url=f"{self._config.base_url.rstrip('/')}/api/v1/query_range",
            params={
                "query": query,
                "start": f"{spans.baseline_start.timestamp():.0f}",
                "end": f"{spans.window_end.timestamp():.0f}",
                "step": str(self._config.step_seconds),
            },
        )
        body = raise_for_status(request, self._http.get(request, self._auth)).body
        try:
            parsed = PromResponse.model_validate_json(body)
        except ValidationError as exc:
            raise HTTPClientError(f"query_range response was not understood: {exc}") from exc
        if parsed.status != "success" or parsed.data is None:
            raise HTTPClientError(
                f"query_range returned status={parsed.status!r} "
                f"({parsed.errorType or ''} {parsed.error or ''})".strip()
            )
        if len(parsed.data.result) == 0:
            raise HTTPClientError("query returned no series")
        if len(parsed.data.result) > 1:
            raise HTTPClientError(
                f"query returned {len(parsed.data.result)} series; exactly one is "
                "required - make the configured query more specific"
            )
        return parsed.data.result[0]

    def _build_series(self, spec: PromQuery, spans: Spans, notes: list[str]) -> MetricSeries | None:
        try:
            result = self._query_range(spec.query, spans)
        except HTTPClientError as exc:
            notes.append(f"{spec.service}/{spec.signal} skipped: {exc}")
            return None

        baseline_samples: list[float] = []
        points: list[MetricPoint] = []
        skipped_non_finite = 0
        for raw_ts, raw_value in result.values:
            try:
                value = float(raw_value)
            except ValueError:
                skipped_non_finite += 1
                continue
            if not math.isfinite(value):
                skipped_non_finite += 1
                continue
            timestamp = datetime.fromtimestamp(raw_ts, tz=UTC)
            if timestamp <= spans.baseline_end:
                baseline_samples.append(value)
            elif timestamp >= spans.window_start:
                points.append(MetricPoint(timestamp=timestamp, value=value))

        if skipped_non_finite:
            notes.append(
                f"{spec.service}/{spec.signal}: {skipped_non_finite} non-finite sample(s) skipped"
            )
        if not baseline_samples:
            notes.append(
                f"{spec.service}/{spec.signal} skipped: no pre-incident samples in the "
                "baseline span to derive a baseline from (docs/assumptions.md)"
            )
            return None
        if not points:
            notes.append(f"{spec.service}/{spec.signal} skipped: no samples in the window")
            return None

        return MetricSeries(
            service=spec.service,
            signal=spec.signal,
            baseline=median_baseline(baseline_samples),
            unit=spec.unit,
            points=sorted(points, key=lambda p: p.timestamp),
        )

    def collect(self, context: CollectionContext) -> PackageContribution:
        spans = compute_spans(context, self._config.post_minutes)
        notes: list[str] = []
        series = [
            built
            for spec in self._config.queries
            if (built := self._build_series(spec, spans, notes)) is not None
        ]
        if not series:
            raise CollectError(
                "no metric series could be collected: " + "; ".join(notes or ["no queries ran"])
            )
        return PackageContribution(metrics=MetricsFile(series=series), notes=notes)


def prometheus_adapter(config: SourcesConfig, http: HTTPClient) -> PrometheusMetricsAdapter:
    """Build the adapter from a sources.toml [prometheus] section."""
    try:
        section = PromConfig.model_validate(config.section(SECTION))
    except ValidationError as exc:
        raise CollectError(f"[{SECTION}] section is invalid: {exc}") from exc
    return PrometheusMetricsAdapter(http, section)

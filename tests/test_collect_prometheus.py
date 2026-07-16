import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_incident_investigator.collect import (
    CollectError,
    CollectionContext,
    CollectionSettings,
    HTTPRequest,
    HTTPResponse,
    RecordingHTTPClient,
    ReplayHTTPClient,
    collect_package,
    load_sources_config,
)
from ai_incident_investigator.collect.prometheus import (
    PromConfig,
    PrometheusMetricsAdapter,
    compute_spans,
    median_baseline,
    prometheus_adapter,
)
from ai_incident_investigator.collect.sentry import SentryAlertSource
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.window import incident_window
from prometheus_stub import (
    BASE_URL,
    DEMO_CONFIG,
    PromStubHTTP,
    demo_collection_context,
    series_payload,
)
from sentry_stub import DEMO_CONFIG as SENTRY_CONFIG
from sentry_stub import DEMO_ISSUE_ID

ANCHOR = datetime(2026, 6, 1, 14, 35, tzinfo=UTC)
CONTEXT = CollectionContext(
    anchor_time=ANCHOR,
    anchor_service="booking-service",
    lookback=timedelta(minutes=30),
    change_lookback=timedelta(days=7),
    services=["booking-service"],
)
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "http" / "prometheus_demo"


def _adapter(stub: PromStubHTTP | None = None) -> PrometheusMetricsAdapter:
    return PrometheusMetricsAdapter(stub or PromStubHTTP(), DEMO_CONFIG)


class ScriptedPromHTTP:
    """Returns a fixed body (or per-call bodies) regardless of the request."""

    def __init__(self, *bodies: str, status: int = 200) -> None:
        self._bodies = list(bodies)
        self.status = status

    def get(self, request: HTTPRequest, auth: object = None) -> HTTPResponse:
        body = self._bodies.pop(0) if len(self._bodies) > 1 else self._bodies[0]
        return HTTPResponse(status=self.status, body=body)


def test_spans_follow_documented_rules() -> None:
    spans = compute_spans(CONTEXT, post_minutes=30)
    assert spans.window_start.isoformat() == "2026-06-01T14:05:00+00:00"
    assert spans.window_end.isoformat() == "2026-06-01T15:05:00+00:00"
    assert spans.baseline_end.isoformat() == "2026-06-01T13:50:00+00:00"  # 15m margin
    assert spans.baseline_start.isoformat() == "2026-06-01T11:50:00+00:00"  # 2h span


def test_spans_are_configurable_for_short_retention() -> None:
    """The sandbox (#81) and short-retention setups shrink the baseline
    machinery; the defaults stay the documented 2h/15m."""
    spans = compute_spans(
        CONTEXT,
        post_minutes=5,
        baseline_span=timedelta(minutes=8),
        baseline_margin=timedelta(minutes=2),
    )
    assert spans.baseline_end.isoformat() == "2026-06-01T14:03:00+00:00"  # 2m margin
    assert spans.baseline_start.isoformat() == "2026-06-01T13:55:00+00:00"  # 8m span
    assert spans.window_end.isoformat() == "2026-06-01T14:40:00+00:00"

    queries = [{"service": "s", "signal": "x", "query": "x"}]
    shrunk = PromConfig.model_validate(
        {
            "base_url": "https://prom.example",
            "baseline_span_minutes": 8,
            "baseline_margin_minutes": 2,
            "queries": queries,
        }
    )
    assert shrunk.baseline_span_minutes == 8
    default = PromConfig.model_validate({"base_url": "https://prom.example", "queries": queries})
    assert (default.baseline_span_minutes, default.baseline_margin_minutes) == (120, 15)


def test_median_baseline_is_median() -> None:
    assert median_baseline([1.0, 100.0, 2.0]) == 2.0
    assert median_baseline([1.0, 2.0, 3.0, 4.0]) == 2.5


def test_collects_series_with_derived_baselines() -> None:
    contribution = _adapter().collect(CONTEXT)
    assert contribution.metrics is not None
    by_signal = {series.signal: series for series in contribution.metrics.series}

    latency = by_signal["p95_latency_ms"]
    assert latency.baseline == 450  # median of the pre-incident plateau
    assert latency.unit == "ms"
    assert latency.points[0].timestamp.isoformat() == "2026-06-01T14:05:00+00:00"
    assert latency.points[-1].timestamp.isoformat() == "2026-06-01T15:05:00+00:00"
    assert max(point.value for point in latency.points) == 3200

    assert by_signal["error_rate_pct"].baseline == 0.3
    assert contribution.notes == []


def test_query_params_follow_config() -> None:
    stub = PromStubHTTP()
    _adapter(stub).collect(CONTEXT)
    request, _ = stub.calls[0]
    assert request.url == f"{BASE_URL}/api/v1/query_range"
    assert request.params["step"] == "300"
    assert float(request.params["end"]) - float(request.params["start"]) == pytest.approx(
        (2 * 60 + 15 + 30 + 30) * 60  # baseline 2h + margin 15m + lookback 30m + post 30m
    )


def test_empty_result_skips_series_with_note() -> None:
    empty = json.dumps({"status": "success", "data": {"resultType": "matrix", "result": []}})
    good = json.dumps(
        series_payload(
            'p95_latency_ms{service="booking-service"}',
            ANCHOR.timestamp() - 9000,
            ANCHOR.timestamp() + 1800,
            300,
        )
    )
    adapter = PrometheusMetricsAdapter(ScriptedPromHTTP(empty, good), DEMO_CONFIG)
    contribution = adapter.collect(CONTEXT)
    assert contribution.metrics is not None
    assert len(contribution.metrics.series) == 1
    assert any("no series" in note for note in contribution.notes)


def test_ambiguous_result_skips_with_note() -> None:
    two = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {"metric": {"pod": "a"}, "values": [[ANCHOR.timestamp(), "1"]]},
                {"metric": {"pod": "b"}, "values": [[ANCHOR.timestamp(), "2"]]},
            ],
        },
    }
    adapter = PrometheusMetricsAdapter(ScriptedPromHTTP(json.dumps(two)), DEMO_CONFIG)
    with pytest.raises(CollectError, match="no metric series could be collected"):
        adapter.collect(CONTEXT)


def test_prometheus_error_status_and_http_error_degrade() -> None:
    prom_error = json.dumps({"status": "error", "errorType": "bad_data", "error": "parse error"})
    with pytest.raises(CollectError, match="bad_data"):
        PrometheusMetricsAdapter(ScriptedPromHTTP(prom_error), DEMO_CONFIG).collect(CONTEXT)
    with pytest.raises(CollectError, match="returned 500"):
        PrometheusMetricsAdapter(ScriptedPromHTTP("boom", status=500), DEMO_CONFIG).collect(CONTEXT)


def test_non_finite_samples_are_skipped_and_counted() -> None:
    start = ANCHOR.timestamp() - 9000
    payload = series_payload(
        'p95_latency_ms{service="booking-service"}', start, ANCHOR.timestamp() + 1800, 300
    )
    values = payload["data"]["result"][0]["values"]  # type: ignore[index]
    values[0][1] = "NaN"
    values[1][1] = "+Inf"
    adapter = PrometheusMetricsAdapter(
        ScriptedPromHTTP(json.dumps(payload)),
        PromConfig(base_url=BASE_URL, queries=[DEMO_CONFIG.queries[0]]),
    )
    contribution = adapter.collect(CONTEXT)
    assert contribution.metrics is not None
    assert any("2 non-finite sample(s)" in note for note in contribution.notes)


def test_no_baseline_samples_skips_series() -> None:
    only_window = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {},
                    "values": [[ANCHOR.timestamp(), "450"], [ANCHOR.timestamp() + 300, "460"]],
                }
            ],
        },
    }
    adapter = PrometheusMetricsAdapter(
        ScriptedPromHTTP(json.dumps(only_window)),
        PromConfig(base_url=BASE_URL, queries=[DEMO_CONFIG.queries[0]]),
    )
    with pytest.raises(CollectError, match="no pre-incident samples"):
        adapter.collect(CONTEXT)


def test_factory_validates_section(tmp_path: Path) -> None:
    good = tmp_path / "sources.toml"
    good.write_text(
        f'[prometheus]\nbase_url = "{BASE_URL}"\n\n'
        '[[prometheus.queries]]\nservice = "booking-service"\n'
        'signal = "p95_latency_ms"\nquery = "p95_latency_ms{service=\\"booking-service\\"}"\n'
    )
    adapter = prometheus_adapter(load_sources_config(good), PromStubHTTP())
    assert adapter.collect(CONTEXT).metrics is not None

    bad = tmp_path / "bad.toml"
    bad.write_text(f'[prometheus]\nbase_url = "{BASE_URL}"\n')  # no queries
    with pytest.raises(CollectError, match=r"\[prometheus\] section is invalid"):
        prometheus_adapter(load_sources_config(bad), PromStubHTTP())


def test_full_collection_with_sentry_anchor_and_committed_fixtures(tmp_path: Path) -> None:
    """Sentry anchor + Prometheus metrics, replayed from committed fixtures,
    produce a package whose incident window matches the deterministic rules."""
    alert_source = SentryAlertSource(
        ReplayHTTPClient(Path(__file__).resolve().parent / "fixtures" / "http" / "sentry_demo"),
        SENTRY_CONFIG,
        DEMO_ISSUE_ID,
    )
    adapter = PrometheusMetricsAdapter(ReplayHTTPClient(FIXTURES), DEMO_CONFIG)
    out = tmp_path / "collected"
    report = collect_package(alert_source, [adapter], out, CollectionSettings())

    assert [s.status for s in report.sources] == ["ok", "ok"]
    loaded = load_package(out)
    assert loaded.package.metrics is not None
    assert len(loaded.package.metrics.series) == 2
    window = incident_window(loaded.package)
    assert window.end is None  # stub series stay elevated: ongoing


def test_fixture_regeneration_matches_committed(tmp_path: Path) -> None:
    recorder = RecordingHTTPClient(PromStubHTTP(), tmp_path)
    PrometheusMetricsAdapter(recorder, DEMO_CONFIG).collect(demo_collection_context())
    fresh = {p.name: p.read_text() for p in tmp_path.glob("*.json")}
    committed = {p.name: p.read_text() for p in FIXTURES.glob("*.json")}
    assert fresh == committed

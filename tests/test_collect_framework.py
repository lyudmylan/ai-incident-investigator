"""Config loading, adapter merge semantics, and the collection orchestrator."""

import json
from pathlib import Path

import pytest

from ai_incident_investigator.collect import (
    AlertBundle,
    CollectError,
    CollectionContext,
    CollectionSettings,
    LocalTopologyAdapter,
    PackageContribution,
    collect_package,
    load_sources_config,
)
from ai_incident_investigator.collect.orchestrator import REPORT_FILENAME, CollectionReport
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.models.package import Alert, LogRecord, MetricsFile
from ai_incident_investigator.pipeline import initial_state
from helpers import EXAMPLE_DIR

ALERT = Alert(
    id="alert_c1",
    title="Collected alert",
    service="booking-service",
    triggered_at="2026-06-01T14:35:00Z",  # type: ignore[arg-type]
)

METRICS = MetricsFile.model_validate(
    {
        "series": [
            {
                "service": "booking-service",
                "signal": "p95_latency_ms",
                "baseline": 450,
                "points": [{"timestamp": "2026-06-01T14:30:00Z", "value": 900}],
            }
        ]
    }
)


def _log(minute: int, service: str = "booking-service") -> LogRecord:
    return LogRecord(
        timestamp=f"2026-06-01T14:{minute:02d}:00Z",  # type: ignore[arg-type]
        service=service,
        level="ERROR",
        message=f"boom at {minute}",
    )


class FakeAlertSource:
    def __init__(self, fail: bool = False, logs: list[LogRecord] | None = None) -> None:
        self.fail = fail
        self.logs = logs or []

    @property
    def name(self) -> str:
        return "fake_alerts"

    def fetch_alert(self) -> AlertBundle:
        if self.fail:
            raise RuntimeError("issue tracker unreachable")
        return AlertBundle(alert=ALERT, logs=self.logs)


class FakeAdapter:
    def __init__(
        self,
        name: str,
        contribution: PackageContribution | None = None,
        error: Exception | None = None,
    ) -> None:
        self._name = name
        self._contribution = contribution or PackageContribution()
        self._error = error
        self.seen_context: CollectionContext | None = None

    @property
    def name(self) -> str:
        return self._name

    def collect(self, context: CollectionContext) -> PackageContribution:
        self.seen_context = context
        if self._error is not None:
            raise self._error
        return self._contribution


SETTINGS = CollectionSettings(services=["booking-service"], lookback_minutes=30)


def test_config_loading_and_sections(tmp_path: Path) -> None:
    config_file = tmp_path / "sources.toml"
    config_file.write_text(
        '[collection]\nservices = ["booking-service"]\nlookback_minutes = 20\n\n'
        '[sentry]\nbase_url = "https://sentry.example.com/api/0"\ntoken_env = "SENTRY_TOKEN"\n'
    )
    config = load_sources_config(config_file)
    assert config.collection.lookback_minutes == 20
    assert config.section("sentry")["base_url"].startswith("https://")
    assert config.has_section("sentry") and not config.has_section("prometheus")
    with pytest.raises(CollectError, match=r"no \[prometheus\] section"):
        config.section("prometheus")
    assert config.resolve_path("topology.json") == (tmp_path / "topology.json").resolve()


def test_config_rejects_pasted_credentials(tmp_path: Path) -> None:
    config_file = tmp_path / "sources.toml"
    config_file.write_text('[sentry]\ntoken = "sntrys_actual_secret"\n')
    with pytest.raises(CollectError, match="must be env-var references"):
        load_sources_config(config_file)

    nested = tmp_path / "nested.toml"
    nested.write_text('[prometheus.auth]\napi_key = "prom_secret"\n')
    with pytest.raises(CollectError, match=r"prometheus\.auth\.api_key"):
        load_sources_config(nested)

    env_ref = tmp_path / "ok.toml"
    env_ref.write_text('[sentry]\ntoken_env = "SENTRY_TOKEN"\n')
    assert load_sources_config(env_ref).section("sentry")["token_env"] == "SENTRY_TOKEN"


def test_config_rejects_stray_top_level_keys(tmp_path: Path) -> None:
    config_file = tmp_path / "sources.toml"
    config_file.write_text('lookback_minutes = 20\n[sentry]\nbase_url = "https://x"\n')
    with pytest.raises(CollectError, match="do not belong to any section"):
        load_sources_config(config_file)


def test_config_missing_or_invalid(tmp_path: Path) -> None:
    with pytest.raises(CollectError, match="not found"):
        load_sources_config(tmp_path / "absent.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("not toml [[[")
    with pytest.raises(CollectError, match="not valid TOML"):
        load_sources_config(bad)


def test_collect_writes_a_loadable_package(tmp_path: Path) -> None:
    out = tmp_path / "pkg"
    adapters = [
        FakeAdapter("metrics_source", PackageContribution(metrics=METRICS)),
        FakeAdapter("logs_source", PackageContribution(logs=[_log(31), _log(29)])),
    ]
    report = collect_package(FakeAlertSource(logs=[_log(30)]), adapters, out, SETTINGS)

    loaded = load_package(out)
    assert loaded.package.alert.id == "alert_c1"
    assert loaded.package.metrics is not None
    # logs merged from the alert bundle and the adapter, sorted by time
    assert [r.timestamp.minute for r in loaded.package.logs] == [29, 30, 31]
    assert [s.status for s in report.sources] == ["ok", "ok", "ok"]
    # the facts pipeline runs on the collected package directly
    state = initial_state(loaded)
    assert state.window.start.isoformat() == "2026-06-01T14:05:00+00:00"


def test_collection_report_sidecar_is_written_and_ignored_by_loader(tmp_path: Path) -> None:
    out = tmp_path / "pkg"
    collect_package(FakeAlertSource(), [], out, SETTINGS)
    report = CollectionReport.model_validate(json.loads((out / REPORT_FILENAME).read_text()))
    assert report.sources[0].name == "fake_alerts"
    assert load_package(out).package.alert.id == "alert_c1"  # sidecar ignored


def test_alert_source_failure_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(CollectError, match="alert anchor"):
        collect_package(FakeAlertSource(fail=True), [], tmp_path / "pkg", SETTINGS)


def test_adapter_failure_degrades_and_others_continue(tmp_path: Path) -> None:
    out = tmp_path / "pkg"
    adapters = [
        FakeAdapter("broken", error=RuntimeError("source down")),
        FakeAdapter("metrics_source", PackageContribution(metrics=METRICS)),
    ]
    report = collect_package(FakeAlertSource(), adapters, out, SETTINGS)
    by_name = {s.name: s for s in report.sources}
    assert by_name["broken"].status == "failed"
    assert "source down" in (by_name["broken"].detail or "")
    assert by_name["metrics_source"].status == "ok"
    assert not (out / "traces.json").exists()  # gap stays a gap
    assert load_package(out).package.metrics is not None


def test_conflicting_single_file_contributions_fail_the_later_adapter(tmp_path: Path) -> None:
    adapters = [
        FakeAdapter("metrics_one", PackageContribution(metrics=METRICS)),
        FakeAdapter("metrics_two", PackageContribution(metrics=METRICS)),
    ]
    report = collect_package(FakeAlertSource(), adapters, tmp_path / "pkg", SETTINGS)
    by_name = {s.name: s for s in report.sources}
    assert by_name["metrics_one"].status == "ok"
    assert by_name["metrics_two"].status == "failed"
    assert "configuration bug" in (by_name["metrics_two"].detail or "")


def test_ok_adapter_notes_surface_in_the_report(tmp_path: Path) -> None:
    adapters = [
        FakeAdapter(
            "metrics_source",
            PackageContribution(
                metrics=METRICS, notes=["svc/other skipped: no series", "1 sample skipped"]
            ),
        )
    ]
    report = collect_package(FakeAlertSource(), adapters, tmp_path / "pkg", SETTINGS)
    status = {s.name: s for s in report.sources}["metrics_source"]
    assert status.status == "ok"
    assert status.detail == "svc/other skipped: no series; 1 sample skipped"


def test_context_carries_documented_spans(tmp_path: Path) -> None:
    adapter = FakeAdapter("probe")
    collect_package(FakeAlertSource(), [adapter], tmp_path / "pkg", SETTINGS)
    context = adapter.seen_context
    assert context is not None
    assert context.anchor_time == ALERT.triggered_at
    assert context.anchor_service == "booking-service"  # from the alert, not config
    assert context.lookback.total_seconds() == 30 * 60
    assert context.services == ["booking-service"]


def test_refuses_non_empty_target(tmp_path: Path) -> None:
    out = tmp_path / "pkg"
    out.mkdir()
    (out / "alert.json").write_text("{}")
    with pytest.raises(CollectError, match="refusing to overwrite"):
        collect_package(FakeAlertSource(), [], out, SETTINGS)


def _context() -> CollectionContext:
    from datetime import timedelta

    return CollectionContext(
        anchor_time=ALERT.triggered_at,
        anchor_service=ALERT.service,
        lookback=timedelta(minutes=30),
        change_lookback=timedelta(days=7),
        services=[],
    )


def test_local_topology_adapter(tmp_path: Path) -> None:
    contribution = LocalTopologyAdapter(EXAMPLE_DIR / "topology.json").collect(_context())
    assert contribution.topology is not None

    with pytest.raises(CollectError, match="not found"):
        LocalTopologyAdapter(tmp_path / "nope.json").collect(_context())

    bad = tmp_path / "topology.json"
    bad.write_text('{"services": "not-a-list"}')
    with pytest.raises(CollectError, match="invalid"):
        LocalTopologyAdapter(bad).collect(_context())

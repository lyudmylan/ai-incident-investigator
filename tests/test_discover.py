"""init --discover (#80): deterministic proposals, a trimmable draft, and
the closing loop - a generated draft is doctor-clean by construction."""

import tomllib
from pathlib import Path

import pytest

from ai_incident_investigator.cli import main
from ai_incident_investigator.collect import RecordingHTTPClient, load_sources_config
from ai_incident_investigator.collect.discover import (
    discover_loki,
    discover_prometheus,
    render_draft,
)
from ai_incident_investigator.collect.doctor import render_doctor, run_doctor
from discover_stub import DEMO_LOKI_URL, DEMO_PROM_URL, DiscoverStubHTTP


def test_prometheus_discovery_per_service_and_notes() -> None:
    metrics, notes = discover_prometheus(
        DiscoverStubHTTP(), DEMO_PROM_URL, "service", ["booking-service", "ghost-svc"]
    )
    assert metrics == {"booking-service": ["error_rate_pct", "p95_latency_ms"]}
    assert any("ghost-svc" in note and "--service-label job" in note for note in notes)


def test_auto_discovery_uses_the_service_label_values() -> None:
    metrics, notes = discover_prometheus(DiscoverStubHTTP(), DEMO_PROM_URL, "service", [])
    assert set(metrics) == {"booking-service", "payment-service"}
    assert metrics["payment-service"] == ["p95_latency_ms"]
    assert notes == []


def test_loki_discovery_filters_requested_services() -> None:
    values, notes = discover_loki(
        DiscoverStubHTTP(), DEMO_LOKI_URL, "app", ["booking-service", "ghost-svc"]
    )
    assert values == ["booking-service"]
    assert any("ghost-svc" in note for note in notes)

    everything, _ = discover_loki(DiscoverStubHTTP(), DEMO_LOKI_URL, "app", [])
    assert "noise-svc" in everything  # trimming the draft is the human's job


def _draft(tmp_path: Path) -> Path:
    stub = DiscoverStubHTTP()
    metrics, _ = discover_prometheus(stub, DEMO_PROM_URL, "service", [])
    loki_services, _ = discover_loki(stub, DEMO_LOKI_URL, "app", [])
    path = tmp_path / "draft.toml"
    path.write_text(
        render_draft(
            DEMO_PROM_URL, None, "service", metrics, DEMO_LOKI_URL, None, "app", loki_services
        )
    )
    return path


def test_draft_is_valid_toml_and_loads(tmp_path: Path) -> None:
    draft = _draft(tmp_path)
    parsed = tomllib.loads(draft.read_text())
    assert {q["signal"] for q in parsed["prometheus"]["queries"]} == {
        "error_rate_pct",
        "p95_latency_ms",
    }
    config = load_sources_config(draft)
    assert config.has_section("prometheus") and config.has_section("loki")
    assert "github" not in config.sections  # TODO blocks stay commented


def test_generated_draft_is_doctor_clean_by_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loop: discover -> draft -> doctor, same endpoints, zero FAILs."""
    monkeypatch.setenv("SENTRY_TOKEN", "placeholder-present")
    config = load_sources_config(_draft(tmp_path))
    checks = run_doctor(config, DiscoverStubHTTP(), issue_id=None)
    failed = [check for check in checks if check.status == "FAIL"]
    assert not failed, render_doctor(checks)


def test_cli_replay_writes_draft_and_never_overwrites(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixtures = tmp_path / "fixtures"
    recorder = RecordingHTTPClient(DiscoverStubHTTP(), fixtures)
    discover_prometheus(recorder, DEMO_PROM_URL, "service", [])
    discover_loki(recorder, DEMO_LOKI_URL, "app", [])

    out = tmp_path / "sources.toml"
    args = [
        "init",
        "--discover",
        "--prometheus",
        DEMO_PROM_URL,
        "--loki",
        DEMO_LOKI_URL,
        "--http",
        "replay",
        "--http-fixtures-dir",
        str(fixtures),
        "--output",
        str(out),
    ]
    assert main(args) == 0
    err = capsys.readouterr().err
    assert "wrote draft" in err and "collect doctor" in err
    assert out.exists()

    assert main(args) == 1  # never overwrites
    assert "already exists" in capsys.readouterr().err

    with pytest.raises(SystemExit):  # at least one endpoint is required
        main(["init", "--discover", "--output", str(tmp_path / "x.toml")])

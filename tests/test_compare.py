"""Pre/post recovery comparison (epic #53): pessimistic, deterministic."""

import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from ai_incident_investigator.cli import main
from ai_incident_investigator.compare import (
    ComparisonError,
    RecoveryComparison,
    build_comparison,
)
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.models.package import (
    IncidentPackage,
    LogRecord,
    MetricPoint,
    MetricSeries,
)
from helpers import ScriptedLLM
from scripted_runs import script_for
from sentry_stub import DEMO_ISSUE_ID

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_DIR = ROOT / "examples" / "incidents" / "latency_spike"
FOLLOW_UP_DIR = ROOT / "examples" / "followups" / "latency_spike"
COMPARISON_GOLDEN = ROOT / "tests" / "golden" / "comparisons" / "latency_spike.json"


def _original() -> IncidentPackage:
    return load_package(ORIGINAL_DIR).package


def _follow_up() -> IncidentPackage:
    return load_package(FOLLOW_UP_DIR).package


def _with_series(package: IncidentPackage, series: MetricSeries) -> IncidentPackage:
    assert package.metrics is not None
    metrics = package.metrics.model_copy(update={"series": [*package.metrics.series, series]})
    return package.model_copy(update={"metrics": metrics})


def _recovered_series(service: str, signal: str, baseline: float) -> MetricSeries:
    anchor = _follow_up().alert.triggered_at
    return MetricSeries(
        service=service,
        signal=signal,
        baseline=baseline,
        points=[
            MetricPoint(timestamp=anchor - timedelta(minutes=30 - 5 * i), value=baseline)
            for i in range(4)
        ],
    )


def test_committed_golden_matches_and_is_inconclusive() -> None:
    comparison = build_comparison(_original(), _follow_up())
    committed = RecoveryComparison.model_validate_json(COMPARISON_GOLDEN.read_text())
    assert comparison == committed
    assert comparison.verdict == "inconclusive"  # cpu_pct deliberately absent
    assert "4/5 watched signals recovered (1 unverifiable)" in comparison.summary
    assert comparison.re_alert == "not_met"
    assert all(not p.still_present for p in comparison.patterns)
    unverifiable = next(s for s in comparison.signals if s.recovered is None)
    assert (unverifiable.service, unverifiable.signal) == ("appointments-db", "cpu_pct")


def test_full_coverage_flips_the_verdict_to_recovered() -> None:
    complete = _with_series(_follow_up(), _recovered_series("appointments-db", "cpu_pct", 38.0))
    comparison = build_comparison(_original(), complete)
    assert comparison.verdict == "recovered"
    assert "5/5 watched signals recovered" in comparison.summary


def test_failed_signal_or_lingering_pattern_means_not_recovered() -> None:
    still_high = _with_series(
        _follow_up(),
        MetricSeries(
            service="appointments-db",
            signal="cpu_pct",
            baseline=38.0,
            points=[
                MetricPoint(
                    timestamp=_follow_up().alert.triggered_at - timedelta(minutes=10 - i),
                    value=90.0,
                )
                for i in range(3)
            ],
        ),
    )
    comparison = build_comparison(_original(), still_high)
    assert comparison.verdict == "not_recovered"
    assert any(
        "Outstanding: appointments-db/cpu_pct" in comparison.postmortem_addendum for _ in [0]
    )

    lingering = _follow_up().model_copy(
        update={
            "logs": [
                *_follow_up().logs,
                LogRecord(
                    timestamp=_follow_up().alert.triggered_at,
                    service="payment-service",
                    level="ERROR",
                    message="Eligibility enrichment query timed out after 2400ms",
                ),
            ]
        }
    )
    comparison = build_comparison(_original(), lingering)
    assert comparison.verdict == "not_recovered"
    pattern = next(p for p in comparison.patterns if p.still_present)
    assert pattern.occurrences_in_follow_up == 1  # normalized Nms matched 2400ms


def test_re_alert_met_forces_not_recovered() -> None:
    breached = _follow_up()
    assert breached.metrics is not None
    series = [
        s.model_copy(
            update={
                "points": [
                    *s.points,
                    MetricPoint(
                        timestamp=breached.alert.triggered_at + timedelta(minutes=5),
                        value=1500.0,
                    ),
                ]
            }
        )
        if (s.service, s.signal) == ("booking-service", "p95_latency_ms")
        else s
        for s in breached.metrics.series
    ]
    breached = breached.model_copy(
        update={"metrics": breached.metrics.model_copy(update={"series": series})}
    )
    comparison = build_comparison(_original(), breached)
    assert comparison.re_alert == "met"
    assert comparison.verdict == "not_recovered"


def test_original_without_derivable_plan_is_an_error(tmp_path: Path) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    shutil.copy(ORIGINAL_DIR / "alert.json", bare / "alert.json")
    with pytest.raises(ComparisonError, match="no recovery verification plan"):
        build_comparison(load_package(bare).package, _follow_up())


def test_cli_json_markdown_and_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["compare", "--incident", str(ORIGINAL_DIR), "--follow-up", str(FOLLOW_UP_DIR)])
    assert code == 0
    captured = capsys.readouterr()
    parsed = RecoveryComparison.model_validate_json(captured.out)
    assert parsed.verdict == "inconclusive"
    assert "verdict: inconclusive" in captured.err

    code = main(
        [
            "compare",
            "--incident",
            str(ORIGINAL_DIR),
            "--follow-up",
            str(FOLLOW_UP_DIR),
            "--format",
            "markdown",
        ]
    )
    assert code == 0
    assert "**Verdict: INCONCLUSIVE**" in capsys.readouterr().out

    bare = tmp_path / "bare"
    bare.mkdir()
    shutil.copy(ORIGINAL_DIR / "alert.json", bare / "alert.json")
    assert main(["compare", "--incident", str(bare), "--follow-up", str(FOLLOW_UP_DIR)]) == 1
    assert "no recovery verification plan" in capsys.readouterr().err


def test_e2e_collect_investigate_snapshot_later_compare(tmp_path: Path) -> None:
    """The issue's chain, entirely from fixtures: collect the demo package,
    investigate it via replay, simulate a later snapshot, compare."""
    from ai_incident_investigator.collect import (
        ReplayHTTPClient,
        collect_package,
        load_sources_config,
    )
    from ai_incident_investigator.collect.registry import build_sources
    from ai_incident_investigator.pipeline import initial_state, run_investigation

    config = load_sources_config(ROOT / "examples" / "collect" / "sources.toml")
    http = ReplayHTTPClient(ROOT / "tests" / "fixtures" / "http" / "demo_collect")
    alert_source, adapters = build_sources(config, http, DEMO_ISSUE_ID)
    collected = tmp_path / "collected_demo"
    collect_package(alert_source, adapters, collected, config.collection)

    state = run_investigation(
        initial_state(load_package(collected)),
        ScriptedLLM(script_for("collected_demo")),
    )
    assert state.recovery_verification is not None  # the plan the follow-up must satisfy

    # the "collected later" snapshot: same package, metrics flat at baseline
    follow_up_dir = tmp_path / "collected_demo_followup"
    shutil.copytree(collected, follow_up_dir)
    metrics = json.loads((follow_up_dir / "metrics.json").read_text())
    for series in metrics["series"]:
        for offset, point in enumerate(series["points"]):
            point["value"] = series["baseline"]
            point["timestamp"] = f"2026-06-01T17:{offset:02d}:00Z"
    (follow_up_dir / "metrics.json").write_text(json.dumps(metrics))
    (follow_up_dir / "logs.jsonl").write_text("")

    comparison = build_comparison(
        load_package(collected).package, load_package(follow_up_dir).package
    )
    assert comparison.verdict == "recovered"
    assert all(s.recovered for s in comparison.signals)

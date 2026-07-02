"""The deterministic recovery verification builder (docs/assumptions.md rules)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_incident_investigator.loading import load_package
from ai_incident_investigator.models.package import (
    Alert,
    IncidentPackage,
    LogRecord,
    MetricPoint,
    MetricSeries,
    MetricsFile,
)
from ai_incident_investigator.models.report import RecoveryVerificationPlan
from ai_incident_investigator.pipeline import initial_state, run_investigation
from ai_incident_investigator.recovery import build_recovery_verification
from ai_incident_investigator.window import incident_window
from helpers import EXAMPLE_DIR, ScriptedLLM, default_script

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "incidents"

T0 = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)


def _package(
    metrics: MetricsFile | None,
    logs: list[LogRecord] | None = None,
    alert_signal: str | None = None,
    alert_threshold: float | None = None,
) -> IncidentPackage:
    return IncidentPackage(
        incident_id="synthetic",
        alert=Alert(
            id="alert_1",
            title="Latency high on svc",
            service="svc",
            triggered_at=T0 + timedelta(minutes=30),
            signal=alert_signal,
            threshold=alert_threshold,
        ),
        metrics=metrics,
        logs=logs or [],
    )


def _series(values: list[float], baseline: float = 100.0) -> MetricSeries:
    return MetricSeries(
        service="svc",
        signal="p95_latency_ms",
        baseline=baseline,
        unit="ms",
        points=[
            MetricPoint(timestamp=T0 + timedelta(minutes=5 * i), value=v)
            for i, v in enumerate(values)
        ],
    )


def _build(
    package: IncidentPackage,
) -> tuple[RecoveryVerificationPlan | None, str]:
    return build_recovery_verification(package, incident_window(package))


def test_no_metrics_and_no_deviation_yield_no_plan_with_reasons() -> None:
    plan, summary = _build(_package(metrics=None))
    assert plan is None
    assert "no metrics" in summary

    flat = MetricsFile(series=[_series([100, 105, 110])])
    plan, summary = _build(_package(metrics=flat))
    assert plan is None
    assert "no metric series deviated" in summary


def test_deviated_series_becomes_watched_signal_with_documented_rule() -> None:
    deviated = MetricsFile(series=[_series([100, 300, 400, 350])])
    plan, summary = _build(_package(metrics=deviated))
    assert plan is not None
    assert plan.mode == "watch_for_recovery"
    signal = plan.signals[0]
    assert signal.baseline == 100.0
    assert signal.recovered_when == ("within 10% of baseline 100 ms for >= 3 consecutive points")
    # anomalous points span 10 minutes (14:05 -> 14:15); 2x = 20 -> min 30
    assert signal.watch_minutes == 30
    assert "1 deviated series" in summary


def test_watch_duration_doubles_long_deviations() -> None:
    values = [100.0] + [400.0] * 13  # anomalous span: 5min .. 65min = 60 minutes
    plan, _ = _build(_package(metrics=MetricsFile(series=[_series(values)])))
    assert plan is not None
    assert plan.signals[0].watch_minutes == 120


def test_recovered_series_switch_to_confirm_mode() -> None:
    # deviates then ends with >= 3 recovered points -> window ends -> confirm
    values = [100.0, 400.0, 400.0, 102.0, 101.0, 100.0]
    plan, _ = _build(_package(metrics=MetricsFile(series=[_series(values)])))
    assert plan is not None
    assert plan.mode == "confirm_sustained_recovery"


def test_log_patterns_recurring_normalized_capped() -> None:
    logs = [
        LogRecord(
            timestamp=T0 + timedelta(minutes=10 + i),
            service="svc",
            level="ERROR",
            message=f"lookup timed out after {2000 + i}ms",
        )
        for i in range(3)
    ]
    logs.append(
        LogRecord(
            timestamp=T0 + timedelta(minutes=12),
            service="svc",
            level="ERROR",
            message="a one-off failure id 12345",
        )
    )
    logs.append(
        LogRecord(
            timestamp=T0 + timedelta(minutes=13),
            service="svc",
            level="WARN",
            message="warned twice",
        )
    )
    deviated = MetricsFile(series=[_series([100, 300, 400])])
    plan, _ = _build(_package(metrics=deviated, logs=logs))
    assert plan is not None
    # the three timeout lines normalize to one recurring shape; one-offs and
    # non-ERROR levels are excluded
    assert plan.log_patterns_should_stop == ["lookup timed out after Nms"]


def test_re_alert_uses_threshold_when_present_else_alert_name() -> None:
    deviated = MetricsFile(series=[_series([100, 300, 400])])
    plan, _ = _build(
        _package(metrics=deviated, alert_signal="p95_latency_ms", alert_threshold=2000)
    )
    assert plan is not None
    assert plan.re_alert_condition == "p95_latency_ms on svc crosses the alert threshold 2000 again"

    plan, _ = _build(_package(metrics=deviated))
    assert plan is not None
    assert plan.re_alert_condition == "the original alert 'Latency high on svc' fires again"


def test_determinism() -> None:
    package = load_package(EXAMPLE_DIR)
    one, _ = build_recovery_verification(package.package, incident_window(package.package))
    two, _ = build_recovery_verification(package.package, incident_window(package.package))
    assert one is not None
    assert one == two


def test_example_shapes_ongoing_vs_recovered() -> None:
    ongoing = load_package(EXAMPLES / "dependency_timeout").package
    plan, _ = build_recovery_verification(ongoing, incident_window(ongoing))
    assert plan is not None
    assert plan.mode == "watch_for_recovery"

    recovered = load_package(EXAMPLES / "error_rate_spike").package
    plan, _ = build_recovery_verification(recovered, incident_window(recovered))
    assert plan is not None
    assert plan.mode == "confirm_sustained_recovery"
    assert plan.signals  # confirms sustained recovery of the series that deviated


def test_full_run_carries_the_plan_even_when_llm_agents_fail() -> None:
    class ExplodingLLM:
        def complete(self, request: object) -> object:
            raise RuntimeError("LLM down")

    state = run_investigation(initial_state(load_package(EXAMPLE_DIR)), ExplodingLLM())  # type: ignore[arg-type]
    assert state.recovery_verification is not None  # deterministic node survived
    assert state.failures  # while every LLM agent failed

    scripted = run_investigation(
        initial_state(load_package(EXAMPLE_DIR)), ScriptedLLM(default_script())
    )
    assert scripted.recovery_verification is not None
    assert scripted.recovery_verification.mode == "watch_for_recovery"
    stages = [step.stage for step in scripted.reasoning_trace]
    assert "recovery_builder" in stages

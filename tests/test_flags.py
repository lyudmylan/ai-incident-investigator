"""The flag adapter (#67): one route, one verb, stubbed, credential-free
fixtures - and the live execute path that reaches it only through clearance.
"""

from pathlib import Path

import pytest

from ai_incident_investigator.cli import main
from ai_incident_investigator.execute import load_executions, perform_execution
from ai_incident_investigator.flags import (
    FlagToggled,
    FlagToggleError,
    RecordingFlagClient,
    ReplayFlagClient,
)
from ai_incident_investigator.models.execution import FlagToggleRequest, load_executor_config
from flag_stub import AuthResolvingFlagStub, FlagToggleStub
from test_execute import (
    EXAMPLE_CONFIG,
    FLAG,
    GOLDEN,
    NOW,
    _approve,
    _report,
    _state_changing_ref,
)

ROOT = Path(__file__).resolve().parents[1]
DEMO_FIXTURES = ROOT / "tests" / "fixtures" / "http" / "flag_toggle_demo"
CANONICAL = FlagToggleRequest(environment="staging", flag_key=FLAG, on=False)


def test_committed_demo_fixture_replays() -> None:
    result = ReplayFlagClient(DEMO_FIXTURES).toggle(CANONICAL)
    assert result == FlagToggled(key=FLAG, on=False)


def test_replay_refuses_unknown_and_mismatched_requests(tmp_path: Path) -> None:
    with pytest.raises(FlagToggleError, match="no flag fixture"):
        ReplayFlagClient(tmp_path).toggle(CANONICAL)
    RecordingFlagClient(FlagToggleStub(), tmp_path).toggle(CANONICAL)
    fixture = next(tmp_path.glob("*.json"))
    tampered = fixture.read_text().replace('"on": false', '"on": true', 1)
    fixture.write_text(tampered)
    with pytest.raises(FlagToggleError, match="different request"):
        ReplayFlagClient(tmp_path).toggle(CANONICAL)


def test_recorded_fixture_is_credential_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recording path resolves the real token (like the live client)
    but the fixture on disk can never contain it: the recordable request
    type has no headers."""
    from ai_incident_investigator.collect.http import EnvBearerAuth

    secret = "flag-token-super-secret-value"
    monkeypatch.setenv("FLAG_TOGGLE_TOKEN", secret)
    stub = AuthResolvingFlagStub()
    RecordingFlagClient(stub, tmp_path).toggle(
        CANONICAL, EnvBearerAuth(env_var="FLAG_TOGGLE_TOKEN")
    )
    assert stub.resolved == [f"Bearer {secret}"]
    for fixture in tmp_path.glob("*.json"):
        assert secret not in fixture.read_text()


def test_publish_refuses_the_executor_credential(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The remaining isolation direction: publish cannot borrow the flag
    executor's token."""
    code = main(
        [
            "publish",
            "--report",
            str(GOLDEN),
            "--repo",
            "acme/incidents",
            "--token-env",
            "FLAG_TOGGLE_TOKEN",
        ]
    )
    assert code == 1
    assert "flag executor's" in capsys.readouterr().err


def _live_cli_args(report_file: Path, plan_id: str, step_index: int, environment: str) -> list[str]:
    return [
        "execute",
        "--report",
        str(report_file),
        "--executor-config",
        str(EXAMPLE_CONFIG),
        "--plan",
        plan_id,
        "--step",
        str(step_index),
        "--environment",
        environment,
        "--flag",
        FLAG,
        "--off",
        "--executed-by",
        "lyudmyla",
        "--live",
        "--http",
        "replay",
        "--http-fixtures-dir",
        str(DEMO_FIXTURES),
    ]


def test_live_execute_applies_via_replayed_adapter(
    report_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The keyless live-path demo: approved staging step, replayed PATCH,
    outcome 'applied' with verification pending for #68."""
    report = _report(report_file)
    plan_id, step_index = _state_changing_ref(report)
    _approve(report_file)

    assert main(_live_cli_args(report_file, plan_id, step_index, "staging")) == 0
    captured = capsys.readouterr()
    assert "EXECUTED - sent PATCH" in captured.err
    records = load_executions(report_file)
    assert len(records) == 1
    assert records[0].mode == "live"
    assert records[0].outcome == "applied"
    assert records[0].verification == "pending"


def test_live_execute_refuses_production_before_touching_the_adapter(
    report_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _report(report_file)
    plan_id, step_index = _state_changing_ref(report)
    _approve(report_file)
    _approve(report_file, approver="peer")

    assert main(_live_cli_args(report_file, plan_id, step_index, "prod-us")) == 1
    assert "does not allow live execution during the pilot" in capsys.readouterr().err
    records = load_executions(report_file)
    assert records[0].outcome == "refused"


def test_live_failure_is_recorded_as_failed(
    report_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty replay dir makes the adapter raise; the record says
    'failed', never 'applied'."""
    report = _report(report_file)
    plan_id, step_index = _state_changing_ref(report)
    _approve(report_file)

    args = _live_cli_args(report_file, plan_id, step_index, "staging")
    args[args.index(str(DEMO_FIXTURES))] = str(tmp_path)
    assert main(args) == 1
    assert "failed: flag toggle failed" in capsys.readouterr().err
    records = load_executions(report_file)
    assert records[0].outcome == "failed"
    assert records[0].verification == "not_applicable"


def test_cleared_live_without_client_is_refused(report_file: Path) -> None:
    """perform_execution never assumes a transport exists."""
    from ai_incident_investigator.approvals import load_approvals, report_hash

    report = _report(report_file)
    plan_id, step_index = _state_changing_ref(report)
    _approve(report_file)
    record = perform_execution(
        report,
        load_approvals(report_file),
        report_hash(report_file),
        load_executor_config(EXAMPLE_CONFIG),
        plan_id,
        step_index,
        CANONICAL,
        "lyudmyla",
        NOW,
        "live",
        client=None,
    )
    assert record.outcome == "refused"
    assert record.detail is not None and "no flag client" in record.detail


def test_dry_run_and_live_are_mutually_exclusive(report_file: Path) -> None:
    report = _report(report_file)
    plan_id, step_index = _state_changing_ref(report)
    args = [*_live_cli_args(report_file, plan_id, step_index, "staging"), "--dry-run"]
    with pytest.raises(SystemExit):
        main(args)


@pytest.fixture()
def report_file(tmp_path: Path) -> Path:
    import shutil

    path = tmp_path / "report.json"
    shutil.copy(GOLDEN, path)
    return path

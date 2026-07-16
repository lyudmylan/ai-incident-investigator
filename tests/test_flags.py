"""The flag adapter (#67): one route, one verb, stubbed, credential-free
fixtures - and the live execute path that reaches it only through clearance.
"""

from pathlib import Path

import pytest

from ai_incident_investigator.cli import main
from ai_incident_investigator.collect.http import EnvBearerAuth, request_key
from ai_incident_investigator.execute import load_executions, perform_execution
from ai_incident_investigator.flags import (
    FlagToggled,
    FlagToggleError,
    LiveFlagClient,
    RecordingFlagClient,
    ReplayFlagClient,
    _parse_toggle_response,
    toggle_route,
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


def test_route_is_derived_in_exactly_one_place() -> None:
    assert (
        toggle_route("https://flags.example/", CANONICAL)
        == f"https://flags.example/flags/staging/{FLAG}"
    )


def test_success_status_handling_without_a_network() -> None:
    """200 with a body is authoritative; 201/204 empty bodies echo the
    DESIRED state (idempotent desired-state action, verification pending);
    non-2xx and garbage bodies are errors - never a silent mislabel."""
    assert _parse_toggle_response(200, '{"key": "k", "on": true}', CANONICAL) == FlagToggled(
        key="k", on=True
    )
    assert _parse_toggle_response(204, "", CANONICAL) == FlagToggled(key=FLAG, on=False)
    assert _parse_toggle_response(201, "  ", CANONICAL) == FlagToggled(key=FLAG, on=False)
    with pytest.raises(FlagToggleError, match="unexpected HTTP 500"):
        _parse_toggle_response(500, "", CANONICAL)
    with pytest.raises(FlagToggleError, match="not understood"):
        _parse_toggle_response(200, "<html>gateway</html>", CANONICAL)


def test_missing_credential_raises_flag_toggle_error_before_any_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The auth header is assembled inside the guarded path: an unset env
    var must surface as FlagToggleError so the executor can still write
    its audit record."""
    monkeypatch.delenv("FLAG_TOGGLE_TOKEN", raising=False)
    client = LiveFlagClient("https://flags.invalid.example")
    with pytest.raises(FlagToggleError, match="FLAG_TOGGLE_TOKEN"):
        client.toggle(CANONICAL, EnvBearerAuth(env_var="FLAG_TOGGLE_TOKEN"))


def test_corrupt_fixture_raises_flag_toggle_error(tmp_path: Path) -> None:
    (tmp_path / f"{request_key(CANONICAL)}.json").write_text("{ not json")
    with pytest.raises(FlagToggleError, match="unusable"):
        ReplayFlagClient(tmp_path).toggle(CANONICAL)


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
    assert "failed: no flag fixture" in capsys.readouterr().err
    records = load_executions(report_file)
    assert records[0].outcome == "failed"
    assert records[0].verification == "not_applicable"
    # single prefix: the detail is the error itself, not a re-wrapped one
    assert records[0].detail is not None
    assert not records[0].detail.startswith("flag toggle failed: flag toggle failed")


def _cleared_live_kwargs(report_file: Path) -> dict[str, object]:
    from ai_incident_investigator.approvals import load_approvals, report_hash

    report = _report(report_file)
    plan_id, step_index = _state_changing_ref(report)
    _approve(report_file)
    return {
        "report": report,
        "records": load_approvals(report_file),
        "current_hash": report_hash(report_file),
        "config": load_executor_config(EXAMPLE_CONFIG),
        "plan_id": plan_id,
        "step_index": step_index,
        "action": CANONICAL,
        "executed_by": "lyudmyla",
        "now": NOW,
        "mode": "live",
    }


def test_cleared_live_without_client_is_a_wiring_error(report_file: Path) -> None:
    """A missing transport is a programming bug, never recorded as a policy
    refusal that could mislead an audit."""
    with pytest.raises(ValueError, match="requires a flag client"):
        perform_execution(**_cleared_live_kwargs(report_file), client=None)  # type: ignore[arg-type]


def test_foreign_credential_is_refused_at_the_library_boundary(report_file: Path) -> None:
    """Credential isolation is not just a CLI convention: perform_execution
    only ever presents the executor's own token."""
    with pytest.raises(ValueError, match="credential isolation"):
        perform_execution(
            **_cleared_live_kwargs(report_file),  # type: ignore[arg-type]
            client=FlagToggleStub(),
            auth=EnvBearerAuth(env_var="GITHUB_PUBLISH_TOKEN"),
        )


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

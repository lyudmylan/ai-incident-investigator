"""The v5 pilot executor (#66): decision chain and audit, no action possible.

Approval gate -> allowlist -> tier quorum -> record-before-report. Live
execution does not exist; the epic #60 refusal matrix is pinned here.
"""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_incident_investigator.approvals import (
    ApprovalRecord,
    append_approval,
    report_hash,
)
from ai_incident_investigator.approvals import (
    load_approvals as _load_approvals,
)
from ai_incident_investigator.cli import main
from ai_incident_investigator.execute import load_executions, plan_execution
from ai_incident_investigator.models.execution import (
    ExecutionRecord,
    ExecutorConfig,
    FlagToggleRequest,
    executions_path,
    load_executor_config,
)
from ai_incident_investigator.models.report import InvestigationReport

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "latency_spike.json"
EXAMPLE_CONFIG = ROOT / "examples" / "execute" / "executor.toml"
NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
FLAG = "payment_enrichment"


@pytest.fixture()
def report_file(tmp_path: Path) -> Path:
    path = tmp_path / "report.json"
    shutil.copy(GOLDEN, path)
    return path


@pytest.fixture()
def config() -> ExecutorConfig:
    return load_executor_config(EXAMPLE_CONFIG)


def _report(path: Path) -> InvestigationReport:
    return InvestigationReport.model_validate_json(path.read_text())


def _state_changing_ref(report: InvestigationReport) -> tuple[str, int]:
    for plan in report.remediation_plans:
        for index, step in enumerate(plan.steps):
            if step.kind == "state_changing":
                return plan.id, index
    raise AssertionError("golden report has no state-changing step")


def _approve(report_file: Path, approver: str = "lyudmyla") -> None:
    report = _report(report_file)
    plan_id, step_index = _state_changing_ref(report)
    append_approval(
        report_file,
        ApprovalRecord.model_validate(
            {
                "approver": approver,
                "approved_at": NOW.isoformat(),
                "plan_id": plan_id,
                "step_index": step_index,
                "report_sha256": report_hash(report_file),
            }
        ),
    )


def _decide(
    report_file: Path,
    config: ExecutorConfig,
    environment: str = "staging",
    flag_key: str = FLAG,
    executed_by: str = "lyudmyla",
    mode: str = "dry_run",
) -> ExecutionRecord:
    report = _report(report_file)
    plan_id, step_index = _state_changing_ref(report)
    return plan_execution(
        report,
        _load_approvals(report_file),
        report_hash(report_file),
        config,
        plan_id,
        step_index,
        FlagToggleRequest(environment=environment, flag_key=flag_key, on=False),
        executed_by,
        NOW,
        mode="live" if mode == "live" else "dry_run",
    )


def test_refused_without_any_approval(report_file: Path, config: ExecutorConfig) -> None:
    record = _decide(report_file, config)
    assert record.outcome == "refused"
    assert record.detail is not None and "no approval record" in record.detail
    assert record.required_approvals == 1  # staging tier
    assert record.approvals_satisfied == []


def test_dry_run_previewed_after_staging_approval(
    report_file: Path, config: ExecutorConfig
) -> None:
    _approve(report_file)
    record = _decide(report_file, config)
    assert record.outcome == "previewed"
    assert record.mode == "dry_run"
    assert record.verification == "not_applicable"
    assert record.approvals_satisfied == ["lyudmyla"]
    assert record.detail is not None
    assert (
        "would send PATCH "
        "https://flags.staging.internal.example/flags/staging/payment_enrichment" in record.detail
    )
    assert "on=false" in record.detail


def test_production_tier_demands_two_distinct_approvers(
    report_file: Path, config: ExecutorConfig
) -> None:
    """The owner requirement, end to end: one person cannot execute against
    production - not even with a valid approval."""
    _approve(report_file)
    record = _decide(report_file, config, environment="prod-us")
    assert record.outcome == "refused"
    assert record.required_approvals == 2
    assert record.detail is not None and "quorum not met: 1/2" in record.detail

    _approve(report_file, approver="peer")
    record = _decide(report_file, config, environment="prod-us")
    assert record.outcome == "previewed"
    assert record.approvals_satisfied == ["lyudmyla", "peer"]


def test_live_is_refused_everywhere_in_the_pilot(report_file: Path, config: ExecutorConfig) -> None:
    _approve(report_file)
    _approve(report_file, approver="peer")
    production = _decide(report_file, config, environment="prod-us", mode="live")
    assert production.outcome == "refused"
    assert production.detail is not None
    assert "does not allow live execution during the pilot" in production.detail

    staging = _decide(report_file, config, mode="live")
    assert staging.outcome == "refused"
    assert staging.detail is not None and "flag adapter lands with #67" in staging.detail


def test_unknown_environment_and_unlisted_flag_are_refused(
    report_file: Path, config: ExecutorConfig
) -> None:
    _approve(report_file)
    unknown = _decide(report_file, config, environment="nowhere")
    assert unknown.outcome == "refused"
    assert unknown.detail is not None and "not in the executor allowlist" in unknown.detail

    unlisted = _decide(report_file, config, flag_key="some_other_flag")
    assert unlisted.outcome == "refused"
    assert unlisted.detail is not None and "structurally unreachable" in unlisted.detail


def test_tampered_report_voids_the_execution(report_file: Path, config: ExecutorConfig) -> None:
    _approve(report_file)
    report_file.write_text(report_file.read_text() + "\n")
    record = _decide(report_file, config)
    assert record.outcome == "refused"
    assert record.detail is not None and "void_report_changed" in record.detail


def test_invoker_exclusion_blocks_self_execution(report_file: Path, tmp_path: Path) -> None:
    """Stricter separation of duties: with invoker_counts_toward_quorum off,
    the approver cannot be the one who executes on their own approval."""
    strict = tmp_path / "executor.toml"
    strict.write_text(
        'base_url = "https://flags.example"\n'
        "[policy]\n"
        "invoker_counts_toward_quorum = false\n"
        "[[environments]]\n"
        'name = "staging"\n'
        'tier = "staging"\n'
        f'flags = ["{FLAG}"]\n'
    )
    config = load_executor_config(strict)
    _approve(report_file)
    record = _decide(report_file, config)
    assert record.outcome == "refused"
    assert record.detail is not None
    assert "lyudmyla excluded from quorum as the invoker" in record.detail

    _approve(report_file, approver="peer")
    record = _decide(report_file, config)
    assert record.outcome == "previewed"
    assert record.approvals_satisfied == ["peer"]


def _cli_args(report_file: Path, plan_id: str, step_index: int) -> list[str]:
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
        "staging",
        "--flag",
        FLAG,
        "--off",
        "--executed-by",
        "lyudmyla",
        "--dry-run",
    ]


def test_cli_records_before_reporting_and_appends(
    report_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _report(report_file)
    plan_id, step_index = _state_changing_ref(report)

    assert main(_cli_args(report_file, plan_id, step_index)) == 1
    captured = capsys.readouterr()
    assert "refused" in captured.err
    assert captured.out.strip().endswith(".executions.json")
    records = load_executions(report_file)
    assert len(records) == 1 and records[0].outcome == "refused"

    _approve(report_file)
    assert main(_cli_args(report_file, plan_id, step_index)) == 0
    captured = capsys.readouterr()
    assert "DRY RUN - would send PATCH" in captured.err
    records = load_executions(report_file)
    assert [r.outcome for r in records] == ["refused", "previewed"]


def test_cli_refuses_without_dry_run_flag(report_file: Path) -> None:
    report = _report(report_file)
    plan_id, step_index = _state_changing_ref(report)
    args = [a for a in _cli_args(report_file, plan_id, step_index) if a != "--dry-run"]
    with pytest.raises(SystemExit):
        main(args)


def test_unrepresentable_action_gets_no_record(
    report_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _report(report_file)
    plan_id, step_index = _state_changing_ref(report)
    args = _cli_args(report_file, plan_id, step_index)
    args[args.index(FLAG)] = "../route-bender"
    assert main(args) == 1
    assert "not representable" in capsys.readouterr().err
    assert not executions_path(report_file).exists()

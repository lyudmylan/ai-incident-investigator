"""Approval records (epic #52): content-bound, append-only, never execution.

The last test IS the epic's design test: the v5 executor gate consuming
this format unchanged.
"""

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_incident_investigator.approvals import (
    ApprovalRecord,
    append_approval,
    approvals_path,
    distinct_valid_approvers,
    is_actionable,
    load_approvals,
    record_status,
    report_hash,
    step_statuses,
)
from ai_incident_investigator.cli import main
from ai_incident_investigator.markdown import render_markdown
from ai_incident_investigator.models.execution import ApprovalPolicy, EnvironmentTier
from ai_incident_investigator.models.report import InvestigationReport

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "latency_spike.json"
NOW = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)


@pytest.fixture()
def report_file(tmp_path: Path) -> Path:
    path = tmp_path / "report.json"
    shutil.copy(GOLDEN, path)
    return path


def _report(path: Path) -> InvestigationReport:
    return InvestigationReport.model_validate_json(path.read_text())


def _state_changing_ref(report: InvestigationReport) -> tuple[str, int]:
    for plan in report.remediation_plans:
        for index, step in enumerate(plan.steps):
            if step.kind == "state_changing":
                return plan.id, index
    raise AssertionError("golden report has no state-changing step")


def _record(report_file: Path, **overrides: object) -> ApprovalRecord:
    report = _report(report_file)
    plan_id, step_index = _state_changing_ref(report)
    payload: dict[str, object] = {
        "approver": "lyudmyla",
        "approved_at": NOW.isoformat(),
        "plan_id": plan_id,
        "step_index": step_index,
        "report_sha256": report_hash(report_file),
    }
    payload.update(overrides)
    return ApprovalRecord.model_validate(payload)


def test_append_only_sidecar_round_trip(report_file: Path) -> None:
    first = _record(report_file)
    path = append_approval(report_file, first)
    assert path == approvals_path(report_file)
    second = _record(report_file, approver="second-approver")
    append_approval(report_file, second)
    records = load_approvals(report_file)
    assert [r.approver for r in records] == ["lyudmyla", "second-approver"]


def test_status_valid_and_every_refusal(report_file: Path) -> None:
    report = _report(report_file)
    current = report_hash(report_file)
    plan_id, _step_index = _state_changing_ref(report)

    assert record_status(_record(report_file), report, current, NOW) == "valid"
    assert (
        record_status(_record(report_file, report_sha256="0" * 64), report, current, NOW)
        == "void_report_changed"
    )
    expired = _record(report_file, expires_at=(NOW - timedelta(hours=1)).isoformat())
    assert record_status(expired, report, current, NOW) == "expired"
    assert (
        record_status(_record(report_file, plan_id="plan_ghost"), report, current, NOW)
        == "unknown_plan"
    )
    assert (
        record_status(_record(report_file, step_index=99), report, current, NOW) == "unknown_step"
    )
    read_only_index = next(
        i
        for p in report.remediation_plans
        if p.id == plan_id
        for i, s in enumerate(p.steps)
        if s.kind == "read_only"
    )
    assert (
        record_status(_record(report_file, step_index=read_only_index), report, current, NOW)
        == "step_not_state_changing"
    )


def test_the_v5_executor_gate_design_test(report_file: Path) -> None:
    """The epic's acceptance criterion, executable: the gate refuses
    everything but a valid, hash-matching, unexpired approval - with the
    format exactly as shipped."""
    report = _report(report_file)
    current = report_hash(report_file)
    plan_id, step_index = _state_changing_ref(report)

    ok, reason = is_actionable(report, [], current, plan_id, step_index, NOW)
    assert not ok and "no approval record" in reason

    valid = _record(report_file)
    ok, reason = is_actionable(report, [valid], current, plan_id, step_index, NOW)
    assert ok and "approved by lyudmyla" in reason

    # regenerating the report voids the approval: same records, new hash
    ok, reason = is_actionable(report, [valid], "f" * 64, plan_id, step_index, NOW)
    assert not ok and "void_report_changed" in reason

    expired = _record(report_file, expires_at=(NOW - timedelta(minutes=1)).isoformat())
    ok, reason = is_actionable(report, [expired], current, plan_id, step_index, NOW)
    assert not ok and "expired" in reason

    # approval for step N never actions step M
    ok, _ = is_actionable(report, [valid], current, plan_id, step_index + 1, NOW)
    assert not ok


def test_step_statuses_and_markdown_annotation(report_file: Path) -> None:
    report = _report(report_file)
    current = report_hash(report_file)
    plan_id, step_index = _state_changing_ref(report)
    append_approval(report_file, _record(report_file))

    statuses = step_statuses(report, load_approvals(report_file), current, NOW)
    assert statuses[(plan_id, step_index)].startswith("approved by lyudmyla")
    unapproved = [s for s in statuses.values() if s == "unapproved"]
    assert unapproved  # the other plan's step has no record

    text = render_markdown(report, step_statuses=statuses)
    assert "- approval: approved by lyudmyla" in text
    assert "- approval: unapproved" in text
    # and the default rendering is byte-stable without statuses
    assert "- approval:" not in render_markdown(report)

    void = step_statuses(report, load_approvals(report_file), "e" * 64, NOW)
    assert void[(plan_id, step_index)] == "VOID - report changed since approval"


def test_approvals_never_unlock_executed_phrasing(report_file: Path) -> None:
    """The v3 invariant restated over the new data: approval records change
    nothing about how the report may speak."""
    from ai_incident_investigator.safety import EXECUTED_ACTION_PATTERNS

    append_approval(report_file, _record(report_file))
    text = render_markdown(
        _report(report_file),
        step_statuses=step_statuses(
            _report(report_file), load_approvals(report_file), report_hash(report_file), NOW
        ),
    )
    plan_section = text.split("## Remediation plans")[1].split("## Recovery")[0]
    for pattern in EXECUTED_ACTION_PATTERNS:
        assert not pattern.search(plan_section)


def test_cli_approve_list_and_error_paths(
    report_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _report(report_file)
    plan_id, step_index = _state_changing_ref(report)

    assert main(["approve", "--report", str(report_file), "--list"]) == 0
    assert "unapproved" in capsys.readouterr().out

    code = main(
        [
            "approve",
            "--report",
            str(report_file),
            "--plan",
            plan_id,
            "--step",
            str(step_index),
            "--approver",
            "lyudmyla",
            "--expires-in-hours",
            "4",
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert "approval recorded" in captured.err
    assert captured.out.strip().endswith(".approvals.json")

    assert main(["approve", "--report", str(report_file), "--list"]) == 0
    assert "approved by lyudmyla" in capsys.readouterr().out

    # read-only step refused
    read_only = next(
        (p.id, i)
        for p in report.remediation_plans
        for i, s in enumerate(p.steps)
        if s.kind == "read_only"
    )
    code = main(
        [
            "approve",
            "--report",
            str(report_file),
            "--plan",
            read_only[0],
            "--step",
            str(read_only[1]),
            "--approver",
            "x",
        ]
    )
    assert code == 1
    assert "read-only" in capsys.readouterr().err

    assert (
        main(
            [
                "approve",
                "--report",
                str(report_file),
                "--plan",
                "plan_ghost",
                "--step",
                "0",
                "--approver",
                "x",
            ]
        )
        == 1
    )
    assert "not in this report" in capsys.readouterr().err

    # editing the report file voids the recorded approval
    report_file.write_text(report_file.read_text() + "\n")
    assert main(["approve", "--report", str(report_file), "--list"]) == 0
    assert "VOID - report changed since approval" in capsys.readouterr().out


def test_quorum_counts_distinct_identities_once(report_file: Path) -> None:
    """Issue #65, the owner requirement: no single individual green-lights a
    production-tier action. The same claimed identity approving twice counts
    once; a second distinct identity completes the quorum."""
    report = _report(report_file)
    current = report_hash(report_file)
    plan_id, step_index = _state_changing_ref(report)

    once = _record(report_file)
    again = _record(report_file, approved_at=(NOW + timedelta(minutes=5)).isoformat())
    ok, reason = is_actionable(
        report, [once, again], current, plan_id, step_index, NOW, required_approvals=2
    )
    assert not ok
    assert "quorum not met: 1/2" in reason
    assert "needs 1 more distinct approver" in reason

    peer = _record(report_file, approver="peer")
    valid = distinct_valid_approvers(report, [once, again, peer], current, plan_id, step_index, NOW)
    assert list(valid) == ["lyudmyla", "peer"]
    ok, reason = is_actionable(
        report, [once, again, peer], current, plan_id, step_index, NOW, required_approvals=2
    )
    assert ok
    assert "approved by lyudmyla, peer (2/2 distinct approvals)" in reason


def test_quorum_ignores_expired_and_void_records(report_file: Path) -> None:
    report = _report(report_file)
    current = report_hash(report_file)
    plan_id, step_index = _state_changing_ref(report)

    valid = _record(report_file)
    expired_peer = _record(
        report_file, approver="peer", expires_at=(NOW - timedelta(minutes=1)).isoformat()
    )
    void_peer = _record(report_file, approver="other-peer", report_sha256="0" * 64)
    ok, reason = is_actionable(
        report,
        [valid, expired_peer, void_peer],
        current,
        plan_id,
        step_index,
        NOW,
        required_approvals=2,
    )
    assert not ok
    assert "quorum not met: 1/2" in reason


def test_quorum_invoker_exclusion_policy(report_file: Path) -> None:
    """Stricter separation of duties: the execute invoker's own approval
    does not count when policy says so."""
    report = _report(report_file)
    current = report_hash(report_file)
    plan_id, step_index = _state_changing_ref(report)
    records = [_record(report_file), _record(report_file, approver="peer")]

    ok, _ = is_actionable(
        report,
        records,
        current,
        plan_id,
        step_index,
        NOW,
        required_approvals=2,
        invoker="peer",
        invoker_counts_toward_quorum=True,
    )
    assert ok

    ok, reason = is_actionable(
        report,
        records,
        current,
        plan_id,
        step_index,
        NOW,
        required_approvals=2,
        invoker="peer",
        invoker_counts_toward_quorum=False,
    )
    assert not ok
    assert "quorum not met: 1/2" in reason
    assert "peer excluded from quorum as the invoker" in reason


def test_quorum_derives_from_the_tier_policy(report_file: Path) -> None:
    """The #64 contract and the gate compose: one approval satisfies the
    sandbox tier but never production (schema floor: 2)."""
    report = _report(report_file)
    current = report_hash(report_file)
    plan_id, step_index = _state_changing_ref(report)
    policy = ApprovalPolicy()
    records = [_record(report_file)]

    ok, _ = is_actionable(
        report,
        records,
        current,
        plan_id,
        step_index,
        NOW,
        required_approvals=policy.required_for(EnvironmentTier.SANDBOX),
    )
    assert ok
    ok, _ = is_actionable(
        report,
        records,
        current,
        plan_id,
        step_index,
        NOW,
        required_approvals=policy.required_for(EnvironmentTier.PRODUCTION),
    )
    assert not ok


def test_step_statuses_and_cli_show_quorum_progress(
    report_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _report(report_file)
    plan_id, step_index = _state_changing_ref(report)
    append_approval(report_file, _record(report_file))

    statuses = step_statuses(
        report, load_approvals(report_file), report_hash(report_file), NOW, required_approvals=2
    )
    assert "quorum not met: 1/2" in statuses[(plan_id, step_index)]

    assert (
        main(["approve", "--report", str(report_file), "--list", "--required-approvals", "2"]) == 0
    )
    assert "quorum not met: 1/2" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        main(["approve", "--report", str(report_file), "--list", "--required-approvals", "0"])

"""Post-execution verification (#68): pending -> terminal by APPENDING.

The original ExecutionRecord is never mutated; the mapping from the
recovery comparison is deterministic and pessimistic; only executions
bound to the current report content are verified; idempotent.
"""

import shutil
from pathlib import Path

import pytest

from ai_incident_investigator.approvals import load_approvals, report_hash
from ai_incident_investigator.cli import main
from ai_incident_investigator.compare import RecoveryComparison, build_comparison
from ai_incident_investigator.execute import (
    append_execution,
    append_verifications,
    load_executions_file,
    perform_execution,
    verification_from_comparison,
)
from ai_incident_investigator.flags import ReplayFlagClient
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.models.execution import FlagToggleRequest, load_executor_config
from test_execute import EXAMPLE_CONFIG, FLAG, GOLDEN, NOW, _approve, _report, _state_changing_ref

ROOT = Path(__file__).resolve().parents[1]
INCIDENT = ROOT / "examples" / "incidents" / "latency_spike"
FOLLOW_UP = ROOT / "examples" / "followups" / "latency_spike"
DEMO_FIXTURES = ROOT / "tests" / "fixtures" / "http" / "flag_toggle_demo"
CANONICAL = FlagToggleRequest(environment="staging", flag_key=FLAG, on=False)


@pytest.fixture()
def report_file(tmp_path: Path) -> Path:
    path = tmp_path / "report.json"
    shutil.copy(GOLDEN, path)
    return path


def _comparison() -> RecoveryComparison:
    return build_comparison(load_package(INCIDENT).package, load_package(FOLLOW_UP).package)


def _applied_execution(report_file: Path) -> None:
    _approve(report_file)
    report = _report(report_file)
    plan_id, step_index = _state_changing_ref(report)
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
        client=ReplayFlagClient(DEMO_FIXTURES),
    )
    assert record.outcome == "applied" and record.verification == "pending"
    append_execution(report_file, record)


def test_mapping_is_deterministic_and_pessimistic() -> None:
    inconclusive = _comparison()  # the committed follow-up: 4/5, one absent
    assert inconclusive.verdict == "inconclusive"
    outcome, detail = verification_from_comparison(inconclusive)
    assert outcome == "unverifiable" and "never assumed recovered" in detail

    recovered = inconclusive.model_copy(update={"verdict": "recovered"})
    assert verification_from_comparison(recovered)[0] == "verified"

    not_recovered = inconclusive.model_copy(update={"verdict": "not_recovered"})
    outcome, detail = verification_from_comparison(not_recovered)
    assert outcome == "aborted" and "abort semantics" in detail

    # a met re-alert aborts BEFORE the verdict is consulted
    re_alert = inconclusive.model_copy(update={"verdict": "recovered", "re_alert": "met"})
    outcome, detail = verification_from_comparison(re_alert)
    assert outcome == "aborted" and "re-alert condition met" in detail


def test_verification_appends_without_mutating_and_is_idempotent(report_file: Path) -> None:
    _applied_execution(report_file)
    original = load_executions_file(report_file).executions[0]

    fresh = append_verifications(report_file, report_hash(report_file), _comparison(), NOW)
    assert len(fresh) == 1
    assert fresh[0].outcome == "unverifiable"
    assert fresh[0].executed_at == original.executed_at
    assert fresh[0].action == original.action

    sidecar = load_executions_file(report_file)
    assert sidecar.executions[0] == original  # untouched, still 'pending'
    assert sidecar.executions[0].verification == "pending"
    assert len(sidecar.verifications) == 1

    assert append_verifications(report_file, report_hash(report_file), _comparison(), NOW) == []


def test_only_executions_bound_to_current_report_content_verify(report_file: Path) -> None:
    _applied_execution(report_file)
    report_file.write_text(report_file.read_text() + "\n")  # voids the binding
    assert append_verifications(report_file, report_hash(report_file), _comparison(), NOW) == []


def test_dry_runs_and_refusals_are_never_verified(report_file: Path) -> None:
    _approve(report_file)
    report = _report(report_file)
    plan_id, step_index = _state_changing_ref(report)
    dry = perform_execution(
        report,
        load_approvals(report_file),
        report_hash(report_file),
        load_executor_config(EXAMPLE_CONFIG),
        plan_id,
        step_index,
        CANONICAL,
        "lyudmyla",
        NOW,
        "dry_run",
    )
    append_execution(report_file, dry)
    assert append_verifications(report_file, report_hash(report_file), _comparison(), NOW) == []


def test_cli_verify_execution_end_to_end(
    report_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _applied_execution(report_file)
    args = [
        "compare",
        "--incident",
        str(INCIDENT),
        "--follow-up",
        str(FOLLOW_UP),
        "--verify-execution",
        str(report_file),
    ]
    assert main(args) == 0
    err = capsys.readouterr().err
    assert "verification: unverifiable" in err

    assert main(args) == 0
    assert "no applied-and-pending live executions" in capsys.readouterr().err

    missing = [*args[:-1], str(report_file.parent / "missing.json")]
    assert main(missing) == 1
    assert "report not found" in capsys.readouterr().err

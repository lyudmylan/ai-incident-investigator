"""v3 adversarial safety matrix (epic #33): hostile agent output, full runs.

Unit tests in test_planner.py / test_external_drafts.py cover the converter
and linter in isolation; these tests drive the WHOLE pipeline with hostile
scripted responses and assert the assembled report shows the catch - the
wiring, not just the parts.
"""

import json
import shutil
from pathlib import Path

from ai_incident_investigator.agents.reporter import JIRA_PRIORITY_BY_SEVERITY
from ai_incident_investigator.assemble import build_report
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.models.report import InvestigationReport
from ai_incident_investigator.pipeline import initial_state, run_investigation
from ai_incident_investigator.state import InvestigationState
from helpers import EXAMPLE_DIR, ScriptedLLM, ScriptEntry
from scripted_runs import script_for, scripted_planner

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = ROOT / "tests" / "golden"

BASE_STEPS = [
    {
        "kind": "state_changing",
        "action": "disable the payment_enrichment feature flag",
        "verification": "error rate returns toward baseline within 10 minutes",
    }
]


def _run(script: dict[str, ScriptEntry]) -> InvestigationState:
    return run_investigation(initial_state(load_package(EXAMPLE_DIR)), ScriptedLLM(script))


def _hostile_plan(**overrides: object) -> dict[str, object]:
    plan: dict[str, object] = {
        "kind": "mitigation",
        "title": "Consider disabling the payment_enrichment flag",
        "link_mitigation": True,
        "preconditions": [],
        "steps": BASE_STEPS,
        "abort_conditions": ["error rate rises further"],
        "owner_role": "on-call engineer",
    }
    plan.update(overrides)
    return plan


def test_executed_action_phrasing_in_a_plan_is_flagged_in_the_report() -> None:
    script = script_for("latency_spike")
    script["Role: remediation planner"] = scripted_planner(
        _hostile_plan(
            steps=[
                {
                    "kind": "state_changing",
                    "action": "we have already rolled back the release",
                    "verification": "deployed version reports the previous release",
                }
            ]
        )
    )
    report = build_report(_run(script))
    check = {c.check: c for c in report.safety_review.checks}["no_executed_action_phrasing"]
    assert check.result == "warning"
    assert "rolled back" in (check.detail or "")


def test_plan_without_abort_conditions_degrades_to_a_gap_not_a_crash() -> None:
    script = script_for("latency_spike")
    script["Role: remediation planner"] = scripted_planner(_hostile_plan(abort_conditions=[]))
    report = build_report(_run(script))
    assert report.remediation_plans == []
    assert any("no abort conditions" in m.description for m in report.missing_data)
    # the report is otherwise complete
    assert report.communication_drafts.internal_update
    assert report.recovery_verification is not None


def test_ghost_hypothesis_plan_is_stopped_at_the_first_defense_layer() -> None:
    """The converter drops it (layer 1), so the linter check (layer 2,
    unit-tested with model_construct in test_planner.py) stays green."""

    def ghost_planner(request: object) -> str:
        from ai_incident_investigator.agents.responses import PlanDraft, PlannerResponse

        draft = PlanDraft.model_validate(
            {
                "kind": "mitigation",
                "title": "Plan for a hypothesis that does not exist",
                "hypothesis_id": "hypothesis_0000000000",
                "mitigation_id": None,
                "preconditions": [],
                "steps": BASE_STEPS,
                "abort_conditions": ["x"],
                "owner_role": "on-call",
            }
        )
        return PlannerResponse(plans=[draft], gaps=[], reasoning="hostile").model_dump_json()

    script = script_for("latency_spike")
    script["Role: remediation planner"] = ghost_planner
    report = build_report(_run(script))
    assert report.remediation_plans == []
    assert any("unknown hypothesis" in m.description for m in report.missing_data)
    check = {c.check: c for c in report.safety_review.checks}["plans_reference_existing_items"]
    assert check.result == "pass"


def test_leaky_status_page_is_blocked_in_the_report() -> None:
    script = script_for("latency_spike")
    reporter_json = script["Role: reporter"]
    assert isinstance(reporter_json, str)
    payload = json.loads(reporter_json)
    payload["status_page"] = {
        "phase": "identified",
        "text": (
            "The root cause is likely a bad deploy of booking-service; "
            "we believe appointments-db is overloaded."
        ),
    }
    script["Role: reporter"] = json.dumps(payload)
    report = build_report(_run(script))
    check = {c.check: c for c in report.safety_review.checks}["status_page_customer_safe"]
    assert check.result == "blocked"
    detail = check.detail or ""
    assert "internal service name 'booking-service'" in detail
    assert "internal service name 'appointments-db'" in detail
    assert "speculation language" in detail
    assert "root-cause claim" in detail


def test_approval_and_reference_invariants_hold_across_all_goldens() -> None:
    goldens = sorted(GOLDEN_DIR.glob("*.json"))
    assert len(goldens) == 10  # 4 originals + the 6-scenario adversarial corpus
    for path in goldens:
        report = InvestigationReport.model_validate_json(path.read_text())
        hypothesis_ids = {h.id for h in report.hypotheses}
        mitigation_ids = {m.id for m in report.safe_mitigation_options}

        for option in report.safe_mitigation_options:
            assert option.requires_human_approval is True
        for plan in report.remediation_plans:
            assert plan.hypothesis_id in hypothesis_ids
            assert plan.mitigation_id is None or plan.mitigation_id in mitigation_ids
            assert plan.abort_conditions
            for step in plan.steps:
                if step.kind == "state_changing":
                    assert step.requires_human_approval is True
                    assert step.verification
        if report.communication_drafts.jira_ticket is not None:
            assert (
                report.communication_drafts.jira_ticket.priority_suggestion
                in JIRA_PRIORITY_BY_SEVERITY.values()
            )
        assert all(c.result != "blocked" for c in report.safety_review.checks), path.name


def test_planner_failure_still_yields_a_complete_report() -> None:
    script = script_for("latency_spike")
    script["Role: remediation planner"] = RuntimeError("planner exploded")
    state = _run(script)
    assert any(f.agent == "planner" for f in state.failures)
    report = build_report(state)
    assert report.remediation_plans == []
    assert report.recovery_verification is not None  # deterministic node unaffected
    assert report.communication_drafts.internal_update
    assert report.safety_review.checks


def test_reporter_failure_degrades_drafts_but_planner_and_report_survive() -> None:
    script = script_for("latency_spike")
    script["Role: reporter"] = RuntimeError("reporter exploded")
    state = _run(script)
    assert any(f.agent == "reporter" for f in state.failures)
    report = build_report(state)
    # fallback drafts, no mitigations to link - plans ground on the hypothesis
    assert report.communication_drafts.internal_update
    assert report.safe_mitigation_options == []
    for plan in report.remediation_plans:
        assert plan.mitigation_id is None
    assert all(c.result != "blocked" for c in report.safety_review.checks)


def test_recovery_builder_without_metrics_is_honest_end_to_end(tmp_path: Path) -> None:
    stripped = tmp_path / "no_metrics"
    shutil.copytree(EXAMPLE_DIR, stripped)
    (stripped / "metrics.json").unlink()

    state = run_investigation(
        initial_state(load_package(stripped)), ScriptedLLM(script_for("latency_spike"))
    )
    report = build_report(state)
    assert report.recovery_verification is None
    trace = {step.stage: step.summary for step in report.reasoning_trace}
    assert "no metrics" in trace["recovery_builder"]
    assert any("metrics.json" in m.description for m in report.missing_data)

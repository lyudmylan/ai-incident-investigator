"""Recommendation builder, reporter, report assembly, and markdown rendering."""

from ai_incident_investigator.agents.reporter import make_reporter
from ai_incident_investigator.assemble import build_report
from ai_incident_investigator.ids import stable_id
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.markdown import render_markdown
from ai_incident_investigator.models.common import Confidence, SeverityLevel, Source
from ai_incident_investigator.models.report import (
    ConfidenceRubric,
    Hypothesis,
    InvestigationReport,
    MissingData,
)
from ai_incident_investigator.pipeline import initial_state, run_investigation
from ai_incident_investigator.recommendations import build_next_steps
from ai_incident_investigator.state import InvestigationState, StateUpdate, apply_update
from helpers import EXAMPLE_DIR, ScriptedLLM, default_script, make_evidence

NO_DEPS = frozenset[str]()

EV_A = make_evidence(Source.METRICS, "latency deviated")
EV_B = make_evidence(Source.LOGS, "retries escalated")


def _hypothesis(hyp_id: str, checks: list[str]) -> Hypothesis:
    return Hypothesis(
        id=hyp_id,
        title=hyp_id,
        statement="consistent with cited evidence",
        confidence=Confidence.MEDIUM,
        rubric=ConfidenceRubric(
            aligned_signals=2, timing_alignment="aligned", conflicting_evidence_count=0
        ),
        supporting_evidence_ids=[EV_A.id, EV_B.id],
        recommended_checks=checks,
    )


def _base_state() -> InvestigationState:
    return initial_state(load_package(EXAMPLE_DIR))


def test_next_steps_merge_shared_checks_with_backrefs() -> None:
    state = apply_update(
        _base_state(),
        StateUpdate(
            evidence=[EV_A, EV_B],
            hypotheses=[
                _hypothesis("hypothesis_one", ["Compare error rates", "Inspect pool"]),
                _hypothesis("hypothesis_two", ["compare error rates"]),  # same, case-differs
            ],
        ),
    )
    steps = build_next_steps(state)
    by_description = {s.description: s for s in steps}
    merged = by_description["Compare error rates"]
    assert merged.source_hypothesis_ids == ["hypothesis_one", "hypothesis_two"]
    assert by_description["Inspect pool"].source_hypothesis_ids == ["hypothesis_one"]


def test_next_steps_from_missing_data_rules() -> None:
    state = apply_update(
        _base_state(),
        StateUpdate(
            missing_data=[
                MissingData(id="m1", description="traces.json not provided", impact="i"),
                MissingData(
                    id="m2", description="triage: retry bound behavior unknown", impact="i"
                ),
                MissingData(id="m3", description="agent 'triage' failed: boom", impact="i"),
                MissingData(
                    id="m4",
                    description="logs.txt ignored because logs.jsonl is present",
                    impact="i",
                ),
            ]
        ),
    )
    steps = build_next_steps(state)
    descriptions = [s.description for s in steps]
    assert any(d.startswith("Provide or repair") and "traces.json" in d for d in descriptions)
    assert any(d.startswith("Investigate open question") for d in descriptions)
    assert not any("failed" in d for d in descriptions)
    assert not any("ignored" in d for d in descriptions)
    followup = next(s for s in steps if "traces.json" in s.description)
    assert followup.source_missing_data_ids == ["m1"]


def test_next_step_ids_stable_across_runs() -> None:
    state = apply_update(
        _base_state(),
        StateUpdate(evidence=[EV_A, EV_B], hypotheses=[_hypothesis("h", ["Check X"])]),
    )
    assert [s.id for s in build_next_steps(state)] == [s.id for s in build_next_steps(state)]


def test_reporter_converts_response() -> None:
    state = apply_update(
        _base_state(),
        StateUpdate(evidence=[EV_A, EV_B], hypotheses=[_hypothesis("h1", ["Check X"])]),
    )
    update = make_reporter(ScriptedLLM(default_script()), NO_DEPS).run(state)

    assert len(update.safe_mitigation_options) == 1
    option = update.safe_mitigation_options[0]
    assert option.requires_human_approval is True
    assert option.id == stable_id("mitigation", option.action, option.rationale)
    assert update.communication_drafts is not None
    assert "human approval" in update.communication_drafts.internal_update
    assert update.postmortem_draft is not None
    assert update.postmortem_draft.contributing_factors == [
        "likely: deploy-driven retry amplification"
    ]


def test_reporter_stub_when_nothing_to_report() -> None:
    llm = ScriptedLLM({})  # would raise if called
    update = make_reporter(llm, NO_DEPS).run(_base_state())
    assert llm.requests == []
    assert update.communication_drafts is not None
    assert "placeholder" in update.communication_drafts.internal_update
    assert update.postmortem_draft is not None


def test_build_report_fallbacks_on_bare_state() -> None:
    report = build_report(_base_state())
    assert isinstance(report, InvestigationReport)
    assert report.severity.level == SeverityLevel.SEV4
    assert "floor, not a verdict" in report.severity.explanation
    assert report.summary.affected_services == ["booking-service"]
    assert report.safety_review.checks == []
    assert report.communication_drafts.internal_update.startswith("Internal update draft")


def test_full_pipeline_report_is_contract_complete_and_deterministic() -> None:
    def run() -> InvestigationReport:
        return build_report(run_investigation(_base_state(), ScriptedLLM(default_script())))

    report_one, report_two = run(), run()
    assert report_one == report_two  # stable ids + deterministic merge order
    assert report_one.safe_mitigation_options[0].requires_human_approval is True
    assert report_one.recommended_next_steps  # action_items source exists
    # the linter ran last and saw the mitigations
    assert any(
        c.check == "mitigations_require_human_approval"
        and c.detail == "1 mitigation option(s) checked"
        for c in report_one.safety_review.checks
    )


def test_markdown_rendering_contains_key_sections() -> None:
    report = build_report(run_investigation(_base_state(), ScriptedLLM(default_script())))
    text = render_markdown(report)
    assert text.startswith("# Incident investigation: latency_spike")
    assert "**Severity: SEV-2**" in text
    assert "## Safe mitigation options" in text
    assert "Human approval required" in text
    assert "## Reasoning trace" in text
    assert "## Postmortem draft" in text


def test_markdown_renders_degraded_report() -> None:
    text = render_markdown(build_report(_base_state()))
    assert "SEV-4" in text
    assert "## Ranked hypotheses\n\nNone produced." in text

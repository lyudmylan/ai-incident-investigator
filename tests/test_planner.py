from ai_incident_investigator.agents.planner import make_planner
from ai_incident_investigator.agents.responses import (
    PlanDraft,
    PlannerResponse,
    PlanStepDraft,
)
from ai_incident_investigator.llm import LLMRequest, LLMResponse
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.models.common import Confidence, Source
from ai_incident_investigator.models.report import (
    ConfidenceRubric,
    Hypothesis,
    MitigationOption,
    ReadOnlyStep,
    StateChangingStep,
)
from ai_incident_investigator.pipeline import initial_state, run_investigation
from ai_incident_investigator.state import InvestigationState, StateUpdate, apply_update
from helpers import EXAMPLE_DIR, ScriptedLLM, default_script, make_evidence

NO_DEPS = frozenset[str]()

EVIDENCE = make_evidence(Source.METRICS, "latency deviated")
HYPOTHESIS = Hypothesis(
    id="hypothesis_aaaaaaaaaa",
    title="Deploy-driven retries",
    statement="consistent with cited evidence",
    confidence=Confidence.MEDIUM,
    rubric=ConfidenceRubric(
        aligned_signals=2, timing_alignment="aligned", conflicting_evidence_count=0
    ),
    supporting_evidence_ids=[EVIDENCE.id],
)
MITIGATION = MitigationOption(
    id="mitigation_bbbbbbbbbb", action="Consider disabling the flag", rationale="runbook-safe"
)


def _state() -> InvestigationState:
    state = initial_state(load_package(EXAMPLE_DIR))
    return apply_update(
        state,
        StateUpdate(
            evidence=[EVIDENCE],
            hypotheses=[HYPOTHESIS],
            safe_mitigation_options=[MITIGATION],
        ),
    )


def _draft(**overrides: object) -> PlanDraft:
    payload: dict[str, object] = {
        "kind": "mitigation",
        "title": "Consider disabling the flag",
        "hypothesis_id": HYPOTHESIS.id,
        "mitigation_id": MITIGATION.id,
        "preconditions": [],
        "steps": [
            PlanStepDraft(
                kind="state_changing",
                action="disable the flag",
                verification="error rate returns toward baseline",
            )
        ],
        "abort_conditions": ["error rate rises further"],
        "owner_role": "on-call engineer",
    }
    payload.update(overrides)
    return PlanDraft.model_validate(payload)


def _planner_json(*drafts: PlanDraft) -> str:
    return PlannerResponse(
        plans=list(drafts), gaps=[], reasoning="structured options"
    ).model_dump_json()


class OneShotLLM:
    def __init__(self, body: str) -> None:
        self.body = body
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(text=self.body, model="m", stop_reason="end_turn")


def test_valid_draft_converts_with_stable_id_and_typed_steps() -> None:
    update = make_planner(OneShotLLM(_planner_json(_draft())), NO_DEPS).run(_state())
    assert len(update.remediation_plans) == 1
    plan = update.remediation_plans[0]
    assert plan.id.startswith("plan_")
    assert plan.mitigation_id == MITIGATION.id
    assert isinstance(plan.steps[0], StateChangingStep)
    assert plan.steps[0].requires_human_approval is True

    two = make_planner(OneShotLLM(_planner_json(_draft())), NO_DEPS).run(_state())
    assert two.remediation_plans[0].id == plan.id  # stable across runs


def test_unknown_hypothesis_drops_plan_with_gap() -> None:
    update = make_planner(
        OneShotLLM(_planner_json(_draft(hypothesis_id="hypothesis_ghost"))), NO_DEPS
    ).run(_state())
    assert update.remediation_plans == []
    assert any("unknown hypothesis" in m.description for m in update.missing_data)


def test_unknown_mitigation_strips_link_but_keeps_plan() -> None:
    update = make_planner(
        OneShotLLM(_planner_json(_draft(mitigation_id="mitigation_ghost"))), NO_DEPS
    ).run(_state())
    assert len(update.remediation_plans) == 1
    assert update.remediation_plans[0].mitigation_id is None
    assert any("unknown mitigation" in m.description for m in update.missing_data)


def test_state_changing_step_without_verification_drops_plan() -> None:
    bad = _draft(steps=[PlanStepDraft(kind="state_changing", action="flip it", verification=None)])
    update = make_planner(OneShotLLM(_planner_json(bad)), NO_DEPS).run(_state())
    assert update.remediation_plans == []
    assert any("without verification" in m.description for m in update.missing_data)


def test_empty_steps_or_aborts_drop_plan_before_contract_error() -> None:
    update = make_planner(
        OneShotLLM(_planner_json(_draft(steps=[]), _draft(abort_conditions=[]))), NO_DEPS
    ).run(_state())
    assert update.remediation_plans == []
    descriptions = " | ".join(m.description for m in update.missing_data)
    assert "no steps" in descriptions
    assert "no abort conditions" in descriptions


def test_read_only_steps_keep_optional_verification() -> None:
    draft = _draft(
        steps=[
            PlanStepDraft(kind="read_only", action="check state", verification=None),
            PlanStepDraft(kind="state_changing", action="change", verification="observable check"),
        ]
    )
    update = make_planner(OneShotLLM(_planner_json(draft)), NO_DEPS).run(_state())
    assert isinstance(update.remediation_plans[0].steps[0], ReadOnlyStep)


def test_short_circuits_without_hypotheses() -> None:
    llm = OneShotLLM(_planner_json())
    state = initial_state(load_package(EXAMPLE_DIR))  # no hypotheses
    update = make_planner(llm, NO_DEPS).run(state)
    assert llm.requests == []
    assert update.remediation_plans == []


def test_planner_input_carries_the_grounding_sections() -> None:
    llm = OneShotLLM(_planner_json())
    make_planner(llm, NO_DEPS).run(_state())
    request = llm.requests[0]
    content = request.messages[0].content
    assert "RANKED HYPOTHESES" in content
    assert "SAFE MITIGATION OPTIONS" in content
    assert "DEPLOYS AND CHANGES" in content
    assert "RUNBOOK (verbatim)" in content
    assert request.json_schema is not None


def test_full_graph_places_planner_before_linter() -> None:
    state = run_investigation(
        initial_state(load_package(EXAMPLE_DIR)), ScriptedLLM(default_script())
    )
    stages = [step.stage for step in state.reasoning_trace]
    assert stages.index("planner") > stages.index("reporter")
    assert stages.index("safety_linter") > stages.index("planner")
    review = state.safety_review
    assert review is not None
    check_names = {check.check for check in review.checks}
    assert "plans_reference_existing_items" in check_names
    assert "state_changing_steps_verified_and_approved" in check_names


def test_linter_blocks_dangling_and_unverified_plans() -> None:
    import pydantic

    from ai_incident_investigator.models.report import RemediationPlan
    from ai_incident_investigator.safety import lint_state

    dangling = RemediationPlan(
        id="plan_dangling",
        kind="mitigation",
        title="t",
        hypothesis_id="hypothesis_ghost",
        steps=[StateChangingStep(kind="state_changing", action="a", verification="v")],
        abort_conditions=["x"],
        owner_role="on-call",
    )
    # an unverified state-changing step is unconstructible via validation;
    # model_construct simulates a foreign/deserialized report
    bad_step = StateChangingStep.model_construct(
        kind="state_changing", action="a", verification="", requires_human_approval=True
    )
    unverified = RemediationPlan.model_construct(
        id="plan_unverified",
        kind="mitigation",
        title="t",
        hypothesis_id=HYPOTHESIS.id,
        mitigation_id=None,
        preconditions=[],
        steps=[bad_step],
        abort_conditions=["x"],
        owner_role="on-call",
    )
    assert isinstance(dangling, pydantic.BaseModel)
    state = apply_update(_state(), StateUpdate(remediation_plans=[dangling, unverified]))
    by_name = {check.check: check for check in lint_state(state)}
    assert by_name["plans_reference_existing_items"].result == "blocked"
    assert "plan_dangling" in (by_name["plans_reference_existing_items"].detail or "")
    assert by_name["state_changing_steps_verified_and_approved"].result == "blocked"
    assert "plan_unverified" in (by_name["state_changing_steps_verified_and_approved"].detail or "")


def test_scripted_latency_run_produces_mitigation_and_rollback_plans() -> None:
    from scripted_runs import script_for

    state = run_investigation(
        initial_state(load_package(EXAMPLE_DIR)), ScriptedLLM(script_for("latency_spike"))
    )
    kinds = sorted(plan.kind for plan in state.remediation_plans)
    assert kinds == ["mitigation", "rollback"]
    rollback = next(p for p in state.remediation_plans if p.kind == "rollback")
    assert "2026.06.01-1420" in rollback.title
    assert rollback.hypothesis_id in {h.id for h in state.hypotheses}
    mitigation = next(p for p in state.remediation_plans if p.kind == "mitigation")
    assert mitigation.mitigation_id in {m.id for m in state.safe_mitigation_options}

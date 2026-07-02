"""Remediation planner: reviewed hypotheses and mitigations in, guided
human-approved plans out - including the rollback checklist when a deploy-
or flag-correlated hypothesis exists.

The LLM structures; code validates (docs/assumptions.md, plan invariants):
- a plan citing a nonexistent hypothesis is dropped, with a gap
- a dangling mitigation link is stripped (the plan survives), with a gap
- a state-changing step without verification invalidates its plan
- empty steps or abort conditions invalidate the plan before the contract
  model would reject it, so degradation is a note rather than a crash
"""

from ai_incident_investigator.agents.base import complete_typed, gaps_to_missing_data
from ai_incident_investigator.agents.rendering import (
    render_assessment,
    render_deploys,
    render_evidence,
    render_hypotheses,
    render_mitigations,
    render_runbook,
    render_window,
)
from ai_incident_investigator.agents.responses import PlanDraft, PlannerResponse
from ai_incident_investigator.graph import FunctionAgent
from ai_incident_investigator.ids import stable_id
from ai_incident_investigator.llm import LLMClient
from ai_incident_investigator.models.report import (
    PlanStep,
    ReadOnlyStep,
    ReasoningStep,
    RemediationPlan,
    StateChangingStep,
)
from ai_incident_investigator.state import InvestigationState, StateUpdate

PLANNER_NAME = "planner"

PLANNER_PROMPT = """\
Role: remediation planner. You structure the reviewed mitigation options
into guided, human-approved plans - and produce a rollback checklist when
a deploy- or flag-correlated hypothesis exists.

Rules for every plan:
- cite the hypothesis it addresses by exact id from RANKED HYPOTHESES, and
  the mitigation option it structures by exact id when one exists
- only structure options that are well-grounded in the runbook or evidence;
  an empty plans list is correct when nothing qualifies
- steps are ordered; each is read_only (observe) or state_changing. Every
  state_changing step MUST carry verification: how a human confirms it
  worked before continuing. If you cannot state a verification, do not
  propose the step.
- preconditions: what must already be true before starting, from observed
  state or documented runbook constraints
- abort_conditions: observable signals (from the EVIDENCE) that mean stop
  and back out; at least one per plan
- owner_role: who realistically drives this (e.g. "on-call engineer")

Rollback checklist (kind=rollback), when a deploy/flag hypothesis exists:
- name the exact release version or flag from DEPLOYS AND CHANGES; never
  guess a version
- pre-checks first: does the release include data migrations or schema
  changes (ask, as a read_only step, if unknown); flag interactions
- the rollback itself is state_changing with verification; post-checks
  confirm the symptom signals recover

Plans are options for a human. Never present a plan or step as chosen,
scheduled, in progress, or done."""


def _planner_input(state: InvestigationState) -> str:
    sections = [
        render_window(state.window),
        render_assessment(state.summary, state.severity),
        render_hypotheses(state.hypotheses),
        render_mitigations(state.safe_mitigation_options),
        render_evidence(state.evidence),
    ]
    if state.package.deploys is not None:
        sections.append(render_deploys(state.package))
    if state.package.runbook:
        sections.append(render_runbook(state.package))
    return "\n\n".join(sections)


def _convert_draft(
    draft: PlanDraft,
    hypothesis_ids: set[str],
    mitigation_ids: set[str],
    gaps: list[str],
) -> RemediationPlan | None:
    if draft.hypothesis_id not in hypothesis_ids:
        gaps.append(
            f"plan '{draft.title}' cited unknown hypothesis '{draft.hypothesis_id}'; dropped"
        )
        return None
    mitigation_id = draft.mitigation_id
    if mitigation_id is not None and mitigation_id not in mitigation_ids:
        gaps.append(
            f"plan '{draft.title}' cited unknown mitigation '{mitigation_id}'; link stripped"
        )
        mitigation_id = None
    if not draft.steps:
        gaps.append(f"plan '{draft.title}' had no steps; dropped")
        return None
    if not draft.abort_conditions:
        gaps.append(f"plan '{draft.title}' had no abort conditions; dropped")
        return None

    steps: list[PlanStep] = []
    for step in draft.steps:
        if step.kind == "state_changing":
            if not step.verification:
                gaps.append(
                    f"plan '{draft.title}' has a state-changing step without "
                    f"verification ({step.action!r}); plan dropped"
                )
                return None
            steps.append(
                StateChangingStep(
                    kind="state_changing", action=step.action, verification=step.verification
                )
            )
        else:
            steps.append(
                ReadOnlyStep(kind="read_only", action=step.action, verification=step.verification)
            )

    return RemediationPlan(
        id=stable_id("plan", draft.kind, draft.title, draft.hypothesis_id),
        kind=draft.kind,
        title=draft.title,
        hypothesis_id=draft.hypothesis_id,
        mitigation_id=mitigation_id,
        preconditions=draft.preconditions,
        steps=steps,
        abort_conditions=draft.abort_conditions,
        owner_role=draft.owner_role,
    )


def make_planner(llm: LLMClient, depends_on: frozenset[str]) -> FunctionAgent:
    def run(state: InvestigationState) -> StateUpdate:
        if not state.hypotheses:
            return StateUpdate(
                reasoning_trace=[
                    ReasoningStep(
                        stage=PLANNER_NAME,
                        summary="skipped LLM call: no hypotheses to plan against",
                    )
                ]
            )

        parsed = complete_typed(
            llm, PLANNER_NAME, PLANNER_PROMPT, _planner_input(state), PlannerResponse
        )
        hypothesis_ids = {h.id for h in state.hypotheses}
        mitigation_ids = {m.id for m in state.safe_mitigation_options}
        gaps = list(parsed.gaps)
        plans: list[RemediationPlan] = []
        seen: set[str] = set()
        for draft in parsed.plans:
            plan = _convert_draft(draft, hypothesis_ids, mitigation_ids, gaps)
            if plan is not None and plan.id not in seen:
                seen.add(plan.id)
                plans.append(plan)

        state_changing = sum(
            1 for plan in plans for step in plan.steps if step.kind == "state_changing"
        )
        return StateUpdate(
            remediation_plans=plans,
            missing_data=gaps_to_missing_data(PLANNER_NAME, gaps),
            reasoning_trace=[
                ReasoningStep(
                    stage=PLANNER_NAME,
                    summary=(
                        f"{parsed.reasoning} ({len(plans)} plan(s), "
                        f"{state_changing} state-changing step(s), all requiring approval)"
                    ),
                    input_ids=[plan.id for plan in plans],
                )
            ],
        )

    return FunctionAgent(name=PLANNER_NAME, run=run, depends_on=depends_on)

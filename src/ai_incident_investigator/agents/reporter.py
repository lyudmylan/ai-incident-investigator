"""Reporter agent: mitigation options plus communication and postmortem drafts.

One schema-constrained call produces all three (they share the same inputs;
one call keeps cost down). Mitigations come back without any approval field:
the contract model hard-codes requires_human_approval=True and this module
sets it nowhere - it cannot be unset (Principle 5).

When there is nothing to report on (no severity, no hypotheses, no
evidence), deterministic stub drafts are produced without an LLM call so the
final report is always complete.
"""

from ai_incident_investigator.agents.base import complete_typed, gaps_to_missing_data
from ai_incident_investigator.agents.rendering import (
    render_assessment,
    render_evidence,
    render_hypotheses,
    render_missing_data,
    render_next_steps,
    render_safety_review_summary,
    render_window,
)
from ai_incident_investigator.agents.responses import ReporterResponse
from ai_incident_investigator.graph import FunctionAgent
from ai_incident_investigator.ids import stable_id
from ai_incident_investigator.llm import LLMClient
from ai_incident_investigator.models.report import (
    CommunicationDrafts,
    MitigationOption,
    PostmortemDraft,
    ReasoningStep,
)
from ai_incident_investigator.state import InvestigationState, StateUpdate

REPORTER_NAME = "reporter"

REPORTER_PROMPT = """\
Role: reporter. You produce three deliverables from the reviewed
investigation: safe mitigation OPTIONS, an internal update draft, and a
postmortem draft.

Mitigation options:
- propose only actions grounded in the evidence and hypotheses you were
  given (runbook-documented mitigations are the strongest candidates;
  standard reversible operations are acceptable when clearly relevant)
- each option needs a rationale tied to a hypothesis and an honest list of
  risks; note conditions ("only if X") in the action wording when the
  evidence says so
- these are options for a human to consider - never present one as chosen,
  scheduled, or done; no option is ever pre-approved
- an empty list is correct when nothing is well-grounded

Internal update (for the incident channel, written now, mid-incident):
- lead with status: severity, user impact, affected services
- current leading hypothesis WITH its confidence label and what conflicts
  with or limits it; never present a hypothesis as a confirmed cause
- what is being checked next
- state explicitly that no remediation has been executed and that
  mitigation options await human approval
- plain sentences a stressed on-call engineer can skim; no headings

Postmortem draft (blameless, honest about uncertainty):
- postmortem_summary: what happened and how it progressed, past tense
- postmortem_impact: who/what was affected, with the observed numbers
- contributing_factors: from hypotheses, each qualified by its confidence
  ("likely", "possibly" for medium/low); never assert an unproven cause
- open_questions: what the investigation could not establish
- action_items: follow-ups grounded in the recommended next steps you were
  given, plus durable improvements the evidence supports"""


def _reporter_input(state: InvestigationState) -> str:
    return "\n\n".join(
        [
            render_window(state.window),
            render_assessment(state.summary, state.severity),
            render_hypotheses(state.hypotheses),
            render_evidence(state.evidence),
            render_next_steps(state.recommended_next_steps),
            render_safety_review_summary(state.safety_review),
            render_missing_data(state.missing_data),
        ]
    )


def _stub_update(state: InvestigationState) -> StateUpdate:
    note = (
        "The automated investigation produced no findings to report "
        "(see missing_data for why). This draft is a placeholder."
    )
    return StateUpdate(
        communication_drafts=CommunicationDrafts(internal_update=note),
        postmortem_draft=PostmortemDraft(
            title=f"Postmortem draft: {state.package.alert.title}",
            summary=note,
            impact="Impact could not be established from the available data.",
            contributing_factors=[],
            open_questions=["The investigation pipeline produced no usable findings."],
            action_items=[],
        ),
        reasoning_trace=[
            ReasoningStep(
                stage=REPORTER_NAME,
                summary="skipped LLM call: no severity, hypotheses, or evidence to report on",
            )
        ],
    )


def make_reporter(llm: LLMClient, depends_on: frozenset[str]) -> FunctionAgent:
    def run(state: InvestigationState) -> StateUpdate:
        if state.severity is None and not state.hypotheses and not state.evidence:
            return _stub_update(state)

        parsed = complete_typed(
            llm, REPORTER_NAME, REPORTER_PROMPT, _reporter_input(state), ReporterResponse
        )
        mitigations_by_id = {
            stable_id("mitigation", draft.action, draft.rationale): MitigationOption(
                id=stable_id("mitigation", draft.action, draft.rationale),
                action=draft.action,
                rationale=draft.rationale,
                risks=draft.risks,
            )
            for draft in parsed.mitigation_options
        }
        mitigations = list(mitigations_by_id.values())
        return StateUpdate(
            safe_mitigation_options=mitigations,
            communication_drafts=CommunicationDrafts(internal_update=parsed.internal_update),
            postmortem_draft=PostmortemDraft(
                title=parsed.postmortem_title,
                summary=parsed.postmortem_summary,
                impact=parsed.postmortem_impact,
                contributing_factors=parsed.contributing_factors,
                open_questions=parsed.open_questions,
                action_items=parsed.action_items,
            ),
            missing_data=gaps_to_missing_data(REPORTER_NAME, list(parsed.gaps)),
            reasoning_trace=[
                ReasoningStep(
                    stage=REPORTER_NAME,
                    summary=(
                        f"{parsed.reasoning} ({len(mitigations)} mitigation option(s) proposed)"
                    ),
                    input_ids=[m.id for m in mitigations],
                )
            ],
        )

    return FunctionAgent(name=REPORTER_NAME, run=run, depends_on=depends_on)

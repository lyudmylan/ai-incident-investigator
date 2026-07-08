"""Hypothesis ranker: combines investigator evidence into ranked hypotheses.

Split of responsibilities (docs/assumptions.md, Confidence rubric):
- the LLM proposes hypotheses, cites evidence ids, and judges timing
- code validates every cited id, counts distinct sources and conflicts,
  and derives the confidence label from the documented table

A hypothesis whose citations don't survive validation is dropped and the
drop is recorded - no evidence, no hypothesis (Principle 2).
"""

from ai_incident_investigator.agents.base import complete_typed, gaps_to_missing_data
from ai_incident_investigator.agents.rendering import (
    render_assessment,
    render_evidence,
    render_missing_data,
    render_window,
)
from ai_incident_investigator.agents.responses import HypothesisDraft, RankerResponse
from ai_incident_investigator.graph import FunctionAgent
from ai_incident_investigator.ids import stable_id
from ai_incident_investigator.llm import LLMClient
from ai_incident_investigator.models.report import EvidenceItem, Hypothesis, ReasoningStep
from ai_incident_investigator.rubric import CONFIDENCE_ORDER, build_rubric, derive_confidence
from ai_incident_investigator.state import InvestigationState, StateUpdate

RANKER_NAME = "hypothesis_ranker"

RANKER_PROMPT = """\
Role: hypothesis ranker. You combine the collected evidence into ranked,
falsifiable hypotheses about what contributed to the incident.

Rules for hypotheses:
- order them most likely first
- every hypothesis must cite supporting evidence by exact id from the
  EVIDENCE section; cite conflicting evidence ids too - surfacing conflict
  is required, not optional (an unaffected control service often conflicts
  with broad-outage hypotheses)
- cite the STRONGEST supporting ids, at most 8 per hypothesis - every
  cited id must independently pull weight; omit merely-consistent items
  (a human reads each citation, and twenty of them bury the three that
  matter)
- ids must be copied VERBATIM from the EVIDENCE section: a cited id that
  is not in that list invalidates the whole hypothesis (code checks every
  citation) - a right idea with a wrong id is a dropped idea
- returning ZERO hypotheses is the correct answer when nothing falsifiable
  is grounded in the evidence; never force a hypothesis from thin signal -
  put what you would need to know into gaps instead
- statement wording: falsifiable and proportionate ("X increased load on Y,
  consistent with...") - never "root cause confirmed"
- timing_alignment: does the suspected cause precede symptom onset with a
  plausible gap? aligned / misaligned / unknown, with a one-line
  justification citing the relevant timestamps
- assumptions: what you are taking for granted that the data does not show
- recommended_checks: read-only verification steps a human could take next
  (inspect, compare, query); never state-changing actions
- prefer two or three well-evidenced hypotheses over many thin ones; include
  a genuine alternative if the evidence permits one

You do NOT assign confidence. The pipeline derives it from your citations:
distinct supporting sources, timing alignment, and conflict count. Cite
accordingly - citing three findings from one source counts as one signal."""


def _rank_key(hypothesis: Hypothesis) -> int:
    return CONFIDENCE_ORDER[hypothesis.confidence]


def _ranker_input(state: InvestigationState) -> str:
    return "\n\n".join(
        [
            render_window(state.window),
            render_assessment(state.summary, state.severity),
            render_evidence(state.evidence),
            render_missing_data(state.missing_data),
        ]
    )


def _convert_draft(
    draft: HypothesisDraft,
    by_id: dict[str, EvidenceItem],
    gaps: list[str],
) -> Hypothesis | None:
    def valid(ids: list[str], kind: str) -> list[str]:
        kept: list[str] = []
        for evidence_id in dict.fromkeys(ids):  # preserve order, drop repeats
            if evidence_id in by_id:
                kept.append(evidence_id)
            else:
                gaps.append(
                    f"hypothesis '{draft.title}' cited unknown {kind} evidence id "
                    f"'{evidence_id}'; citation dropped"
                )
        return kept

    supporting = valid(draft.supporting_evidence_ids, "supporting")
    conflicting = valid(draft.conflicting_evidence_ids, "conflicting")
    overlap = set(supporting) & set(conflicting)
    if overlap:
        gaps.append(
            f"hypothesis '{draft.title}' cited {sorted(overlap)} as both supporting "
            "and conflicting; kept as conflicting (conservative)"
        )
        supporting = [i for i in supporting if i not in overlap]
    if not supporting:
        gaps.append(
            f"hypothesis '{draft.title}' had no valid supporting evidence and was dropped "
            "(no evidence, no hypothesis)"
        )
        return None

    rubric = build_rubric([by_id[i] for i in supporting], len(conflicting), draft.timing_alignment)
    return Hypothesis(
        id=stable_id("hypothesis", draft.title, draft.statement),
        title=draft.title,
        statement=draft.statement,
        confidence=derive_confidence(rubric),
        rubric=rubric,
        supporting_evidence_ids=supporting,
        conflicting_evidence_ids=conflicting,
        assumptions=draft.assumptions,
        recommended_checks=draft.recommended_checks,
    )


def make_ranker(llm: LLMClient, depends_on: frozenset[str]) -> FunctionAgent:
    def run(state: InvestigationState) -> StateUpdate:
        if not state.evidence:
            return StateUpdate(
                missing_data=gaps_to_missing_data(
                    RANKER_NAME, ["no evidence was collected; no hypotheses can be ranked"]
                ),
                reasoning_trace=[
                    ReasoningStep(
                        stage=RANKER_NAME,
                        summary="skipped LLM call: no evidence available to rank",
                    )
                ],
            )

        parsed = complete_typed(
            llm, RANKER_NAME, RANKER_PROMPT, _ranker_input(state), RankerResponse
        )
        by_id = {item.id: item for item in state.evidence}
        gaps = list(parsed.gaps)
        hypotheses: list[Hypothesis] = []
        kept_drafts: list[HypothesisDraft] = []
        seen_ids: set[str] = set()
        for draft in parsed.hypotheses:
            hypothesis = _convert_draft(draft, by_id, gaps)
            if hypothesis is not None and hypothesis.id not in seen_ids:
                seen_ids.add(hypothesis.id)
                hypotheses.append(hypothesis)
                kept_drafts.append(draft)
        # Stable sort: derived-confidence tier first, LLM likelihood order within.
        hypotheses.sort(key=_rank_key)

        # Only surviving hypotheses: the trace must not cite dropped ones.
        timing_notes = "; ".join(
            f"'{draft.title}': {draft.timing_justification}" for draft in kept_drafts
        )
        summary = (
            parsed.reasoning if not timing_notes else f"{parsed.reasoning} | timing: {timing_notes}"
        )
        return StateUpdate(
            hypotheses=hypotheses,
            missing_data=gaps_to_missing_data(RANKER_NAME, gaps),
            reasoning_trace=[
                ReasoningStep(
                    stage=RANKER_NAME,
                    summary=summary,
                    input_ids=[h.id for h in hypotheses],
                )
            ],
        )

    return FunctionAgent(name=RANKER_NAME, run=run, depends_on=depends_on)

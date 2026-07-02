"""Deterministic safety linter: structural invariants the report must hold.

Runs after the LLM critic and merges its checks into the same safety_review.
Unlike the critic these checks are code, so they hold even when every LLM
agent failed (Safety Model: enforced, not hoped for).

- blocked: structural violations (evidence-less hypothesis, dangling
  citation, confidence label not matching the documented rubric, mitigation
  without human approval)
- warning: executed-action phrasing in recommendation-like text - the tool
  must never sound like it did something (kept at warning because observed
  facts can legitimately mention e.g. an operator's rollback)
"""

import re

from ai_incident_investigator.graph import FunctionAgent
from ai_incident_investigator.models.report import ReasoningStep, SafetyCheck, SafetyReview
from ai_incident_investigator.rubric import derive_confidence
from ai_incident_investigator.state import InvestigationState, StateUpdate

LINTER_NAME = "safety_linter"

_ACTION_VERBS = (
    r"(?:rolled\s+back|restarted|re-?deployed|deployed|disabled|enabled|scaled|"
    r"executed|applied|migrated|deleted|paged)"
)
EXECUTED_ACTION_PATTERNS = (
    re.compile(rf"\b(?:i|we)\s+(?:have\s+)?(?:already\s+)?{_ACTION_VERBS}\b", re.IGNORECASE),
    re.compile(rf"\bhas\s+been\s+{_ACTION_VERBS}\b", re.IGNORECASE),
    re.compile(rf"\b{_ACTION_VERBS}\s+successfully\b", re.IGNORECASE),
)


def _recommendation_texts(state: InvestigationState) -> list[tuple[str, str]]:
    """(where, text) pairs for fields that speak about actions or conclusions."""
    texts: list[tuple[str, str]] = []
    for hypothesis in state.hypotheses:
        texts.append((f"hypothesis {hypothesis.id} statement", hypothesis.statement))
        texts.extend(
            (f"hypothesis {hypothesis.id} recommended check", check)
            for check in hypothesis.recommended_checks
        )
    texts.extend(
        (f"next step {step.id}", step.description) for step in state.recommended_next_steps
    )
    texts.extend(
        (f"mitigation {option.id} action", option.action)
        for option in state.safe_mitigation_options
    )
    return texts


def lint_state(state: InvestigationState) -> list[SafetyCheck]:
    checks: list[SafetyCheck] = []
    evidence_ids = {item.id for item in state.evidence}

    dangling: list[str] = []
    evidence_less: list[str] = []
    mislabeled: list[str] = []
    for hypothesis in state.hypotheses:
        cited = [*hypothesis.supporting_evidence_ids, *hypothesis.conflicting_evidence_ids]
        dangling.extend(
            f"{hypothesis.id} -> {evidence_id}"
            for evidence_id in cited
            if evidence_id not in evidence_ids
        )
        if not hypothesis.supporting_evidence_ids:
            evidence_less.append(hypothesis.id)
        if derive_confidence(hypothesis.rubric) != hypothesis.confidence:
            mislabeled.append(hypothesis.id)

    checks.append(
        SafetyCheck(
            check="hypotheses_cite_resolvable_evidence",
            result="blocked" if dangling else "pass",
            detail=f"dangling citations: {', '.join(dangling)}" if dangling else None,
        )
    )
    checks.append(
        SafetyCheck(
            check="no_hypothesis_without_supporting_evidence",
            result="blocked" if evidence_less else "pass",
            detail=f"evidence-less: {', '.join(evidence_less)}" if evidence_less else None,
        )
    )
    checks.append(
        SafetyCheck(
            check="confidence_labels_match_documented_rubric",
            result="blocked" if mislabeled else "pass",
            detail=f"label != derived rubric for: {', '.join(mislabeled)}" if mislabeled else None,
        )
    )

    unapproved = [
        option.id
        for option in state.safe_mitigation_options
        if option.requires_human_approval is not True
    ]
    checks.append(
        SafetyCheck(
            check="mitigations_require_human_approval",
            result="blocked" if unapproved else "pass",
            detail=f"unapproved: {', '.join(unapproved)}"
            if unapproved
            else f"{len(state.safe_mitigation_options)} mitigation option(s) checked",
        )
    )

    phrased = [
        f"{where}: {text!r}"
        for where, text in _recommendation_texts(state)
        if any(pattern.search(text) for pattern in EXECUTED_ACTION_PATTERNS)
    ]
    checks.append(
        SafetyCheck(
            check="no_executed_action_phrasing",
            result="warning" if phrased else "pass",
            detail="; ".join(phrased) if phrased else None,
        )
    )
    return checks


def make_safety_linter(depends_on: frozenset[str]) -> FunctionAgent:
    def run(state: InvestigationState) -> StateUpdate:
        linter_checks = lint_state(state)
        if state.safety_review is not None:
            merged = SafetyReview(
                checks=[*state.safety_review.checks, *linter_checks],
                notes=state.safety_review.notes,
            )
        else:
            merged = SafetyReview(
                checks=linter_checks,
                notes="LLM critic unavailable; deterministic checks only.",
            )
        blocked = sum(1 for c in merged.checks if c.result == "blocked")
        warnings = sum(1 for c in merged.checks if c.result == "warning")
        return StateUpdate(
            safety_review=merged,
            reasoning_trace=[
                ReasoningStep(
                    stage=LINTER_NAME,
                    summary=(
                        f"deterministic lint: {len(linter_checks)} checks "
                        f"(total review: {blocked} blocked, {warnings} warning)"
                    ),
                )
            ],
        )

    return FunctionAgent(name=LINTER_NAME, run=run, depends_on=depends_on)

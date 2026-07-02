"""Deterministic recommendation builder.

Top-level next steps are an aggregation, not new reasoning (product.md,
Current Shape): every step traces back to the hypothesis checks or
missing-data entries it came from. Rules:

- each hypothesis's recommended checks become steps, in hypothesis rank
  order; identical checks recommended by several hypotheses merge into one
  step citing all of them
- missing-data entries become follow-up steps when they are actionable:
  package-file problems ("not provided", unreadable, invalid) become
  "provide or repair" steps; investigator-flagged open questions become
  "investigate" steps; agent-failure and informational entries do not
  become steps (retrying the tool is not an incident next step)
"""

import re

from ai_incident_investigator.graph import FunctionAgent
from ai_incident_investigator.ids import stable_id
from ai_incident_investigator.models.report import MissingData, NextStep, ReasoningStep
from ai_incident_investigator.state import InvestigationState, StateUpdate

BUILDER_NAME = "recommendation_builder"

_FILE_PROBLEM = re.compile(
    r"not provided|could not be read|failed validation|not valid JSON|unparseable line"
)
_NOT_ACTIONABLE = re.compile(r"' failed:|ignored because|no evidence was collected")


def _normalize(text: str) -> str:
    return " ".join(text.split()).rstrip(".").lower()


def _step_for_missing(item: MissingData) -> str | None:
    if _NOT_ACTIONABLE.search(item.description):
        return None
    if _FILE_PROBLEM.search(item.description):
        return f"Provide or repair the package data: {item.description}"
    return f"Investigate open question: {item.description}"


def build_next_steps(state: InvestigationState) -> list[NextStep]:
    steps: dict[str, NextStep] = {}  # normalized description -> step (insertion-ordered)

    for hypothesis in state.hypotheses:
        for check in hypothesis.recommended_checks:
            key = _normalize(check)
            existing = steps.get(key)
            if existing is None:
                steps[key] = NextStep(
                    id=stable_id("next_step", key),
                    description=check,
                    source_hypothesis_ids=[hypothesis.id],
                )
            elif hypothesis.id not in existing.source_hypothesis_ids:
                existing.source_hypothesis_ids.append(hypothesis.id)

    for item in state.missing_data:
        description = _step_for_missing(item)
        if description is None:
            continue
        key = _normalize(description)
        existing = steps.get(key)
        if existing is None:
            steps[key] = NextStep(
                id=stable_id("next_step", key),
                description=description,
                source_missing_data_ids=[item.id],
            )
        elif item.id not in existing.source_missing_data_ids:
            existing.source_missing_data_ids.append(item.id)

    return list(steps.values())


def make_recommendation_builder(depends_on: frozenset[str]) -> FunctionAgent:
    def run(state: InvestigationState) -> StateUpdate:
        steps = build_next_steps(state)
        from_hypotheses = sum(1 for s in steps if s.source_hypothesis_ids)
        return StateUpdate(
            recommended_next_steps=steps,
            reasoning_trace=[
                ReasoningStep(
                    stage=BUILDER_NAME,
                    summary=(
                        f"aggregated {len(steps)} next steps deterministically "
                        f"({from_hypotheses} from hypothesis checks, "
                        f"{len(steps) - from_hypotheses} from missing data)"
                    ),
                    input_ids=[s.id for s in steps],
                )
            ],
        )

    return FunctionAgent(name=BUILDER_NAME, run=run, depends_on=depends_on)

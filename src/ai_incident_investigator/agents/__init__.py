"""Investigator agents (epic #5). Ranking/critic/comms arrive in epics #6-#7."""

from ai_incident_investigator.agents.base import InvestigatorSpec, make_investigator
from ai_incident_investigator.agents.investigators import SOURCE_SPECS
from ai_incident_investigator.agents.triage import make_triage
from ai_incident_investigator.graph import FunctionAgent
from ai_incident_investigator.llm import LLMClient
from ai_incident_investigator.state import InvestigationState

__all__ = ["InvestigatorSpec", "build_investigators", "make_investigator", "make_triage"]


def build_investigators(
    llm: LLMClient, state: InvestigationState
) -> tuple[list[FunctionAgent], list[tuple[str, str]]]:
    """All investigators applicable to this package, plus (name, reason) skips."""
    agents: list[FunctionAgent] = [make_triage(llm)]
    skipped: list[tuple[str, str]] = []
    for spec in SOURCE_SPECS:
        if spec.is_available(state):
            agents.append(make_investigator(spec, llm))
        else:
            skipped.append((spec.name, spec.unavailable_reason))
    return agents, skipped

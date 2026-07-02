"""Triage agent: severity assessment and incident summary.

Unlike the source investigators it does not emit evidence; it sets the
`severity` and `summary` scalars from a cross-source overview, applying the
documented severity rules verbatim (docs/assumptions.md).
"""

from ai_incident_investigator.agents.base import complete_typed, gaps_to_missing_data
from ai_incident_investigator.agents.rendering import (
    render_alert,
    render_metrics,
    render_timeline,
    render_topology,
    render_window,
)
from ai_incident_investigator.agents.responses import TriageResponse
from ai_incident_investigator.graph import FunctionAgent
from ai_incident_investigator.llm import LLMClient
from ai_incident_investigator.models.common import Confidence, SeverityLevel
from ai_incident_investigator.models.report import ReasoningStep, SeverityAssessment, Summary
from ai_incident_investigator.state import InvestigationState, StateUpdate

TRIAGE_PROMPT = """\
Role: triage. You assess severity, affected services, blast radius, and
customer impact from the package overview.

Severity rules (docs/assumptions.md - apply these, do not invent your own):
- SEV-1: critical user-facing flow broken for most users, no workaround
  (e.g. error rate > 25% on a critical flow, or hard outage)
- SEV-2: significant degradation of an important flow (e.g. error rate
  1-25% or latency > 4x baseline on a critical flow)
- SEV-3: limited degradation, workaround exists, small share of users
  affected
- SEV-4: warning-level signal, no established user impact yet
Also per the rules: assess from observed impact, not the monitoring alert's
own severity label (that label is evidence, not a verdict); patient- or
safety-impacting flows bias one level up when in doubt; high confidence
requires multiple aligned signals.

Output guidance:
- what_happened: one or two sentences, symptoms only, no cause claims
- affected_services: only services whose data shows degradation; use the
  topology to judge blast radius but do not list a service as affected on
  topology alone - note suspected-but-unconfirmed ones in gaps
- customer_impact: what a user of the product experiences, stated plainly
- severity_explanation: cite the specific numbers that place it at the
  chosen level under the rules above"""


def _triage_input(state: InvestigationState) -> str:
    sections = [
        render_window(state.window),
        render_alert(state.package),
        render_timeline(state.timeline),
        render_topology(state.package),
    ]
    if state.package.metrics is not None:
        sections.append(render_metrics(state.package))
    return "\n\n".join(sections)


def make_triage(llm: LLMClient) -> FunctionAgent:
    def run(state: InvestigationState) -> StateUpdate:
        parsed = complete_typed(llm, "triage", TRIAGE_PROMPT, _triage_input(state), TriageResponse)
        return StateUpdate(
            severity=SeverityAssessment(
                level=SeverityLevel(parsed.severity_level),
                explanation=parsed.severity_explanation,
                confidence=Confidence(parsed.severity_confidence),
            ),
            summary=Summary(
                what_happened=parsed.what_happened,
                affected_services=parsed.affected_services,
                customer_impact=parsed.customer_impact,
                incident_window=state.window,
            ),
            missing_data=gaps_to_missing_data("triage", list(parsed.gaps)),
            reasoning_trace=[ReasoningStep(stage="triage", summary=parsed.reasoning)],
        )

    return FunctionAgent(name="triage", run=run)

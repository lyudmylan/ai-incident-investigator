"""Assemble the final InvestigationReport from the investigation state.

The output contract requires every top-level field, but the pipeline
degrades: any scalar an agent failed to produce gets an explicit,
honest fallback here (marked as unavailable, never invented). Fallbacks are
deterministic so degraded reports stay diffable.
"""

from ai_incident_investigator.models.common import Confidence, SeverityLevel
from ai_incident_investigator.models.report import (
    CommunicationDrafts,
    InvestigationReport,
    PostmortemDraft,
    SafetyReview,
    SeverityAssessment,
    Summary,
)
from ai_incident_investigator.state import InvestigationState

_UNAVAILABLE = "unavailable: the responsible pipeline stage failed (see missing_data)"


def _fallback_summary(state: InvestigationState) -> Summary:
    return Summary(
        what_happened=f"Triage {_UNAVAILABLE}. Alert: {state.package.alert.title}",
        affected_services=[state.package.alert.service],
        customer_impact=f"Impact assessment {_UNAVAILABLE}",
        incident_window=state.window,
    )


def _fallback_severity() -> SeverityAssessment:
    return SeverityAssessment(
        level=SeverityLevel.SEV4,
        explanation=(
            f"Severity assessment {_UNAVAILABLE}; SEV-4 is a floor, not a verdict - "
            "treat the actual severity as unknown and assess manually."
        ),
        confidence=Confidence.LOW,
    )


def _fallback_drafts() -> CommunicationDrafts:
    return CommunicationDrafts(internal_update=f"Internal update draft {_UNAVAILABLE}")


def _fallback_postmortem(state: InvestigationState) -> PostmortemDraft:
    return PostmortemDraft(
        title=f"Postmortem draft: {state.package.alert.title}",
        summary=f"Postmortem draft {_UNAVAILABLE}",
        impact=f"Impact section {_UNAVAILABLE}",
        contributing_factors=[],
        open_questions=[],
        action_items=[],
    )


def build_report(state: InvestigationState) -> InvestigationReport:
    return InvestigationReport(
        incident_id=state.package.incident_id,
        summary=state.summary or _fallback_summary(state),
        severity=state.severity or _fallback_severity(),
        timeline=state.timeline,
        evidence=state.evidence,
        hypotheses=state.hypotheses,
        missing_data=state.missing_data,
        recommended_next_steps=state.recommended_next_steps,
        safe_mitigation_options=state.safe_mitigation_options,
        safety_review=state.safety_review
        or SafetyReview(checks=[], notes=f"Safety review {_UNAVAILABLE}"),
        communication_drafts=state.communication_drafts or _fallback_drafts(),
        postmortem_draft=state.postmortem_draft or _fallback_postmortem(state),
        reasoning_trace=state.reasoning_trace,
    )

"""Investigation report output contract.

The JSON-first output of an investigation run. Field-level rules:

- every hypothesis cites evidence by id (Principle 2: evidence-backed reasoning)
- confidence carries its rubric inputs so the label is auditable (Principle 3)
- mitigation options cannot exist without `requires_human_approval: true`
  (Principle 5: human-in-the-loop by design) — this is enforced by the schema
- top-level next steps reference the hypotheses / missing-data entries they
  come from, so the report stays internally consistent
"""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ai_incident_investigator.models.common import (
    CheckResult,
    Confidence,
    SeverityLevel,
    Source,
)


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IncidentWindow(ReportModel):
    start: AwareDatetime
    end: AwareDatetime | None = Field(default=None, description="None while ongoing")
    rule: str = Field(description="The documented rule that determined this window")


class Summary(ReportModel):
    what_happened: str
    affected_services: list[str]
    customer_impact: str
    incident_window: IncidentWindow


class SeverityAssessment(ReportModel):
    level: SeverityLevel
    explanation: str = Field(description="Why this level, per docs/assumptions.md rules")
    confidence: Confidence


class TimelineEntry(ReportModel):
    id: str
    timestamp: AwareDatetime
    source: Source
    service: str | None = None
    description: str


class EvidenceItem(ReportModel):
    id: str
    source: Source
    interpretation: str
    timestamp: AwareDatetime | None = None
    service: str | None = None
    signal: str | None = None
    value: float | str | None = None


class ConfidenceRubric(ReportModel):
    """The auditable inputs behind a confidence label (docs/assumptions.md)."""

    aligned_signals: int = Field(ge=0, description="Independent sources pointing the same way")
    timing_alignment: Literal["aligned", "misaligned", "unknown"]
    conflicting_evidence_count: int = Field(ge=0)


class Hypothesis(ReportModel):
    id: str
    title: str
    statement: str = Field(description="The full falsifiable claim")
    confidence: Confidence
    rubric: ConfidenceRubric
    supporting_evidence_ids: list[str]
    conflicting_evidence_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    recommended_checks: list[str] = Field(default_factory=list)


class MissingData(ReportModel):
    id: str
    description: str
    impact: str = Field(description="What this gap prevents the investigation from concluding")


class NextStep(ReportModel):
    id: str
    description: str
    source_hypothesis_ids: list[str] = Field(default_factory=list)
    source_missing_data_ids: list[str] = Field(default_factory=list)


class MitigationOption(ReportModel):
    id: str
    action: str
    rationale: str
    risks: list[str] = Field(default_factory=list)
    requires_human_approval: Literal[True] = Field(
        default=True, description="Schema-enforced: a mitigation can never be pre-approved"
    )


class SafetyCheck(ReportModel):
    check: str
    result: CheckResult
    detail: str | None = None


class SafetyReview(ReportModel):
    checks: list[SafetyCheck]
    notes: str | None = None


class CommunicationDrafts(ReportModel):
    internal_update: str


class PostmortemDraft(ReportModel):
    title: str
    summary: str
    impact: str
    contributing_factors: list[str]
    open_questions: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)


class ReasoningStep(ReportModel):
    """One entry of the reasoning trace: why a stage concluded what it did."""

    stage: str = Field(description="Pipeline stage or agent name")
    summary: str = Field(description="What was concluded and why")
    input_ids: list[str] = Field(
        default_factory=list, description="Evidence/timeline/hypothesis ids this step used"
    )


class InvestigationReport(ReportModel):
    incident_id: str
    summary: Summary
    severity: SeverityAssessment
    timeline: list[TimelineEntry]
    evidence: list[EvidenceItem]
    hypotheses: list[Hypothesis]
    missing_data: list[MissingData]
    recommended_next_steps: list[NextStep]
    safe_mitigation_options: list[MitigationOption]
    safety_review: SafetyReview
    communication_drafts: CommunicationDrafts
    postmortem_draft: PostmortemDraft
    reasoning_trace: list[ReasoningStep]

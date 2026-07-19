"""Investigation report output contract.

The JSON-first output of an investigation run. Field-level rules:

- every hypothesis cites evidence by id (Principle 2: evidence-backed reasoning)
- confidence carries its rubric inputs so the label is auditable (Principle 3)
- mitigation options cannot exist without `requires_human_approval: true`
  (Principle 5: human-in-the-loop by design) — this is enforced by the schema
- top-level next steps reference the hypotheses / missing-data entries they
  come from, so the report stays internally consistent
"""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ai_incident_investigator.models.common import (
    CheckResult,
    Confidence,
    SeverityLevel,
    Source,
)
from ai_incident_investigator.models.history import PatternMatch


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
    precedent: str | None = Field(
        default=None,
        description="deterministic annotation from prior incidents (v7): set only "
        "when a matched incident executed a fix this option names; wording rule - "
        "only a verified outcome reads as precedent, anything else is a caution "
        "(docs/assumptions.md, 'Pattern matching rule')",
    )


class ReadOnlyStep(ReportModel):
    """A plan step that observes without changing anything."""

    kind: Literal["read_only"]
    action: str
    verification: str | None = Field(
        default=None, description="what confirms this check told you what you needed"
    )


class StateChangingStep(ReportModel):
    """A plan step that changes system state - never pre-approved, always verified."""

    kind: Literal["state_changing"]
    action: str
    verification: str = Field(
        description="required: how a human confirms this step worked before continuing"
    )
    requires_human_approval: Literal[True] = Field(
        default=True, description="schema-enforced: a state change can never be pre-approved"
    )


PlanStep = Annotated[ReadOnlyStep | StateChangingStep, Field(discriminator="kind")]


class RemediationPlan(ReportModel):
    """A guided, human-approved plan (docs/assumptions.md, plan invariants)."""

    id: str
    kind: Literal["mitigation", "rollback"]
    title: str
    hypothesis_id: str = Field(description="the hypothesis this plan addresses; must exist")
    mitigation_id: str | None = Field(
        default=None, description="the mitigation option this plan structures, when one exists"
    )
    preconditions: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(min_length=1)
    abort_conditions: list[str] = Field(
        min_length=1, description="mandatory: when to stop and back out"
    )
    owner_role: str = Field(description="who should drive this, e.g. 'on-call engineer'")


class WatchedSignal(ReportModel):
    service: str
    signal: str
    baseline: float
    recovered_when: str = Field(description="the documented recovery rule, spelled out")
    watch_minutes: int


class RecoveryVerificationPlan(ReportModel):
    """What to watch to call the incident recovered (docs/assumptions.md rules)."""

    mode: Literal["watch_for_recovery", "confirm_sustained_recovery"]
    signals: list[WatchedSignal]
    log_patterns_should_stop: list[str] = Field(default_factory=list)
    re_alert_condition: str | None = None


class JiraTicketDraft(ReportModel):
    summary: str
    description: str
    priority_suggestion: str = Field(description="mapped from severity per docs/assumptions.md")
    labels: list[str] = Field(default_factory=list)


class SlackUpdateDraft(ReportModel):
    text: str


class StatusPageDraft(ReportModel):
    """Customer-facing: held to the customer-safe wording rules (lintable)."""

    phase: Literal["investigating", "identified", "monitoring"]
    text: str


class SafetyCheck(ReportModel):
    check: str
    result: CheckResult
    detail: str | None = None


class SafetyReview(ReportModel):
    checks: list[SafetyCheck]
    notes: str | None = None


class CommunicationDrafts(ReportModel):
    internal_update: str
    jira_ticket: JiraTicketDraft | None = None
    slack_update: SlackUpdateDraft | None = None
    status_page: StatusPageDraft | None = None


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
    remediation_plans: list[RemediationPlan]
    recovery_verification: RecoveryVerificationPlan | None = Field(
        default=None, description="None when no metrics were available to derive it from"
    )
    safety_review: SafetyReview
    communication_drafts: CommunicationDrafts
    postmortem_draft: PostmortemDraft
    reasoning_trace: list[ReasoningStep]
    prior_incidents: list[PatternMatch] = Field(
        default_factory=list,
        description="deterministic pattern matches against a local history of past "
        "investigations (v7 pilot). Additive context ONLY: severity, hypotheses, "
        "confidence, and rankings are byte-identical with and without history - "
        "a match asserts behavioral resemblance, never a shared root cause",
    )

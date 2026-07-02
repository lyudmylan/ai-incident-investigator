"""Shared investigation state flowing through the agent graph.

The state is frozen: agents read it and return a StateUpdate; only the graph
runner merges updates (between levels, in deterministic agent-name order).
List fields merge additively; scalar fields are last-write-wins.

Note: pydantic's frozen is shallow — it prevents field reassignment, not
in-place mutation of list contents. Agents must treat the state as strictly
read-only; all merge paths here build new lists and never mutate in place.
"""

import typing

from pydantic import BaseModel, ConfigDict, Field

from ai_incident_investigator.ids import stable_id
from ai_incident_investigator.models.package import IncidentPackage
from ai_incident_investigator.models.report import (
    CommunicationDrafts,
    EvidenceItem,
    Hypothesis,
    IncidentWindow,
    MissingData,
    MitigationOption,
    NextStep,
    PostmortemDraft,
    ReasoningStep,
    RecoveryVerificationPlan,
    RemediationPlan,
    SafetyReview,
    SeverityAssessment,
    Summary,
    TimelineEntry,
)


class AgentFailure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent: str
    error: str


class InvestigationState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Deterministic facts (epic #3), set once before the graph runs.
    package: IncidentPackage
    window: IncidentWindow
    timeline: list[TimelineEntry]

    # Accumulated by agents.
    missing_data: list[MissingData] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    recommended_next_steps: list[NextStep] = Field(default_factory=list)
    safe_mitigation_options: list[MitigationOption] = Field(default_factory=list)
    remediation_plans: list[RemediationPlan] = Field(default_factory=list)
    reasoning_trace: list[ReasoningStep] = Field(default_factory=list)
    failures: list[AgentFailure] = Field(default_factory=list)

    summary: Summary | None = None
    severity: SeverityAssessment | None = None
    recovery_verification: RecoveryVerificationPlan | None = None
    safety_review: SafetyReview | None = None
    communication_drafts: CommunicationDrafts | None = None
    postmortem_draft: PostmortemDraft | None = None


class StateUpdate(BaseModel):
    """An agent's contribution. Lists extend the state; scalars overwrite when set."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    missing_data: list[MissingData] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    recommended_next_steps: list[NextStep] = Field(default_factory=list)
    safe_mitigation_options: list[MitigationOption] = Field(default_factory=list)
    remediation_plans: list[RemediationPlan] = Field(default_factory=list)
    reasoning_trace: list[ReasoningStep] = Field(default_factory=list)

    summary: Summary | None = None
    severity: SeverityAssessment | None = None
    recovery_verification: RecoveryVerificationPlan | None = None
    safety_review: SafetyReview | None = None
    communication_drafts: CommunicationDrafts | None = None
    postmortem_draft: PostmortemDraft | None = None


def _split_fields() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Derive merge behavior from StateUpdate itself so it cannot drift."""
    lists: list[str] = []
    scalars: list[str] = []
    for name, info in StateUpdate.model_fields.items():
        if typing.get_origin(info.annotation) is list:
            lists.append(name)
        else:
            scalars.append(name)
    return tuple(lists), tuple(scalars)


_LIST_FIELDS, _SCALAR_FIELDS = _split_fields()


def apply_update(state: InvestigationState, update: StateUpdate) -> InvestigationState:
    changes: dict[str, object] = {}
    for field in _LIST_FIELDS:
        added = getattr(update, field)
        if added:
            changes[field] = [*getattr(state, field), *added]
    for field in _SCALAR_FIELDS:
        value = getattr(update, field)
        if value is not None:
            changes[field] = value
    if not changes:
        return state
    return state.model_copy(update=changes)


def record_failure(state: InvestigationState, agent: str, error: str) -> InvestigationState:
    """Degrade, never crash: a failed agent becomes missing data plus trace."""
    update = StateUpdate(
        missing_data=[
            MissingData(
                id=stable_id("missing", "agent", agent, error),
                description=f"agent '{agent}' failed: {error}",
                impact="its findings are absent; the report is partial",
            )
        ],
        reasoning_trace=[ReasoningStep(stage=agent, summary=f"failed and was skipped: {error}")],
    )
    state = apply_update(state, update)
    return state.model_copy(
        update={"failures": [*state.failures, AgentFailure(agent=agent, error=error)]}
    )

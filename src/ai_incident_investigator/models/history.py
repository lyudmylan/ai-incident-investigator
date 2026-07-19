"""Learning contracts for the v7 pattern pilot (epic #86, issue #87).

Contracts only: what one past investigation IS to the pattern layer (the
fingerprint), what the history store holds (an entry), and what a match
asserts (a match record). Derivation and matching logic live in
patterns.py; decisions and rationale in docs/learning_design.md; the
normative matching rule in docs/assumptions.md ("Pattern matching rule");
the generated schema in docs/learning_contract.md.

Honesty properties carried by the schema itself:

- `PatternMatch.score` must equal the sum of its matched features' weights
  (model-validated) - the score is auditable from the record alone, the
  same way a confidence label carries its rubric inputs.
- A match record carries `unmatched` differences alongside `matched`
  features: a match can never present similarity without also presenting
  how the incidents differ (Principle 3: do not hide uncertainty).
- `ExecutedFix.outcome` can only be "applied" or "failed" - a previewed or
  refused execution is not representable as a fix that was tried, so
  precedent can never be built from a dry-run.
- Every field is derived from the tool's own artifacts (report +
  execution/verification sidecars). Nothing here reads a network or an
  LLM, and nothing here can change a conclusion: severity, hypotheses,
  and confidence types are not even importable from this module.
"""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_incident_investigator.models.common import SeverityLevel
from ai_incident_investigator.models.execution import FlagToggleRequest, VerificationOutcome

SignalDirection = Literal["elevated", "depressed", "unknown"]
"""How an abnormal signal deviated from its baseline, when the report's own
recovery-verification baselines make that derivable; "unknown" otherwise -
never guessed from wording."""

MatchFeature = Literal["signal", "direction", "service", "severity", "deploy_correlated"]

FEATURE_WEIGHTS: dict[MatchFeature, int] = {
    "signal": 2,
    "direction": 1,
    "service": 1,
    "severity": 1,
    "deploy_correlated": 1,
}
"""Score contribution per matched feature (docs/assumptions.md, "Pattern
matching rule"). A shared (service, signal) pair is the required gate and
weighs most; everything else is additive color."""


class HistoryModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SignalObservation(HistoryModel):
    """One (service, signal) pair the report cites as evidence."""

    service: str
    signal: str
    direction: SignalDirection = "unknown"


class ExecutedFix(HistoryModel):
    """A live execution that was actually attempted on this incident,
    with how its verification ended. The consumer's wording rule
    (docs/assumptions.md): only `verification == "verified"` may be
    presented as precedent; every other outcome is a caution."""

    action: FlagToggleRequest
    outcome: Literal["applied", "failed"]
    verification: VerificationOutcome
    executed_at: AwareDatetime


class IncidentFingerprint(HistoryModel):
    """The comparable, structured features of one investigation - a pure
    function of the report file (plus optional executions sidecar), never
    of wall-clock time or free text."""

    incident_id: str
    report_sha256: str = Field(
        min_length=64, max_length=64, description="hash of the exact report fingerprinted"
    )
    window_start: AwareDatetime = Field(description="the report's incident window start")
    services: list[str] = Field(
        description="sorted union of affected services and abnormal-signal services"
    )
    severity: SeverityLevel
    abnormal_signals: list[SignalObservation] = Field(
        description="sorted, deduplicated (service, signal) pairs cited as evidence"
    )
    deploy_correlated: bool = Field(
        description="whether the top-ranked hypothesis cites deploys-sourced evidence"
    )
    executed_fixes: list[ExecutedFix] = Field(default_factory=list)

    @model_validator(mode="after")
    def _signal_services_are_listed(self) -> "IncidentFingerprint":
        missing = {o.service for o in self.abnormal_signals} - set(self.services)
        if missing:
            raise ValueError(
                f"abnormal-signal services {sorted(missing)} are not in services - "
                "a fingerprint edited out of internal consistency would distort "
                "service scoring"
            )
        return self


class HistoryEntry(HistoryModel):
    """One past investigation as the store keeps it."""

    entry_id: str = Field(description="<incident_id>-<report sha256 first 16>; content-addressed")
    fingerprint: IncidentFingerprint


class MatchedFeature(HistoryModel):
    """One shared feature, carrying its own score weight so the total is
    auditable from the record."""

    feature: MatchFeature
    detail: str
    weight: int = Field(ge=1)


class PatternMatch(HistoryModel):
    """The assertion "this new incident resembles that past one", with the
    exact shared features, the exact differences, and the fixes that were
    actually tried there. It asserts resemblance of observed behavior -
    never "same root cause"."""

    entry_id: str
    incident_id: str
    window_start: AwareDatetime
    re_investigation: bool = Field(
        description="true when the matched entry is an earlier investigation "
        "of this same incident_id - labeled, never passed off as independent precedent"
    )
    score: int = Field(ge=1)
    matched: list[MatchedFeature] = Field(min_length=1)
    unmatched: list[str] = Field(
        default_factory=list,
        description="how the incidents differ; empty only when nothing differs",
    )
    executed_fixes: list[ExecutedFix] = Field(default_factory=list)
    explanation: str

    @model_validator(mode="after")
    def _score_is_sum_of_weights(self) -> "PatternMatch":
        total = sum(feature.weight for feature in self.matched)
        if self.score != total:
            raise ValueError(
                f"score {self.score} must equal the sum of matched feature "
                f"weights ({total}) - the score is auditable by contract"
            )
        return self


def entry_id_for(fingerprint: IncidentFingerprint) -> str:
    return f"{fingerprint.incident_id}-{fingerprint.report_sha256[:16]}"

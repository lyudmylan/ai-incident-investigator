"""Deterministic confidence rubric (docs/assumptions.md).

The LLM ranker never chooses a confidence label. It cites evidence and judges
timing; this module counts what was cited and derives the label from the
documented table. Overclaiming is structurally impossible.
"""

from typing import Literal

from ai_incident_investigator.models.common import Confidence
from ai_incident_investigator.models.report import ConfidenceRubric, EvidenceItem

TimingAlignment = Literal["aligned", "misaligned", "unknown"]

CONFIDENCE_ORDER: dict[Confidence, int] = {
    Confidence.HIGH: 0,
    Confidence.MEDIUM: 1,
    Confidence.LOW: 2,
}
"""Ranking order for confidence tiers; lives here with the rest of the
confidence semantics so adding a tier means touching one module."""


def build_rubric(
    supporting: list[EvidenceItem],
    conflicting_count: int,
    timing_alignment: TimingAlignment,
) -> ConfidenceRubric:
    """aligned_signals counts distinct evidence *sources*, not items:
    three metrics findings are one signal; metrics + logs + traces are three."""
    return ConfidenceRubric(
        aligned_signals=len({item.source for item in supporting}),
        timing_alignment=timing_alignment,
        conflicting_evidence_count=conflicting_count,
    )


def derive_confidence(rubric: ConfidenceRubric) -> Confidence:
    """The documented table, verbatim (docs/assumptions.md, Confidence rubric)."""
    if (
        rubric.aligned_signals >= 3
        and rubric.timing_alignment == "aligned"
        and rubric.conflicting_evidence_count == 0
    ):
        return Confidence.HIGH
    if (
        rubric.aligned_signals >= 2
        and rubric.timing_alignment != "misaligned"
        and rubric.conflicting_evidence_count <= 1
    ):
        return Confidence.MEDIUM
    return Confidence.LOW

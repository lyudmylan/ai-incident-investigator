"""The documented confidence table (docs/assumptions.md), case by case."""

import pytest

from ai_incident_investigator.models.common import Confidence, Source
from ai_incident_investigator.models.report import ConfidenceRubric
from ai_incident_investigator.rubric import TimingAlignment, build_rubric, derive_confidence
from helpers import make_evidence


def test_aligned_signals_counts_distinct_sources_not_items() -> None:
    supporting = [
        make_evidence(Source.METRICS, "one"),
        make_evidence(Source.METRICS, "two"),
        make_evidence(Source.LOGS, "three"),
    ]
    rubric = build_rubric(supporting, conflicting_count=0, timing_alignment="aligned")
    assert rubric.aligned_signals == 2  # metrics counted once


@pytest.mark.parametrize(
    ("signals", "timing", "conflicts", "expected"),
    [
        (3, "aligned", 0, Confidence.HIGH),
        (4, "aligned", 0, Confidence.HIGH),
        (3, "aligned", 1, Confidence.MEDIUM),  # one conflict forfeits high, not medium
        (3, "unknown", 0, Confidence.MEDIUM),  # timing unproven forfeits high
        (2, "aligned", 0, Confidence.MEDIUM),
        (2, "unknown", 1, Confidence.MEDIUM),
        (2, "misaligned", 0, Confidence.LOW),
        (1, "aligned", 0, Confidence.LOW),
        (3, "aligned", 2, Confidence.LOW),
        (0, "unknown", 0, Confidence.LOW),
    ],
)
def test_documented_confidence_table(
    signals: int, timing: TimingAlignment, conflicts: int, expected: Confidence
) -> None:
    rubric = ConfidenceRubric(
        aligned_signals=signals,
        timing_alignment=timing,
        conflicting_evidence_count=conflicts,
    )
    assert derive_confidence(rubric) == expected

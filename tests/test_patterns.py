"""Fingerprint derivation and matching (issue #87): the documented rule in
docs/assumptions.md ("Pattern matching rule"), feature by feature, plus the
schema honesty floors of the match record."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_incident_investigator.approvals import report_hash
from ai_incident_investigator.models.common import Confidence, SeverityLevel, Source
from ai_incident_investigator.models.execution import (
    ExecutionRecord,
    ExecutionsFile,
    FlagToggleRequest,
    VerificationRecord,
)
from ai_incident_investigator.models.history import (
    ExecutedFix,
    HistoryEntry,
    IncidentFingerprint,
    MatchedFeature,
    PatternMatch,
    SignalDirection,
    SignalObservation,
    entry_id_for,
)
from ai_incident_investigator.models.report import (
    CommunicationDrafts,
    ConfidenceRubric,
    EvidenceItem,
    Hypothesis,
    IncidentWindow,
    InvestigationReport,
    PostmortemDraft,
    RecoveryVerificationPlan,
    SafetyReview,
    SeverityAssessment,
    Summary,
    WatchedSignal,
)
from ai_incident_investigator.patterns import (
    fingerprint_report,
    latest_verification,
    match_fingerprints,
)

GOLDEN = Path(__file__).resolve().parents[1] / "tests" / "golden" / "latency_spike.json"
WINDOW_START = datetime(2026, 6, 1, 14, 5, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def evidence(
    eid: str,
    source: Source,
    service: str | None = None,
    signal: str | None = None,
    value: float | str | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        id=eid,
        source=source,
        interpretation="observed",
        service=service,
        signal=signal,
        value=value,
    )


def hypothesis(hid: str, supporting: list[str]) -> Hypothesis:
    return Hypothesis(
        id=hid,
        title="a cause",
        statement="a falsifiable claim",
        confidence=Confidence.MEDIUM,
        rubric=ConfidenceRubric(
            aligned_signals=1, timing_alignment="aligned", conflicting_evidence_count=0
        ),
        supporting_evidence_ids=supporting,
    )


def make_report(
    incident_id: str = "incident_a",
    services: list[str] | None = None,
    severity: SeverityLevel = SeverityLevel.SEV2,
    items: list[EvidenceItem] | None = None,
    hypotheses: list[Hypothesis] | None = None,
    watched: list[WatchedSignal] | None = None,
) -> InvestigationReport:
    recovery = (
        RecoveryVerificationPlan(mode="watch_for_recovery", signals=watched)
        if watched is not None
        else None
    )
    return InvestigationReport(
        incident_id=incident_id,
        summary=Summary(
            what_happened="latency rose",
            affected_services=services if services is not None else ["booking-service"],
            customer_impact="slow bookings",
            incident_window=IncidentWindow(start=WINDOW_START, rule="alert minus lookback"),
        ),
        severity=SeverityAssessment(
            level=severity, explanation="rules", confidence=Confidence.HIGH
        ),
        timeline=[],
        evidence=items or [],
        hypotheses=hypotheses or [],
        missing_data=[],
        recommended_next_steps=[],
        safe_mitigation_options=[],
        remediation_plans=[],
        recovery_verification=recovery,
        safety_review=SafetyReview(checks=[]),
        communication_drafts=CommunicationDrafts(internal_update="status"),
        postmortem_draft=PostmortemDraft(
            title="t", summary="s", impact="i", contributing_factors=[]
        ),
        reasoning_trace=[],
    )


def watched_signal(service: str, signal: str, baseline: float) -> WatchedSignal:
    return WatchedSignal(
        service=service,
        signal=signal,
        baseline=baseline,
        recovered_when="within 10% of baseline for 3 points",
        watch_minutes=30,
    )


def toggle(flag: str = "checkout_enrichment", on: bool = False) -> FlagToggleRequest:
    return FlagToggleRequest(environment="staging", flag_key=flag, on=on)


def execution(
    outcome: str,
    mode: str = "live",
    flag: str = "checkout_enrichment",
    executed_at: datetime | None = None,
    verification: str = "pending",
) -> ExecutionRecord:
    return ExecutionRecord.model_validate(
        {
            "executed_by": "lyudmyla",
            "executed_at": (executed_at or datetime(2026, 6, 1, 15, 0, tzinfo=UTC)).isoformat(),
            "mode": mode,
            "action": toggle(flag).model_dump(),
            "plan_id": "plan_1",
            "step_index": 0,
            "report_sha256": SHA_A,
            "required_approvals": 1,
            "approvals_satisfied": ["lyudmyla"],
            "outcome": outcome,
            "verification": verification,
        }
    )


def verification_record(
    outcome: str, verified_at: datetime, executed_at: datetime | None = None
) -> VerificationRecord:
    return VerificationRecord.model_validate(
        {
            "verified_at": verified_at.isoformat(),
            "plan_id": "plan_1",
            "step_index": 0,
            "executed_at": (executed_at or datetime(2026, 6, 1, 15, 0, tzinfo=UTC)).isoformat(),
            "action": toggle().model_dump(),
            "follow_up_incident_id": "incident_a_followup",
            "outcome": outcome,
            "detail": "comparison verdict",
        }
    )


def fingerprint(
    incident_id: str = "incident_a",
    sha: str = SHA_A,
    window_start: datetime = WINDOW_START,
    services: tuple[str, ...] = ("booking-service",),
    severity: SeverityLevel = SeverityLevel.SEV2,
    signals: tuple[tuple[str, str, SignalDirection], ...] = (
        ("booking-service", "p95_latency_ms", "elevated"),
    ),
    deploy_correlated: bool = False,
    fixes: ExecutionsFile | None = None,
) -> IncidentFingerprint:
    return IncidentFingerprint(
        incident_id=incident_id,
        report_sha256=sha,
        window_start=window_start,
        services=sorted(services),
        severity=severity,
        abnormal_signals=[
            SignalObservation(service=s, signal=g, direction=d) for s, g, d in sorted(signals)
        ],
        deploy_correlated=deploy_correlated,
        executed_fixes=[]
        if fixes is None
        else fingerprint_report(make_report(), SHA_A, fixes).executed_fixes,
    )


def entry(fp: IncidentFingerprint) -> HistoryEntry:
    return HistoryEntry(entry_id=entry_id_for(fp), fingerprint=fp)


# --- fingerprint derivation ---


def test_fingerprint_collects_cited_pairs_and_service_union() -> None:
    report = make_report(
        services=["booking-service"],
        items=[
            evidence("e1", Source.METRICS, "booking-service", "p95_latency_ms", 3200.0),
            evidence("e2", Source.ALERT, "booking-service", "p95_latency_ms"),
            evidence("e3", Source.METRICS, "payment-service", "error_rate_pct", 4.8),
            evidence("e4", Source.LOGS, "booking-service"),  # no signal: not a pair
        ],
        watched=[watched_signal("booking-service", "p95_latency_ms", 450.0)],
    )
    fp = fingerprint_report(report, SHA_A)
    assert fp.services == ["booking-service", "payment-service"]  # union, sorted
    assert [(s.service, s.signal, s.direction) for s in fp.abnormal_signals] == [
        ("booking-service", "p95_latency_ms", "elevated"),
        ("payment-service", "error_rate_pct", "unknown"),  # no baseline for the pair
    ]
    assert fp.window_start == WINDOW_START
    assert fp.report_sha256 == SHA_A


def test_direction_is_median_vs_baseline_and_numeric_metrics_only() -> None:
    def direction(values: list[EvidenceItem], baseline: float) -> str:
        report = make_report(
            items=values, watched=[watched_signal("booking-service", "p95_latency_ms", baseline)]
        )
        return fingerprint_report(report, SHA_A).abnormal_signals[0].direction

    triple = [
        evidence("e1", Source.METRICS, "booking-service", "p95_latency_ms", 3200.0),
        evidence("e2", Source.METRICS, "booking-service", "p95_latency_ms", 400.0),
        evidence("e3", Source.METRICS, "booking-service", "p95_latency_ms", 500.0),
    ]
    assert direction(triple, 450.0) == "elevated"  # median 500
    assert direction(triple, 600.0) == "depressed"
    assert direction(triple[2:], 500.0) == "unknown"  # equal median is not a direction
    # a string value and a non-metrics numeric value carry no direction
    assert (
        direction(
            [evidence("e1", Source.METRICS, "booking-service", "p95_latency_ms", "high")], 1.0
        )
        == "unknown"
    )
    assert (
        direction([evidence("e1", Source.ALERT, "booking-service", "p95_latency_ms", 3200.0)], 1.0)
        == "unknown"
    )


def test_deploy_correlation_reads_the_top_hypothesis_only() -> None:
    items = [
        evidence("dep", Source.DEPLOYS, "booking-service"),
        evidence("met", Source.METRICS, "booking-service", "p95_latency_ms", 3200.0),
    ]
    top_cites = make_report(items=items, hypotheses=[hypothesis("h1", ["dep", "met"])])
    second_cites = make_report(
        items=items, hypotheses=[hypothesis("h1", ["met"]), hypothesis("h2", ["dep"])]
    )
    assert fingerprint_report(top_cites, SHA_A).deploy_correlated is True
    assert fingerprint_report(second_cites, SHA_A).deploy_correlated is False
    assert fingerprint_report(make_report(items=items), SHA_A).deploy_correlated is False


def test_executed_fixes_keep_live_attempts_with_latest_verification() -> None:
    executed_at = datetime(2026, 6, 1, 15, 0, tzinfo=UTC)
    executions = ExecutionsFile(
        executions=[
            execution("previewed", mode="dry_run", verification="not_applicable"),
            execution("refused", verification="not_applicable"),
            execution("applied", executed_at=executed_at),
            execution("failed", flag="another_flag"),
        ],
        verifications=[
            verification_record("unverifiable", datetime(2026, 6, 1, 15, 30, tzinfo=UTC)),
            verification_record("verified", datetime(2026, 6, 1, 16, 0, tzinfo=UTC)),
        ],
    )
    fp = fingerprint_report(make_report(), SHA_A, executions)
    # another_flag shares (plan, step, executed_at) with the verified toggle
    # but is a DIFFERENT action: the verification must not vouch for it.
    assert [(f.action.flag_key, f.outcome, f.verification) for f in fp.executed_fixes] == [
        ("another_flag", "failed", "pending"),
        ("checkout_enrichment", "applied", "verified"),  # latest verification won
    ]
    assert latest_verification(executions.executions[2], executions) == "verified"


def test_fingerprint_of_the_committed_golden_report() -> None:
    report = InvestigationReport.model_validate_json(GOLDEN.read_text())
    sha = report_hash(GOLDEN)
    fp = fingerprint_report(report, sha)
    assert fp.incident_id == "latency_spike"
    assert fp.report_sha256 == sha
    assert "booking-service" in fp.services
    assert fp.abnormal_signals, "the golden cites metric evidence"
    assert fp.executed_fixes == []


# --- matching ---


def test_gate_no_shared_pair_means_no_match() -> None:
    probe = fingerprint(deploy_correlated=True)
    other = fingerprint(
        incident_id="incident_b",
        sha=SHA_B,
        services=("booking-service",),  # shared service, severity, deploy...
        signals=(("booking-service", "error_rate_pct", "elevated"),),  # ...but no shared pair
        deploy_correlated=True,
    )
    assert match_fingerprints(probe, [entry(other)]) == []


def test_score_is_the_documented_arithmetic_and_auditable() -> None:
    probe = fingerprint(
        services=("booking-service", "payment-service"),
        signals=(
            ("booking-service", "p95_latency_ms", "elevated"),
            ("booking-service", "error_rate_pct", "unknown"),
        ),
        deploy_correlated=True,
    )
    other = fingerprint(
        incident_id="incident_b",
        sha=SHA_B,
        services=("booking-service", "payment-service"),
        signals=(
            ("booking-service", "p95_latency_ms", "elevated"),
            ("booking-service", "error_rate_pct", "unknown"),
        ),
        deploy_correlated=True,
    )
    (match,) = match_fingerprints(probe, [entry(other)])
    # pairs 2x(+2), direction agreement only on the non-unknown pair (+1),
    # payment-service uncovered by a pair (+1), severity (+1), deploys (+1)
    assert match.score == 8
    assert match.score == sum(f.weight for f in match.matched)
    kinds = sorted(f.feature for f in match.matched)
    assert kinds == ["deploy_correlated", "direction", "service", "severity", "signal", "signal"]
    assert match.unmatched == []
    assert match.re_investigation is False


def test_differences_are_reported_next_to_the_match() -> None:
    probe = fingerprint(
        signals=(
            ("booking-service", "p95_latency_ms", "elevated"),
            ("booking-service", "queue_depth", "elevated"),
        ),
        deploy_correlated=True,
    )
    other = fingerprint(
        incident_id="incident_b",
        sha=SHA_B,
        severity=SeverityLevel.SEV3,
        signals=(
            ("booking-service", "p95_latency_ms", "depressed"),
            ("booking-service", "cpu_pct", "elevated"),
        ),
    )
    (match,) = match_fingerprints(probe, [entry(other)])
    assert match.unmatched == [
        "booking-service/p95_latency_ms direction differs (elevated here, depressed there)",
        "severity differs (SEV-2 here, SEV-3 there)",
        "deploy correlation differs (top hypothesis cites a deploy only here)",
        "not seen there: booking-service/queue_depth",
        "not seen here: booking-service/cpu_pct",
    ]


def test_own_report_never_matches_and_reinvestigation_is_labeled() -> None:
    probe = fingerprint()
    itself = entry(fingerprint())  # identical sha
    earlier = entry(fingerprint(sha=SHA_B, window_start=datetime(2026, 5, 1, tzinfo=UTC)))
    assert match_fingerprints(probe, [itself]) == []
    (match,) = match_fingerprints(probe, [itself, earlier])
    assert match.re_investigation is True
    assert match.explanation.startswith("earlier investigation of this same incident")


def test_ranking_score_then_recency_then_entry_id_capped() -> None:
    probe = fingerprint(deploy_correlated=True)
    strong = fingerprint(incident_id="strong", sha=SHA_B, deploy_correlated=True)
    old_weak = fingerprint(
        incident_id="a_old",
        sha=SHA_C,
        severity=SeverityLevel.SEV3,
        window_start=datetime(2026, 4, 1, tzinfo=UTC),
    )
    new_weak = fingerprint(
        incident_id="b_new",
        sha="d" * 64,
        severity=SeverityLevel.SEV3,
        window_start=datetime(2026, 5, 1, tzinfo=UTC),
    )
    tied_weak = fingerprint(
        incident_id="a_tied",
        sha="e" * 64,
        severity=SeverityLevel.SEV3,
        window_start=datetime(2026, 5, 1, tzinfo=UTC),
    )
    entries = [entry(f) for f in (old_weak, new_weak, strong, tied_weak)]
    matches = match_fingerprints(probe, entries)
    assert len(matches) == 3  # capped
    assert [m.incident_id for m in matches] == ["strong", "a_tied", "b_new"]
    assert [m.incident_id for m in match_fingerprints(probe, entries, top_n=4)] == [
        "strong",
        "a_tied",
        "b_new",
        "a_old",
    ]


def test_unknown_direction_neither_scores_nor_conflicts() -> None:
    probe = fingerprint(signals=(("booking-service", "p95_latency_ms", "unknown"),))
    other = fingerprint(
        incident_id="incident_b",
        sha=SHA_B,
        signals=(("booking-service", "p95_latency_ms", "elevated"),),
    )
    (match,) = match_fingerprints(probe, [entry(other)])
    assert [f.feature for f in match.matched] == ["signal", "severity"]
    assert not any("direction" in note for note in match.unmatched)


def test_duplicate_entries_cannot_crowd_the_cap() -> None:
    probe = fingerprint()
    prior = fingerprint(incident_id="prior", sha=SHA_B)
    copies = [entry(prior), entry(prior), entry(prior), entry(prior)]
    matches = match_fingerprints(probe, copies)
    assert len(matches) == 1


def test_fingerprint_must_be_internally_consistent() -> None:
    with pytest.raises(ValidationError, match="not in services"):
        IncidentFingerprint(
            incident_id="incident_a",
            report_sha256=SHA_A,
            window_start=WINDOW_START,
            services=["booking-service"],
            severity=SeverityLevel.SEV2,
            abnormal_signals=[SignalObservation(service="ghost-service", signal="p95_latency_ms")],
            deploy_correlated=False,
        )


def test_fix_wording_verified_vs_tried_and_did_not_verify() -> None:
    executed_at = datetime(2026, 6, 1, 15, 0, tzinfo=UTC)
    verified = ExecutionsFile(
        executions=[execution("applied", executed_at=executed_at)],
        verifications=[verification_record("verified", datetime(2026, 6, 1, 16, 0, tzinfo=UTC))],
    )
    unverified = ExecutionsFile(executions=[execution("applied", executed_at=executed_at)])
    probe = fingerprint()

    with_verified = entry(fingerprint(incident_id="prior", sha=SHA_B, fixes=verified))
    (match,) = match_fingerprints(probe, [with_verified])
    assert "a verified fix is on record there" in match.explanation
    assert [f.verification for f in match.executed_fixes] == ["verified"]

    with_unverified = entry(fingerprint(incident_id="prior", sha=SHA_B, fixes=unverified))
    (match,) = match_fingerprints(probe, [with_unverified])
    assert "a fix was tried there but did NOT verify" in match.explanation

    bare = entry(fingerprint(incident_id="prior", sha=SHA_B))
    (match,) = match_fingerprints(probe, [bare])
    assert "fix" not in match.explanation


# --- contract honesty floors ---


def test_score_must_equal_the_sum_of_matched_weights() -> None:
    feature = MatchedFeature(feature="signal", detail="x/y abnormal in both", weight=2)
    with pytest.raises(ValidationError, match="auditable"):
        PatternMatch(
            entry_id="incident_b-" + SHA_B[:16],
            incident_id="incident_b",
            window_start=WINDOW_START,
            re_investigation=False,
            score=5,
            matched=[feature],
            unmatched=[],
            executed_fixes=[],
            explanation="resembles incident_b",
        )


def test_a_dry_run_is_not_representable_as_a_tried_fix() -> None:
    with pytest.raises(ValidationError):
        ExecutedFix.model_validate(
            {
                "action": toggle().model_dump(),
                "outcome": "previewed",
                "verification": "not_applicable",
                "executed_at": "2026-06-01T15:00:00+00:00",
            }
        )


def test_entry_id_is_content_addressed() -> None:
    fp = fingerprint()
    assert entry_id_for(fp) == f"incident_a-{SHA_A[:16]}"

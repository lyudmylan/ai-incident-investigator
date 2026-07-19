"""Fingerprint derivation and explainable matching (epic #86, issue #87).

Pure functions over the tool's own contracts: a report (plus optional
executions sidecar) in, a fingerprint out; a probe fingerprint against
stored entries in, ranked match records out. No I/O, no network, no LLM,
no wall clock - the same inputs always produce the same bytes, which is
what makes precedent auditable.

The normative matching rule lives in docs/assumptions.md ("Pattern
matching rule"); this module implements it and nothing beyond it:

- the gate: no shared abnormal (service, signal) pair, no match - shared
  severity or deploy-correlation alone can never manufacture precedent
- the probe's own report (same sha256) is never a match
- every score is the sum of the matched features' documented weights, and
  every difference the rule inspects lands in `unmatched` when it differs
"""

import statistics

from ai_incident_investigator.models.common import Source
from ai_incident_investigator.models.execution import (
    ExecutionRecord,
    ExecutionsFile,
    VerificationOutcome,
)
from ai_incident_investigator.models.history import (
    FEATURE_WEIGHTS,
    ExecutedFix,
    HistoryEntry,
    IncidentFingerprint,
    MatchedFeature,
    PatternMatch,
    SignalDirection,
    SignalObservation,
)
from ai_incident_investigator.models.report import InvestigationReport

TOP_MATCHES = 3
"""Matches reported per probe (docs/assumptions.md): precedent is a
starting point for a human, not a search-results page."""


def latest_verification(record: ExecutionRecord, executions: ExecutionsFile) -> VerificationOutcome:
    """The record's current verification outcome: the latest appended
    verification for (plan_id, step_index, executed_at) - and the SAME
    action: a verification never vouches for a toggle it did not watch -
    falling back to the record's own (pending/not_applicable) state.
    Appended records win ties on verified_at - later appends are later
    knowledge."""
    matching = sorted(
        (
            v
            for v in executions.verifications
            if v.plan_id == record.plan_id
            and v.step_index == record.step_index
            and v.executed_at == record.executed_at
            and v.action == record.action
        ),
        key=lambda v: v.verified_at,
    )
    return matching[-1].outcome if matching else record.verification


def _signal_directions(report: InvestigationReport) -> list[SignalObservation]:
    """Every (service, signal) pair cited as evidence, with the deviation
    direction when the report's own recovery baselines make it derivable:
    median of the metrics-sourced evidence values vs. the watched baseline
    for the same pair. Anything less grounded stays "unknown"."""
    baselines: dict[tuple[str, str], float] = {}
    if report.recovery_verification is not None:
        for watched in report.recovery_verification.signals:
            baselines[(watched.service, watched.signal)] = watched.baseline

    pairs: set[tuple[str, str]] = set()
    metric_values: dict[tuple[str, str], list[float]] = {}
    for item in report.evidence:
        if item.service is None or item.signal is None:
            continue
        pair = (item.service, item.signal)
        pairs.add(pair)
        if item.source == Source.METRICS and isinstance(item.value, float):
            metric_values.setdefault(pair, []).append(item.value)

    observations = []
    for pair in sorted(pairs):
        direction: SignalDirection = "unknown"
        if pair in baselines and metric_values.get(pair):
            median = statistics.median(metric_values[pair])
            if median > baselines[pair]:
                direction = "elevated"
            elif median < baselines[pair]:
                direction = "depressed"
        observations.append(SignalObservation(service=pair[0], signal=pair[1], direction=direction))
    return observations


def _deploy_correlated(report: InvestigationReport) -> bool:
    """Whether the top-ranked hypothesis cites deploys-sourced evidence."""
    if not report.hypotheses:
        return False
    sources = {item.id: item.source for item in report.evidence}
    top = report.hypotheses[0]
    return any(sources.get(eid) == Source.DEPLOYS for eid in top.supporting_evidence_ids)


def _executed_fixes(executions: ExecutionsFile | None) -> list[ExecutedFix]:
    """Live attempts only: a previewed or refused execution never becomes
    precedent. Each fix carries its LATEST verification outcome."""
    if executions is None:
        return []
    fixes = []
    for record in executions.executions:
        if record.mode != "live":
            continue
        if record.outcome != "applied" and record.outcome != "failed":
            continue
        fixes.append(
            ExecutedFix(
                action=record.action,
                outcome=record.outcome,
                verification=latest_verification(record, executions),
                executed_at=record.executed_at,
            )
        )
    return sorted(fixes, key=lambda f: (f.executed_at, f.action.environment, f.action.flag_key))


def fingerprint_report(
    report: InvestigationReport,
    report_sha256: str,
    executions: ExecutionsFile | None = None,
) -> IncidentFingerprint:
    """The comparable features of one investigation, derived purely from
    the report (and executions sidecar when one exists)."""
    signals = _signal_directions(report)
    services = sorted(
        set(report.summary.affected_services) | {observation.service for observation in signals}
    )
    return IncidentFingerprint(
        incident_id=report.incident_id,
        report_sha256=report_sha256,
        window_start=report.summary.incident_window.start,
        services=services,
        severity=report.severity.level,
        abnormal_signals=signals,
        deploy_correlated=_deploy_correlated(report),
        executed_fixes=_executed_fixes(executions),
    )


def _fix_note(fixes: list[ExecutedFix]) -> str | None:
    """The wording rule (docs/assumptions.md): only a verified verdict may
    read as precedent; anything else tried there reads as a caution."""
    if any(fix.verification == "verified" for fix in fixes):
        return "a verified fix is on record there"
    if fixes:
        return "a fix was tried there but did NOT verify"
    return None


def _match_one(probe: IncidentFingerprint, entry: HistoryEntry) -> PatternMatch | None:
    them = entry.fingerprint
    probe_pairs = {(s.service, s.signal): s.direction for s in probe.abnormal_signals}
    entry_pairs = {(s.service, s.signal): s.direction for s in them.abnormal_signals}
    shared_pairs = sorted(set(probe_pairs) & set(entry_pairs))
    if not shared_pairs:
        return None

    matched: list[MatchedFeature] = []
    unmatched: list[str] = []
    for service, signal in shared_pairs:
        matched.append(
            MatchedFeature(
                feature="signal",
                detail=f"{service}/{signal} abnormal in both",
                weight=FEATURE_WEIGHTS["signal"],
            )
        )
        here, there = probe_pairs[(service, signal)], entry_pairs[(service, signal)]
        if here == there and here != "unknown":
            matched.append(
                MatchedFeature(
                    feature="direction",
                    detail=f"{service}/{signal} {here} in both",
                    weight=FEATURE_WEIGHTS["direction"],
                )
            )
        elif here != there and "unknown" not in (here, there):
            unmatched.append(f"{service}/{signal} direction differs ({here} here, {there} there)")

    covered = {service for service, _ in shared_pairs}
    for service in sorted((set(probe.services) & set(them.services)) - covered):
        matched.append(
            MatchedFeature(
                feature="service",
                detail=f"{service} affected in both",
                weight=FEATURE_WEIGHTS["service"],
            )
        )

    if probe.severity == them.severity:
        matched.append(
            MatchedFeature(
                feature="severity",
                detail=f"both assessed {probe.severity}",
                weight=FEATURE_WEIGHTS["severity"],
            )
        )
    else:
        unmatched.append(f"severity differs ({probe.severity} here, {them.severity} there)")

    if probe.deploy_correlated and them.deploy_correlated:
        matched.append(
            MatchedFeature(
                feature="deploy_correlated",
                detail="top hypothesis cites a deploy in both",
                weight=FEATURE_WEIGHTS["deploy_correlated"],
            )
        )
    elif probe.deploy_correlated != them.deploy_correlated:
        where = "here" if probe.deploy_correlated else "there"
        unmatched.append(f"deploy correlation differs (top hypothesis cites a deploy only {where})")

    for label, only in (
        ("not seen there", sorted(set(probe_pairs) - set(entry_pairs))),
        ("not seen here", sorted(set(entry_pairs) - set(probe_pairs))),
    ):
        if only:
            unmatched.append(f"{label}: " + ", ".join(f"{s}/{g}" for s, g in only))

    score = sum(feature.weight for feature in matched)
    re_investigation = them.incident_id == probe.incident_id
    lead = (
        "earlier investigation of this same incident"
        if re_investigation
        else f"resembles {them.incident_id}"
    )
    signal_count = sum(1 for feature in matched if feature.feature == "signal")
    parts = [
        f"{lead} ({them.window_start.date().isoformat()})",
        f"{signal_count} shared abnormal signal(s), score {score}",
    ]
    note = _fix_note(them.executed_fixes)
    if note is not None:
        parts.append(note)
    return PatternMatch(
        entry_id=entry.entry_id,
        incident_id=them.incident_id,
        window_start=them.window_start,
        re_investigation=re_investigation,
        score=score,
        matched=matched,
        unmatched=unmatched,
        executed_fixes=them.executed_fixes,
        explanation="; ".join(parts),
    )


def match_fingerprints(
    probe: IncidentFingerprint, entries: list[HistoryEntry], top_n: int = TOP_MATCHES
) -> list[PatternMatch]:
    """Ranked matches for a probe: score descending, then most recent
    window first, then entry_id - and never the probe's own report.
    Entries are content-addressed; duplicates (a copied store directory)
    dedup by entry_id so they cannot crowd the top-N cap."""
    unique: dict[str, HistoryEntry] = {}
    for entry in entries:
        unique.setdefault(entry.entry_id, entry)
    matches = [
        match
        for entry in unique.values()
        if entry.fingerprint.report_sha256 != probe.report_sha256
        and (match := _match_one(probe, entry)) is not None
    ]
    matches.sort(key=lambda m: (-m.score, -m.window_start.timestamp(), m.entry_id))
    return matches[:top_n]

"""Deterministic rendering of validated facts into investigator prompt input.

Agents never see raw files (Principle 4); they see these renderings of the
already-validated package. Renderings are plain text, chronological, and
byte-stable for a given package so record/replay fixture keys stay stable.
"""

from ai_incident_investigator.models.package import IncidentPackage, LogRecord, Span
from ai_incident_investigator.models.report import (
    EvidenceItem,
    Hypothesis,
    IncidentWindow,
    MissingData,
    NextStep,
    SafetyReview,
    SeverityAssessment,
    Summary,
    TimelineEntry,
)

MAX_LOG_RECORDS = 300
MAX_TIMELINE_ENTRIES = 40
MAX_RUNBOOK_CHARS = 20_000


def render_window(window: IncidentWindow) -> str:
    end = window.end.isoformat() if window.end else "ongoing"
    return f"INCIDENT WINDOW\nstart: {window.start.isoformat()}\nend: {end}\nrule: {window.rule}"


def render_alert(package: IncidentPackage) -> str:
    alert = package.alert
    lines = [
        "ALERT",
        f"id: {alert.id}",
        f"title: {alert.title}",
        f"service: {alert.service}",
        f"triggered_at: {alert.triggered_at.isoformat()}",
    ]
    if alert.severity:
        lines.append(f"monitoring severity label: {alert.severity}")
    if alert.signal:
        lines.append(f"signal: {alert.signal}")
    if alert.threshold is not None:
        lines.append(f"threshold: {alert.threshold}")
    if alert.observed_value is not None:
        lines.append(f"observed value at trigger: {alert.observed_value}")
    if alert.description:
        lines.append(f"description: {alert.description}")
    return "\n".join(lines)


def render_metrics(package: IncidentPackage) -> str:
    assert package.metrics is not None
    blocks = ["METRIC SERIES (each with pre-incident baseline)"]
    for series in package.metrics.series:
        unit = f" {series.unit}" if series.unit else ""
        points = "\n".join(
            f"  {point.timestamp.isoformat()}  {point.value}{unit}"
            for point in sorted(series.points, key=lambda p: p.timestamp)
        )
        blocks.append(
            f"- service={series.service} signal={series.signal} "
            f"baseline={series.baseline}{unit}\n{points}"
        )
    return "\n".join(blocks)


def render_logs(package: IncidentPackage) -> str:
    records = sorted(package.logs, key=lambda r: r.timestamp)
    if len(records) > MAX_LOG_RECORDS:
        half = MAX_LOG_RECORDS // 2
        omitted = f"\n  ... {len(records) - MAX_LOG_RECORDS} records omitted from the middle ...\n"
        rendered = _log_lines(records[:half]) + omitted + _log_lines(records[-half:])
    else:
        rendered = _log_lines(records)
    return f"LOG RECORDS ({len(records)} total)\n{rendered}"


def _log_lines(records: list[LogRecord]) -> str:
    return "\n".join(
        f"{r.timestamp.isoformat()} {r.level} {r.service} {r.message}" for r in records
    )


def render_traces(package: IncidentPackage) -> str:
    assert package.traces is not None
    by_trace: dict[str, list[Span]] = {}
    for span in package.traces.spans:
        by_trace.setdefault(span.trace_id, []).append(span)
    blocks = ["TRACE SPANS (grouped by trace, chronological)"]
    for trace_id in sorted(by_trace, key=lambda t: min(s.start_time for s in by_trace[t])):
        spans = sorted(by_trace[trace_id], key=lambda s: (s.start_time, s.span_id))
        lines = [
            f"  {span.span_id} {span.service} {span.operation} "
            f"start={span.start_time.isoformat()} duration_ms={span.duration_ms:g} "
            f"status={span.status} parent={span.parent_span_id or '-'}"
            for span in spans
        ]
        blocks.append(f"trace {trace_id}\n" + "\n".join(lines))
    return "\n".join(blocks)


def render_deploys(package: IncidentPackage) -> str:
    assert package.deploys is not None
    lines = ["DEPLOYS AND CHANGES (chronological)"]
    for deploy in sorted(package.deploys.deploys, key=lambda d: d.deployed_at):
        description = f": {deploy.description}" if deploy.description else ""
        lines.append(
            f"- {deploy.id} {deploy.change_type} service={deploy.service} "
            f"version={deploy.version} at {deploy.deployed_at.isoformat()}{description}"
        )
    return "\n".join(lines)


def render_topology(package: IncidentPackage) -> str:
    if package.topology is None:
        return "TOPOLOGY: not provided"
    lines = ["TOPOLOGY (service -> depends on)"]
    for node in sorted(package.topology.services, key=lambda n: n.name):
        deps = ", ".join(node.depends_on) if node.depends_on else "(nothing)"
        lines.append(f"- {node.name} ({node.kind}) -> {deps}")
    return "\n".join(lines)


def render_runbook(package: IncidentPackage) -> str:
    assert package.runbook is not None
    text = package.runbook
    if len(text) > MAX_RUNBOOK_CHARS:
        text = text[:MAX_RUNBOOK_CHARS] + "\n... (runbook truncated) ..."
    return f"RUNBOOK (verbatim)\n{text}"


def render_evidence(evidence: list[EvidenceItem]) -> str:
    if not evidence:
        return "EVIDENCE: none collected"
    lines = ["EVIDENCE (cite items by their exact id)"]
    for item in evidence:
        parts = [f"[{item.source.value}]"]
        if item.service:
            parts.append(f"service={item.service}")
        if item.signal:
            parts.append(f"signal={item.signal}")
        if item.value is not None:
            parts.append(f"value={item.value}")
        if item.timestamp:
            parts.append(f"at {item.timestamp.isoformat()}")
        lines.append(f"- {item.id} {' '.join(parts)}: {item.interpretation}")
    return "\n".join(lines)


def render_missing_data(items: list[MissingData]) -> str:
    if not items:
        return "KNOWN DATA GAPS: none recorded"
    lines = ["KNOWN DATA GAPS"]
    lines.extend(f"- {item.description} (impact: {item.impact})" for item in items)
    return "\n".join(lines)


def render_assessment(summary: Summary | None, severity: SeverityAssessment | None) -> str:
    lines = ["TRIAGE ASSESSMENT"]
    if summary is None and severity is None:
        return "TRIAGE ASSESSMENT: unavailable (triage did not run or failed)"
    if severity is not None:
        lines.append(
            f"severity: {severity.level.value} (confidence {severity.confidence.value}) "
            f"- {severity.explanation}"
        )
    if summary is not None:
        lines.append(f"what happened: {summary.what_happened}")
        lines.append(f"affected services: {', '.join(summary.affected_services) or 'unknown'}")
        lines.append(f"customer impact: {summary.customer_impact}")
    return "\n".join(lines)


def render_hypotheses(hypotheses: list[Hypothesis]) -> str:
    if not hypotheses:
        return "HYPOTHESES: none produced"
    blocks = ["RANKED HYPOTHESES (with code-derived confidence)"]
    for hypothesis in hypotheses:
        rubric = hypothesis.rubric
        blocks.append(
            f"- {hypothesis.id} [{hypothesis.confidence.value}] {hypothesis.title}\n"
            f"  statement: {hypothesis.statement}\n"
            f"  rubric: aligned_signals={rubric.aligned_signals} "
            f"timing={rubric.timing_alignment} conflicts={rubric.conflicting_evidence_count}\n"
            f"  supporting: {', '.join(hypothesis.supporting_evidence_ids) or '(none)'}\n"
            f"  conflicting: {', '.join(hypothesis.conflicting_evidence_ids) or '(none)'}\n"
            f"  assumptions: {'; '.join(hypothesis.assumptions) or '(none)'}\n"
            f"  recommended checks: {'; '.join(hypothesis.recommended_checks) or '(none)'}"
        )
    return "\n".join(blocks)


def render_next_steps(steps: list[NextStep]) -> str:
    if not steps:
        return "RECOMMENDED NEXT STEPS: none aggregated"
    lines = ["RECOMMENDED NEXT STEPS (aggregated, with back-references)"]
    lines.extend(f"- {step.id}: {step.description}" for step in steps)
    return "\n".join(lines)


def render_safety_review_summary(review: SafetyReview | None) -> str:
    if review is None:
        return "SAFETY REVIEW: not yet available"
    non_pass = [c for c in review.checks if c.result != "pass"]
    if not non_pass:
        return f"SAFETY REVIEW: all {len(review.checks)} checks passed"
    lines = [f"SAFETY REVIEW ({len(non_pass)} non-pass of {len(review.checks)} checks)"]
    lines.extend(f"- [{c.result}] {c.check}: {c.detail or ''}" for c in non_pass)
    return "\n".join(lines)


def render_timeline(timeline: list[TimelineEntry]) -> str:
    entries = timeline[:MAX_TIMELINE_ENTRIES]
    lines = [
        f"{entry.timestamp.isoformat()} [{entry.source.value}] "
        f"{entry.service or '-'}: {entry.description}"
        for entry in entries
    ]
    suffix = ""
    if len(timeline) > MAX_TIMELINE_ENTRIES:
        suffix = f"\n... {len(timeline) - MAX_TIMELINE_ENTRIES} later entries omitted ..."
    return f"DETERMINISTIC TIMELINE ({len(timeline)} events)\n" + "\n".join(lines) + suffix

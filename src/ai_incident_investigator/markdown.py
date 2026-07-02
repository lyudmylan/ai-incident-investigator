"""Human-readable Markdown rendering of an InvestigationReport.

Pure formatting: everything shown comes from the report, nothing is added.
Section order mirrors how an on-call engineer reads: what/how bad first,
then why we think so, then what to do about it, then the paper trail.
"""

from ai_incident_investigator.models.report import (
    Hypothesis,
    InvestigationReport,
    RemediationPlan,
)


def _plan_block(plan: RemediationPlan) -> str:
    lines = [
        f"### {plan.title} ({plan.kind})",
        "",
        "> **Human approval required before any step of this plan is acted on.**",
        "",
        f"- addresses hypothesis: `{plan.hypothesis_id}`"
        + (f", mitigation `{plan.mitigation_id}`" if plan.mitigation_id else ""),
        f"- suggested owner: {plan.owner_role}",
    ]
    if plan.preconditions:
        lines.append("- preconditions: " + "; ".join(plan.preconditions))
    lines.append("")
    for number, step in enumerate(plan.steps, start=1):
        if step.kind == "state_changing":
            lines.append(f"{number}. **[STATE-CHANGING - approval required]** {step.action}")
            lines.append(f"   - verify: {step.verification}")
        else:
            lines.append(f"{number}. [read-only] {step.action}")
            if step.verification:
                lines.append(f"   - verify: {step.verification}")
    lines.append("")
    lines.append("**Abort if:** " + "; ".join(plan.abort_conditions))
    return "\n".join(lines)


def _hypothesis_block(hypothesis: Hypothesis, rank: int) -> str:
    rubric = hypothesis.rubric
    lines = [
        f"### {rank}. {hypothesis.title} — confidence: {hypothesis.confidence.value}",
        "",
        hypothesis.statement,
        "",
        f"- rubric: {rubric.aligned_signals} aligned signal(s), "
        f"timing {rubric.timing_alignment}, {rubric.conflicting_evidence_count} conflict(s)",
        f"- supporting evidence: {', '.join(f'`{i}`' for i in hypothesis.supporting_evidence_ids)}",
    ]
    if hypothesis.conflicting_evidence_ids:
        lines.append(
            "- conflicting evidence: "
            + ", ".join(f"`{i}`" for i in hypothesis.conflicting_evidence_ids)
        )
    if hypothesis.assumptions:
        lines.append("- assumptions: " + "; ".join(hypothesis.assumptions))
    if hypothesis.recommended_checks:
        lines.append("- recommended checks: " + "; ".join(hypothesis.recommended_checks))
    return "\n".join(lines)


def render_markdown(report: InvestigationReport) -> str:
    summary = report.summary
    severity = report.severity
    window = summary.incident_window
    sections: list[str] = []

    sections.append(f"# Incident investigation: {report.incident_id}")
    sections.append(
        f"**Severity: {severity.level.value}** (confidence {severity.confidence.value}) "
        f"— {severity.explanation}"
    )
    sections.append(
        "## Summary\n\n"
        f"{summary.what_happened}\n\n"
        f"- affected services: {', '.join(summary.affected_services) or 'unknown'}\n"
        f"- customer impact: {summary.customer_impact}\n"
        f"- window: {window.start.isoformat()} → "
        f"{window.end.isoformat() if window.end else 'ongoing'} ({window.rule})"
    )

    if report.hypotheses:
        blocks = [_hypothesis_block(h, rank) for rank, h in enumerate(report.hypotheses, start=1)]
        sections.append("## Ranked hypotheses\n\n" + "\n\n".join(blocks))
    else:
        sections.append("## Ranked hypotheses\n\nNone produced.")

    if report.evidence:
        lines = [
            f"- `{item.id}` [{item.source.value}] {item.interpretation}" for item in report.evidence
        ]
        sections.append("## Evidence\n\n" + "\n".join(lines))

    if report.recommended_next_steps:
        lines = []
        for step in report.recommended_next_steps:
            refs = [*step.source_hypothesis_ids, *step.source_missing_data_ids]
            suffix = f" _(from {', '.join(f'`{r}`' for r in refs)})_" if refs else ""
            lines.append(f"1. {step.description}{suffix}")
        sections.append("## Recommended next steps\n\n" + "\n".join(lines))

    if report.safe_mitigation_options:
        lines = ["> **Human approval required before any mitigation is acted on.**", ""]
        for option in report.safe_mitigation_options:
            lines.append(f"- **{option.action}** — {option.rationale}")
            if option.risks:
                lines.append(f"  - risks: {'; '.join(option.risks)}")
        sections.append("## Safe mitigation options\n\n" + "\n".join(lines))

    if report.remediation_plans:
        blocks = [_plan_block(plan) for plan in report.remediation_plans]
        sections.append("## Remediation plans (guided, human-approved)\n\n" + "\n\n".join(blocks))

    if report.recovery_verification is not None:
        verification = report.recovery_verification
        mode_text = (
            "confirm the observed recovery is sustained"
            if verification.mode == "confirm_sustained_recovery"
            else "watch for recovery"
        )
        lines = [f"Mode: {mode_text}.", ""]
        for signal in verification.signals:
            lines.append(
                f"- `{signal.service}` / `{signal.signal}` (baseline {signal.baseline}): "
                f"{signal.recovered_when} - watch for {signal.watch_minutes} minutes"
            )
        if verification.log_patterns_should_stop:
            lines.append("")
            lines.append(
                "Log patterns that should stop appearing: "
                + "; ".join(f"`{p}`" for p in verification.log_patterns_should_stop)
            )
        if verification.re_alert_condition:
            lines.append("")
            lines.append(f"Re-alert if: {verification.re_alert_condition}")
        sections.append("## Recovery verification plan\n\n" + "\n".join(lines))

    non_pass = [c for c in report.safety_review.checks if c.result != "pass"]
    review_lines = [
        f"- [{check.result}] **{check.check}**" + (f": {check.detail}" if check.detail else "")
        for check in (non_pass or report.safety_review.checks)
    ] or ["(no checks recorded)"]
    header = "non-pass findings" if non_pass else "all checks passed"
    notes = f"\n\n{report.safety_review.notes}" if report.safety_review.notes else ""
    sections.append(f"## Safety review ({header})\n\n" + "\n".join(review_lines) + notes)

    if report.missing_data:
        lines = [f"- {item.description} — impact: {item.impact}" for item in report.missing_data]
        sections.append("## Missing data\n\n" + "\n".join(lines))

    sections.append("## Internal update draft\n\n" + report.communication_drafts.internal_update)

    drafts = report.communication_drafts
    if drafts.jira_ticket is not None:
        ticket = drafts.jira_ticket
        sections.append(
            "## Jira ticket draft\n\n"
            f"**Summary:** {ticket.summary}\n\n"
            f"**Priority suggestion:** {ticket.priority_suggestion}"
            + (f" — labels: {', '.join(ticket.labels)}" if ticket.labels else "")
            + f"\n\n{ticket.description}"
        )
    if drafts.slack_update is not None:
        sections.append("## Slack update draft\n\n" + drafts.slack_update.text)
    if drafts.status_page is not None:
        sections.append(
            "## Status-page draft (customer-safe)\n\n"
            f"**Phase:** {drafts.status_page.phase}\n\n{drafts.status_page.text}"
        )

    postmortem = report.postmortem_draft
    postmortem_lines = [
        f"### {postmortem.title}",
        "",
        postmortem.summary,
        "",
        f"**Impact.** {postmortem.impact}",
    ]
    if postmortem.contributing_factors:
        postmortem_lines.append(
            "\n**Contributing factors**\n"
            + "\n".join(f"- {f}" for f in postmortem.contributing_factors)
        )
    if postmortem.open_questions:
        postmortem_lines.append(
            "\n**Open questions**\n" + "\n".join(f"- {q}" for q in postmortem.open_questions)
        )
    if postmortem.action_items:
        postmortem_lines.append(
            "\n**Action items**\n" + "\n".join(f"- {a}" for a in postmortem.action_items)
        )
    sections.append("## Postmortem draft\n\n" + "\n".join(postmortem_lines))

    if report.timeline:
        lines = [
            f"- `{entry.timestamp.isoformat()}` [{entry.source.value}] "
            f"{entry.service or '-'}: {entry.description}"
            for entry in report.timeline
        ]
        sections.append("## Timeline\n\n" + "\n".join(lines))

    if report.reasoning_trace:
        lines = [f"- **{step.stage}**: {step.summary}" for step in report.reasoning_trace]
        sections.append("## Reasoning trace (how this report was produced)\n\n" + "\n".join(lines))

    return "\n\n".join(sections) + "\n"

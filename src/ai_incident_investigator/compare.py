"""Pre/post recovery comparison from a second snapshot (epic #53).

The original package deterministically implies a recovery verification
plan (recovery.py); a follow-up snapshot collected later either satisfies
it or does not. This module answers with numbers - the same recovery rule
that ends incident windows (window.recovery_start), the same pattern
normalization that derived the watch list - and refuses to guess: a signal
absent from the follow-up is UNVERIFIABLE, never assumed recovered.

No LLM, no network, no action on the outcome (a human reads the verdict).
Verdict rules documented in docs/assumptions.md.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_incident_investigator.markdown import postmortem_block
from ai_incident_investigator.models.package import IncidentPackage, MetricSeries
from ai_incident_investigator.models.report import (
    InvestigationReport,
    PostmortemDraft,
    RecoveryVerificationPlan,
    WatchedSignal,
)
from ai_incident_investigator.recovery import (
    build_recovery_verification,
    normalize_pattern,
)
from ai_incident_investigator.window import incident_window, recovery_start


class ComparisonError(Exception):
    """The comparison could not be built at all (no derivable watch plan)."""


class SignalComparison(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    service: str
    signal: str
    baseline: float
    incident_peak: float
    follow_up_last: float | None = Field(
        default=None, description="None: the signal is absent from the follow-up snapshot"
    )
    recovered: bool | None = Field(default=None, description="None: unverifiable, never assumed")
    detail: str


class PatternComparison(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pattern: str
    occurrences_in_follow_up: int
    still_present: bool


class RecoveryComparison(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    original_incident_id: str
    follow_up_incident_id: str
    signals: list[SignalComparison]
    patterns: list[PatternComparison]
    re_alert_condition: str | None
    re_alert: Literal["met", "not_met", "unevaluated"]
    verdict: Literal["recovered", "not_recovered", "inconclusive"]
    summary: str
    postmortem_addendum: str


def _series_index(package: IncidentPackage) -> dict[tuple[str, str], MetricSeries]:
    if package.metrics is None:
        return {}
    return {(s.service, s.signal): s for s in package.metrics.series}


def _compare_signal(
    watched: WatchedSignal,
    original: MetricSeries | None,
    follow_up: MetricSeries | None,
) -> SignalComparison:
    peak = max(p.value for p in original.points) if original is not None else 0.0
    if follow_up is None:
        return SignalComparison(
            service=watched.service,
            signal=watched.signal,
            baseline=watched.baseline,
            incident_peak=peak,
            detail="signal absent from the follow-up snapshot; recovery unverifiable",
        )
    # evaluate against the ORIGINAL baseline: the plan is the anchor, and the
    # documented recovered-when rule is the same code that ends windows
    anchored = follow_up.model_copy(update={"baseline": watched.baseline})
    last = sorted(follow_up.points, key=lambda p: p.timestamp)[-1].value
    recovered_at = recovery_start(anchored)
    if recovered_at is not None:
        detail = (
            f"recovered per the documented rule (sustained from "
            f"{recovered_at.isoformat()}); last value {last:g} vs baseline "
            f"{watched.baseline:g}"
        )
        return SignalComparison(
            service=watched.service,
            signal=watched.signal,
            baseline=watched.baseline,
            incident_peak=peak,
            follow_up_last=last,
            recovered=True,
            detail=detail,
        )
    return SignalComparison(
        service=watched.service,
        signal=watched.signal,
        baseline=watched.baseline,
        incident_peak=peak,
        follow_up_last=last,
        recovered=False,
        detail=(
            f"NOT recovered: no sustained run within tolerance of baseline "
            f"{watched.baseline:g}; last value {last:g}"
        ),
    )


def _compare_patterns(
    plan: RecoveryVerificationPlan, follow_up: IncidentPackage
) -> list[PatternComparison]:
    counts: dict[str, int] = {pattern: 0 for pattern in plan.log_patterns_should_stop}
    for record in follow_up.logs:
        if record.level not in ("ERROR", "FATAL"):
            continue
        shape = normalize_pattern(record.message)
        if shape in counts:
            counts[shape] += 1
    return [
        PatternComparison(pattern=pattern, occurrences_in_follow_up=count, still_present=count > 0)
        for pattern, count in counts.items()
    ]


def _evaluate_re_alert(
    original: IncidentPackage, follow_up: IncidentPackage
) -> Literal["met", "not_met", "unevaluated"]:
    alert = original.alert
    if alert.signal is None or alert.threshold is None:
        return "unevaluated"
    series = _series_index(follow_up).get((alert.service, alert.signal))
    if series is None:
        return "unevaluated"
    last = sorted(series.points, key=lambda p: p.timestamp)[-1].value
    return "met" if last > alert.threshold else "not_met"


def build_comparison(original: IncidentPackage, follow_up: IncidentPackage) -> RecoveryComparison:
    plan, _ = build_recovery_verification(original, incident_window(original))
    if plan is None:
        raise ComparisonError(
            "the original package yields no recovery verification plan "
            "(no metrics or no deviated series); nothing to compare against"
        )

    original_series = _series_index(original)
    follow_up_series = _series_index(follow_up)
    signals = [
        _compare_signal(
            watched,
            original_series.get((watched.service, watched.signal)),
            follow_up_series.get((watched.service, watched.signal)),
        )
        for watched in plan.signals
    ]
    patterns = _compare_patterns(plan, follow_up)
    re_alert = _evaluate_re_alert(original, follow_up)

    recovered_count = sum(1 for s in signals if s.recovered is True)
    failed = [s for s in signals if s.recovered is False]
    unverifiable = [s for s in signals if s.recovered is None]
    still_present = [p for p in patterns if p.still_present]

    if failed or still_present or re_alert == "met":
        verdict: Literal["recovered", "not_recovered", "inconclusive"] = "not_recovered"
    elif unverifiable:
        verdict = "inconclusive"
    else:
        verdict = "recovered"

    parts = [f"{recovered_count}/{len(signals)} watched signals recovered"]
    if unverifiable:
        parts[-1] += f" ({len(unverifiable)} unverifiable)"
    if patterns:
        parts.append(
            f"{len(still_present)} of {len(patterns)} watched error patterns still present"
        )
    if re_alert != "unevaluated":
        parts.append(f"re-alert condition {re_alert.replace('_', ' ')}")
    summary = "; ".join(parts)

    addendum_lines = [
        f"Recovery verification ({follow_up.incident_id} vs {original.incident_id}): "
        f"{verdict.replace('_', ' ').upper()}.",
        summary + ".",
    ]
    for s in failed:
        addendum_lines.append(
            f"Outstanding: {s.service}/{s.signal} last observed {s.follow_up_last:g} "
            f"against baseline {s.baseline:g}."
        )
    for s in unverifiable:
        addendum_lines.append(
            f"Unverifiable: {s.service}/{s.signal} was not in the follow-up snapshot."
        )
    for p in still_present:
        addendum_lines.append(
            f"Still occurring: '{p.pattern}' ({p.occurrences_in_follow_up}x in follow-up)."
        )

    return RecoveryComparison(
        original_incident_id=original.incident_id,
        follow_up_incident_id=follow_up.incident_id,
        signals=signals,
        patterns=patterns,
        re_alert_condition=plan.re_alert_condition,
        re_alert=re_alert,
        verdict=verdict,
        summary=summary,
        postmortem_addendum=" ".join(addendum_lines),
    )


def render_comparison(comparison: RecoveryComparison) -> str:
    lines = [
        "# Recovery verification comparison",
        "",
        f"**Verdict: {comparison.verdict.replace('_', ' ').upper()}** — {comparison.summary}",
        "",
        f"Original: `{comparison.original_incident_id}`; follow-up: "
        f"`{comparison.follow_up_incident_id}`.",
        "",
        "## Watched signals",
        "",
    ]
    for s in comparison.signals:
        marker = {True: "RECOVERED", False: "NOT RECOVERED", None: "UNVERIFIABLE"}[s.recovered]
        lines.append(
            f"- [{marker}] `{s.service}` / `{s.signal}`: baseline {s.baseline:g}, "
            f"incident peak {s.incident_peak:g}, follow-up last "
            f"{s.follow_up_last if s.follow_up_last is not None else 'absent'} — {s.detail}"
        )
    if comparison.patterns:
        lines += ["", "## Watched error patterns", ""]
        for p in comparison.patterns:
            state = f"still present ({p.occurrences_in_follow_up}x)" if p.still_present else "gone"
            lines.append(f"- [{state}] `{p.pattern}`")
    if comparison.re_alert_condition:
        lines += [
            "",
            f"Re-alert condition: {comparison.re_alert_condition} — "
            f"**{comparison.re_alert.replace('_', ' ')}**",
        ]
    lines += [
        "",
        "## Postmortem addendum (paste block)",
        "",
        "> " + comparison.postmortem_addendum,
        "",
    ]
    return "\n".join(lines)


def merge_comparison_into_postmortem(
    report: InvestigationReport, comparison: RecoveryComparison
) -> PostmortemDraft:
    """Deterministically fold a recovery comparison into the report's
    postmortem draft (docs/product.md v5: update postmortem from verified
    recovery). Purely additive: the draft's existing text is never
    rewritten, unverifiable signals become open questions, unrecovered
    signals and surviving error patterns become action items, and a met
    re-alert is never silently dropped. No LLM anywhere."""
    draft = report.postmortem_draft
    impact = (
        f"{draft.impact} Recovery verification "
        f"({comparison.follow_up_incident_id} follow-up): "
        f"{comparison.verdict.upper().replace('_', ' ')} - {comparison.summary}"
    )
    open_questions = list(draft.open_questions)
    action_items = list(draft.action_items)
    for signal in comparison.signals:
        if signal.recovered is None:
            open_questions.append(
                f"recovery of {signal.service}/{signal.signal} is unverifiable: {signal.detail}"
            )
        elif signal.recovered is False:
            action_items.append(
                f"{signal.service}/{signal.signal} had not recovered in the "
                f"follow-up ({signal.detail}); keep watching or mitigating"
            )
    for pattern in comparison.patterns:
        if pattern.still_present:
            action_items.append(
                f"error pattern still present in the follow-up "
                f"({pattern.occurrences_in_follow_up} occurrence(s)): {pattern.pattern}"
            )
    if comparison.re_alert == "met":
        action_items.append(
            f"re-alert condition was met in the follow-up snapshot: {comparison.re_alert_condition}"
        )
    return draft.model_copy(
        update={
            "impact": impact,
            "open_questions": open_questions,
            "action_items": action_items,
        }
    )


def render_updated_postmortem(report: InvestigationReport, comparison: RecoveryComparison) -> str:
    """The sidecar document compare --update-postmortem writes. The report
    file itself is NEVER rewritten: its hash anchors every approval, so the
    updated draft lives next to it, not inside it."""
    merged = merge_comparison_into_postmortem(report, comparison)
    return (
        "# Postmortem draft (updated from verified recovery)\n\n"
        + postmortem_block(merged)
        + "\n\n"
        + f"_Deterministically merged from the {comparison.follow_up_incident_id} "
        f"follow-up snapshot (verdict: {comparison.verdict}). The report file is "
        "untouched - rewriting it would void the approvals bound to its hash._\n"
    )

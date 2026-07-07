"""Per-example scripted LLM responses used to bootstrap replay fixtures.

These stand in for real model output until fixtures are recorded live
(AGENTS.md); scripts/bootstrap_fixtures.py records them through the real
RecordingClient so golden tests exercise the genuine replay path. Content is
plausible and grounded in each example package, but its purpose is plumbing,
not model-quality evaluation.
"""

import re
from collections.abc import Callable

from ai_incident_investigator.agents.responses import (
    CriticCheck,
    CriticResponse,
    HypothesisDraft,
    JiraDraft,
    MitigationDraft,
    PlanDraft,
    PlannerResponse,
    PlanStepDraft,
    RankerResponse,
    ReporterResponse,
    SlackDraft,
    StatusPageResponseDraft,
    TriageResponse,
)
from ai_incident_investigator.llm import LLMRequest
from helpers import (
    ScriptEntry,
    default_script,
    investigator_json,
    make_finding,
)

_EVIDENCE_LINE = re.compile(r"- (evidence_[0-9a-f]{10}) \[(\w+)\]")
_HYPOTHESIS_ID = re.compile(r"(hypothesis_[0-9a-f]{10})")
_MITIGATION_ID = re.compile(r"(mitigation_[0-9a-f]{10})")


def scripted_ranker(
    title: str,
    statement: str,
    timing_justification: str,
    checks: list[str],
) -> ScriptEntry:
    """One hypothesis citing the first evidence id of every source in the input."""

    def reply(request: LLMRequest) -> str:
        first_per_source: dict[str, str] = {}
        for evidence_id, source in _EVIDENCE_LINE.findall(request.messages[0].content):
            first_per_source.setdefault(source, evidence_id)
        draft = HypothesisDraft(
            title=title,
            statement=statement,
            supporting_evidence_ids=list(first_per_source.values()),
            conflicting_evidence_ids=[],
            timing_alignment="aligned",
            timing_justification=timing_justification,
            assumptions=["telemetry in the package is complete for the window"],
            recommended_checks=checks,
        )
        return RankerResponse(
            hypotheses=[draft], gaps=[], reasoning="combined aligned findings across sources"
        ).model_dump_json()

    return reply


def scripted_hypotheses(*specs: dict[str, object], gaps: list[str] | None = None) -> ScriptEntry:
    """Hypotheses citing evidence by (source, index) at runtime.

    Unlike scripted_ranker this supports several hypotheses, per-hypothesis
    timing, and CONFLICTING citations - the adversarial corpus needs all
    three (a control series that contradicts, a misaligned deploy)."""

    def reply(request: LLMRequest) -> str:
        per_source: dict[str, list[str]] = {}
        for evidence_id, source in _EVIDENCE_LINE.findall(request.messages[0].content):
            per_source.setdefault(source, []).append(evidence_id)

        def ids(refs: object) -> list[str]:
            assert isinstance(refs, list)
            out: list[str] = []
            for source, index in refs:
                pool = per_source.get(source, [])
                if index < len(pool):
                    out.append(pool[index])
            return out

        drafts = []
        for spec in specs:
            payload = {k: v for k, v in spec.items() if k not in ("support", "conflict")}
            payload.setdefault("assumptions", ["package telemetry is complete for the window"])
            payload["recommended_checks"] = payload.pop("checks")
            payload["supporting_evidence_ids"] = ids(spec["support"])
            payload["conflicting_evidence_ids"] = ids(spec.get("conflict", []))
            drafts.append(HypothesisDraft.model_validate(payload))
        return RankerResponse(
            hypotheses=drafts,
            gaps=list(gaps or []),
            reasoning="ranked by evidence alignment and timing",
        ).model_dump_json()

    return reply


EMPTY_PLANNER_JSON = PlannerResponse(
    plans=[], gaps=[], reasoning="no runbook-grounded option to structure into a plan"
).model_dump_json()


def basic_reporter(
    internal_update: str,
    postmortem_title: str,
    postmortem_summary: str,
    postmortem_impact: str,
    contributing_factors: list[str],
    open_questions: list[str],
    action_items: list[str],
    mitigations: list[MitigationDraft] | None = None,
    status_page: StatusPageResponseDraft | None = None,
    gaps: list[str] | None = None,
) -> str:
    """Compact reporter response: external drafts default to omitted (valid
    when nothing groundable), which keeps corpus scripts small."""
    return ReporterResponse(
        mitigation_options=mitigations or [],
        internal_update=internal_update,
        jira_ticket=None,
        slack_update=None,
        status_page=status_page,
        postmortem_title=postmortem_title,
        postmortem_summary=postmortem_summary,
        postmortem_impact=postmortem_impact,
        contributing_factors=contributing_factors,
        open_questions=open_questions,
        action_items=action_items,
        gaps=gaps or [],
        reasoning="drafted from the reviewed investigation output",
    ).model_dump_json()


def scripted_planner(*specs: dict[str, object]) -> ScriptEntry:
    """Plans citing the runtime hypothesis id (and mitigation id when the
    spec sets link_mitigation) extracted from the rendered planner input."""

    def reply(request: LLMRequest) -> str:
        content = request.messages[0].content
        hypothesis = _HYPOTHESIS_ID.search(content)
        mitigation = _MITIGATION_ID.search(content)
        assert hypothesis is not None, "planner input carried no hypothesis ids"
        plans = []
        for spec in specs:
            fields = dict(spec)
            link = bool(fields.pop("link_mitigation", False))
            raw_steps = fields.pop("steps")
            assert isinstance(raw_steps, list)
            plans.append(
                PlanDraft.model_validate(
                    {
                        **fields,
                        "hypothesis_id": hypothesis.group(1),
                        "mitigation_id": (
                            mitigation.group(1) if link and mitigation is not None else None
                        ),
                        "steps": [PlanStepDraft.model_validate(s) for s in raw_steps],
                    }
                )
            )
        return PlannerResponse(
            plans=plans, gaps=[], reasoning="structured the reviewed options into guided plans"
        ).model_dump_json()

    return reply


BOOKING_PLANNER = scripted_planner(
    {
        "kind": "mitigation",
        "title": "Consider disabling the payment_enrichment feature flag",
        "link_mitigation": True,
        "preconditions": ["staging fallback verification from 2026-05-28 still applies"],
        "steps": [
            {
                "kind": "read_only",
                "action": "confirm current payment_enrichment flag state and rollout scope",
                "verification": "flag console shows the expected current state",
            },
            {
                "kind": "state_changing",
                "action": "disable the payment_enrichment feature flag",
                "verification": (
                    "eligibility retry warnings stop within 5 minutes and booking p95 "
                    "trends toward the 450ms baseline"
                ),
            },
        ],
        "abort_conditions": [
            "booking error rate rises further within 10 minutes of the flag change"
        ],
        "owner_role": "on-call engineer",
    },
    {
        "kind": "rollback",
        "title": "Rollback checklist for booking-service release 2026.06.01-1420",
        "link_mitigation": False,
        "preconditions": ["previous release 2026.05.28 artifacts still deployable"],
        "steps": [
            {
                "kind": "read_only",
                "action": (
                    "check whether release 2026.06.01-1420 shipped data migrations or "
                    "schema changes"
                ),
                "verification": "release notes and migration directory reviewed",
            },
            {
                "kind": "state_changing",
                "action": "roll booking-service back to the previous release",
                "verification": (
                    "deployed version reports the previous release and appointments-db "
                    "CPU falls below 60%"
                ),
            },
        ],
        "abort_conditions": ["rollback pods crash-loop or error rate exceeds 10%"],
        "owner_role": "on-call engineer",
    },
)


CRITIC_PASS_JSON = CriticResponse(
    checks=[
        CriticCheck(check="overconfidence", result="pass", detail=None),
        CriticCheck(check="evidence_grounding", result="pass", detail=None),
        CriticCheck(check="action_safety", result="pass", detail=None),
        CriticCheck(check="uncertainty", result="pass", detail=None),
    ],
    notes=None,
    gaps=[],
    reasoning="statements stay within what the cited evidence shows",
).model_dump_json()


def latency_spike_script() -> dict[str, ScriptEntry]:
    script: dict[str, ScriptEntry] = dict(default_script())
    script["Role: hypothesis ranker"] = scripted_ranker(
        title="Deploy-driven eligibility retries saturating appointments-db",
        statement=(
            "The 14:20 booking-service deploy enabled payment eligibility enrichment "
            "whose slow queries and unbounded-looking retries saturated appointments-db, "
            "consistent with the latency and error escalation from 14:25."
        ),
        timing_justification="deploy 14:20 precedes first metric deviation 14:25",
        checks=[
            "compare booking error rates before and after release 2026.06.01-1420",
            "inspect payment eligibility retry bounds and backoff",
        ],
    )
    script["Role: safety critic"] = CRITIC_PASS_JSON
    script["Role: remediation planner"] = BOOKING_PLANNER
    return script


def error_rate_spike_script() -> dict[str, ScriptEntry]:
    return {
        "Role: triage": TriageResponse(
            severity_level="SEV-3",
            severity_explanation=(
                "send error rate peaked at 9.1% but notifications are retried from a "
                "dead-letter queue and are not on the booking critical path; latency "
                "stayed at baseline and the incident recovered within the window"
            ),
            severity_confidence="high",
            what_happened=(
                "Notification sends began failing at 09:42 with template rendering "
                "errors; the error rate recovered after 10:20."
            ),
            affected_services=["notifications-service"],
            customer_impact=(
                "Patient notifications were delayed (queued for retry); no booking "
                "functionality was affected."
            ),
            gaps=[],
            reasoning="applied documented severity rules; recovery and workaround observed",
        ).model_dump_json(),
        "Role: metrics investigator": investigator_json(
            make_finding(
                "error_rate_pct first crossed the 2x-baseline rule at 09:45 (0.2% -> 4.8%), "
                "peaked at 9.1%, and returned to baseline from 10:25",
                timestamp="2026-06-15T09:45:00Z",
                service="notifications-service",
                signal="error_rate_pct",
                value=4.8,
            ),
            make_finding(
                "p95 latency stayed at baseline throughout - failures are fast, "
                "pointing away from a capacity or dependency problem",
                service="notifications-service",
                signal="p95_latency_ms",
            ),
            make_finding(
                "email-gateway error rate stayed at baseline, ruling the gateway out",
                service="email-gateway",
                signal="error_rate_pct",
            ),
        ),
        "Role: logs investigator": investigator_json(
            make_finding(
                "TemplateRenderError for placeholder 'patient_name' appears at 09:42:38, "
                "two minutes after the rich_templates flag was enabled at 09:40:12",
                timestamp="2026-06-15T09:42:38Z",
                service="notifications-service",
            ),
            make_finding(
                "flag disabled by on-call at 10:20:05; the next send batch at 10:24:41 "
                "succeeded on the legacy template path",
                timestamp="2026-06-15T10:20:05Z",
                service="notifications-service",
            ),
        ),
        "Role: trace investigator": investigator_json(
            make_finding(
                "failing sends error inside render_template in ~10ms; the email-gateway "
                "deliver span is absent from failing traces - failures never reach the gateway",
                timestamp="2026-06-15T09:58:42Z",
                service="notifications-service",
                signal="duration_ms",
                value=9,
            )
        ),
        "Role: deploy correlation": investigator_json(
            make_finding(
                "feature_flag rich_templates@on at 09:40 precedes first errors by ~2 minutes; "
                "flag@off at 10:20 precedes recovery by ~5 minutes - alignment in both directions",
                timestamp="2026-06-15T09:40:00Z",
                service="notifications-service",
            ),
            make_finding(
                "the 06-13 deploy shipped the rendering engine dormant behind the flag, "
                "so the code change predates the incident but its activation does not",
                timestamp="2026-06-13T11:05:00Z",
                service="notifications-service",
            ),
        ),
        "Role: runbook investigator": investigator_json(
            make_finding(
                "runbook failure mode 'template rendering errors after template or flag "
                "changes' matches: TemplateRenderError with flat latency",
                service="notifications-service",
            ),
            gaps=["dead-letter queue depth limit before notification delay becomes user-visible"],
        ),
        "Role: hypothesis ranker": scripted_ranker(
            title="rich_templates flag enabled templates with unprovided placeholders",
            statement=(
                "Enabling rich_templates at 09:40 activated templates referencing "
                "placeholders the send context does not provide, failing renders fast; "
                "disabling the flag at 10:20 restored sends, consistent with the flag "
                "being the trigger."
            ),
            timing_justification=(
                "flag on 09:40 -> errors 09:42; flag off 10:20 -> recovery 10:25"
            ),
            checks=[
                "diff welcome_v2/reminder_v2 placeholders against the send context fields",
                "confirm dead-letter retry drain completed without duplicate sends",
            ],
        ),
        "Role: safety critic": CRITIC_PASS_JSON,
        "Role: remediation planner": scripted_planner(
            {
                "kind": "mitigation",
                "title": "Consider a guarded re-enable process for rich_templates",
                "link_mitigation": True,
                "preconditions": [
                    "operator flag revert at 10:20 is holding (error rate at baseline)"
                ],
                "steps": [
                    {
                        "kind": "read_only",
                        "action": "diff template placeholders against send-context fields",
                        "verification": "every placeholder resolves against the context schema",
                    },
                    {
                        "kind": "state_changing",
                        "action": "drain the dead-letter queue in business-hours batches",
                        "verification": (
                            "queue depth decreases batch by batch with no duplicate-send reports"
                        ),
                    },
                ],
                "abort_conditions": ["duplicate notifications reported during the drain"],
                "owner_role": "messaging team on-call",
            }
        ),
        "Role: reporter": ReporterResponse(
            mitigation_options=[
                MitigationDraft(
                    action=(
                        "Keep rich_templates disabled until template placeholders are "
                        "reconciled with the send context"
                    ),
                    rationale="the flag flip is the aligned trigger; legacy path is healthy",
                    risks=["rich template features unavailable until fixed"],
                ),
                MitigationDraft(
                    action="Drain the dead-letter queue in batches during business hours",
                    rationale="runbook documents batch drain to avoid duplicate-send spikes",
                    risks=["notifications delivered late", "duplicate sends if drained too fast"],
                ),
            ],
            internal_update=(
                "SEV-3, recovered: notification sends failed at up to 9% between 09:42 "
                "and 10:20 due to template rendering errors after the rich_templates "
                "flag was enabled; on-call disabled the flag and sends recovered. "
                "1,204 notifications are queued for retry. No remediation beyond the "
                "already-performed operator flag revert has been executed; queue-drain "
                "options await human approval."
            ),
            jira_ticket=JiraDraft(
                summary="Reconcile notification templates with send context after flag incident",
                description=(
                    "Notification send error rate peaked at 9.1% between 09:42Z and "
                    "10:20Z (recovered in-window after the operator reverted "
                    "rich_templates). Leading hypothesis (high confidence): templates "
                    "referenced placeholders absent from the send context. 1,204 "
                    "notifications queued for retry. Affected: notifications-service."
                ),
                labels=["incident", "notifications"],
            ),
            slack_update=SlackDraft(
                text=(
                    "SEV-3, recovered: notification sends failed at up to 9% for ~40 "
                    "minutes after rich_templates was enabled; on-call reverted the "
                    "flag and sends recovered. 1,204 notifications queued for retry. "
                    "No remediation has been executed by the tool; queue-drain "
                    "options await human approval."
                )
            ),
            status_page=StatusPageResponseDraft(
                phase="monitoring",
                text=(
                    "Some notification emails were delayed earlier today. Delivery "
                    "has returned to normal and delayed messages are being retried. "
                    "We are monitoring to confirm full recovery."
                ),
            ),
            postmortem_title="Postmortem draft: notification send failures 2026-06-15",
            postmortem_summary=(
                "Enabling the rich_templates flag activated templates that referenced "
                "placeholders the send context did not provide; renders failed fast at "
                "up to 9.1% of sends until the flag was reverted 40 minutes later."
            ),
            postmortem_impact=(
                "~40 minutes of degraded notification delivery; 1,204 notifications "
                "delayed into retry; no booking impact."
            ),
            contributing_factors=[
                "likely: templates and send-context fields were not validated together "
                "before the flag was enabled",
            ],
            open_questions=["why template validation did not run against production contexts"],
            action_items=[
                "diff template placeholders against send-context fields before re-enabling",
                "add a render smoke test to the flag-enable checklist",
            ],
            gaps=[],
            reasoning="drafted from the reviewed flag-rollback narrative",
        ).model_dump_json(),
    }


def dependency_timeout_script() -> dict[str, ScriptEntry]:
    return {
        "Role: triage": TriageResponse(
            severity_level="SEV-2",
            severity_explanation=(
                "checkout error rate 6.1% (>1% on a revenue-critical flow) with p95 "
                "latency pinned near the 5000ms client timeout, ongoing at window end"
            ),
            severity_confidence="high",
            what_happened=(
                "Checkout latency saturated near the tax-api client timeout and error "
                "rate rose to ~6% starting 15:53; the incident is ongoing."
            ),
            affected_services=["payments-service", "tax-api"],
            customer_impact="A meaningful share of checkouts fail or take over 5 seconds.",
            gaps=["no direct telemetry from inside the vendor's infrastructure"],
            reasoning="applied documented severity rules to the revenue-path degradation",
        ).model_dump_json(),
        "Role: metrics investigator": investigator_json(
            make_finding(
                "payments p95 rose 380ms -> 5400ms and plateaued at the 5000ms client "
                "timeout ceiling, first crossing 2x baseline at 15:55",
                timestamp="2026-06-20T15:55:00Z",
                service="payments-service",
                signal="p95_latency_ms",
                value=980,
            ),
            make_finding(
                "tax-api p95 pinned at exactly 5000 from 16:05 onward - a flat ceiling "
                "characteristic of client-side timeout truncation, not load",
                timestamp="2026-06-20T16:05:00Z",
                service="tax-api",
                signal="p95_latency_ms",
                value=5000,
            ),
            make_finding(
                "checkout-db CPU stayed at baseline (41-46%), ruling out the database",
                service="checkout-db",
                signal="cpu_pct",
            ),
        ),
        "Role: logs investigator": investigator_json(
            make_finding(
                "first slow tax-api warning at 15:53:41 (1840ms vs ~90ms p50), "
                "escalating to 5000ms TimeoutErrors by 15:57",
                timestamp="2026-06-20T15:53:41Z",
                service="payments-service",
            ),
            make_finding(
                "circuit breaker for tax-api opened at 16:08:55 after 12 consecutive "
                "timeouts and never closed; probes keep timing out",
                timestamp="2026-06-20T16:08:55Z",
                service="payments-service",
            ),
            make_finding(
                "card authorization path reported healthy at 16:29 (charge_card p95 182ms)",
                timestamp="2026-06-20T16:29:52Z",
                service="payments-service",
            ),
        ),
        "Role: trace investigator": investigator_json(
            make_finding(
                "quote_tax accounts for 5001ms of the 5600ms failing checkout root; "
                "charge_card stays at 175ms - degradation isolated to the tax dependency",
                timestamp="2026-06-20T16:07:12Z",
                service="tax-api",
                signal="duration_ms",
                value=5001,
            )
        ),
        "Role: deploy correlation": investigator_json(
            make_finding(
                "the only change in the package is a routine payments-service deploy on "
                "06-15, five days before onset with no checkout-path changes - timing "
                "misaligned, internal change ruled out as the trigger",
                timestamp="2026-06-15T09:30:00Z",
                service="payments-service",
            )
        ),
        "Role: runbook investigator": investigator_json(
            make_finding(
                "runbook failure mode 'tax-api degradation (third party)' matches: spans "
                "pinned at the 5000ms timeout, circuit breaker opening, healthy card path",
                service="payments-service",
            ),
            make_finding(
                "runbook explicitly warns against harder retries during vendor timeouts "
                "(retry amplification)",
                service="payments-service",
            ),
            gaps=["vendor status page state at incident time is not in the package"],
        ),
        "Role: hypothesis ranker": scripted_ranker(
            title="Third-party tax-api degradation saturating checkout",
            statement=(
                "tax-api stopped answering within the 5000ms client timeout from ~15:53, "
                "pinning checkout latency at the timeout ceiling and failing ~6% of "
                "checkouts; no internal change aligns with onset, consistent with a "
                "vendor-side degradation."
            ),
            timing_justification=(
                "tax-api slowness 15:53 immediately precedes checkout degradation 15:55; "
                "the only internal change is five days old"
            ),
            checks=[
                "check the tax-api vendor status page and error rates from a second region",
                "verify quote_tax durations are pinned at the client timeout rather than spread",
            ],
        ),
        "Role: safety critic": CRITIC_PASS_JSON,
        "Role: remediation planner": scripted_planner(
            {
                "kind": "mitigation",
                "title": "Consider enabling the cached_tax_rates fallback",
                "link_mitigation": True,
                "preconditions": [
                    "finance sign-off for cached rates (documented, up to 24h staleness)",
                    "cached rates dataset is fresher than 24 hours",
                ],
                "steps": [
                    {
                        "kind": "read_only",
                        "action": "check the tax-api vendor status page and open a P1 with them",
                        "verification": "vendor case id recorded in the incident channel",
                    },
                    {
                        "kind": "state_changing",
                        "action": "enable the cached_tax_rates fallback flag",
                        "verification": (
                            "checkout error rate falls below 1% and p95 leaves the "
                            "5000ms timeout ceiling within 10 minutes"
                        ),
                    },
                    {
                        "kind": "state_changing",
                        "action": "reduce the tax-api client timeout to 1500ms",
                        "verification": "no new checkout failures attributable to the timeout",
                    },
                ],
                "abort_conditions": [
                    "checkout error rate rises after the flag change",
                    "finance flags a rate-accuracy problem",
                ],
                "owner_role": "payments on-call",
            }
        ),
        "Role: reporter": ReporterResponse(
            mitigation_options=[
                MitigationDraft(
                    action="Enable the cached_tax_rates fallback flag",
                    rationale=(
                        "runbook documents it as verified safe for up to 24h staleness "
                        "with finance sign-off; bypasses the degraded vendor"
                    ),
                    risks=["tax rates up to 24h stale", "finance must be notified"],
                ),
                MitigationDraft(
                    action=(
                        "Reduce the tax-api client timeout to 1500ms only together with "
                        "the cached-rates fallback"
                    ),
                    rationale="fail fast instead of holding checkouts at 5000ms",
                    risks=["without the fallback this only makes checkouts fail faster"],
                ),
            ],
            internal_update=(
                "SEV-2, ongoing: checkout is degraded - about 6% of checkouts fail and "
                "p95 is pinned near 5 seconds. Evidence points at the third-party "
                "tax-api timing out (high confidence); database and card paths are "
                "healthy and no recent internal change aligns. Next: vendor status "
                "check and a second-region probe. No remediation has been executed; "
                "the cached-tax-rates fallback awaits human approval."
            ),
            jira_ticket=JiraDraft(
                summary="Track tax-api degradation impact on checkout and vendor follow-up",
                description=(
                    "Checkout error rate reached 6.1% with p95 pinned at the 5000ms "
                    "tax-api client timeout from 15:53Z (ongoing). Leading hypothesis "
                    "(high confidence): third-party tax-api degradation - internal "
                    "database and card paths healthy, no aligned internal change in "
                    "7 days. Affected: payments-service (checkout flow)."
                ),
                labels=["incident", "checkout", "vendor"],
            ),
            slack_update=SlackDraft(
                text=(
                    "SEV-2, ongoing: ~6% of checkouts fail and p95 is pinned near 5s. "
                    "Evidence points at the third-party tax vendor (high confidence); "
                    "internal paths are healthy. Next: vendor status check + "
                    "second-region probe. No remediation has been executed; the "
                    "cached-rates fallback awaits human approval."
                )
            ),
            status_page=StatusPageResponseDraft(
                phase="identified",
                text=(
                    "Some customers may be unable to complete checkout, or may see "
                    "checkout take longer than usual. We have identified the issue "
                    "and are working to restore normal service. Updates will be "
                    "posted here."
                ),
            ),
            postmortem_title="Postmortem draft: checkout degradation via tax-api 2026-06-20",
            postmortem_summary=(
                "The third-party tax-api stopped responding within our 5000ms timeout; "
                "checkout latency saturated at the timeout and ~6% of checkouts failed. "
                "The circuit breaker opened and remained open through the window."
            ),
            postmortem_impact=(
                "Ongoing at window end: checkout p95 5400ms (baseline 380ms), error "
                "rate 6.1% (baseline 0.4%) - direct revenue impact."
            ),
            contributing_factors=[
                "likely: vendor-side degradation of tax-api",
                "possibly: 5000ms timeout too generous for a checkout-path dependency, "
                "amplifying user-visible latency",
            ],
            open_questions=[
                "vendor root cause and timeline",
                "why no cached-rates fallback was enabled automatically",
            ],
            action_items=[
                "open a P1 with the tax-api vendor with trace exemplars",
                "evaluate automatic fallback to cached tax rates on circuit-open",
            ],
            gaps=[],
            reasoning="drafted from the reviewed third-party-degradation narrative",
        ).model_dump_json(),
    }


def collected_demo_script() -> dict[str, ScriptEntry]:
    """The collected_demo example is the booking scenario as gathered by the
    collect CLI from the stub sources (no traces source exists in v2), so the
    latency_spike responses fit; the trace-investigator entry simply goes
    unused because that investigator is skipped for this package."""
    return latency_spike_script()


# ---------------------------------------------------------------------------
# Adversarial evaluation corpus (epic #50): each script encodes the CORRECT
# handling of a scenario built to mislead. The rubrics in
# scripts/eval_corpus.py assert the resulting reports hold the line.
# ---------------------------------------------------------------------------


def red_herring_deploy_script() -> dict[str, ScriptEntry]:
    return {
        "Role: triage": TriageResponse(
            severity_level="SEV-2",
            severity_explanation=(
                "reports-service p95 reached 2400ms against a 300ms baseline, 8x, "
                "inside the >4x-latency SEV-2 band; no error-rate series is present "
                "to justify SEV-1"
            ),
            severity_confidence="high",
            what_happened=(
                "Report generation latency rose from about 10:30 and saturated above "
                "2 seconds; the incident is ongoing."
            ),
            affected_services=["reports-service", "reports-db"],
            customer_impact="Report pages load slowly or time out.",
            gaps=[],
            reasoning="applied the documented severity bands to the observed latency",
        ).model_dump_json(),
        "Role: metrics investigator": investigator_json(
            make_finding(
                "reports-service p95 first crossed 2x baseline at 10:40 (780ms)",
                timestamp="2026-06-10T10:40:00Z",
                service="reports-service",
                signal="p95_latency_ms",
                value=780,
            ),
            make_finding(
                "reports-db io_wait first deviated at 10:30 (18ms vs 5ms baseline) - "
                "BEFORE any change in the window",
                timestamp="2026-06-10T10:30:00Z",
                service="reports-db",
                signal="io_wait_ms",
                value=18,
            ),
        ),
        "Role: logs investigator": investigator_json(
            make_finding(
                "reports-db disk I/O latency warning at 10:31 precedes all application symptoms",
                timestamp="2026-06-10T10:31:12Z",
                service="reports-db",
            ),
            make_finding(
                "slow aggregation queries escalate 2100ms -> 3900ms between 10:41 and 10:52",
                timestamp="2026-06-10T10:52:07Z",
                service="reports-db",
            ),
        ),
        "Role: deploy correlation": investigator_json(
            make_finding(
                "deploy 2026.06.10-1058 (footer copy, config-only) landed 10:58 - "
                "28 minutes AFTER the io_wait deviation began at 10:30; timing rules "
                "it out as the trigger",
                timestamp="2026-06-10T10:58:00Z",
                service="reports-service",
            )
        ),
        "Role: hypothesis ranker": scripted_hypotheses(
            {
                "title": "reports-db disk I/O degradation slowing aggregation queries",
                "statement": (
                    "reports-db io_wait rose from 10:30, slow queries followed, and "
                    "reports-service latency saturated waiting on the database - "
                    "consistent with storage-level degradation on reports-db."
                ),
                "support": [("metrics", 1), ("logs", 0)],
                "timing_alignment": "aligned",
                "timing_justification": (
                    "io_wait deviation 10:30 precedes slow queries 10:41 and app "
                    "latency crossing 2x at 10:40"
                ),
                "checks": [
                    "check reports-db host SMART/disk metrics and cloud volume events",
                    "compare query plans against last week's for the same aggregation",
                ],
            },
            {
                "title": "Deploy 2026.06.10-1058 caused the latency increase",
                "statement": (
                    "A deploy landed inside the window, but symptoms began 28 minutes "
                    "before it and the change is a static footer text update - the "
                    "timing and content both argue against it."
                ),
                "support": [("deploys", 0)],
                "timing_alignment": "misaligned",
                "timing_justification": (
                    "io_wait deviation began 10:30; the deploy landed 10:58, after "
                    "onset - a cause cannot postdate its effect"
                ),
                "checks": ["confirm the deploy diff touches no query or pool code"],
            },
        ),
        "Role: safety critic": CRITIC_PASS_JSON,
        "Role: remediation planner": EMPTY_PLANNER_JSON,
        "Role: reporter": basic_reporter(
            internal_update=(
                "SEV-2, ongoing: report generation degraded. Leading hypothesis "
                "(medium confidence): disk I/O degradation on reports-db - its "
                "io_wait rose 28 minutes before the only deploy in the window, "
                "which is a config-only footer change ranked low. No remediation "
                "has been executed; options await human approval."
            ),
            postmortem_title="Postmortem draft: reports latency 2026-06-10",
            postmortem_summary=(
                "reports-db storage latency degraded from 10:30; aggregation "
                "queries slowed and report generation saturated above 2s."
            ),
            postmortem_impact="Report pages degraded ~50 minutes and ongoing.",
            contributing_factors=[
                "likely: storage-level I/O degradation on reports-db",
                "ruled unlikely: the 10:58 config-only deploy (postdates onset)",
            ],
            open_questions=["what degraded the volume - hardware, noisy neighbor?"],
            action_items=["add io_wait alerting ahead of query latency"],
        ),
    }


def conflicting_metrics_script() -> dict[str, ScriptEntry]:
    return {
        "Role: triage": TriageResponse(
            severity_level="SEV-2",
            severity_explanation=(
                "checkout error rate 3.8% against a 0.4% baseline, inside the 1-25% "
                "SEV-2 band on a revenue-critical flow"
            ),
            severity_confidence="high",
            what_happened=(
                "Checkout errors rose from 09:00; the session cache hit rate "
                "collapsed while the payment gateway stayed at baseline."
            ),
            affected_services=["checkout-service", "session-cache"],
            customer_impact="A few percent of checkouts fail on first attempt.",
            gaps=[],
            reasoning="applied the documented severity bands to observed error rate",
        ).model_dump_json(),
        "Role: metrics investigator": investigator_json(
            make_finding(
                "checkout-service error rate first crossed 2x baseline at 09:00 (1.8%)",
                timestamp="2026-06-12T09:00:00Z",
                service="checkout-service",
                signal="error_rate_pct",
                value=1.8,
            ),
            make_finding(
                "payment-gateway p95 stayed at baseline (117-125ms) through the whole "
                "window - CONFLICTS with any gateway-outage explanation",
                service="payment-gateway",
                signal="p95_latency_ms",
            ),
            make_finding(
                "session-cache hit rate collapsed 96% -> 44% starting 09:00",
                timestamp="2026-06-12T09:00:00Z",
                service="session-cache",
                signal="hit_rate_pct",
                value=44,
            ),
        ),
        "Role: logs investigator": investigator_json(
            make_finding(
                "session-cache eviction storm (1200-1400 keys/s) begins 08:59, "
                "checkout session misses and cold-rebuild timeouts follow",
                timestamp="2026-06-12T08:59:41Z",
                service="session-cache",
            )
        ),
        "Role: deploy correlation": investigator_json(
            make_finding(
                "deploys.json is an empty list: the change window was checked and "
                "nothing shipped - internal change ruled out as trigger",
                service="checkout-service",
            )
        ),
        "Role: hypothesis ranker": scripted_hypotheses(
            {
                "title": "session-cache eviction storm forcing cold cart rebuilds",
                "statement": (
                    "Cache evictions from 08:59 collapsed the hit rate; checkout "
                    "rebuilt carts from cold state and timed out, consistent with "
                    "the 500s. The flat payment-gateway series conflicts with any "
                    "broader payment-path outage."
                ),
                "support": [("metrics", 2), ("logs", 0)],
                "conflict": [("metrics", 1)],
                "timing_alignment": "aligned",
                "timing_justification": (
                    "evictions 08:59 precede the first error-rate deviation 09:00"
                ),
                "assumptions": [
                    "cart rebuild is the dominant source of the 500s",
                    "cache memory pressure is not itself caused by a traffic surge",
                ],
                "checks": [
                    "inspect session-cache memory usage and key-size distribution",
                    "sample 500 responses to confirm cold-rebuild timeouts dominate",
                ],
            }
        ),
        "Role: safety critic": CRITIC_PASS_JSON,
        "Role: remediation planner": EMPTY_PLANNER_JSON,
        "Role: reporter": basic_reporter(
            internal_update=(
                "SEV-2, ongoing: ~4% of checkouts fail. Leading hypothesis (medium "
                "confidence): session-cache eviction storm forcing cold cart "
                "rebuilds; the flat payment-gateway series conflicts with a wider "
                "payment outage, which caps confidence. No remediation has been "
                "executed; options await human approval."
            ),
            postmortem_title="Postmortem draft: checkout errors 2026-06-12",
            postmortem_summary=(
                "Session-cache evictions collapsed the hit rate; checkout cold "
                "rebuilds timed out for a few percent of carts."
            ),
            postmortem_impact="~3.5% checkout failures for 20+ minutes, ongoing.",
            contributing_factors=[
                "likely: cache memory pressure and eviction storm",
                "conflicting signal: payment gateway healthy throughout",
            ],
            open_questions=["what filled the cache - key growth or memory shrink?"],
            action_items=["alert on eviction rate before hit-rate collapse"],
        ),
    }


def missing_baselines_script() -> dict[str, ScriptEntry]:
    return {
        "Role: triage": TriageResponse(
            severity_level="SEV-3",
            severity_explanation=(
                "login failures are confirmed by logs but no metrics are in the "
                "package, so failure rate and user share cannot be quantified - "
                "SEV-3 as limited-degradation floor, low confidence"
            ),
            severity_confidence="low",
            what_happened=(
                "Logins fail intermittently on SSO token-exchange timeouts from about 07:18."
            ),
            affected_services=["auth-service"],
            customer_impact="Some users cannot log in on first attempt.",
            gaps=["no metrics in the package: failure rate unquantifiable"],
            reasoning="logs alone cannot support a stronger severity claim",
        ).model_dump_json(),
        "Role: logs investigator": investigator_json(
            make_finding(
                "SSO token exchange degrades from slow (1900ms, 07:18) to timeouts "
                "(3000ms) causing login failures",
                timestamp="2026-06-15T07:25:41Z",
                service="auth-service",
            )
        ),
        "Role: deploy correlation": investigator_json(
            make_finding(
                "deploys.json is an empty list: change window checked, nothing "
                "shipped - internal change ruled out",
                service="auth-service",
            )
        ),
        "Role: hypothesis ranker": scripted_hypotheses(
            {
                "title": "sso-provider degradation timing out token exchanges",
                "statement": (
                    "Log timeouts against the external SSO provider are consistent "
                    "with upstream degradation, but with no metrics the scale and "
                    "onset cannot be corroborated - single-source evidence only."
                ),
                "support": [("logs", 0)],
                "timing_alignment": "unknown",
                "timing_justification": (
                    "no metric series exists to anchor onset; first log symptom 07:18"
                ),
                "assumptions": ["log sampling did not hide earlier symptoms"],
                "checks": [
                    "check the SSO provider status page and error rates from a probe",
                    "pull auth-service success-rate metrics from the source system",
                ],
            },
            gaps=["metrics absent: deviation-based corroboration impossible"],
        ),
        "Role: safety critic": CRITIC_PASS_JSON,
        "Role: remediation planner": EMPTY_PLANNER_JSON,
        "Role: reporter": basic_reporter(
            internal_update=(
                "SEV-3 (low confidence, data-poor): intermittent login failures on "
                "SSO timeouts. Single-source evidence; metrics are missing from the "
                "package so scale is unknown. No remediation has been executed."
            ),
            postmortem_title="Postmortem draft: login failures 2026-06-15",
            postmortem_summary=(
                "Intermittent SSO token-exchange timeouts caused login failures; "
                "scale unquantified due to missing metrics."
            ),
            postmortem_impact="Unknown share of logins affected (no metrics).",
            contributing_factors=["possibly: sso-provider upstream degradation"],
            open_questions=["actual failure rate; SSO provider incident status"],
            action_items=["include auth metrics in future packages"],
            gaps=["impact quantification impossible without metrics"],
        ),
    }


def cascade_victim_alert_script() -> dict[str, ScriptEntry]:
    return {
        "Role: triage": TriageResponse(
            severity_level="SEV-3",
            severity_explanation=(
                "notification delivery lags by minutes but delivery retries exist "
                "and no booking-path service is degraded; no error-rate or latency "
                "series is in the package to place this in a numeric band"
            ),
            severity_confidence="medium",
            what_happened=(
                "session-store began mass-evicting at 13:00; broker consumers lost "
                "sessions and rebalanced; worker lag grew from 13:15. The alerting "
                "service is the end of the chain, not its start."
            ),
            affected_services=["session-store", "message-broker", "notifications-worker"],
            customer_impact="Notifications arrive minutes late.",
            gaps=[],
            reasoning="impact limited to delayed notifications with retries in place",
        ).model_dump_json(),
        "Role: metrics investigator": investigator_json(
            make_finding(
                "notifications-worker lag first deviated at 13:15 (22s vs 4s baseline)",
                timestamp="2026-06-18T13:15:00Z",
                service="notifications-worker",
                signal="consumer_lag_seconds",
                value=22,
            ),
            make_finding(
                "message-broker queue depth first deviated at 13:05 (480 vs 150)",
                timestamp="2026-06-18T13:05:00Z",
                service="message-broker",
                signal="queue_depth_msgs",
                value=480,
            ),
            make_finding(
                "session-store evictions first deviated at 13:00 (420/s vs 2/s) - "
                "the EARLIEST deviation, two topology hops from the alerting service",
                timestamp="2026-06-18T13:00:00Z",
                service="session-store",
                signal="evictions_per_sec",
                value=420,
            ),
        ),
        "Role: logs investigator": investigator_json(
            make_finding(
                "eviction warning 13:01 -> broker session-lookup failures 13:07 -> "
                "worker rejoin errors 13:16: the log chain follows the topology",
                timestamp="2026-06-18T13:01:09Z",
                service="session-store",
            )
        ),
        "Role: deploy correlation": investigator_json(
            make_finding(
                "deploys.json empty: nothing shipped in the change window; "
                "capacity/state cause more plausible than change-driven",
                service="notifications-worker",
            )
        ),
        "Role: runbook investigator": investigator_json(
            make_finding(
                "runbook documents this exact failure mode: 'session-store eviction "
                "cascade' - worker lag is named as the symptom, the store the cause; "
                "scaling workers during a rebalance storm is explicitly warned against",
                service="notifications-worker",
            )
        ),
        "Role: hypothesis ranker": scripted_hypotheses(
            {
                "title": "session-store eviction cascade through broker to workers",
                "statement": (
                    "session-store evictions (13:00) broke broker consumer sessions "
                    "(13:07) causing worker rebalance churn and lag (13:15) - the "
                    "deviation order matches the topology chain and the runbook's "
                    "documented cascade."
                ),
                "support": [("metrics", 2), ("logs", 0), ("runbook", 0)],
                "timing_alignment": "aligned",
                "timing_justification": (
                    "deviations ordered store 13:00 -> broker 13:05 -> worker 13:15, "
                    "matching dependency direction"
                ),
                "checks": [
                    "confirm session-store memory limit and what grew into it",
                    "verify consumer session TTLs against eviction age",
                ],
            }
        ),
        "Role: safety critic": CRITIC_PASS_JSON,
        "Role: remediation planner": scripted_planner(
            {
                "kind": "mitigation",
                "title": "Consider raising session-store maxmemory one step",
                "link_mitigation": True,
                "preconditions": ["runbook marks the step verified safe (keys re-derivable)"],
                "steps": [
                    {
                        "kind": "read_only",
                        "action": "confirm current maxmemory and eviction rate",
                        "verification": "store INFO shows the expected limit",
                    },
                    {
                        "kind": "state_changing",
                        "action": "raise session-store maxmemory one step per runbook",
                        "verification": (
                            "evictions fall below 100 keys/s and broker redelivery "
                            "stops growing within 10 minutes"
                        ),
                    },
                ],
                "abort_conditions": ["store memory saturates again within 10 minutes of the raise"],
                "owner_role": "platform-cache on-call",
            }
        ),
        "Role: reporter": basic_reporter(
            internal_update=(
                "SEV-3, ongoing: notifications delayed minutes. Leading hypothesis "
                "(high confidence): session-store eviction cascade - deviations "
                "follow the topology chain store->broker->worker and match the "
                "runbook's documented failure mode. Do NOT scale workers (runbook: "
                "worsens rebalance churn). No remediation has been executed; the "
                "maxmemory raise awaits human approval."
            ),
            postmortem_title="Postmortem draft: notification lag 2026-06-18",
            postmortem_summary=(
                "session-store memory pressure caused mass evictions; broker "
                "consumers lost sessions and worker throughput collapsed into "
                "rebalance churn."
            ),
            postmortem_impact="Notification delivery delayed up to 4 minutes.",
            contributing_factors=[
                "likely: session-store sized below current session volume",
            ],
            open_questions=["what grew memory usage to the limit"],
            action_items=["alert on eviction rate; review store sizing"],
            mitigations=[
                MitigationDraft(
                    action="Raise session-store maxmemory one step per the runbook",
                    rationale="runbook-verified safe; keys are re-derivable",
                    risks=["higher memory footprint on the cache host"],
                )
            ],
            status_page=StatusPageResponseDraft(
                phase="identified",
                text=(
                    "Some notifications are currently delayed by a few minutes. We "
                    "have identified the cause and a fix is being prepared. No "
                    "notifications are lost; delayed messages will be delivered."
                ),
            ),
        ),
    }


def insufficient_evidence_script() -> dict[str, ScriptEntry]:
    return {
        "Role: triage": TriageResponse(
            severity_level="SEV-4",
            severity_explanation=(
                "six edge 502s over 17 minutes with no metrics, no upstream detail, "
                "and no established user impact - warning-level signal only"
            ),
            severity_confidence="low",
            what_happened="Sporadic 502s at the payments-web edge; nothing localizes them.",
            affected_services=["payments-web"],
            customer_impact="Not established; possibly a handful of failed page loads.",
            gaps=[
                "no metrics, traces, or deploy records in the package",
                "502 lines carry no upstream identity",
            ],
            reasoning="a signal this thin cannot support impact claims",
        ).model_dump_json(),
        "Role: logs investigator": investigator_json(
            make_finding(
                "six identical '502 upstream prematurely closed' lines 02:58-03:15 "
                "with no upstream name, no request ids, no pattern change",
                timestamp="2026-06-21T02:58:03Z",
                service="payments-web",
            ),
            gaps=["edge logs lack upstream identity; cannot localize the closer"],
        ),
        "Role: hypothesis ranker": RankerResponse(
            hypotheses=[],
            gaps=[
                "no hypothesis is honest on this evidence: six identical edge 502s "
                "with no metrics, traces, or change records cannot distinguish "
                "upstream restarts, LB timeouts, or network resets",
            ],
            reasoning=(
                "declining to rank: any hypothesis would be speculation; the "
                "correct output is the data needed to form one"
            ),
        ).model_dump_json(),
        "Role: safety critic": CRITIC_PASS_JSON,
        "Role: remediation planner": EMPTY_PLANNER_JSON,
        "Role: reporter": basic_reporter(
            internal_update=(
                "SEV-4 signal, insufficient evidence: sporadic 502s at the "
                "payments-web edge. The package cannot localize a cause - no "
                "hypothesis is proposed. Needed next: payments-api logs/metrics "
                "for the window and LB target health history. No remediation has "
                "been executed."
            ),
            postmortem_title="Postmortem draft: sporadic 502s 2026-06-21 (data-poor)",
            postmortem_summary=(
                "Six 502s at the edge over 17 minutes; the investigation could not "
                "establish a cause from the available data."
            ),
            postmortem_impact="Unknown; at most a handful of failed requests.",
            contributing_factors=[],
            open_questions=[
                "which upstream closed connections",
                "whether payments-api restarted or redeployed",
            ],
            action_items=[
                "collect payments-api telemetry for the window",
                "add upstream identity to edge error logs",
            ],
            gaps=["cause not established - insufficient evidence"],
        ),
    }


def operator_already_mitigated_script() -> dict[str, ScriptEntry]:
    return {
        "Role: triage": TriageResponse(
            severity_level="SEV-3",
            severity_explanation=(
                "p95 peaked at 940ms (5.2x baseline, inside the >4x SEV-2 band) and "
                "error rate 1.1% (inside 1-25%), but recovery was observed in-window "
                "after the operator scale-up and impact was brief - judged one level "
                "below the numeric ceiling"
            ),
            severity_confidence="high",
            what_happened=(
                "Search latency saturated from 16:05 under queue pressure; the "
                "operator scaled replicas 3->6 at 16:32 and metrics recovered by "
                "16:45."
            ),
            affected_services=["search-service"],
            customer_impact="Search was slow or timed out for ~40 minutes; recovered.",
            gaps=[],
            reasoning="numeric bands support SEV-2; brevity and recovery justify SEV-3",
        ).model_dump_json(),
        "Role: metrics investigator": investigator_json(
            make_finding(
                "search p95 first crossed 2x baseline at 16:05 (520ms) and recovered "
                "to within 10% of baseline from 16:45",
                timestamp="2026-06-25T16:05:00Z",
                service="search-service",
                signal="p95_latency_ms",
                value=520,
            ),
            make_finding(
                "recovery begins the interval after the 16:32 scale-up: 880ms at "
                "16:30 -> 430ms at 16:35 -> baseline by 16:45",
                timestamp="2026-06-25T16:35:00Z",
                service="search-service",
                signal="p95_latency_ms",
                value=430,
            ),
        ),
        "Role: logs investigator": investigator_json(
            make_finding(
                "queue saturation warnings precede timeouts; the observed scaling "
                "event at 16:33 is followed by 'queue drained' at 16:44",
                timestamp="2026-06-25T16:33:02Z",
                service="search-service",
            )
        ),
        "Role: deploy correlation": investigator_json(
            make_finding(
                "change record change_om_1 16:32: operator scaled replicas 3->6 "
                "during response - a mitigation record, not a candidate cause "
                "(symptoms began 16:05, before it)",
                timestamp="2026-06-25T16:32:00Z",
                service="search-service",
            )
        ),
        "Role: hypothesis ranker": scripted_hypotheses(
            {
                "title": "capacity saturation at 3 replicas, resolved by operator scale-up",
                "statement": (
                    "Query load saturated the 8-worker pool at 3 replicas from "
                    "16:05; the operator's 16:32 scale-up to 6 replicas was followed "
                    "by drain and recovery by 16:45 - consistent with a pure "
                    "capacity shortfall."
                ),
                "support": [("metrics", 0), ("logs", 0), ("deploys", 0)],
                "timing_alignment": "aligned",
                "timing_justification": (
                    "saturation 16:05 precedes the scale-up 16:32; recovery follows "
                    "within one interval - response, not cause"
                ),
                "assumptions": ["query volume did not drop independently at 16:35"],
                "checks": [
                    "compare query volume 16:00-17:00 against the previous day",
                    "confirm replica count history matches change_om_1",
                ],
            }
        ),
        "Role: safety critic": CRITIC_PASS_JSON,
        "Role: remediation planner": EMPTY_PLANNER_JSON,
        "Role: reporter": basic_reporter(
            internal_update=(
                "SEV-3, recovered: search latency saturated for ~40 minutes; the "
                "operator scaled replicas 3->6 at 16:32 (change_om_1) and metrics "
                "recovered by 16:45. The tool has executed nothing; the only action "
                "taken was the operator's, recorded as an observed change. Remaining "
                "options await human approval."
            ),
            postmortem_title="Postmortem draft: search saturation 2026-06-25",
            postmortem_summary=(
                "Search capacity at 3 replicas was insufficient for the afternoon "
                "query load; latency saturated until the operator scaled to 6 "
                "replicas, after which queues drained and metrics recovered."
            ),
            postmortem_impact="Search slow/timing out ~40 minutes; fully recovered.",
            contributing_factors=[
                "likely: replica count sized below current query volume",
            ],
            open_questions=["was the load organic growth or a one-off spike"],
            action_items=[
                "review autoscaling thresholds for search-service",
                "keep replicas at 6 pending the volume review",
            ],
            status_page=StatusPageResponseDraft(
                phase="monitoring",
                text=(
                    "Search was slow for some users earlier today. Service has "
                    "returned to normal and we are monitoring to confirm full "
                    "recovery."
                ),
            ),
        ),
    }


ScriptFactory = Callable[[], dict[str, ScriptEntry]]

SCRIPTED_INCIDENTS: dict[str, ScriptFactory] = {
    "latency_spike": latency_spike_script,
    "error_rate_spike": error_rate_spike_script,
    "dependency_timeout": dependency_timeout_script,
    "collected_demo": collected_demo_script,
    "red_herring_deploy": red_herring_deploy_script,
    "conflicting_metrics": conflicting_metrics_script,
    "missing_baselines": missing_baselines_script,
    "cascade_victim_alert": cascade_victim_alert_script,
    "insufficient_evidence": insufficient_evidence_script,
    "operator_already_mitigated": operator_already_mitigated_script,
}


def script_for(incident_id: str) -> dict[str, ScriptEntry]:
    return SCRIPTED_INCIDENTS[incident_id]()

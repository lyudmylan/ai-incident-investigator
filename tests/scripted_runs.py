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
    MitigationDraft,
    RankerResponse,
    ReporterResponse,
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


ScriptFactory = Callable[[], dict[str, ScriptEntry]]

SCRIPTED_INCIDENTS: dict[str, ScriptFactory] = {
    "latency_spike": latency_spike_script,
    "error_rate_spike": error_rate_spike_script,
    "dependency_timeout": dependency_timeout_script,
    "collected_demo": collected_demo_script,
}


def script_for(incident_id: str) -> dict[str, ScriptEntry]:
    return SCRIPTED_INCIDENTS[incident_id]()

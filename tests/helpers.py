"""Shared test helpers: the example package path, canned LLM responses, and
the scripted fake client used by agent and pipeline tests."""

from collections.abc import Callable, Mapping
from pathlib import Path

from ai_incident_investigator.agents.responses import (
    CriticCheck,
    CriticResponse,
    Finding,
    InvestigatorResponse,
    JiraDraft,
    MitigationDraft,
    PlannerResponse,
    RankerResponse,
    ReporterResponse,
    SlackDraft,
    StatusPageResponseDraft,
    TriageResponse,
)
from ai_incident_investigator.ids import stable_id
from ai_incident_investigator.llm import LLMRequest, LLMResponse
from ai_incident_investigator.models.common import Source
from ai_incident_investigator.models.report import EvidenceItem

EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "incidents" / "latency_spike"


def make_evidence(source: Source, note: str) -> EvidenceItem:
    return EvidenceItem(
        id=stable_id("evidence", source.value, note), source=source, interpretation=note
    )


def make_finding(
    interpretation: str,
    timestamp: str | None = None,
    service: str | None = None,
    signal: str | None = None,
    value: float | str | None = None,
) -> Finding:
    return Finding(
        interpretation=interpretation,
        timestamp=timestamp,
        service=service,
        signal=signal,
        value=value,
    )


def investigator_json(*findings: Finding, gaps: list[str] | None = None) -> str:
    return InvestigatorResponse(
        findings=list(findings), gaps=gaps or [], reasoning="compared data against baselines"
    ).model_dump_json()


TRIAGE_JSON = TriageResponse(
    severity_level="SEV-2",
    severity_explanation="error rate 4.8% and p95 latency 7x baseline on the booking flow",
    severity_confidence="high",
    what_happened="Appointment booking latency and errors rose sharply.",
    affected_services=["booking-service", "payment-service", "appointments-db"],
    customer_impact="Patients experience slow or failed appointment booking.",
    gaps=[],
    reasoning="applied documented severity rules to observed impact",
).model_dump_json()


ScriptEntry = str | Exception | Callable[[LLMRequest], str]


class ScriptedLLM:
    """Returns canned JSON keyed by a marker found in the system prompt.

    An entry may be a string (returned as-is), an Exception (raised), or a
    callable receiving the full request (for responses that must reference
    runtime content such as evidence ids)."""

    def __init__(self, by_marker: Mapping[str, ScriptEntry]) -> None:
        self.by_marker = by_marker
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        for marker, scripted in self.by_marker.items():
            if marker in request.system:
                if isinstance(scripted, Exception):
                    raise scripted
                body = scripted(request) if callable(scripted) else scripted
                return LLMResponse(text=body, model=request.model, stop_reason="end_turn")
        raise AssertionError(f"no scripted response for system prompt: {request.system[:80]!r}")


def default_script() -> dict[str, str | Exception]:
    """A full-graph script: every agent answers plausibly for latency_spike."""
    return {
        "Role: triage": TRIAGE_JSON,
        # Benign synthesis defaults; ranker/critic behavior has its own tests
        # in test_ranker_critic_safety.py.
        "Role: hypothesis ranker": RankerResponse(
            hypotheses=[], gaps=[], reasoning="no combination assessed in this scripted run"
        ).model_dump_json(),
        "Role: safety critic": CriticResponse(
            checks=[CriticCheck(check="overconfidence", result="pass", detail=None)],
            notes=None,
            gaps=[],
            reasoning="reviewed scripted output",
        ).model_dump_json(),
        # Benign default; real planner behavior is scripted per-example in
        # scripted_runs.py and unit-tested in test_planner.py.
        "Role: remediation planner": PlannerResponse(
            plans=[], gaps=[], reasoning="no plans structured in this scripted run"
        ).model_dump_json(),
        "Role: reporter": ReporterResponse(
            mitigation_options=[
                MitigationDraft(
                    action="Consider disabling feature flag payment_enrichment",
                    rationale="the runbook documents it as a verified safe fallback",
                    risks=["eligibility enrichment unavailable while disabled"],
                )
            ],
            internal_update=(
                "SEV-2: appointment booking degraded. No remediation has been "
                "executed; mitigation options await human approval."
            ),
            jira_ticket=JiraDraft(
                summary="Investigate booking latency degradation after the 14:20 deploy",
                description=(
                    "Booking p95 rose 450ms -> 3200ms and errors 0.3% -> 4.8% from "
                    "2026-06-01T14:25Z (window start 14:05Z, ongoing). Leading "
                    "hypothesis (medium confidence): deploy-driven eligibility retry "
                    "amplification saturating appointments-db. Affected: "
                    "booking-service, payment-service, appointments-db."
                ),
                labels=["incident", "booking"],
            ),
            slack_update=SlackDraft(
                text=(
                    "SEV-2, booking degraded: patients see slow or failed appointment "
                    "booking. Leading hypothesis (medium confidence): the 14:20 deploy "
                    "amplified eligibility retries. Checking retry bounds and pre/post "
                    "deploy error rates next. No remediation has been executed; "
                    "mitigation options await human approval."
                )
            ),
            status_page=StatusPageResponseDraft(
                phase="investigating",
                text=(
                    "Some users may currently experience slow or failed appointment "
                    "scheduling. Our team is actively investigating with high "
                    "priority. Updates will be posted here as we learn more."
                ),
            ),
            postmortem_title="Postmortem draft: booking latency 2026-06-01",
            postmortem_summary="Booking latency and errors rose after a deploy.",
            postmortem_impact="p95 rose 450ms to 3200ms; errors 0.3% to 4.8%.",
            contributing_factors=["likely: deploy-driven retry amplification"],
            open_questions=["whether retries are bounded"],
            action_items=["compare error rates before and after the deploy"],
            gaps=[],
            reasoning="drafted from reviewed investigation output",
        ).model_dump_json(),
        "Role: metrics investigator": investigator_json(
            make_finding(
                "p95 latency first crossed 2x baseline at 14:30",
                timestamp="2026-06-01T14:30:00Z",
                service="booking-service",
                signal="p95_latency_ms",
                value=900,
            ),
            make_finding(
                "notifications-service stayed at baseline throughout the window",
                service="notifications-service",
                signal="p95_latency_ms",
            ),
        ),
        "Role: logs investigator": investigator_json(
            make_finding(
                "eligibility retries escalate to exhaustion starting 14:29",
                timestamp="2026-06-01T14:29:14Z",
                service="booking-service",
            )
        ),
        "Role: trace investigator": investigator_json(
            make_finding(
                "eligibility_query dominates degraded traces (2150ms of 3180ms root)",
                timestamp="2026-06-01T14:39:05Z",
                service="appointments-db",
                signal="duration_ms",
                value=2150,
            )
        ),
        "Role: deploy correlation": investigator_json(
            make_finding(
                "booking-service deploy 2026.06.01-1420 landed 11 minutes before latency "
                "crossed 2x baseline; timing aligned",
                timestamp="2026-06-01T14:20:00Z",
                service="booking-service",
            )
        ),
        "Role: runbook investigator": investigator_json(
            make_finding(
                "runbook failure mode 'retry amplification after payment changes' matches "
                "observed retry warnings",
                service="booking-service",
            ),
            gaps=["runbook does not cover queue consumer scaling"],
        ),
    }

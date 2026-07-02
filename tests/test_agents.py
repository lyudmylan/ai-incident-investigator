import json
from pathlib import Path

import pytest

from ai_incident_investigator.agents import build_investigators
from ai_incident_investigator.agents.base import make_investigator
from ai_incident_investigator.agents.investigators import SOURCE_SPECS
from ai_incident_investigator.agents.responses import (
    Finding,
    InvestigatorResponse,
    TriageResponse,
)
from ai_incident_investigator.agents.triage import make_triage
from ai_incident_investigator.llm import LLMRequest, LLMResponse, request_key
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.models.common import Confidence, SeverityLevel, Source
from ai_incident_investigator.pipeline import initial_state, run_investigation
from ai_incident_investigator.state import InvestigationState

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "incidents" / "latency_spike"
SPECS_BY_NAME = {spec.name: spec for spec in SOURCE_SPECS}


def _finding(
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


def _investigator_json(*findings: Finding, gaps: list[str] | None = None) -> str:
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


class ScriptedLLM:
    """Returns canned JSON keyed by a marker found in the system prompt."""

    def __init__(self, by_marker: dict[str, str | Exception]) -> None:
        self.by_marker = by_marker
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        for marker, scripted in self.by_marker.items():
            if marker in request.system:
                if isinstance(scripted, Exception):
                    raise scripted
                return LLMResponse(text=scripted, model=request.model, stop_reason="end_turn")
        raise AssertionError(f"no scripted response for system prompt: {request.system[:80]!r}")


def _state() -> InvestigationState:
    return initial_state(load_package(EXAMPLE))


def _default_script() -> dict[str, str | Exception]:
    return {
        "Role: triage": TRIAGE_JSON,
        "Role: metrics investigator": _investigator_json(
            _finding(
                "p95 latency first crossed 2x baseline at 14:30",
                timestamp="2026-06-01T14:30:00Z",
                service="booking-service",
                signal="p95_latency_ms",
                value=900,
            ),
            _finding(
                "notifications-service stayed at baseline throughout the window",
                service="notifications-service",
                signal="p95_latency_ms",
            ),
        ),
        "Role: logs investigator": _investigator_json(
            _finding(
                "eligibility retries escalate to exhaustion starting 14:29",
                timestamp="2026-06-01T14:29:14Z",
                service="booking-service",
            )
        ),
        "Role: trace investigator": _investigator_json(
            _finding(
                "eligibility_query dominates degraded traces (2150ms of 3180ms root)",
                timestamp="2026-06-01T14:39:05Z",
                service="appointments-db",
                signal="duration_ms",
                value=2150,
            )
        ),
        "Role: deploy correlation": _investigator_json(
            _finding(
                "booking-service deploy 2026.06.01-1420 landed 11 minutes before latency "
                "crossed 2x baseline; timing aligned",
                timestamp="2026-06-01T14:20:00Z",
                service="booking-service",
            )
        ),
        "Role: runbook investigator": _investigator_json(
            _finding(
                "runbook failure mode 'retry amplification after payment changes' matches "
                "observed retry warnings",
                service="booking-service",
            ),
            gaps=["runbook does not cover queue consumer scaling"],
        ),
    }


def test_all_six_agents_built_for_full_package() -> None:
    agents, skipped = build_investigators(ScriptedLLM({}), _state())
    assert sorted(a.name for a in agents) == [
        "deploy_correlation",
        "logs_investigator",
        "metrics_investigator",
        "runbook_investigator",
        "trace_investigator",
        "triage",
    ]
    assert skipped == []


def test_absent_sources_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "alert.json").write_text(
        json.dumps(
            {"id": "a1", "title": "t", "service": "svc", "triggered_at": "2026-06-01T14:35:00Z"}
        )
    )
    agents, skipped = build_investigators(ScriptedLLM({}), initial_state(load_package(tmp_path)))
    assert [a.name for a in agents] == ["triage"]
    assert len(skipped) == 5


def test_investigator_converts_findings_to_typed_evidence() -> None:
    llm = ScriptedLLM(_default_script())
    agent = make_investigator(SPECS_BY_NAME["metrics_investigator"], llm)
    update = agent.run(_state())

    assert len(update.evidence) == 2
    first = update.evidence[0]
    assert first.source == Source.METRICS
    assert first.id.startswith("evidence_")
    assert (
        first.timestamp is not None and first.timestamp.isoformat() == "2026-06-01T14:30:00+00:00"
    )
    assert first.value == 900
    assert update.reasoning_trace[0].input_ids == [item.id for item in update.evidence]

    request = llm.requests[0]
    assert request.json_schema is not None
    assert "Cite only information present in the input" in request.system
    assert "baseline=450" in request.messages[0].content


def test_evidence_ids_are_deterministic_and_deduped() -> None:
    script: dict[str, str | Exception] = {
        "Role: metrics investigator": _investigator_json(
            _finding("same finding", service="svc"),
            _finding("same finding", service="svc"),
        )
    }
    agent = make_investigator(SPECS_BY_NAME["metrics_investigator"], ScriptedLLM(script))
    update_one = agent.run(_state())
    update_two = agent.run(_state())
    assert len(update_one.evidence) == 1  # duplicates collapse
    assert update_one.evidence[0].id == update_two.evidence[0].id


def test_bad_timestamps_become_gaps_not_crashes() -> None:
    script: dict[str, str | Exception] = {
        "Role: metrics investigator": _investigator_json(
            _finding("finding with junk time", timestamp="yesterday-ish"),
            _finding("finding with naive time", timestamp="2026-06-01T14:30:00"),
        )
    }
    agent = make_investigator(SPECS_BY_NAME["metrics_investigator"], ScriptedLLM(script))
    update = agent.run(_state())
    assert all(item.timestamp is None for item in update.evidence)
    descriptions = " ".join(m.description for m in update.missing_data)
    assert "unparseable timestamp" in descriptions
    assert "non-timezone-aware timestamp" in descriptions


def test_gaps_become_missing_data() -> None:
    agent = make_investigator(SPECS_BY_NAME["runbook_investigator"], ScriptedLLM(_default_script()))
    update = agent.run(_state())
    assert any("queue consumer scaling" in m.description for m in update.missing_data)


def test_schema_violation_raises_llm_error() -> None:
    from ai_incident_investigator.llm import LLMError

    script: dict[str, str | Exception] = {"Role: metrics investigator": '{"not": "the schema"}'}
    agent = make_investigator(SPECS_BY_NAME["metrics_investigator"], ScriptedLLM(script))
    with pytest.raises(LLMError, match="not matching its schema"):
        agent.run(_state())


def test_triage_sets_severity_and_summary() -> None:
    state = _state()
    update = make_triage(ScriptedLLM(_default_script())).run(state)
    assert update.severity is not None
    assert update.severity.level == SeverityLevel.SEV2
    assert update.severity.confidence == Confidence.HIGH
    assert update.summary is not None
    assert update.summary.incident_window == state.window
    assert "booking-service" in update.summary.affected_services


def test_requests_are_byte_stable_across_loads() -> None:
    llm_one = ScriptedLLM(_default_script())
    llm_two = ScriptedLLM(_default_script())
    make_investigator(SPECS_BY_NAME["metrics_investigator"], llm_one).run(_state())
    make_investigator(SPECS_BY_NAME["metrics_investigator"], llm_two).run(_state())
    assert request_key(llm_one.requests[0]) == request_key(llm_two.requests[0])


def test_run_investigation_end_to_end_with_fakes() -> None:
    state = run_investigation(_state(), ScriptedLLM(_default_script()))
    assert state.severity is not None and state.severity.level == SeverityLevel.SEV2
    assert state.summary is not None
    assert {item.source for item in state.evidence} == {
        Source.METRICS,
        Source.LOGS,
        Source.TRACES,
        Source.DEPLOYS,
        Source.RUNBOOK,
    }
    assert state.failures == []
    stages = {step.stage for step in state.reasoning_trace}
    assert "triage" in stages and "metrics_investigator" in stages


def test_one_failing_agent_degrades_only_itself() -> None:
    script = _default_script()
    script["Role: logs investigator"] = RuntimeError("simulated API outage")
    state = run_investigation(_state(), ScriptedLLM(script))
    assert [f.agent for f in state.failures] == ["logs_investigator"]
    assert any("logs_investigator" in m.description for m in state.missing_data)
    assert state.severity is not None  # triage still landed
    assert Source.METRICS in {item.source for item in state.evidence}


def test_skipped_investigators_are_traced(tmp_path: Path) -> None:
    (tmp_path / "alert.json").write_text(
        json.dumps(
            {"id": "a1", "title": "t", "service": "svc", "triggered_at": "2026-06-01T14:35:00Z"}
        )
    )
    state = run_investigation(
        initial_state(load_package(tmp_path)), ScriptedLLM({"Role: triage": TRIAGE_JSON})
    )
    skip_steps = [s for s in state.reasoning_trace if s.summary.startswith("skipped:")]
    assert len(skip_steps) == 5

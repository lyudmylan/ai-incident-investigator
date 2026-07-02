import json
from pathlib import Path

import pytest

from ai_incident_investigator.agents import build_investigators
from ai_incident_investigator.agents.base import make_investigator
from ai_incident_investigator.agents.investigators import SOURCE_SPECS
from ai_incident_investigator.agents.triage import make_triage
from ai_incident_investigator.llm import request_key
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.models.common import Confidence, SeverityLevel, Source
from ai_incident_investigator.pipeline import initial_state, run_investigation
from ai_incident_investigator.state import InvestigationState
from helpers import (
    EXAMPLE_DIR as EXAMPLE,
)
from helpers import (
    TRIAGE_JSON,
    ScriptedLLM,
)
from helpers import (
    default_script as _default_script,
)
from helpers import (
    investigator_json as _investigator_json,
)
from helpers import (
    make_finding as _finding,
)

SPECS_BY_NAME = {spec.name: spec for spec in SOURCE_SPECS}


def _state() -> InvestigationState:
    return initial_state(load_package(EXAMPLE))


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

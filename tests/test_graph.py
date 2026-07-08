import threading
from pathlib import Path

import pytest

from ai_incident_investigator.graph import FunctionAgent, GraphError, plan_levels, run_graph
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.models.report import ReasoningStep
from ai_incident_investigator.state import InvestigationState, StateUpdate, apply_update
from ai_incident_investigator.timeline import build_timeline
from ai_incident_investigator.window import incident_window

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "incidents" / "latency_spike"


def _initial_state() -> InvestigationState:
    loaded = load_package(EXAMPLE)
    return InvestigationState(
        package=loaded.package,
        window=incident_window(loaded.package),
        timeline=build_timeline(loaded.package),
        missing_data=loaded.missing_data,
    )


def _step(name: str) -> StateUpdate:
    return StateUpdate(reasoning_trace=[ReasoningStep(stage=name, summary=f"{name} ran")])


def test_levels_respect_dependencies() -> None:
    agents = [
        FunctionAgent(name="c", run=lambda s: _step("c"), depends_on=frozenset({"a", "b"})),
        FunctionAgent(name="a", run=lambda s: _step("a")),
        FunctionAgent(name="b", run=lambda s: _step("b")),
        FunctionAgent(name="d", run=lambda s: _step("d"), depends_on=frozenset({"c"})),
    ]
    levels = plan_levels(agents)
    assert [[a.name for a in level] for level in levels] == [["a", "b"], ["c"], ["d"]]


def test_duplicate_names_raise() -> None:
    agents = [
        FunctionAgent(name="a", run=lambda s: _step("a")),
        FunctionAgent(name="a", run=lambda s: _step("a")),
    ]
    with pytest.raises(GraphError, match="duplicate"):
        plan_levels(agents)


def test_unknown_dependency_raises() -> None:
    agents = [FunctionAgent(name="a", run=lambda s: _step("a"), depends_on=frozenset({"ghost"}))]
    with pytest.raises(GraphError, match="unknown agent"):
        plan_levels(agents)


def test_cycle_raises() -> None:
    agents = [
        FunctionAgent(name="a", run=lambda s: _step("a"), depends_on=frozenset({"b"})),
        FunctionAgent(name="b", run=lambda s: _step("b"), depends_on=frozenset({"a"})),
    ]
    with pytest.raises(GraphError, match="cycle"):
        plan_levels(agents)


def test_same_level_agents_run_concurrently() -> None:
    # Both agents must be inside run() at the same time to pass the barrier;
    # sequential execution would block 2 seconds and fail it.
    barrier = threading.Barrier(2, timeout=2)

    def meet(name: str) -> StateUpdate:
        barrier.wait()
        return _step(name)

    agents = [
        FunctionAgent(name="x", run=lambda s: meet("x")),
        FunctionAgent(name="y", run=lambda s: meet("y")),
    ]
    state = run_graph(agents, _initial_state())
    assert [step.stage for step in state.reasoning_trace] == ["x", "y"]


def test_updates_apply_in_name_order_not_completion_order() -> None:
    import time

    def slow(name: str, delay: float) -> StateUpdate:
        time.sleep(delay)
        return _step(name)

    agents = [
        FunctionAgent(name="a", run=lambda s: slow("a", 0.05)),  # finishes last
        FunctionAgent(name="b", run=lambda s: slow("b", 0.0)),
    ]
    state = run_graph(agents, _initial_state())
    assert [step.stage for step in state.reasoning_trace] == ["a", "b"]


def test_failing_agent_degrades_and_downstream_still_runs() -> None:
    def boom(_: InvestigationState) -> StateUpdate:
        raise RuntimeError("LLM call failed")

    seen_partial: list[int] = []

    def downstream(state: InvestigationState) -> StateUpdate:
        seen_partial.append(len(state.failures))
        return _step("downstream")

    agents = [
        FunctionAgent(name="broken", run=boom),
        FunctionAgent(name="downstream", run=downstream, depends_on=frozenset({"broken"})),
    ]
    state = run_graph(agents, _initial_state())

    assert [f.agent for f in state.failures] == ["broken"]
    assert seen_partial == [1]  # downstream saw the recorded failure
    assert any("agent 'broken' failed" in m.description for m in state.missing_data)
    assert any(s.stage == "broken" and "skipped" in s.summary for s in state.reasoning_trace)
    assert any(s.stage == "downstream" for s in state.reasoning_trace)


def test_state_is_frozen_for_agents() -> None:
    state = _initial_state()
    with pytest.raises(Exception, match="frozen"):
        state.evidence = []


def test_apply_update_merges_lists_and_overwrites_scalars() -> None:
    state = _initial_state()
    first = apply_update(state, _step("one"))
    second = apply_update(first, _step("two"))
    assert [s.stage for s in second.reasoning_trace] == ["one", "two"]
    assert second.summary is None
    assert second.missing_data == state.missing_data  # untouched lists carry over


def test_failure_text_is_stabilized_for_replay() -> None:
    """Issue #61: a live API error carries a per-request id; two otherwise
    identical failures must produce IDENTICAL state (else downstream prompts
    differ between live and replay runs and fixtures never match)."""
    from ai_incident_investigator.loading import load_package
    from ai_incident_investigator.pipeline import initial_state
    from ai_incident_investigator.state import record_failure, stable_error_text
    from helpers import EXAMPLE_DIR

    error_one = "Claude API call failed: 400 {'request_id': 'req_011CcnmDtvkFgEP58T7PwKgq'}"
    error_two = "Claude API call failed: 400 {'request_id': 'req_9ZZ9ZZ9ZZ9ZZ9ZZ9ZZ9ZZ9'}"
    assert stable_error_text(error_one) == stable_error_text(error_two)
    assert "req_<redacted>" in stable_error_text(error_one)

    base = initial_state(load_package(EXAMPLE_DIR))
    one = record_failure(base, "triage", error_one)
    two = record_failure(base, "triage", error_two)
    assert one.missing_data[-1] == two.missing_data[-1]  # same id, same description
    assert one.failures[-1] == two.failures[-1]
    assert "req_011" not in one.missing_data[-1].description

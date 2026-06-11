"""Hand-rolled agent graph runner.

Decision record in docs/architecture.md: the v1 graph is a fixed fan-out /
fan-in shape, so a dependency-free, fully typed runner beats a framework.

Semantics:
- agents declare dependencies by name; the runner topologically levels them
- agents within a level run concurrently (LLM calls are I/O-bound)
- updates are applied between levels in agent-name order, so the final state
  is deterministic regardless of completion order
- a raising agent degrades the report (missing_data + reasoning trace entry)
  and downstream agents still run on the partial state
- construction errors (cycles, unknown deps, duplicate names) raise GraphError:
  those are programming errors, not data problems
"""

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ai_incident_investigator.state import (
    InvestigationState,
    StateUpdate,
    apply_update,
    record_failure,
)


class GraphError(Exception):
    """The graph is malformed (duplicate name, unknown dependency, or cycle)."""


@dataclass(frozen=True)
class FunctionAgent:
    """An agent defined by a plain function. The standard building block."""

    name: str
    run: Callable[[InvestigationState], StateUpdate]
    depends_on: frozenset[str] = field(default_factory=frozenset)


def plan_levels(agents: Sequence[FunctionAgent]) -> list[list[FunctionAgent]]:
    """Topologically sort agents into parallel-executable levels."""
    names = [agent.name for agent in agents]
    if len(names) != len(set(names)):
        duplicates = sorted({n for n in names if names.count(n) > 1})
        raise GraphError(f"duplicate agent name(s): {', '.join(duplicates)}")
    known = set(names)
    for agent in agents:
        unknown = agent.depends_on - known
        if unknown:
            raise GraphError(
                f"agent '{agent.name}' depends on unknown agent(s): {', '.join(sorted(unknown))}"
            )

    levels: list[list[FunctionAgent]] = []
    remaining = list(agents)
    placed: set[str] = set()
    while remaining:
        ready = [agent for agent in remaining if agent.depends_on <= placed]
        if not ready:
            stuck = ", ".join(sorted(agent.name for agent in remaining))
            raise GraphError(f"dependency cycle among agents: {stuck}")
        ready.sort(key=lambda agent: agent.name)
        levels.append(ready)
        placed.update(agent.name for agent in ready)
        remaining = [agent for agent in remaining if agent.name not in placed]
    return levels


def run_graph(
    agents: Sequence[FunctionAgent],
    state: InvestigationState,
    max_workers: int = 6,
) -> InvestigationState:
    """Run the graph to completion; the state must be treated as read-only by agents."""
    if max_workers < 1:
        raise GraphError(f"max_workers must be >= 1, got {max_workers}")
    for level in plan_levels(agents):
        outcomes: list[tuple[str, StateUpdate | Exception]] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [(agent.name, pool.submit(agent.run, state)) for agent in level]
            for name, future in futures:
                exc = future.exception()
                if exc is None:
                    outcomes.append((name, future.result()))
                elif isinstance(exc, Exception):
                    outcomes.append((name, exc))
                else:
                    raise exc  # KeyboardInterrupt and friends propagate

        # Name order, not completion order: keeps the final state deterministic.
        for name, outcome in sorted(outcomes, key=lambda pair: pair[0]):
            if isinstance(outcome, Exception):
                state = record_failure(state, name, str(outcome))
            else:
                state = apply_update(state, outcome)
    return state

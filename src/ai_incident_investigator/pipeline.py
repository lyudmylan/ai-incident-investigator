"""Investigation pipeline assembly: deterministic facts in, agent graph out.

Graph shape (docs/architecture.md): investigators fan out in parallel, the
ranker fans in, the critic reviews the ranker, and the deterministic safety
linter always runs last - its checks hold even when every LLM agent failed.

Investigators whose source is absent are skipped (the loader already
recorded the missing file); the skip is noted in the reasoning trace so the
report explains why a source went uninvestigated.
"""

from datetime import timedelta
from pathlib import Path
from typing import Literal

from ai_incident_investigator.agents import build_investigators
from ai_incident_investigator.agents.critic import CRITIC_NAME, make_critic
from ai_incident_investigator.agents.planner import PLANNER_NAME, make_planner
from ai_incident_investigator.agents.ranker import RANKER_NAME, make_ranker
from ai_incident_investigator.agents.reporter import REPORTER_NAME, make_reporter
from ai_incident_investigator.graph import run_graph
from ai_incident_investigator.llm import (
    AnthropicClient,
    LLMClient,
    RecordingClient,
    ReplayClient,
)
from ai_incident_investigator.loading import LoadedPackage
from ai_incident_investigator.models.report import ReasoningStep
from ai_incident_investigator.recommendations import BUILDER_NAME, make_recommendation_builder
from ai_incident_investigator.safety import make_safety_linter
from ai_incident_investigator.state import InvestigationState, StateUpdate, apply_update
from ai_incident_investigator.timeline import build_timeline
from ai_incident_investigator.window import DEFAULT_LOOKBACK, incident_window

LLMMode = Literal["live", "record", "replay"]
DEFAULT_FIXTURES_ROOT = Path("tests") / "fixtures" / "llm"


def initial_state(
    loaded: LoadedPackage, lookback: timedelta = DEFAULT_LOOKBACK
) -> InvestigationState:
    return InvestigationState(
        package=loaded.package,
        window=incident_window(loaded.package, lookback),
        timeline=build_timeline(loaded.package),
        missing_data=loaded.missing_data,
    )


def make_client(mode: LLMMode, fixtures_dir: Path) -> LLMClient:
    if mode == "live":
        return AnthropicClient()
    if mode == "record":
        return RecordingClient(AnthropicClient(), fixtures_dir)
    return ReplayClient(fixtures_dir)


def run_investigation(
    state: InvestigationState, llm: LLMClient, max_workers: int = 6
) -> InvestigationState:
    agents, skipped = build_investigators(llm, state)
    investigator_names = frozenset(agent.name for agent in agents)
    agents.append(make_ranker(llm, depends_on=investigator_names))
    agents.append(make_critic(llm, depends_on=frozenset({RANKER_NAME})))
    agents.append(make_recommendation_builder(depends_on=frozenset({CRITIC_NAME})))
    agents.append(make_reporter(llm, depends_on=frozenset({BUILDER_NAME})))
    agents.append(make_planner(llm, depends_on=frozenset({REPORTER_NAME})))
    # The linter runs dead last so it lints plans, mitigations, and next steps.
    agents.append(make_safety_linter(depends_on=frozenset({PLANNER_NAME})))
    if skipped:
        state = apply_update(
            state,
            StateUpdate(
                reasoning_trace=[
                    ReasoningStep(stage=name, summary=f"skipped: {reason}")
                    for name, reason in skipped
                ]
            ),
        )
    return run_graph(agents, state, max_workers=max_workers)

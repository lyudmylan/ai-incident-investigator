"""Investigation pipeline assembly: deterministic facts in, agent graph out.

Investigators whose source is absent are skipped (the loader already
recorded the missing file); the skip is noted in the reasoning trace so the
report explains why a source went uninvestigated.
"""

from datetime import timedelta
from pathlib import Path
from typing import Literal

from ai_incident_investigator.agents import build_investigators
from ai_incident_investigator.graph import run_graph
from ai_incident_investigator.llm import (
    AnthropicClient,
    LLMClient,
    RecordingClient,
    ReplayClient,
)
from ai_incident_investigator.loading import LoadedPackage
from ai_incident_investigator.models.report import ReasoningStep
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

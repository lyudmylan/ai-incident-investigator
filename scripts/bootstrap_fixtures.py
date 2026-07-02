# ruff: noqa: E402
"""Regenerate LLM fixtures and golden reports for the example incidents.

Usage:
    uv run --no-sync python scripts/bootstrap_fixtures.py [incident_id ...]

Default fixtures come from the scripted fake responses (tests/scripted_runs.py)
recorded through the real RecordingClient, so golden tests exercise the
genuine replay path without an API key. To use real model output instead,
record live fixtures first (see AGENTS.md), then rerun this script with
--goldens-only to refresh the goldens from whatever fixtures are on disk.
"""

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

# Fixture keys embed the model name; a stray env override would poison them.
os.environ.pop("AI_INCIDENT_INVESTIGATOR_MODEL", None)

from ai_incident_investigator.assemble import build_report
from ai_incident_investigator.llm import RecordingClient, ReplayClient
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.pipeline import initial_state, run_investigation
from helpers import ScriptedLLM
from scripted_runs import SCRIPTED_INCIDENTS, script_for


def regenerate(incident_id: str, goldens_only: bool) -> None:
    example = ROOT / "examples" / "incidents" / incident_id
    fixtures = ROOT / "tests" / "fixtures" / "llm" / incident_id
    golden = ROOT / "tests" / "golden" / f"{incident_id}.json"

    if not goldens_only:
        shutil.rmtree(fixtures, ignore_errors=True)
        recorder = RecordingClient(ScriptedLLM(script_for(incident_id)), fixtures)
        run_investigation(initial_state(load_package(example)), recorder)
        print(f"recorded {len(list(fixtures.glob('*.json')))} fixtures -> {fixtures}")

    # The golden always comes from a replay run, proving the fixtures serve it.
    state = run_investigation(initial_state(load_package(example)), ReplayClient(fixtures))
    if state.failures:
        failed = ", ".join(f.agent for f in state.failures)
        raise SystemExit(f"{incident_id}: replay run had agent failures ({failed}); aborting")
    golden.parent.mkdir(parents=True, exist_ok=True)
    golden.write_text(build_report(state).model_dump_json(indent=2) + "\n")
    print(f"wrote golden -> {golden}")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    goldens_only = "--goldens-only" in sys.argv
    for incident_id in args or sorted(SCRIPTED_INCIDENTS):
        regenerate(incident_id, goldens_only)


if __name__ == "__main__":
    main()

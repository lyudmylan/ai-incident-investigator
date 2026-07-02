"""Golden replay tests: full pipeline output locked byte-for-byte per example.

Fixtures live in tests/fixtures/llm/<incident>/, goldens in
tests/golden/<incident>.json. Regenerate both after intentional changes:

    uv run --no-sync python scripts/bootstrap_fixtures.py
"""

import json
from pathlib import Path

import pytest

from ai_incident_investigator.assemble import build_report
from ai_incident_investigator.llm import MODEL_ENV_VAR, ReplayClient
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.pipeline import initial_state, run_investigation

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = ROOT / "examples" / "incidents"
FIXTURES_ROOT = ROOT / "tests" / "fixtures" / "llm"
GOLDEN_DIR = ROOT / "tests" / "golden"

REGEN_HINT = "regenerate with: uv run --no-sync python scripts/bootstrap_fixtures.py"

incident_ids = sorted(p.name for p in EXAMPLES_DIR.iterdir() if p.is_dir())


def test_every_example_has_fixtures_and_a_golden() -> None:
    """Coverage guard: adding an example obliges fixtures + golden for it."""
    assert incident_ids, "no example incidents found"
    for incident_id in incident_ids:
        assert (FIXTURES_ROOT / incident_id).is_dir(), (
            f"{incident_id} has no LLM fixtures; {REGEN_HINT}"
        )
        assert (GOLDEN_DIR / f"{incident_id}.json").is_file(), (
            f"{incident_id} has no golden report; {REGEN_HINT}"
        )


@pytest.mark.parametrize("incident_id", incident_ids)
def test_replay_run_matches_golden(incident_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)  # fixture keys embed the model

    state = run_investigation(
        initial_state(load_package(EXAMPLES_DIR / incident_id)),
        ReplayClient(FIXTURES_ROOT / incident_id),
    )
    assert state.failures == [], (
        f"replay misses for {incident_id}: {[f.error for f in state.failures]}; {REGEN_HINT}"
    )

    produced = json.loads(build_report(state).model_dump_json())
    golden = json.loads((GOLDEN_DIR / f"{incident_id}.json").read_text())
    assert produced == golden, f"report for {incident_id} drifted from its golden; {REGEN_HINT}"


def test_error_rate_spike_window_recovers() -> None:
    """This example exists to exercise the window-end (recovery) path."""
    state = initial_state(load_package(EXAMPLES_DIR / "error_rate_spike"))
    assert state.window.end is not None
    assert state.window.end.isoformat() == "2026-06-15T10:25:00+00:00"


def test_dependency_timeout_is_ongoing() -> None:
    state = initial_state(load_package(EXAMPLES_DIR / "dependency_timeout"))
    assert state.window.end is None

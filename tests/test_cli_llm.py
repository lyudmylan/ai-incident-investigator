"""CLI behavior for the --llm modes (off/replay covered; live/record need keys)."""

import json
from pathlib import Path

import pytest

from ai_incident_investigator.cli import main

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "incidents" / "latency_spike"


def test_llm_off_has_no_investigation_section(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--incident", str(EXAMPLE)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert "evidence" not in output
    assert "--llm" in output["note"]


def test_llm_replay_without_fixtures_degrades_not_crashes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # No fixtures recorded: every agent fails with a replay miss, the run
    # still succeeds, and the failures are visible in the output.
    assert (
        main(
            [
                "--incident",
                str(EXAMPLE),
                "--llm",
                "replay",
                "--fixtures-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert len(output["agent_failures"]) == 6
    assert output["severity"] is None
    assert any("no fixture" in f["error"] for f in output["agent_failures"])
    assert any("failed:" in m["description"] for m in output["missing_data"])

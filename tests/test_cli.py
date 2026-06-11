import json
from pathlib import Path

import pytest

from ai_incident_investigator.cli import main

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "incidents" / "latency_spike"


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0


def test_missing_incident_dir_is_investigation_failure(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--incident", "/nonexistent/incident"]) == 1
    assert "not found" in capsys.readouterr().err


def test_empty_package_fails_with_clear_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--incident", str(tmp_path)]) == 1
    assert "alert.json is required" in capsys.readouterr().err


def test_negative_lookback_is_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--incident", str(EXAMPLE), "--lookback-minutes", "-5"])
    assert excinfo.value.code == 2


def test_example_package_produces_facts_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--incident", str(EXAMPLE)]) == 0
    facts = json.loads(capsys.readouterr().out)
    assert facts["incident_id"] == "latency_spike"
    assert facts["incident_window"]["start"] == "2026-06-01T14:05:00Z"
    assert facts["incident_window"]["end"] is None
    assert facts["missing_data"] == []
    assert len(facts["timeline"]) == 25
    assert facts["timeline"][0]["source"] == "deploys"  # 2026-05-30 notifications deploy


def test_lookback_override_changes_window(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--incident", str(EXAMPLE), "--lookback-minutes", "10"]) == 0
    facts = json.loads(capsys.readouterr().out)
    assert facts["incident_window"]["start"] == "2026-06-01T14:25:00Z"

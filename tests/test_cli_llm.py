"""CLI behavior for --llm modes, --format, and --output."""

import json
from pathlib import Path

import pytest

from ai_incident_investigator.cli import main
from helpers import EXAMPLE_DIR as EXAMPLE

CONTRACT_KEYS = {
    "incident_id",
    "summary",
    "severity",
    "timeline",
    "evidence",
    "hypotheses",
    "missing_data",
    "recommended_next_steps",
    "safe_mitigation_options",
    "safety_review",
    "communication_drafts",
    "postmortem_draft",
    "reasoning_trace",
}


def test_llm_off_emits_facts_not_contract(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--incident", str(EXAMPLE)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert "evidence" not in output
    assert "--llm" in output["note"]


def test_llm_replay_without_fixtures_emits_degraded_contract_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # No fixtures: every LLM agent fails, deterministic stages still complete,
    # and the output is a full contract-shaped report with honest fallbacks.
    args = ["--incident", str(EXAMPLE), "--llm", "replay", "--fixtures-dir", str(tmp_path)]
    assert main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert set(report.keys()) == CONTRACT_KEYS
    assert report["severity"]["level"] == "SEV-4"
    assert "floor, not a verdict" in report["severity"]["explanation"]
    assert report["hypotheses"] == []
    assert "placeholder" in report["communication_drafts"]["internal_update"]
    assert any("failed:" in m["description"] for m in report["missing_data"])
    check_names = {c["check"] for c in report["safety_review"]["checks"]}
    assert "no_executed_action_phrasing" in check_names


def test_markdown_format(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = [
        "--incident",
        str(EXAMPLE),
        "--llm",
        "replay",
        "--fixtures-dir",
        str(tmp_path),
        "--format",
        "markdown",
    ]
    assert main(args) == 0
    text = capsys.readouterr().out
    assert text.startswith("# Incident investigation: latency_spike")
    assert "## Safety review" in text


def test_output_writes_file_and_keeps_stdout_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "report.json"
    args = [
        "--incident",
        str(EXAMPLE),
        "--llm",
        "replay",
        "--fixtures-dir",
        str(tmp_path),
        "--output",
        str(target),
    ]
    assert main(args) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(target) in captured.err
    report = json.loads(target.read_text())
    assert set(report.keys()) == CONTRACT_KEYS


def test_llm_off_markdown_facts(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--incident", str(EXAMPLE), "--format", "markdown"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("# Incident facts: latency_spike")
    assert "Deterministic facts only" in text

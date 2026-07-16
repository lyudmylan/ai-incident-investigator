"""Postmortem updated from verified recovery (#70): deterministic,
additive, and the report file - the hash every approval binds to - is
never rewritten.
"""

import shutil
from pathlib import Path

import pytest

from ai_incident_investigator.cli import main
from ai_incident_investigator.compare import (
    PatternComparison,
    RecoveryComparison,
    SignalComparison,
    build_comparison,
    merge_comparison_into_postmortem,
    render_updated_postmortem,
)
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.models.report import InvestigationReport

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_REPORT = ROOT / "tests" / "golden" / "latency_spike.json"
GOLDEN_POSTMORTEM = ROOT / "tests" / "golden" / "postmortems" / "latency_spike.md"
INCIDENT = ROOT / "examples" / "incidents" / "latency_spike"
FOLLOW_UP = ROOT / "examples" / "followups" / "latency_spike"


def _report() -> InvestigationReport:
    return InvestigationReport.model_validate_json(GOLDEN_REPORT.read_text())


def _comparison() -> RecoveryComparison:
    return build_comparison(load_package(INCIDENT).package, load_package(FOLLOW_UP).package)


def test_merge_is_additive_and_flags_the_unverifiable_signal() -> None:
    report = _report()
    merged = merge_comparison_into_postmortem(report, _comparison())
    draft = report.postmortem_draft

    assert merged.title == draft.title
    assert merged.summary == draft.summary
    assert merged.contributing_factors == draft.contributing_factors
    assert merged.impact.startswith(draft.impact)
    assert "INCONCLUSIVE" in merged.impact
    assert merged.open_questions[: len(draft.open_questions)] == draft.open_questions
    assert any("appointments-db/cpu_pct is unverifiable" in q for q in merged.open_questions)
    # nothing unrecovered, no surviving patterns, no re-alert -> unchanged
    assert merged.action_items == draft.action_items


def test_merge_turns_regressions_into_action_items() -> None:
    comparison = _comparison().model_copy(
        update={
            "signals": [
                SignalComparison(
                    service="booking-service",
                    signal="p95_latency_ms",
                    baseline=450.0,
                    incident_peak=3300.0,
                    follow_up_last=2000.0,
                    recovered=False,
                    detail="still 4.4x baseline",
                )
            ],
            "patterns": [
                PatternComparison(
                    pattern="Booking failed for appointment N",
                    occurrences_in_follow_up=3,
                    still_present=True,
                )
            ],
            "re_alert": "met",
        }
    )
    merged = merge_comparison_into_postmortem(_report(), comparison)
    added = merged.action_items[len(_report().postmortem_draft.action_items) :]
    assert len(added) == 3
    assert "had not recovered" in added[0]
    assert "error pattern still present" in added[1]
    assert "re-alert condition was met" in added[2]


def test_rendered_golden_stays_current() -> None:
    regenerate = "uv run --no-sync python scripts/bootstrap_fixtures.py"
    assert GOLDEN_POSTMORTEM.exists(), f"{GOLDEN_POSTMORTEM} missing; run: {regenerate}"
    assert render_updated_postmortem(_report(), _comparison()) == GOLDEN_POSTMORTEM.read_text(), (
        f"updated-postmortem golden is stale; run: {regenerate}"
    )


def test_cli_writes_the_sidecar_and_never_touches_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report_file = tmp_path / "report.json"
    shutil.copy(GOLDEN_REPORT, report_file)
    before = report_file.read_bytes()

    code = main(
        [
            "compare",
            "--incident",
            str(INCIDENT),
            "--follow-up",
            str(FOLLOW_UP),
            "--update-postmortem",
            str(report_file),
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert "report untouched; approvals remain valid" in captured.err

    sidecar = tmp_path / "report.postmortem.md"
    assert sidecar.exists()
    assert "updated from verified recovery" in sidecar.read_text()
    assert report_file.read_bytes() == before  # the approval anchor is intact


def test_cli_update_postmortem_with_missing_report_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "compare",
            "--incident",
            str(INCIDENT),
            "--follow-up",
            str(FOLLOW_UP),
            "--update-postmortem",
            str(tmp_path / "missing.json"),
        ]
    )
    assert code == 1
    assert "could not load the report" in capsys.readouterr().err

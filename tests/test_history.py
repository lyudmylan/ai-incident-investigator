"""The history store and its CLI (issue #88): content-addressed idempotent
add, degradation-with-notes on scan, read-only match, and the renderers'
wording rules."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_incident_investigator.approvals import report_hash
from ai_incident_investigator.cli import main
from ai_incident_investigator.history import (
    APPROVALS_FILE,
    ENTRY_FILE,
    EXECUTIONS_FILE,
    REPORT_FILE,
    HistoryError,
    add_entry,
    load_entries,
    match_report,
    render_entries,
    render_matches,
)
from ai_incident_investigator.models.history import HistoryEntry

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "latency_spike.json"
PROBE_GOLDEN = ROOT / "tests" / "golden" / "collected_demo.json"
EXECUTED_AT = datetime(2026, 6, 1, 15, 0, tzinfo=UTC)


def executions_payload(verified: bool = True) -> dict[str, object]:
    action = {
        "method": "PATCH",
        "environment": "staging",
        "flag_key": "checkout_enrichment",
        "on": False,
    }
    record = {
        "executed_by": "lyudmyla",
        "executed_at": EXECUTED_AT.isoformat(),
        "mode": "live",
        "action": action,
        "plan_id": "plan_1",
        "step_index": 0,
        "report_sha256": "a" * 64,
        "required_approvals": 1,
        "approvals_satisfied": ["lyudmyla"],
        "outcome": "applied",
        "verification": "pending",
    }
    verifications = (
        [
            {
                "verified_at": "2026-06-01T16:00:00+00:00",
                "plan_id": "plan_1",
                "step_index": 0,
                "executed_at": EXECUTED_AT.isoformat(),
                "action": action,
                "follow_up_incident_id": "latency_spike_followup",
                "outcome": "verified",
                "detail": "recovered",
            }
        ]
        if verified
        else []
    )
    return {"executions": [record], "verifications": verifications}


@pytest.fixture()
def report_path(tmp_path: Path) -> Path:
    path = tmp_path / "report.json"
    shutil.copy(GOLDEN, path)
    return path


def test_add_is_content_addressed_verbatim_and_idempotent(
    tmp_path: Path, report_path: Path
) -> None:
    history = tmp_path / "history"
    entry, created = add_entry(history, report_path)
    assert created is True
    entry_dir = history / entry.entry_id
    assert (entry_dir / REPORT_FILE).read_bytes() == report_path.read_bytes()
    stored = HistoryEntry.model_validate_json((entry_dir / ENTRY_FILE).read_text())
    assert stored == entry
    assert entry.entry_id == f"latency_spike-{entry.fingerprint.report_sha256[:16]}"

    again, created_again = add_entry(history, report_path)
    assert created_again is False
    assert again == entry
    assert len(list(history.iterdir())) == 1


def test_add_recovers_from_a_crashed_staging_dir(tmp_path: Path, report_path: Path) -> None:
    """A crash mid-add leaves only a .tmp staging dir - never a partial
    entry under the final name; the next add cleans it up and succeeds."""
    history = tmp_path / "history"
    expected_id = f"latency_spike-{report_hash(report_path)[:16]}"
    leftover = history / f"{expected_id}.tmp"
    leftover.mkdir(parents=True)
    (leftover / REPORT_FILE).write_text("partial")

    entry, created = add_entry(history, report_path)
    assert created is True
    assert entry.entry_id == expected_id
    assert not leftover.exists()
    assert (history / expected_id / ENTRY_FILE).is_file()


def test_add_auto_discovers_conventional_sidecars(tmp_path: Path, report_path: Path) -> None:
    report_path.with_suffix(".executions.json").write_text(json.dumps(executions_payload()))
    report_path.with_suffix(".approvals.json").write_text(json.dumps({"approvals": []}))
    entry, _ = add_entry(tmp_path / "history", report_path)
    assert [f.verification for f in entry.fingerprint.executed_fixes] == ["verified"]
    entry_dir = tmp_path / "history" / entry.entry_id
    assert (entry_dir / EXECUTIONS_FILE).is_file()
    assert (entry_dir / APPROVALS_FILE).is_file()


def test_add_explicit_sidecar_paths_and_refusals(tmp_path: Path, report_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps(executions_payload(verified=False)))
    entry, _ = add_entry(tmp_path / "history", report_path, executions_file=elsewhere)
    assert [f.verification for f in entry.fingerprint.executed_fixes] == ["pending"]

    with pytest.raises(HistoryError, match="executions sidecar not found"):
        add_entry(tmp_path / "h2", report_path, executions_file=tmp_path / "missing.json")
    with pytest.raises(HistoryError, match="approvals sidecar not found"):
        add_entry(tmp_path / "h3", report_path, approvals_file=tmp_path / "missing.json")

    bad_report = tmp_path / "bad.json"
    bad_report.write_text('{"incident_id": "x"}')
    with pytest.raises(HistoryError, match="not a valid investigation report"):
        add_entry(tmp_path / "h4", bad_report)

    bad_sidecar = tmp_path / "bad_sidecar.json"
    bad_sidecar.write_text("not json")
    with pytest.raises(HistoryError, match="not a valid executions sidecar"):
        add_entry(tmp_path / "h5", report_path, executions_file=bad_sidecar)


def test_scan_degrades_per_entry_with_notes(tmp_path: Path, report_path: Path) -> None:
    history = tmp_path / "history"
    entry, _ = add_entry(history, report_path)

    (history / "not-an-entry").mkdir()
    corrupt = history / "corrupt-entry"
    corrupt.mkdir()
    (corrupt / ENTRY_FILE).write_text("{broken")
    renamed = history / "renamed-entry"
    shutil.copytree(history / entry.entry_id, renamed)

    entries, notes = load_entries(history)
    assert [e.entry_id for e in entries] == [entry.entry_id]
    assert len(notes) == 3
    assert any("no entry.json" in note for note in notes)
    assert any("corrupt entry.json" in note for note in notes)
    assert any("does not match its directory" in note for note in notes)

    with pytest.raises(HistoryError, match="history directory not found"):
        load_entries(tmp_path / "missing")


def test_match_report_is_read_only_and_excludes_itself(tmp_path: Path, report_path: Path) -> None:
    history = tmp_path / "history"
    add_entry(history, report_path)
    before = report_path.read_bytes()

    matches, notes = match_report(history, PROBE_GOLDEN)
    assert notes == []
    assert [m.incident_id for m in matches][:1] == ["latency_spike"]
    assert matches[0].score == sum(f.weight for f in matches[0].matched)

    assert match_report(history, report_path)[0] == []  # its own report: excluded
    assert report_path.read_bytes() == before  # nothing rewritten


def test_renderers_wording_rules(tmp_path: Path, report_path: Path) -> None:
    report_path.with_suffix(".executions.json").write_text(
        json.dumps(executions_payload(verified=False))
    )
    history = tmp_path / "history"
    add_entry(history, report_path)
    entries, notes = load_entries(history)
    listing = render_entries(entries, notes)
    assert "SEV-2" in listing and "deploy-correlated" in listing
    assert "[did NOT verify: pending] staging/checkout_enrichment -> off" in listing
    assert "1 entry in the history" in listing

    matches, _ = match_report(history, PROBE_GOLDEN)
    rendered = render_matches(matches, [])
    assert "match 1: resembles latency_spike" in rendered
    assert "shared (+2): booking-service/p95_latency_ms abnormal in both" in rendered
    assert "executed there:" in rendered
    assert "only [verified] fixes are precedent" in rendered

    assert "no prior incidents match" in render_matches([], [])


def test_cli_round_trip_and_exit_codes(
    tmp_path: Path, report_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history = str(tmp_path / "history")
    assert main(["history", "add", "--history", history, "--report", str(report_path)]) == 0
    first = capsys.readouterr()
    assert "added latency_spike-" in first.err

    assert main(["history", "add", "--history", history, "--report", str(report_path)]) == 0
    assert "no-op" in capsys.readouterr().err

    assert main(["history", "list", "--history", history]) == 0
    assert "1 entry in the history" in capsys.readouterr().out

    assert main(["history", "match", "--history", history, "--report", str(PROBE_GOLDEN)]) == 0
    assert "resembles latency_spike" in capsys.readouterr().out

    assert main(["history", "list", "--history", str(tmp_path / "missing")]) == 1
    assert "history directory not found" in capsys.readouterr().err

    assert main(["history", "bogus"]) == 2
    assert main(["history"]) == 2

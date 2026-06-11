import json
import shutil
from pathlib import Path

import pytest

from ai_incident_investigator.ids import stable_id
from ai_incident_investigator.loading import PackageLoadError, load_package

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "incidents" / "latency_spike"

MINIMAL_ALERT = {
    "id": "alert_001",
    "title": "test alert",
    "service": "svc",
    "triggered_at": "2026-06-01T14:35:00Z",
}


def test_stable_ids_are_deterministic() -> None:
    assert stable_id("x", "a", "b") == stable_id("x", "a", "b")
    assert stable_id("x", "a", "b") != stable_id("x", "a", "c")
    assert stable_id("x", "ab") != stable_id("x", "a", "b")


def test_full_example_loads_with_no_missing_data() -> None:
    loaded = load_package(EXAMPLE)
    assert loaded.package.incident_id == "latency_spike"
    assert loaded.missing_data == []
    assert loaded.package.metrics is not None
    assert len(loaded.package.logs) == 20


def test_missing_directory_raises() -> None:
    with pytest.raises(PackageLoadError, match="not found"):
        load_package(Path("/nonexistent/incident"))


def test_missing_alert_raises(tmp_path: Path) -> None:
    with pytest.raises(PackageLoadError, match=r"alert\.json is required"):
        load_package(tmp_path)


def test_invalid_alert_raises(tmp_path: Path) -> None:
    (tmp_path / "alert.json").write_text('{"id": "a1"}')
    with pytest.raises(PackageLoadError, match="failed validation"):
        load_package(tmp_path)


def test_alert_only_package_reports_all_optional_files_missing(tmp_path: Path) -> None:
    (tmp_path / "alert.json").write_text(json.dumps(MINIMAL_ALERT))
    loaded = load_package(tmp_path)
    descriptions = {m.description for m in loaded.missing_data}
    assert descriptions == {
        "metrics.json not provided",
        "traces.json not provided",
        "deploys.json not provided",
        "topology.json not provided",
        "logs.jsonl / logs.txt not provided",
        "runbook.md not provided",
    }
    assert all(m.impact for m in loaded.missing_data)


def test_corrupt_optional_file_degrades_not_crashes(tmp_path: Path) -> None:
    (tmp_path / "alert.json").write_text(json.dumps(MINIMAL_ALERT))
    (tmp_path / "metrics.json").write_text("{not json")
    loaded = load_package(tmp_path)
    assert loaded.package.metrics is None
    assert any("not valid JSON" in m.description for m in loaded.missing_data)


def test_schema_invalid_optional_file_degrades(tmp_path: Path) -> None:
    (tmp_path / "alert.json").write_text(json.dumps(MINIMAL_ALERT))
    (tmp_path / "metrics.json").write_text(json.dumps({"series": []}))
    loaded = load_package(tmp_path)
    assert loaded.package.metrics is None
    assert any("failed validation" in m.description for m in loaded.missing_data)


def test_bad_jsonl_lines_are_skipped_and_reported(tmp_path: Path) -> None:
    shutil.copy(EXAMPLE / "alert.json", tmp_path / "alert.json")
    good = json.dumps(
        {
            "timestamp": "2026-06-01T14:00:00Z",
            "service": "svc",
            "level": "ERROR",
            "message": "boom",
        }
    )
    (tmp_path / "logs.jsonl").write_text(f"{good}\nnot json\n{good}\n")
    loaded = load_package(tmp_path)
    assert len(loaded.package.logs) == 2
    assert any("1 unparseable line(s)" in m.description for m in loaded.missing_data)


def test_logs_txt_fallback(tmp_path: Path) -> None:
    (tmp_path / "alert.json").write_text(json.dumps(MINIMAL_ALERT))
    (tmp_path / "logs.txt").write_text(
        "2026-06-01T14:31:08Z ERROR booking-service Eligibility lookup timed out\n"
        "this line does not match\n"
        "2026-06-01T14:32:36Z WARN booking-service Retrying eligibility lookup\n"
    )
    loaded = load_package(tmp_path)
    assert len(loaded.package.logs) == 2
    assert loaded.package.logs[0].level == "ERROR"
    assert loaded.package.logs[0].message == "Eligibility lookup timed out"
    assert any("did not match" in m.description for m in loaded.missing_data)


def test_jsonl_preferred_over_txt(tmp_path: Path) -> None:
    (tmp_path / "alert.json").write_text(json.dumps(MINIMAL_ALERT))
    good = json.dumps(
        {
            "timestamp": "2026-06-01T14:00:00Z",
            "service": "svc",
            "level": "INFO",
            "message": "from jsonl",
        }
    )
    (tmp_path / "logs.jsonl").write_text(f"{good}\n")
    (tmp_path / "logs.txt").write_text("2026-06-01T14:00:00Z INFO svc from txt\n")
    loaded = load_package(tmp_path)
    assert [r.message for r in loaded.package.logs] == ["from jsonl"]
    assert any("logs.txt ignored" in m.description for m in loaded.missing_data)

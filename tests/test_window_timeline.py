import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_incident_investigator.loading import load_package
from ai_incident_investigator.models.common import Source
from ai_incident_investigator.models.package import IncidentPackage
from ai_incident_investigator.timeline import build_timeline
from ai_incident_investigator.window import incident_window, is_anomalous

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "incidents" / "latency_spike"


def _example_package() -> IncidentPackage:
    return load_package(EXAMPLE).package


def _recovered_package(tmp_path: Path) -> IncidentPackage:
    (tmp_path / "alert.json").write_text(
        json.dumps(
            {
                "id": "a1",
                "title": "t",
                "service": "svc",
                "triggered_at": "2026-06-01T14:35:00Z",
            }
        )
    )
    points = [
        {"timestamp": f"2026-06-01T14:{m:02d}:00Z", "value": v}
        for m, v in [(0, 100), (10, 350), (20, 400), (30, 105), (40, 98), (50, 102)]
    ]
    (tmp_path / "metrics.json").write_text(
        json.dumps(
            {"series": [{"service": "svc", "signal": "lat", "baseline": 100, "points": points}]}
        )
    )
    return load_package(tmp_path).package


def test_is_anomalous_rules() -> None:
    assert is_anomalous(900, 450)
    assert is_anomalous(200, 450)
    assert not is_anomalous(800, 450)
    assert is_anomalous(0.5, 0)
    assert not is_anomalous(0, 0)


def test_window_start_uses_lookback() -> None:
    window = incident_window(_example_package())
    assert window.start == datetime(2026, 6, 1, 14, 5, tzinfo=UTC)
    assert "30m lookback" in window.rule


def test_window_ongoing_when_not_recovered() -> None:
    window = incident_window(_example_package())
    assert window.end is None
    assert "not recovered" in window.rule


def test_window_custom_lookback() -> None:
    window = incident_window(_example_package(), timedelta(minutes=10))
    assert window.start == datetime(2026, 6, 1, 14, 25, tzinfo=UTC)


def test_window_without_metrics_says_so(tmp_path: Path) -> None:
    (tmp_path / "alert.json").write_text(
        json.dumps(
            {"id": "a1", "title": "t", "service": "svc", "triggered_at": "2026-06-01T14:35:00Z"}
        )
    )
    window = incident_window(load_package(tmp_path).package)
    assert window.end is None
    assert "no metrics available" in window.rule


def test_window_end_at_recovery_start(tmp_path: Path) -> None:
    window = incident_window(_recovered_package(tmp_path))
    assert window.end == datetime(2026, 6, 1, 14, 30, tzinfo=UTC)
    assert "sustained recovery" in window.rule


def test_timeline_contents_and_order() -> None:
    timeline = build_timeline(_example_package())
    by_source = {source: [e for e in timeline if e.source == source] for source in Source}

    assert len(by_source[Source.ALERT]) == 1
    assert len(by_source[Source.DEPLOYS]) == 2
    assert len(by_source[Source.LOGS]) == 15  # WARN/ERROR only; 5 INFO lines excluded
    assert len(by_source[Source.METRICS]) == 5  # notifications-service never deviates
    assert len(by_source[Source.TRACES]) == 2  # root error spans only

    timestamps = [entry.timestamp for entry in timeline]
    assert timestamps == sorted(timestamps)

    ids = [entry.id for entry in timeline]
    assert len(ids) == len(set(ids))


def test_timeline_is_deterministic() -> None:
    first = build_timeline(_example_package())
    second = build_timeline(_example_package())
    assert [e.id for e in first] == [e.id for e in second]


def test_metric_deviation_event_is_first_crossing() -> None:
    timeline = build_timeline(_example_package())
    booking_latency = [
        e
        for e in timeline
        if e.source == Source.METRICS
        and e.service == "booking-service"
        and "p95_latency_ms" in e.description
    ]
    assert len(booking_latency) == 1
    assert booking_latency[0].timestamp == datetime(2026, 6, 1, 14, 30, tzinfo=UTC)

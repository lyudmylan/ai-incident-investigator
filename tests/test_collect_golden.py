"""End-to-end golden across the collection boundary.

Collection from the committed HTTP fixtures must reproduce the committed
example package byte-for-byte; investigating that package is then covered
by the ordinary golden replay test (test_golden.py picks up collected_demo
like any other example). Regenerate both after intentional changes:

    uv run --no-sync python scripts/bootstrap_fixtures.py
"""

from pathlib import Path

from ai_incident_investigator.collect import (
    ReplayHTTPClient,
    collect_package,
    load_sources_config,
)
from ai_incident_investigator.collect.registry import build_sources
from sentry_stub import DEMO_ISSUE_ID

ROOT = Path(__file__).resolve().parents[1]
COMMITTED = ROOT / "examples" / "incidents" / "collected_demo"
DEMO_SOURCES = ROOT / "examples" / "collect" / "sources.toml"
DEMO_FIXTURES = ROOT / "tests" / "fixtures" / "http" / "demo_collect"


def test_collection_reproduces_the_committed_package_byte_for_byte(tmp_path: Path) -> None:
    config = load_sources_config(DEMO_SOURCES)
    alert_source, adapters = build_sources(config, ReplayHTTPClient(DEMO_FIXTURES), DEMO_ISSUE_ID)
    out = tmp_path / "collected_demo"
    report = collect_package(alert_source, adapters, out, config.collection)
    assert all(s.status == "ok" for s in report.sources)

    fresh = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
    committed = {p.name: p.read_bytes() for p in COMMITTED.iterdir() if p.is_file()}
    # collection_report.json embeds the output path; compare it separately
    fresh_report = fresh.pop("collection_report.json").decode()
    committed_report = committed.pop("collection_report.json").decode()
    assert fresh == committed, (
        "collected package drifted from examples/incidents/collected_demo; "
        "regenerate with scripts/bootstrap_fixtures.py"
    )
    assert fresh_report.replace(str(out), str(COMMITTED)) == committed_report

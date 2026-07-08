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


def regenerate_http_fixtures() -> None:
    """HTTP fixtures for the collection adapters, recorded from the local stubs."""
    from ai_incident_investigator.collect import RecordingHTTPClient
    from ai_incident_investigator.collect.prometheus import PrometheusMetricsAdapter
    from ai_incident_investigator.collect.sentry import SentryAlertSource
    from prometheus_stub import DEMO_CONFIG as PROM_CONFIG
    from prometheus_stub import PromStubHTTP, demo_collection_context
    from sentry_stub import DEMO_CONFIG as SENTRY_CONFIG
    from sentry_stub import DEMO_ISSUE_ID, SentryStubHTTP

    http_root = ROOT / "tests" / "fixtures" / "http"

    sentry_dir = http_root / "sentry_demo"
    shutil.rmtree(sentry_dir, ignore_errors=True)
    recorder = RecordingHTTPClient(SentryStubHTTP(), sentry_dir)
    SentryAlertSource(recorder, SENTRY_CONFIG, DEMO_ISSUE_ID).fetch_alert()
    print(f"recorded {len(list(sentry_dir.glob('*.json')))} HTTP fixtures -> {sentry_dir}")

    prom_dir = http_root / "prometheus_demo"
    shutil.rmtree(prom_dir, ignore_errors=True)
    prom_recorder = RecordingHTTPClient(PromStubHTTP(), prom_dir)
    PrometheusMetricsAdapter(prom_recorder, PROM_CONFIG).collect(demo_collection_context())
    print(f"recorded {len(list(prom_dir.glob('*.json')))} HTTP fixtures -> {prom_dir}")

    from ai_incident_investigator.collect.github import GitHubDeploysAdapter
    from ai_incident_investigator.collect.loki import LokiLogsAdapter
    from loki_github_stubs import (
        GITHUB_DEMO_CONFIG,
        LOKI_DEMO_CONFIG,
        GitHubStubHTTP,
        LokiStubHTTP,
    )

    loki_dir = http_root / "loki_demo"
    shutil.rmtree(loki_dir, ignore_errors=True)
    LokiLogsAdapter(RecordingHTTPClient(LokiStubHTTP(), loki_dir), LOKI_DEMO_CONFIG).collect(
        demo_collection_context()
    )
    print(f"recorded {len(list(loki_dir.glob('*.json')))} HTTP fixtures -> {loki_dir}")

    github_dir = http_root / "github_demo"
    shutil.rmtree(github_dir, ignore_errors=True)
    GitHubDeploysAdapter(
        RecordingHTTPClient(GitHubStubHTTP(), github_dir), GITHUB_DEMO_CONFIG
    ).collect(demo_collection_context())
    print(f"recorded {len(list(github_dir.glob('*.json')))} HTTP fixtures -> {github_dir}")

    # Combined dir: every demo fixture in one place, so the CLI's single
    # --http-fixtures-dir can replay a full multi-source collection
    # (README demo and the #22 end-to-end goldens).
    combined = http_root / "demo_collect"
    shutil.rmtree(combined, ignore_errors=True)
    combined.mkdir(parents=True)
    for source_dir in (sentry_dir, prom_dir, loki_dir, github_dir):
        for fixture in source_dir.glob("*.json"):
            shutil.copy(fixture, combined / fixture.name)
    print(f"combined {len(list(combined.glob('*.json')))} HTTP fixtures -> {combined}")


def regenerate_collected_example() -> None:
    """Re-collect examples/incidents/collected_demo from the committed HTTP
    fixtures - the package that proves collection determinism end-to-end."""
    from ai_incident_investigator.collect import (
        ReplayHTTPClient,
        collect_package,
        load_sources_config,
    )
    from ai_incident_investigator.collect.registry import build_sources
    from sentry_stub import DEMO_ISSUE_ID

    out = ROOT / "examples" / "incidents" / "collected_demo"
    shutil.rmtree(out, ignore_errors=True)
    config = load_sources_config(ROOT / "examples" / "collect" / "sources.toml")
    http = ReplayHTTPClient(ROOT / "tests" / "fixtures" / "http" / "demo_collect")
    alert_source, adapters = build_sources(config, http, DEMO_ISSUE_ID)
    report = collect_package(alert_source, adapters, out, config.collection)
    failed = [s.name for s in report.sources if s.status != "ok"]
    if failed:
        raise SystemExit(f"collected_demo regeneration had failed sources: {failed}")
    print(f"collected example package -> {out}")


def regenerate_publish_fixture() -> None:
    """The publish demo fixture: the latency_spike golden report rendered
    and 'created' against the stub issue endpoint. Runs after golden regen
    so the recorded request matches the committed report byte-for-byte."""
    from ai_incident_investigator.markdown import render_markdown
    from ai_incident_investigator.models.report import InvestigationReport
    from ai_incident_investigator.publish import RecordingPublishClient, render_issue
    from publish_stub import GitHubIssueStub

    fixtures = ROOT / "tests" / "fixtures" / "http" / "github_publish_demo"
    shutil.rmtree(fixtures, ignore_errors=True)
    report = InvestigationReport.model_validate_json(
        (ROOT / "tests" / "golden" / "latency_spike.json").read_text()
    )
    request = render_issue(report, "acme/incidents", render_markdown(report))
    RecordingPublishClient(GitHubIssueStub(), fixtures).create_issue(request)
    print(f"recorded {len(list(fixtures.glob('*.json')))} publish fixture -> {fixtures}")


def regenerate_comparison_golden() -> None:
    """The committed recovery-comparison golden: latency_spike vs its
    committed follow-up snapshot (deterministic, no fixtures involved)."""
    from ai_incident_investigator.compare import build_comparison

    original = load_package(ROOT / "examples" / "incidents" / "latency_spike").package
    follow_up = load_package(ROOT / "examples" / "followups" / "latency_spike").package
    comparison = build_comparison(original, follow_up)
    out = ROOT / "tests" / "golden" / "comparisons" / "latency_spike.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(comparison.model_dump_json(indent=2) + "\n")
    print(f"wrote comparison golden -> {out} (verdict: {comparison.verdict})")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    goldens_only = "--goldens-only" in sys.argv
    if "--http" in sys.argv:
        regenerate_http_fixtures()
        regenerate_publish_fixture()
        return
    regenerate_http_fixtures()
    regenerate_collected_example()
    for incident_id in args or sorted(SCRIPTED_INCIDENTS):
        regenerate(incident_id, goldens_only)
    regenerate_publish_fixture()
    regenerate_comparison_golden()


if __name__ == "__main__":
    main()

"""Degradation matrix: each optional source down or malformed, one at a time.

The invariant under test: collection completes, the package merely lacks
that source's contribution, the collection report names the failure, and
the v1 investigation degrades exactly as it does for hand-authored partial
packages. Only the alert anchor is fatal.
"""

from pathlib import Path

import pytest

from ai_incident_investigator.collect import (
    CollectError,
    CollectionReport,
    EnvBearerAuth,
    HTTPRequest,
    HTTPResponse,
    ReplayHTTPClient,
    collect_package,
    load_sources_config,
)
from ai_incident_investigator.collect.http import HTTPClient
from ai_incident_investigator.collect.registry import build_sources
from ai_incident_investigator.loading import LoadedPackage, load_package
from ai_incident_investigator.pipeline import initial_state
from sentry_stub import DEMO_ISSUE_ID

ROOT = Path(__file__).resolve().parents[1]
DEMO_SOURCES = ROOT / "examples" / "collect" / "sources.toml"
DEMO_FIXTURES = ROOT / "tests" / "fixtures" / "http" / "demo_collect"


class SabotagedHTTP:
    """Replays fixtures, except requests matching a URL fragment."""

    def __init__(self, url_fragment: str, mode: str) -> None:
        self._inner = ReplayHTTPClient(DEMO_FIXTURES)
        self._fragment = url_fragment
        self._mode = mode

    def get(self, request: HTTPRequest, auth: EnvBearerAuth | None = None) -> HTTPResponse:
        if self._fragment in request.url:
            if self._mode == "down":
                return HTTPResponse(status=503, body="service unavailable")
            return HTTPResponse(status=200, body="<html>definitely not json</html>")
        return self._inner.get(request, auth)


def _collect_with(http: HTTPClient, out: Path) -> tuple[CollectionReport, LoadedPackage]:
    config = load_sources_config(DEMO_SOURCES)
    alert_source, adapters = build_sources(config, http, DEMO_ISSUE_ID)
    report = collect_package(alert_source, adapters, out, config.collection)
    return report, load_package(out)


def _statuses(report: CollectionReport) -> dict[str, str]:
    return {s.name: s.status for s in report.sources}


@pytest.mark.parametrize("mode", ["down", "malformed"])
@pytest.mark.parametrize(
    ("source", "fragment", "filename"),
    [
        ("prometheus", "prom.stub.local", "metrics.json"),
        ("github", "github.stub.local", "deploys.json"),
    ],
)
def test_single_file_source_failure_degrades_not_crashes(
    source: str, fragment: str, filename: str, mode: str, tmp_path: Path
) -> None:
    out = tmp_path / "pkg"
    report, loaded = _collect_with(SabotagedHTTP(fragment, mode), out)

    statuses = _statuses(report)
    assert statuses.pop(source) == "failed"
    assert all(status == "ok" for status in statuses.values())

    assert not (out / filename).exists()
    assert any(f"{filename} not provided" in m.description for m in loaded.missing_data)

    # the investigation still runs on the partial package
    state = initial_state(loaded)
    assert state.window.start is not None


@pytest.mark.parametrize("mode", ["down", "malformed"])
def test_loki_failure_keeps_the_alert_bundle_logs(mode: str, tmp_path: Path) -> None:
    out = tmp_path / "pkg"
    report, loaded = _collect_with(SabotagedHTTP("loki.stub.local", mode), out)

    assert _statuses(report)["loki"] == "failed"
    # logs.jsonl still exists: the sentry event + breadcrumbs are logs too
    assert (out / "logs.jsonl").exists()
    assert len(loaded.package.logs) == 4  # bundle only; the loki lines are gone
    assert initial_state(loaded).window.start is not None


def test_sentry_down_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(CollectError, match="alert anchor"):
        _collect_with(SabotagedHTTP("sentry.stub.local", "down"), tmp_path / "pkg")


def test_sentry_malformed_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(CollectError, match="alert anchor"):
        _collect_with(SabotagedHTTP("sentry.stub.local", "malformed"), tmp_path / "pkg")

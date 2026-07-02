import json
from pathlib import Path

import pytest

from ai_incident_investigator.collect import (
    CollectError,
    HTTPRequest,
    HTTPResponse,
    RecordingHTTPClient,
    load_sources_config,
)
from ai_incident_investigator.collect.github import (
    GitHubDeploysAdapter,
    github_adapter,
)
from ai_incident_investigator.collect.loki import LokiConfig, LokiLogsAdapter, loki_adapter
from ai_incident_investigator.collect.normalize import level_from_text, normalize_level
from loki_github_stubs import (
    GITHUB_DEMO_CONFIG,
    LOKI_DEMO_CONFIG,
    GitHubStubHTTP,
    LokiStubHTTP,
)
from prometheus_stub import demo_collection_context

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures" / "http"
CONTEXT = demo_collection_context()


class OneBodyHTTP:
    def __init__(self, body: str, status: int = 200) -> None:
        self.body = body
        self.status = status

    def get(self, request: HTTPRequest, auth: object = None) -> HTTPResponse:
        return HTTPResponse(status=self.status, body=self.body)


# --- level normalization -----------------------------------------------------


def test_level_from_text_first_token_wins() -> None:
    assert level_from_text("WARN retrying eligibility lookup") == "WARN"
    assert level_from_text("warning: slow query") == "WARN"
    assert level_from_text("2026-06-01 error: boom") == "ERROR"
    assert level_from_text("all quiet on this line") == "INFO"
    assert normalize_level("critical") == "FATAL"


# --- loki ---------------------------------------------------------------------


def test_loki_collects_and_merges_streams() -> None:
    contribution = LokiLogsAdapter(LokiStubHTTP(), LOKI_DEMO_CONFIG).collect(CONTEXT)
    records = contribution.logs
    assert len(records) == 5
    timestamps = [record.timestamp for record in records]
    assert timestamps == sorted(timestamps)  # streams merged chronologically

    labeled = next(r for r in records if "enrichment query timed out" in r.message)
    assert labeled.level == "ERROR"  # from the stream label
    from_text = next(r for r in records if "slow eligibility enrichment" in r.message)
    assert from_text.level == "WARN"  # from the line text
    assert {r.service for r in records} == {"booking-service", "payment-service"}


def test_loki_request_params_follow_documented_rules() -> None:
    stub = LokiStubHTTP()
    LokiLogsAdapter(stub, LOKI_DEMO_CONFIG).collect(CONTEXT)
    request, _ = stub.calls[0]
    assert request.params["direction"] == "forward"
    assert request.params["limit"] == "500"
    span_ns = int(request.params["end"]) - int(request.params["start"])
    assert span_ns == (30 + 30) * 60 * 1_000_000_000  # lookback + post_minutes


def test_loki_truncation_and_empty_windows_are_noted() -> None:
    config = LokiConfig(
        base_url=LOKI_DEMO_CONFIG.base_url,
        limit=3,  # booking stream returns exactly 3 lines -> truncation warning
        streams=LOKI_DEMO_CONFIG.streams,
    )
    contribution = LokiLogsAdapter(LokiStubHTTP(), config).collect(CONTEXT)
    assert any("hit the 3-line limit" in note for note in contribution.notes)


def test_loki_partial_and_total_failure() -> None:
    error_body = json.dumps({"status": "error", "error": "parse error"})
    with pytest.raises(CollectError, match="no log stream could be collected"):
        LokiLogsAdapter(OneBodyHTTP(error_body), LOKI_DEMO_CONFIG).collect(CONTEXT)


def test_loki_unparseable_lines_are_counted() -> None:
    payload = {
        "status": "success",
        "data": {
            "result": [
                {
                    "stream": {},
                    "values": [
                        ["not-a-nanosecond-timestamp", "line one"],
                        ["1780000000000000000", "line two ERROR ok"],
                    ],
                }
            ]
        },
    }
    config = LokiConfig(base_url="https://x", streams=[LOKI_DEMO_CONFIG.streams[0]])
    contribution = LokiLogsAdapter(OneBodyHTTP(json.dumps(payload)), config).collect(CONTEXT)
    assert len(contribution.logs) == 1
    assert any("1 unparseable log line(s)" in note for note in contribution.notes)


# --- github --------------------------------------------------------------------


def test_github_maps_releases_and_deployments_in_change_window() -> None:
    contribution = GitHubDeploysAdapter(GitHubStubHTTP(), GITHUB_DEMO_CONFIG).collect(CONTEXT)
    assert contribution.deploys is not None
    deploys = contribution.deploys.deploys
    ids = [deploy.id for deploy in deploys]
    assert ids == [
        "release_booking-service_2026.06.01-1420",
        "deployment_booking-service_7001",
    ]  # sorted by time; the 05-20 release is outside the 7-day change window
    release = deploys[0]
    assert release.version == "2026.06.01-1420"
    assert release.change_type == "deploy"
    assert release.deployed_at.isoformat() == "2026-06-01T14:20:00+00:00"
    deployment = deploys[1]
    assert deployment.description is not None
    assert "deployment to production: automated deploy" in deployment.description
    assert any("draft/unpublished release(s) skipped" in n for n in contribution.notes)


def test_github_environment_filter_is_sent() -> None:
    stub = GitHubStubHTTP()
    GitHubDeploysAdapter(stub, GITHUB_DEMO_CONFIG).collect(CONTEXT)
    deployment_calls = [r for r, _ in stub.calls if r.url.endswith("/deployments")]
    assert deployment_calls[0].params["environment"] == "production"


def test_github_empty_change_window_writes_empty_deploys() -> None:
    empty = OneBodyHTTP(json.dumps([]))
    contribution = GitHubDeploysAdapter(empty, GITHUB_DEMO_CONFIG).collect(CONTEXT)
    assert contribution.deploys is not None
    assert contribution.deploys.deploys == []
    assert any("recorded as an empty deploys.json" in n for n in contribution.notes)


def test_github_total_failure_raises() -> None:
    with pytest.raises(CollectError, match="no repo could be collected"):
        GitHubDeploysAdapter(OneBodyHTTP("gone", status=502), GITHUB_DEMO_CONFIG).collect(CONTEXT)


def test_factories_validate_sections(tmp_path: Path) -> None:
    config_file = tmp_path / "sources.toml"
    config_file.write_text(
        '[loki]\nbase_url = "https://loki.stub.local"\n\n'
        '[[loki.streams]]\nservice = "booking-service"\nselector = "{app=\\"b\\"}"\n\n'
        '[github]\nbase_url = "https://github.stub.local/api/v3"\n\n'
        '[[github.repos]]\nrepo = "acme/booking-service"\nservice = "booking-service"\n'
    )
    config = load_sources_config(config_file)
    assert loki_adapter(config, LokiStubHTTP()).name == "loki"
    assert github_adapter(config, GitHubStubHTTP()).name == "github"

    bad = tmp_path / "bad.toml"
    bad.write_text('[loki]\nbase_url = "https://x"\n')  # no streams
    with pytest.raises(CollectError, match=r"\[loki\] section is invalid"):
        loki_adapter(load_sources_config(bad), LokiStubHTTP())


def test_fixture_regeneration_matches_committed(tmp_path: Path) -> None:
    LokiLogsAdapter(
        RecordingHTTPClient(LokiStubHTTP(), tmp_path / "loki_demo"), LOKI_DEMO_CONFIG
    ).collect(CONTEXT)
    GitHubDeploysAdapter(
        RecordingHTTPClient(GitHubStubHTTP(), tmp_path / "github_demo"), GITHUB_DEMO_CONFIG
    ).collect(CONTEXT)
    for name in ("loki_demo", "github_demo"):
        fresh = {p.name: p.read_text() for p in (tmp_path / name).glob("*.json")}
        committed = {p.name: p.read_text() for p in (FIXTURES_ROOT / name).glob("*.json")}
        assert fresh == committed, f"{name} fixtures drifted; re-run bootstrap --http"

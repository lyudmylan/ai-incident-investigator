"""Security pass (epic #22): credentials cannot reach disk; GET-only holds.

The scrubbing test proves the strongest claim we can make offline: with
every source configured to use tokens, the secrets ARE materialized
in-process (the resolving router records them, as the live client would
build headers) - and still no written byte of the package, the HTTP
fixtures, or the collection report contains a token value or even the env
var names.
"""

from pathlib import Path

import pytest

from ai_incident_investigator.collect import (
    EnvBearerAuth,
    HTTPRequest,
    HTTPResponse,
    RecordingHTTPClient,
    collect_package,
    load_sources_config,
)
from ai_incident_investigator.collect.http import HTTPClient, _resolve_token
from ai_incident_investigator.collect.registry import build_sources
from loki_github_stubs import GitHubStubHTTP, LokiStubHTTP
from prometheus_stub import PromStubHTTP
from sentry_stub import DEMO_ISSUE_ID, SentryStubHTTP

ROOT = Path(__file__).resolve().parents[1]

TOKENS = {
    "SEC_SENTRY_TOKEN": "sentry-sekrit-4471",
    "SEC_PROM_TOKEN": "prom-sekrit-9182",
    "SEC_LOKI_TOKEN": "loki-sekrit-5530",
    "SEC_GITHUB_TOKEN": "github-sekrit-7756",
}


class AuthResolvingRouter:
    """Routes to the per-host stubs AND resolves auth exactly like the live
    client would, recording the materialized header values as proof the
    secrets really were in play."""

    def __init__(self) -> None:
        self._stubs: dict[str, HTTPClient] = {
            "sentry.stub.local": SentryStubHTTP(),
            "prom.stub.local": PromStubHTTP(),
            "loki.stub.local": LokiStubHTTP(),
            "github.stub.local": GitHubStubHTTP(),
        }
        self.resolved_headers: list[str] = []

    def get(self, request: HTTPRequest, auth: EnvBearerAuth | None = None) -> HTTPResponse:
        if auth is not None:
            token = _resolve_token(auth)  # raises if the env var is unset
            self.resolved_headers.append(f"{auth.scheme} {token}".strip())
        host = next(h for h in self._stubs if h in request.url)
        return self._stubs[host].get(request, auth)


def _security_config(tmp_path: Path) -> Path:
    runbook = ROOT / "examples" / "incidents" / "latency_spike" / "runbook.md"
    topology = ROOT / "examples" / "incidents" / "latency_spike" / "topology.json"
    config = tmp_path / "sources.toml"
    config.write_text(
        f"""
[collection]
services = ["booking-service"]

[sentry]
base_url = "https://sentry.stub.local/api/0"
service_tag = "service"
token_env = "SEC_SENTRY_TOKEN"

[prometheus]
base_url = "https://prom.stub.local"
token_env = "SEC_PROM_TOKEN"
[[prometheus.queries]]
service = "booking-service"
signal = "p95_latency_ms"
query = 'p95_latency_ms{{service="booking-service"}}'
unit = "ms"

[loki]
base_url = "https://loki.stub.local"
token_env = "SEC_LOKI_TOKEN"
[[loki.streams]]
service = "booking-service"
selector = '{{app="booking-service"}}'

[github]
base_url = "https://github.stub.local/api/v3"
token_env = "SEC_GITHUB_TOKEN"
[[github.repos]]
repo = "acme/booking-service"
service = "booking-service"
environment = "production"

[runbook]
[[runbook.documents]]
file = "{runbook}"

[topology]
file = "{topology}"
"""
    )
    return config


def test_no_credential_material_reaches_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for env_var, value in TOKENS.items():
        monkeypatch.setenv(env_var, value)

    router = AuthResolvingRouter()
    fixtures_dir = tmp_path / "http_fixtures"
    recording = RecordingHTTPClient(router, fixtures_dir)

    config = load_sources_config(_security_config(tmp_path))
    alert_source, adapters = build_sources(config, recording, DEMO_ISSUE_ID)
    package_dir = tmp_path / "package"
    report = collect_package(alert_source, adapters, package_dir, config.collection)
    assert all(s.status == "ok" for s in report.sources)

    # Control: the secrets genuinely flowed through the collection machinery.
    assert len(router.resolved_headers) >= 4
    assert any("sentry-sekrit" in header for header in router.resolved_headers)

    written = list(package_dir.iterdir()) + list(fixtures_dir.iterdir())
    assert len(written) > 8
    for path in written:
        content = path.read_text()
        for env_var, value in TOKENS.items():
            assert value not in content, f"token value leaked into {path.name}"
            assert env_var not in content, f"credential env var name leaked into {path.name}"
        assert "Authorization" not in content


def test_missing_token_env_fails_before_any_request(tmp_path: Path) -> None:
    from ai_incident_investigator.collect import CollectError, HTTPClientError

    router = AuthResolvingRouter()  # resolves auth: raises on unset env vars
    config = load_sources_config(_security_config(tmp_path))
    alert_source, _ = build_sources(config, router, DEMO_ISSUE_ID)
    with pytest.raises((CollectError, HTTPClientError), match="SEC_SENTRY_TOKEN is not set"):
        alert_source.fetch_alert()


def test_only_http_module_touches_the_network_stack() -> None:
    collect_dir = ROOT / "src" / "ai_incident_investigator" / "collect"
    for module in sorted(collect_dir.glob("*.py")):
        source = module.read_text()
        if module.name == "http.py":
            assert "urllib" in source
            continue
        assert "urllib" not in source, f"{module.name} bypasses the GET-only HTTP wrapper"
        assert "urlopen" not in source, f"{module.name} bypasses the GET-only HTTP wrapper"


def test_recordable_request_cannot_carry_headers() -> None:
    assert set(HTTPRequest.model_fields) == {"method", "url", "params"}
    with pytest.raises(Exception, match="extra"):
        HTTPRequest.model_validate(
            {"url": "https://x", "headers": {"Authorization": "Bearer boom"}}
        )

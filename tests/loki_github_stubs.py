"""Minimal local Loki and GitHub stubs with deterministic payloads.

The demo scenario continues latency_spike as those systems would report it,
anchored on the Sentry demo event time.
"""

import json
from datetime import datetime

from ai_incident_investigator.collect.github import GitHubConfig, GitHubRepo
from ai_incident_investigator.collect.http import (
    EnvBearerAuth,
    HTTPRequest,
    HTTPResponse,
)
from ai_incident_investigator.collect.loki import LokiConfig, LokiStream

LOKI_BASE_URL = "https://loki.stub.local"
GITHUB_BASE_URL = "https://github.stub.local/api/v3"

LOKI_DEMO_CONFIG = LokiConfig(
    base_url=LOKI_BASE_URL,
    limit=500,
    streams=[
        LokiStream(service="booking-service", selector='{app="booking-service"}'),
        LokiStream(service="payment-service", selector='{app="payment-service"}'),
    ],
)

GITHUB_DEMO_CONFIG = GitHubConfig(
    base_url=GITHUB_BASE_URL,
    repos=[
        GitHubRepo(repo="acme/booking-service", service="booking-service", environment="production")
    ],
)


def _ns(iso: str) -> str:
    return str(int(datetime.fromisoformat(iso).timestamp() * 1_000_000_000))


_BOOKING_LINES = [
    (_ns("2026-06-01T14:20:05+00:00"), "INFO deployment 2026.06.01-1420 rolled out"),
    (_ns("2026-06-01T14:29:14+00:00"), "WARN retrying eligibility lookup (attempt 2 of 5)"),
    (_ns("2026-06-01T14:31:08+00:00"), "ERROR eligibility lookup timed out after 2000ms"),
]

_PAYMENT_STREAMS = [
    {
        "stream": {"app": "payment-service", "pod": "payment-7d9f", "level": "error"},
        "values": [
            [_ns("2026-06-01T14:33:21+00:00"), "eligibility enrichment query timed out"],
        ],
    },
    {
        "stream": {"app": "payment-service", "pod": "payment-8c2a"},
        "values": [
            [_ns("2026-06-01T14:27:39+00:00"), "warning: slow eligibility enrichment 1210ms"],
        ],
    },
]

RELEASES_PAYLOAD = [
    {
        "tag_name": "2026.06.01-1420",
        "name": "Enable payment eligibility enrichment",
        "published_at": "2026-06-01T14:20:00Z",
        "draft": False,
    },
    {
        "tag_name": "2026.05.20-0900",
        "name": "Too old to be in the change window",
        "published_at": "2026-05-20T09:00:00Z",
        "draft": False,
    },
    {"tag_name": "draft-next", "name": "Draft", "published_at": None, "draft": True},
]

DEPLOYMENTS_PAYLOAD = [
    {
        "id": 7001,
        "ref": "2026.06.01-1420",
        "sha": "abc123def456",
        "environment": "production",
        "created_at": "2026-06-01T14:20:30Z",
        "description": "automated deploy",
    }
]


class LokiStubHTTP:
    def __init__(self) -> None:
        self.calls: list[tuple[HTTPRequest, EnvBearerAuth | None]] = []

    def get(self, request: HTTPRequest, auth: EnvBearerAuth | None = None) -> HTTPResponse:
        self.calls.append((request, auth))
        if not request.url.endswith("/loki/api/v1/query_range"):
            return HTTPResponse(status=404, body=f"no stub route for {request.url}")
        selector = request.params["query"]
        if "booking-service" in selector:
            result = [{"stream": {"app": "booking-service"}, "values": _BOOKING_LINES}]
        elif "payment-service" in selector:
            result = _PAYMENT_STREAMS
        else:
            result = []
        payload = {"status": "success", "data": {"resultType": "streams", "result": result}}
        return HTTPResponse(status=200, body=json.dumps(payload))


class GitHubStubHTTP:
    def __init__(self) -> None:
        self.calls: list[tuple[HTTPRequest, EnvBearerAuth | None]] = []

    def get(self, request: HTTPRequest, auth: EnvBearerAuth | None = None) -> HTTPResponse:
        self.calls.append((request, auth))
        if request.url.endswith("/repos/acme/booking-service/releases"):
            return HTTPResponse(status=200, body=json.dumps(RELEASES_PAYLOAD))
        if request.url.endswith("/repos/acme/booking-service/deployments"):
            return HTTPResponse(status=200, body=json.dumps(DEPLOYMENTS_PAYLOAD))
        return HTTPResponse(status=404, body=f"no stub route for {request.url}")

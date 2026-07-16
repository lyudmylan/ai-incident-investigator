"""Canned Prometheus/Loki discovery endpoints for init --discover tests
and the committed keyless demo fixtures."""

import json

from ai_incident_investigator.collect.http import EnvBearerAuth, HTTPRequest, HTTPResponse

DEMO_PROM_URL = "https://prom.stub.local"
DEMO_LOKI_URL = "https://loki.stub.local"


class DiscoverStubHTTP:
    """Serves the discovery APIs for two demo services (plus one noisy
    Loki-only value, so trimming is part of the demo) and the query_range
    endpoint, so a generated draft is doctor-clean against this stub."""

    def __init__(self) -> None:
        self.calls: list[tuple[HTTPRequest, EnvBearerAuth | None]] = []

    def get(self, request: HTTPRequest, auth: EnvBearerAuth | None = None) -> HTTPResponse:
        self.calls.append((request, auth))
        url = request.url
        if url.endswith("/api/v1/label/service/values") and "loki" not in url:
            return _ok_list(["booking-service", "payment-service"])
        if url.endswith("/api/v1/series"):
            matcher = request.params.get("match[]", "")
            if "booking-service" in matcher:
                return _ok_series(
                    [
                        {"__name__": "error_rate_pct", "service": "booking-service"},
                        {"__name__": "p95_latency_ms", "service": "booking-service"},
                    ]
                )
            if "payment-service" in matcher:
                return _ok_series([{"__name__": "p95_latency_ms", "service": "payment-service"}])
            return _ok_series([])
        if url.endswith("/loki/api/v1/label/app/values"):
            return _ok_list(["booking-service", "noise-svc", "payment-service"])
        if url.endswith("/api/v1/query_range") and "loki" not in url:
            body = {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [{"metric": {}, "values": [[1752666000, "1"], [1752666300, "2"]]}],
                },
            }
            return HTTPResponse(status=200, body=json.dumps(body))
        if "/loki/api/v1/query_range" in url:
            body = {
                "status": "success",
                "data": {
                    "resultType": "streams",
                    "result": [
                        {"stream": {"app": "x"}, "values": [["1752666000000000000", "INFO ok"]]}
                    ],
                },
            }
            return HTTPResponse(status=200, body=json.dumps(body))
        return HTTPResponse(status=404, body=f"no discover stub route for {url}")


def _ok_list(values: list[str]) -> HTTPResponse:
    return HTTPResponse(status=200, body=json.dumps({"status": "success", "data": values}))


def _ok_series(entries: list[dict[str, str]]) -> HTTPResponse:
    return HTTPResponse(status=200, body=json.dumps({"status": "success", "data": entries}))

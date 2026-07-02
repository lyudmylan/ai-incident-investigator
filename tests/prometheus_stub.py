"""Minimal local Prometheus stub: deterministic query_range responses.

Series values are a pure function of the requested start/end/step and the
query string, so recorded fixtures are stable. The demo scenario matches
latency_spike: p95 latency at ~450ms baseline spiking after 14:25Z, error
rate at ~0.3% spiking to ~4.8%.
"""

import json
from datetime import UTC, datetime, timedelta

from ai_incident_investigator.collect.adapter import CollectionContext
from ai_incident_investigator.collect.http import (
    EnvBearerAuth,
    HTTPRequest,
    HTTPResponse,
)
from ai_incident_investigator.collect.prometheus import PromConfig, PromQuery
from ai_incident_investigator.collect.sentry import parse_sentry_time
from sentry_stub import DEMO_EVENT_TIME

BASE_URL = "https://prom.stub.local"
SPIKE_START = datetime(2026, 6, 1, 14, 25, tzinfo=UTC).timestamp()

DEMO_CONFIG = PromConfig(
    base_url=BASE_URL,
    step_seconds=300,
    post_minutes=30,
    queries=[
        PromQuery(
            service="booking-service",
            signal="p95_latency_ms",
            query='p95_latency_ms{service="booking-service"}',
            unit="ms",
        ),
        PromQuery(
            service="booking-service",
            signal="error_rate_pct",
            query='error_rate_pct{service="booking-service"}',
            unit="%",
        ),
    ],
)


def demo_collection_context() -> CollectionContext:
    """The context a real collection run derives from the Sentry demo issue."""
    return CollectionContext(
        anchor_time=parse_sentry_time(DEMO_EVENT_TIME),
        lookback=timedelta(minutes=30),
        change_lookback=timedelta(days=7),
        services=["booking-service"],
    )


def _value_for(query: str, ts: float) -> str:
    spiking = ts >= SPIKE_START
    if "p95_latency_ms" in query:
        return "3200" if spiking else "450"
    if "error_rate_pct" in query:
        return "4.8" if spiking else "0.3"
    return "1"


def series_payload(query: str, start: float, end: float, step: int) -> dict[str, object]:
    values = []
    ts = start
    while ts <= end:
        values.append([ts, _value_for(query, ts)])
        ts += step
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{"metric": {"__name__": query.split("{")[0]}, "values": values}],
        },
    }


class PromStubHTTP:
    def __init__(self) -> None:
        self.calls: list[tuple[HTTPRequest, EnvBearerAuth | None]] = []

    def get(self, request: HTTPRequest, auth: EnvBearerAuth | None = None) -> HTTPResponse:
        self.calls.append((request, auth))
        if not request.url.endswith("/api/v1/query_range"):
            return HTTPResponse(status=404, body=f"no stub route for {request.url}")
        params = request.params
        payload = series_payload(
            params["query"], float(params["start"]), float(params["end"]), int(params["step"])
        )
        return HTTPResponse(status=200, body=json.dumps(payload))

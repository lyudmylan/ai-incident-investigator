"""Minimal local Sentry stub: canned wire payloads served by URL suffix.

The demo issue is the latency_spike scenario as Sentry would report it, so
collected packages line up with the existing example corpus.
"""

import json

from ai_incident_investigator.collect.http import (
    EnvBearerAuth,
    HTTPRequest,
    HTTPResponse,
)
from ai_incident_investigator.collect.sentry import SentryConfig

DEMO_ISSUE_ID = "9101"
BASE_URL = "https://sentry.stub.local/api/0"
DEMO_CONFIG = SentryConfig(base_url=BASE_URL, service_tag="service")

ISSUE_PAYLOAD: dict[str, object] = {
    "id": DEMO_ISSUE_ID,
    "title": "EligibilityTimeout: eligibility lookup timed out after 2000ms",
    "culprit": "booking.eligibility.check_eligibility",
    "level": "error",
    "firstSeen": "2026-06-01T14:31:08Z",
    "lastSeen": "2026-06-01T14:55:46Z",
    "permalink": "https://sentry.stub.local/organizations/acme/issues/9101/",
    "project": {"id": "42", "name": "Booking", "slug": "booking"},
    "metadata": {"type": "EligibilityTimeout", "value": "eligibility lookup timed out"},
    "count": "184",
}

EVENT_PAYLOAD: dict[str, object] = {
    "eventID": "ab12cd34ef56",
    "dateCreated": "2026-06-01T14:35:52Z",
    "tags": [
        {"key": "environment", "value": "production"},
        {"key": "service", "value": "booking-service"},
        {"key": "release", "value": "2026.06.01-1420"},
    ],
    "entries": [
        {
            "type": "breadcrumbs",
            "data": {
                "values": [
                    {
                        "timestamp": "2026-06-01T14:35:40Z",
                        "level": "info",
                        "category": "query",
                        "message": "eligibility lookup started for booking 84163",
                    },
                    {
                        # naive timestamp: Sentry emits UTC; adapter attaches UTC
                        "timestamp": "2026-06-01T14:35:44",
                        "level": "warning",
                        "category": "retry",
                        "message": "eligibility lookup retry (attempt 4 of 5)",
                    },
                    {
                        "timestamp": "2026-06-01T14:35:50Z",
                        "level": "critical",
                        "category": None,
                        "message": "giving up after 5 attempts",
                    },
                    {
                        # no message: skipped by the documented rule
                        "timestamp": "2026-06-01T14:35:51Z",
                        "level": "info",
                        "category": "http",
                        "message": None,
                    },
                ]
            },
        },
        {"type": "exception", "data": {"values": []}},
    ],
}


class SentryStubHTTP:
    """Serves the canned payloads; optionally fails the latest-event endpoint."""

    def __init__(self, latest_event_status: int = 200) -> None:
        self.latest_event_status = latest_event_status
        self.calls: list[tuple[HTTPRequest, EnvBearerAuth | None]] = []

    def get(self, request: HTTPRequest, auth: EnvBearerAuth | None = None) -> HTTPResponse:
        self.calls.append((request, auth))
        if request.url.endswith(f"/issues/{DEMO_ISSUE_ID}/"):
            return HTTPResponse(status=200, body=json.dumps(ISSUE_PAYLOAD))
        if request.url.endswith(f"/issues/{DEMO_ISSUE_ID}/events/latest/"):
            if self.latest_event_status != 200:
                return HTTPResponse(status=self.latest_event_status, body="not found")
            return HTTPResponse(status=200, body=json.dumps(EVENT_PAYLOAD))
        return HTTPResponse(status=404, body=f"no stub route for {request.url}")

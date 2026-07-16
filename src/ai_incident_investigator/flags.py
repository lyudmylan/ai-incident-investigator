"""The second (and last pilot) write this codebase can perform: toggle ONE flag.

Structural narrowing, mirroring publish/github_issue.py:

- `FlagToggleRequest` (models.execution) carries validated NAME segments,
  never a URL - the endpoint is derived by `toggle_route` (the single
  source for it, shared with the executor's audit detail) and no other
  route is representable through this module's API.
- `method` is Literal["PATCH"]; there is no generic request type here.
- Credentials are env-var references; key formatting, fixture keying, and
  the atomic credential-free fixture write are the SHARED collect/http.py
  primitives, so the three transport modules cannot drift.
- Every failure mode raises FlagToggleError so the executor can always
  write its audit record; nothing else escapes toggle().

WHO may call this is not this module's job: the executor (execute.py)
reaches it only through clearance - allowlist, tier quorum, pilot
live-tier rule - and records the outcome before reporting. That guard is
the executor's path, not a property of this type; do not call the live
client directly.
"""

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from ai_incident_investigator.collect.http import (
    EnvBearerAuth,
    HTTPClientError,
    auth_header_value,
    request_key,
    write_fixture_atomically,
)
from ai_incident_investigator.models.execution import FlagToggleRequest

DEFAULT_TIMEOUT_SECONDS = 30.0

# PATCH success statuses: some flag backends answer 200+body, others
# 201/204 with no body. A 2xx means the desired state was accepted.
_SUCCESS_STATUSES = (200, 201, 204)


class FlagToggleError(Exception):
    """The toggle failed or the response was not usable."""


class FlagToggled(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    key: str
    on: bool


class FlagClient(Protocol):
    def toggle(
        self, request: FlagToggleRequest, auth: EnvBearerAuth | None = None
    ) -> FlagToggled: ...


def toggle_route(base_url: str, request: FlagToggleRequest) -> str:
    """The ONE route the pilot can address - used by the live client to
    send and by the executor to record, so the audit trail and the wire
    can never name different URLs."""
    return f"{base_url.rstrip('/')}/flags/{request.environment}/{request.flag_key}"


def _parse_toggle_response(status: int, body: str, request: FlagToggleRequest) -> FlagToggled:
    """2xx handling, unit-testable without a network: a parseable body is
    authoritative; an empty body on a success status echoes the DESIRED
    state (the action is idempotent desired-state, and #68's verification
    starts 'pending' regardless - nothing is assumed verified)."""
    if status not in _SUCCESS_STATUSES:
        raise FlagToggleError(f"flag toggle returned unexpected HTTP {status}")
    if not body.strip():
        return FlagToggled(key=request.flag_key, on=request.on)
    try:
        return FlagToggled.model_validate_json(body)
    except Exception as exc:
        raise FlagToggleError(f"flag-toggle response was not understood: {exc}") from exc


class LiveFlagClient:
    """PATCHes exactly one derived route; auth resolved at send time."""

    def __init__(self, base_url: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._base = base_url
        self._timeout = timeout_seconds

    def toggle(self, request: FlagToggleRequest, auth: EnvBearerAuth | None = None) -> FlagToggled:
        url = toggle_route(self._base, request)
        payload = json.dumps({"on": request.on}).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        try:
            if auth is not None:
                headers[auth.header] = auth_header_value(auth)
        except HTTPClientError as exc:
            # a missing credential must surface as FlagToggleError so the
            # executor can still write its audit record
            raise FlagToggleError(f"flag toggle failed: {exc}") from exc
        raw = urllib.request.Request(url, data=payload, headers=headers, method="PATCH")
        try:
            with urllib.request.urlopen(raw, timeout=self._timeout) as reply:
                body = reply.read().decode("utf-8")
                status = reply.status
        except urllib.error.HTTPError as exc:
            raise FlagToggleError(
                f"flag toggle failed: HTTP {exc.code}: "
                f"{exc.read().decode('utf-8', 'replace')[:300]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise FlagToggleError(f"flag toggle failed: {exc.reason}") from exc
        return _parse_toggle_response(status, body, request)


class RecordingFlagClient:
    """Wraps a real client and writes a replayable fixture (credential-free
    by construction: the request type cannot carry headers)."""

    def __init__(self, inner: FlagClient, fixtures_dir: Path) -> None:
        self._inner = inner
        self._dir = fixtures_dir

    def toggle(self, request: FlagToggleRequest, auth: EnvBearerAuth | None = None) -> FlagToggled:
        response = self._inner.toggle(request, auth)
        write_fixture_atomically(self._dir, request_key(request), request, response)
        return response


class ReplayFlagClient:
    """Serves recorded fixtures; never touches the network."""

    def __init__(self, fixtures_dir: Path) -> None:
        self._dir = fixtures_dir

    def toggle(self, request: FlagToggleRequest, auth: EnvBearerAuth | None = None) -> FlagToggled:
        path = self._dir / f"{request_key(request)}.json"
        if not path.exists():
            raise FlagToggleError(f"no flag fixture {path.name} in {self._dir}")
        try:
            data = json.loads(path.read_text())
            stored_request = data["request"]
            response = FlagToggled.model_validate(data["response"])
        except FlagToggleError:
            raise
        except Exception as exc:
            # a corrupt fixture must not escape as a bare JSON/Key error:
            # the executor's audit record depends on catching this
            raise FlagToggleError(f"flag fixture {path.name} is unusable: {exc}") from exc
        if stored_request != request.model_dump(mode="json"):
            raise FlagToggleError(f"flag fixture {path.name} stores a different request")
        return response

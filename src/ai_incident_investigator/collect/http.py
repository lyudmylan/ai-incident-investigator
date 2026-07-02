"""Read-only HTTP harness with record/replay (the llm.py philosophy for APIs).

Structural guarantees, not conventions:

- GET-only: there is no way to express another method through this module.
- Credentials never touch fixtures: the request model that is hashed and
  stored carries method/url/params only. Auth is an env-var reference
  resolved by the live client at send time; recording stores the request
  model, so a token cannot leak into a fixture even by accident.

Clients: LiveHTTPClient (network, resolves auth), RecordingHTTPClient
(wraps another client, saves fixtures atomically), ReplayHTTPClient (serves
fixtures; no network, no credentials). CI uses replay only.
"""

import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_TIMEOUT_SECONDS = 30.0


class EnvBearerAuth(BaseModel):
    """A reference to a credential, never the credential itself."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    env_var: str
    header: str = "Authorization"
    scheme: str = Field(default="Bearer", description="Prefix before the token; '' for none")


class HTTPRequest(BaseModel):
    """The recordable identity of a request. Deliberately has no headers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: Literal["GET"] = "GET"
    url: str
    params: dict[str, str] = Field(default_factory=dict)


class HTTPResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: int
    body: str


class HTTPClientError(Exception):
    """A request could not be made or must not be trusted."""


class HTTPReplayMissError(HTTPClientError):
    """No recorded fixture matches the request."""


class HTTPClient(Protocol):
    def get(self, request: HTTPRequest, auth: EnvBearerAuth | None = None) -> HTTPResponse: ...


def request_key(request: HTTPRequest) -> str:
    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def raise_for_status(request: HTTPRequest, response: HTTPResponse) -> HTTPResponse:
    if response.status >= 400:
        raise HTTPClientError(
            f"GET {request.url} returned {response.status}: {response.body[:200]}"
        )
    return response


def _resolve_token(auth: EnvBearerAuth) -> str:
    token = os.environ.get(auth.env_var, "")
    if not token:
        raise HTTPClientError(
            f"credential env var {auth.env_var} is not set "
            "(collection uses read-only tokens supplied via the environment)"
        )
    return token


class LiveHTTPClient:
    """GET-only network client on stdlib urllib; auth resolved at send time."""

    def __init__(self, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout_seconds

    def get(self, request: HTTPRequest, auth: EnvBearerAuth | None = None) -> HTTPResponse:
        url = request.url
        if request.params:
            url = f"{url}?{urllib.parse.urlencode(sorted(request.params.items()))}"
        headers: dict[str, str] = {"Accept": "application/json"}
        if auth is not None:
            token = _resolve_token(auth)
            value = f"{auth.scheme} {token}".strip()
            headers[auth.header] = value
        raw = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(raw, timeout=self._timeout) as reply:
                return HTTPResponse(status=reply.status, body=reply.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return HTTPResponse(status=exc.code, body=exc.read().decode("utf-8", "replace"))
        except urllib.error.URLError as exc:
            raise HTTPClientError(f"GET {request.url} failed: {exc.reason}") from exc


class ReplayHTTPClient:
    """Serves recorded fixtures. Never touches the network or credentials."""

    def __init__(self, fixtures_dir: Path) -> None:
        self._dir = fixtures_dir

    def get(self, request: HTTPRequest, auth: EnvBearerAuth | None = None) -> HTTPResponse:
        path = self._dir / f"{request_key(request)}.json"
        if not path.exists():
            raise HTTPReplayMissError(
                f"no HTTP fixture {path.name} in {self._dir} for GET {request.url}; "
                "record one with RecordingHTTPClient"
            )
        data = json.loads(path.read_text())
        if data["request"] != request.model_dump(mode="json"):
            raise HTTPReplayMissError(
                f"HTTP fixture {path.name} stores a different request "
                "(key collision or stale fixture); re-record it"
            )
        return HTTPResponse.model_validate(data["response"])


def make_http_client(mode: str, fixtures_dir: Path | None) -> HTTPClient:
    """CLI helper: live network, or record/replay against a fixtures dir."""
    if mode == "live":
        return LiveHTTPClient()
    if fixtures_dir is None:
        raise HTTPClientError(f"--http {mode} requires --http-fixtures-dir")
    if mode == "replay":
        return ReplayHTTPClient(fixtures_dir)
    return RecordingHTTPClient(LiveHTTPClient(), fixtures_dir)


class RecordingHTTPClient:
    """Wraps a client and saves a replayable fixture per request (atomically).

    Only the HTTPRequest identity and the response are stored; auth is
    forwarded to the inner client but cannot be written (it is not part of
    either model)."""

    def __init__(self, inner: HTTPClient, fixtures_dir: Path) -> None:
        self._inner = inner
        self._dir = fixtures_dir

    def get(self, request: HTTPRequest, auth: EnvBearerAuth | None = None) -> HTTPResponse:
        response = self._inner.get(request, auth)
        self._dir.mkdir(parents=True, exist_ok=True)
        fixture = {
            "request": request.model_dump(mode="json"),
            "response": response.model_dump(mode="json"),
        }
        path = self._dir / f"{request_key(request)}.json"
        fd, tmp_name = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(json.dumps(fixture, indent=2, sort_keys=True) + "\n")
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return response

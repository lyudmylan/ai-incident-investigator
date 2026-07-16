"""The second (and last pilot) write this codebase can perform: toggle ONE flag.

Structural narrowing, mirroring publish/github_issue.py exactly:

- `FlagToggleRequest` (models.execution) carries validated NAME segments,
  never a URL - the endpoint is derived inside the client as
  /flags/{environment}/{flag_key} and no other route is representable.
- `method` is Literal["PATCH"]; there is no generic request type here.
- Credentials are env-var references (EnvBearerAuth, shared primitive);
  the executor token env is its own name from ExecutorConfig, refused if
  it aliases the publish or LLM credentials (#64), and refused by
  collection from the other side.
- Record/replay fixtures follow the adapter pattern: the recordable
  request cannot carry headers, so credentials cannot reach disk.

WHO may call this is not this module's job: the executor (execute.py)
reaches it only through `plan_execution`'s clearance - allowlist, tier
quorum, pilot live-tier rule - and records the outcome before reporting.
"""

import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from ai_incident_investigator.collect.http import EnvBearerAuth, _resolve_token
from ai_incident_investigator.models.execution import FlagToggleRequest

DEFAULT_TIMEOUT_SECONDS = 30.0


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


class LiveFlagClient:
    """PATCHes exactly one derived route; auth resolved at send time."""

    def __init__(self, base_url: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def toggle(self, request: FlagToggleRequest, auth: EnvBearerAuth | None = None) -> FlagToggled:
        url = f"{self._base}/flags/{request.environment}/{request.flag_key}"
        payload = json.dumps({"on": request.on}).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if auth is not None:
            headers[auth.header] = f"{auth.scheme} {_resolve_token(auth)}".strip()
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
        if status != 200:
            raise FlagToggleError(f"flag toggle returned unexpected HTTP {status}")
        try:
            return FlagToggled.model_validate_json(body)
        except Exception as exc:
            raise FlagToggleError(f"flag-toggle response was not understood: {exc}") from exc


def _request_key(request: FlagToggleRequest) -> str:
    import hashlib

    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class RecordingFlagClient:
    """Wraps a real client and writes a replayable fixture (credential-free
    by construction: the request type cannot carry headers)."""

    def __init__(self, inner: FlagClient, fixtures_dir: Path) -> None:
        self._inner = inner
        self._dir = fixtures_dir

    def toggle(self, request: FlagToggleRequest, auth: EnvBearerAuth | None = None) -> FlagToggled:
        response = self._inner.toggle(request, auth)
        self._dir.mkdir(parents=True, exist_ok=True)
        fixture = {
            "request": request.model_dump(mode="json"),
            "response": response.model_dump(mode="json"),
        }
        path = self._dir / f"{_request_key(request)}.json"
        fd, tmp_name = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(json.dumps(fixture, indent=2, sort_keys=True) + "\n")
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return response


class ReplayFlagClient:
    """Serves recorded fixtures; never touches the network."""

    def __init__(self, fixtures_dir: Path) -> None:
        self._dir = fixtures_dir

    def toggle(self, request: FlagToggleRequest, auth: EnvBearerAuth | None = None) -> FlagToggled:
        path = self._dir / f"{_request_key(request)}.json"
        if not path.exists():
            raise FlagToggleError(f"no flag fixture {path.name} in {self._dir}")
        data = json.loads(path.read_text())
        if data["request"] != request.model_dump(mode="json"):
            raise FlagToggleError(f"flag fixture {path.name} stores a different request")
        return FlagToggled.model_validate(data["response"])

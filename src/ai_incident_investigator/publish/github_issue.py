"""The one write this codebase can perform: create a GitHub issue.

Structural narrowing, mirroring collect/http.py's GET-only philosophy from
the opposite side:

- `IssueCreateRequest` carries a validated `repo` NAME, not a URL - the
  endpoint is derived inside the client as /repos/{repo}/issues and no
  other route is representable.
- `method` is Literal["POST"]; there is no generic request type here.
- Credentials are env-var references (EnvBearerAuth, shared primitive with
  collection) but the publish token env is its own name, passed via CLI -
  sources.toml has no publish section and collect/ never references it.
- Record/replay fixtures follow the adapter pattern: the recordable
  request cannot carry headers, so credentials cannot reach disk.
"""

import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_incident_investigator.collect.http import EnvBearerAuth, _resolve_token
from ai_incident_investigator.models.report import InvestigationReport

DEFAULT_BASE_URL = "https://api.github.com"
DEFAULT_TOKEN_ENV = "GITHUB_PUBLISH_TOKEN"
DEFAULT_TIMEOUT_SECONDS = 30.0

_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class PublishError(Exception):
    """Publishing failed or the response was not usable."""


class IssueCreateRequest(BaseModel):
    """The ONLY write request in the codebase (docs/product.md Safety Model)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: Literal["POST"] = "POST"
    repo: str = Field(description="owner/name; the endpoint is derived, never free-form")
    title: str
    body: str
    labels: list[str] = Field(default_factory=list)

    @field_validator("repo")
    @classmethod
    def _repo_shape(cls, value: str) -> str:
        if not _REPO_PATTERN.match(value):
            raise ValueError("repo must be owner/name (no paths, no URLs)")
        return value


class IssueCreated(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    number: int
    html_url: str


class PublishClient(Protocol):
    def create_issue(
        self, request: IssueCreateRequest, auth: EnvBearerAuth | None = None
    ) -> IssueCreated: ...


def render_issue(report: InvestigationReport, repo: str, markdown_body: str) -> IssueCreateRequest:
    """Title and labels derive from the report; the body is the rendering."""
    headline = report.summary.what_happened.split(". ")[0].strip().rstrip(".")
    if len(headline) > 90:
        headline = headline[:87] + "..."
    return IssueCreateRequest(
        repo=repo,
        title=f"[{report.severity.level}] {report.incident_id}: {headline}",
        body=markdown_body,
        labels=["incident", report.severity.level.lower()],
    )


class LivePublishClient:
    """POSTs to exactly one derived route; auth resolved at send time."""

    def __init__(
        self, base_url: str = DEFAULT_BASE_URL, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def create_issue(
        self, request: IssueCreateRequest, auth: EnvBearerAuth | None = None
    ) -> IssueCreated:
        url = f"{self._base}/repos/{request.repo}/issues"
        payload = json.dumps(
            {"title": request.title, "body": request.body, "labels": request.labels}
        ).encode("utf-8")
        headers = {"Accept": "application/vnd.github+json", "Content-Type": "application/json"}
        if auth is not None:
            headers[auth.header] = f"{auth.scheme} {_resolve_token(auth)}".strip()
        raw = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(raw, timeout=self._timeout) as reply:
                body = reply.read().decode("utf-8")
                status = reply.status
        except urllib.error.HTTPError as exc:
            raise PublishError(
                f"issue creation failed: HTTP {exc.code}: "
                f"{exc.read().decode('utf-8', 'replace')[:300]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise PublishError(f"issue creation failed: {exc.reason}") from exc
        if status not in (200, 201):
            raise PublishError(f"issue creation returned unexpected HTTP {status}")
        try:
            return IssueCreated.model_validate_json(body)
        except Exception as exc:
            raise PublishError(f"issue-create response was not understood: {exc}") from exc


def _request_key(request: IssueCreateRequest) -> str:
    import hashlib

    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class RecordingPublishClient:
    """Wraps a real client and writes a replayable fixture (credential-free
    by construction: the request type cannot carry headers)."""

    def __init__(self, inner: PublishClient, fixtures_dir: Path) -> None:
        self._inner = inner
        self._dir = fixtures_dir

    def create_issue(
        self, request: IssueCreateRequest, auth: EnvBearerAuth | None = None
    ) -> IssueCreated:
        response = self._inner.create_issue(request, auth)
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


class ReplayPublishClient:
    """Serves recorded fixtures; never touches the network."""

    def __init__(self, fixtures_dir: Path) -> None:
        self._dir = fixtures_dir

    def create_issue(
        self, request: IssueCreateRequest, auth: EnvBearerAuth | None = None
    ) -> IssueCreated:
        path = self._dir / f"{_request_key(request)}.json"
        if not path.exists():
            raise PublishError(f"no publish fixture {path.name} in {self._dir}")
        data = json.loads(path.read_text())
        if data["request"] != request.model_dump(mode="json"):
            raise PublishError(f"publish fixture {path.name} stores a different request")
        return IssueCreated.model_validate(data["response"])

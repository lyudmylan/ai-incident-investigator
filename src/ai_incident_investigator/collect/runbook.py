"""Runbook retrieval: configured documents carried verbatim into runbook.md.

No search, no RAG (out of scope for v2): the operator maps services to
documents in sources.toml, and the document for the alerting service is
retrieved as-is - from a local path or a GitHub file (read-only contents
API, base64-decoded).

Selection rule (docs/collection_sources.md): the entry whose `service`
equals the alert's service wins; else an entry with no `service` acts as
the catch-all; else the package simply has no runbook, with a note.
"""

import base64
import binascii
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ai_incident_investigator.collect.adapter import CollectionContext, PackageContribution
from ai_incident_investigator.collect.config import CollectError, SourcesConfig
from ai_incident_investigator.collect.http import (
    EnvBearerAuth,
    HTTPClient,
    HTTPClientError,
    HTTPRequest,
    raise_for_status,
)

SECTION = "runbook"


class RunbookDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    service: str | None = Field(default=None, description="None acts as the catch-all entry")
    file: str | None = Field(default=None, description="local path relative to sources.toml")
    repo: str | None = Field(default=None, description="owner/name for the GitHub mode")
    path: str | None = None
    ref: str | None = None

    @model_validator(mode="after")
    def _exactly_one_mode(self) -> "RunbookDocument":
        local = self.file is not None
        remote = self.repo is not None or self.path is not None
        if local and remote:
            raise ValueError("a runbook document is either file= or repo=+path=, not both")
        if not local and not (self.repo and self.path):
            raise ValueError("a runbook document needs file= or both repo= and path=")
        return self


class RunbookConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = "https://api.github.com"
    token_env: str | None = None
    documents: list[RunbookDocument] = Field(min_length=1)


class _GitHubContent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str
    encoding: str


def _select(documents: list[RunbookDocument], service: str) -> RunbookDocument | None:
    exact = next((d for d in documents if d.service == service), None)
    if exact is not None:
        return exact
    return next((d for d in documents if d.service is None), None)


class RunbookAdapter:
    def __init__(self, http: HTTPClient, config: RunbookConfig, config_dir: Path) -> None:
        self._http = http
        self._config = config
        self._config_dir = config_dir
        self._auth = (
            EnvBearerAuth(env_var=config.token_env) if config.token_env is not None else None
        )

    @property
    def name(self) -> str:
        return "runbook"

    def _fetch_local(self, document: RunbookDocument) -> str:
        assert document.file is not None
        path = (self._config_dir / document.file).resolve()
        if not path.is_file():
            raise CollectError(f"configured runbook file not found: {path}")
        return path.read_text()

    def _fetch_github(self, document: RunbookDocument) -> str:
        base = self._config.base_url.rstrip("/")
        params = {"ref": document.ref} if document.ref else {}
        request = HTTPRequest(
            url=f"{base}/repos/{document.repo}/contents/{document.path}", params=params
        )
        body = raise_for_status(request, self._http.get(request, self._auth)).body
        try:
            content = _GitHubContent.model_validate_json(body)
        except ValidationError as exc:
            raise CollectError(f"runbook contents response was not understood: {exc}") from exc
        if content.encoding != "base64":
            raise CollectError(f"unsupported runbook content encoding: {content.encoding!r}")
        try:
            return base64.b64decode(content.content).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise CollectError(f"runbook content could not be decoded: {exc}") from exc

    def collect(self, context: CollectionContext) -> PackageContribution:
        document = _select(self._config.documents, context.anchor_service)
        if document is None:
            return PackageContribution(
                notes=[
                    f"no runbook document configured for service "
                    f"'{context.anchor_service}' and no catch-all entry"
                ]
            )
        try:
            if document.file is not None:
                text = self._fetch_local(document)
            else:
                text = self._fetch_github(document)
        except HTTPClientError as exc:
            raise CollectError(f"runbook retrieval failed: {exc}") from exc
        return PackageContribution(runbook=text)


def runbook_adapter(config: SourcesConfig, http: HTTPClient) -> RunbookAdapter:
    """Build the adapter from a sources.toml [runbook] section."""
    try:
        section = RunbookConfig.model_validate(config.section(SECTION))
    except ValidationError as exc:
        raise CollectError(f"[{SECTION}] section is invalid: {exc}") from exc
    return RunbookAdapter(http, section, config.path.parent)

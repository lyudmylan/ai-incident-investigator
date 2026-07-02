"""Sentry-like issue source: the alert anchor for collection.

Speaks a subset of the Sentry REST wire format (issue detail + latest
event). Field mapping and normalization rules are documented in
docs/collection_sources.md - code and doc must be changed together.

Degradation within the source: the issue alone is enough to build the
alert (collection can proceed); the latest event only enriches it (service
tag, fresher trigger time, breadcrumb logs). Only an unusable issue is
fatal, which the orchestrator turns into a loud collection failure.
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ai_incident_investigator.collect.adapter import AlertBundle
from ai_incident_investigator.collect.config import CollectError, SourcesConfig
from ai_incident_investigator.collect.http import (
    EnvBearerAuth,
    HTTPClient,
    HTTPClientError,
    HTTPRequest,
    raise_for_status,
)
from ai_incident_investigator.models.package import Alert, LogRecord

SECTION = "sentry"

LogLevel = Literal["DEBUG", "INFO", "WARN", "ERROR", "FATAL"]

_LEVEL_MAP: dict[str, LogLevel] = {
    "debug": "DEBUG",
    "info": "INFO",
    "warning": "WARN",
    "warn": "WARN",
    "error": "ERROR",
    "fatal": "FATAL",
    "critical": "FATAL",
}

_DATETIME = TypeAdapter(datetime)


class SentryConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str
    token_env: str | None = None
    service_tag: str | None = Field(
        default=None, description="event tag key to resolve the service from"
    )


# Wire models: extra='ignore' on purpose - real payloads carry far more than
# the subset we map, and unknown fields must not break collection.


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SentryProject(_WireModel):
    slug: str


class SentryIssueMetadata(_WireModel):
    type: str | None = None
    value: str | None = None


class SentryIssue(_WireModel):
    id: str
    title: str
    project: SentryProject
    lastSeen: str  # Sentry wire field name; parsed via parse_sentry_time
    level: str | None = None
    culprit: str | None = None
    permalink: str | None = None
    metadata: SentryIssueMetadata | None = None


class SentryTag(_WireModel):
    key: str
    value: str


class SentryBreadcrumb(_WireModel):
    timestamp: str | None = None
    level: str | None = None
    category: str | None = None
    message: str | None = None


class SentryEntry(_WireModel):
    type: str
    data: dict[str, object] = Field(default_factory=dict)


class SentryEvent(_WireModel):
    dateCreated: str  # Sentry wire field name; parsed via parse_sentry_time
    tags: list[SentryTag] = Field(default_factory=list)
    entries: list[SentryEntry] = Field(default_factory=list)


def normalize_level(raw: str | None) -> LogLevel:
    return _LEVEL_MAP.get((raw or "").lower(), "INFO")


def parse_sentry_time(value: str) -> datetime:
    """Sentry timestamps are UTC; naive values get UTC attached (documented)."""
    parsed = _DATETIME.validate_python(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _breadcrumb_logs(event: SentryEvent, service: str) -> list[LogRecord]:
    records: list[LogRecord] = []
    for entry in event.entries:
        if entry.type != "breadcrumbs":
            continue
        values = entry.data.get("values")
        if not isinstance(values, list):
            continue
        for raw in values:
            try:
                crumb = SentryBreadcrumb.model_validate(raw)
            except ValidationError:
                continue
            if not crumb.timestamp or not crumb.message:
                continue  # documented conservative rule: no time or text, no log
            try:
                timestamp = parse_sentry_time(crumb.timestamp)
            except ValidationError:
                continue
            message = f"[{crumb.category}] {crumb.message}" if crumb.category else crumb.message
            records.append(
                LogRecord(
                    timestamp=timestamp,
                    service=service,
                    level=normalize_level(crumb.level),
                    message=message,
                )
            )
    return records


class SentryAlertSource:
    def __init__(self, http: HTTPClient, config: SentryConfig, issue_id: str) -> None:
        self._http = http
        self._config = config
        self._issue_id = issue_id
        self._auth = (
            EnvBearerAuth(env_var=config.token_env) if config.token_env is not None else None
        )

    @property
    def name(self) -> str:
        return "sentry"

    def _get_json(self, url: str) -> str:
        request = HTTPRequest(url=url)
        return raise_for_status(request, self._http.get(request, self._auth)).body

    def _fetch_issue(self) -> SentryIssue:
        base = self._config.base_url.rstrip("/")
        body = self._get_json(f"{base}/issues/{self._issue_id}/")
        try:
            return SentryIssue.model_validate_json(body)
        except ValidationError as exc:
            raise CollectError(
                f"sentry issue {self._issue_id} response was not understood: {exc}"
            ) from exc

    def _fetch_latest_event(self) -> SentryEvent | None:
        base = self._config.base_url.rstrip("/")
        try:
            body = self._get_json(f"{base}/issues/{self._issue_id}/events/latest/")
            return SentryEvent.model_validate_json(body)
        except (HTTPClientError, ValidationError):
            return None  # enrichment only; the issue alone anchors the alert

    def fetch_alert(self) -> AlertBundle:
        issue = self._fetch_issue()
        event = self._fetch_latest_event()

        service = issue.project.slug
        if event is not None and self._config.service_tag:
            for tag in event.tags:
                if tag.key == self._config.service_tag and tag.value:
                    service = tag.value
                    break

        if event is not None:
            triggered_at = parse_sentry_time(event.dateCreated)
        else:
            triggered_at = parse_sentry_time(issue.lastSeen)

        description_parts: list[str] = []
        if issue.metadata is not None and issue.metadata.value:
            description_parts.append(issue.metadata.value)
        if issue.culprit:
            description_parts.append(f"culprit: {issue.culprit}")
        if issue.permalink:
            description_parts.append(issue.permalink)

        alert = Alert(
            id=f"sentry_{issue.id}",
            title=issue.title,
            service=service,
            triggered_at=triggered_at,
            severity=issue.level,
            description=" | ".join(description_parts) or None,
        )

        logs: list[LogRecord] = []
        if event is not None:
            logs.append(
                LogRecord(
                    timestamp=triggered_at,
                    service=service,
                    level=normalize_level(issue.level or "error"),
                    message=issue.title,
                )
            )
            logs.extend(_breadcrumb_logs(event, service))
        return AlertBundle(alert=alert, logs=logs)


def sentry_alert_source(
    config: SourcesConfig, http: HTTPClient, issue_id: str
) -> SentryAlertSource:
    """Build the source from a sources.toml [sentry] section."""
    try:
        section = SentryConfig.model_validate(config.section(SECTION))
    except ValidationError as exc:
        raise CollectError(f"[{SECTION}] section is invalid: {exc}") from exc
    return SentryAlertSource(http, section, issue_id)

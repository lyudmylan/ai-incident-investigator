"""Loki-like log adapter: query_range streams in, logs.jsonl records out.

Wire subset: GET {base_url}/loki/api/v1/query_range with query (a configured
stream selector - no query authoring), start/end in unix nanoseconds,
limit, direction=forward. Mapping rules: docs/collection_sources.md.

Unlike metrics, a selector legitimately matches several streams (pods,
instances); all matched streams merge chronologically. Level resolution is
documented and conservative: stream label first, then the first level token
in the line, else INFO.
"""

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_incident_investigator.collect.adapter import CollectionContext, PackageContribution
from ai_incident_investigator.collect.config import CollectError, SourcesConfig
from ai_incident_investigator.collect.http import (
    EnvBearerAuth,
    HTTPClient,
    HTTPClientError,
    HTTPRequest,
    raise_for_status,
)
from ai_incident_investigator.collect.normalize import level_from_text, normalize_level
from ai_incident_investigator.models.package import LogRecord

SECTION = "loki"

_LEVEL_LABELS = ("level", "detected_level", "severity")


class LokiStream(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    service: str
    selector: str


class LokiConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str
    token_env: str | None = None
    limit: int = 500
    post_minutes: int = 30
    streams: list[LokiStream] = Field(min_length=1)


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class LokiResult(_WireModel):
    stream: dict[str, str] = Field(default_factory=dict)
    values: list[tuple[str, str]] = Field(default_factory=list)  # [ns-string, line]


class LokiData(_WireModel):
    result: list[LokiResult] = Field(default_factory=list)


class LokiResponse(_WireModel):
    status: str
    data: LokiData | None = None
    error: str | None = None


def _record_from(raw_ts: str, line: str, labels: dict[str, str], service: str) -> LogRecord:
    timestamp = datetime.fromtimestamp(int(raw_ts) / 1e9, tz=UTC)
    label_level = next((labels[key] for key in _LEVEL_LABELS if key in labels), None)
    level = normalize_level(label_level) if label_level else level_from_text(line)
    return LogRecord(timestamp=timestamp, service=service, level=level, message=line.strip())


class LokiLogsAdapter:
    def __init__(self, http: HTTPClient, config: LokiConfig) -> None:
        self._http = http
        self._config = config
        self._auth = (
            EnvBearerAuth(env_var=config.token_env) if config.token_env is not None else None
        )

    @property
    def name(self) -> str:
        return "loki"

    def _query_stream(self, stream: LokiStream, start: datetime, end: datetime) -> LokiData:
        request = HTTPRequest(
            url=f"{self._config.base_url.rstrip('/')}/loki/api/v1/query_range",
            params={
                "query": stream.selector,
                # whole-second int math: float ns multiplication loses precision
                "start": str(int(start.timestamp()) * 1_000_000_000),
                "end": str(int(end.timestamp()) * 1_000_000_000),
                "limit": str(self._config.limit),
                "direction": "forward",
            },
        )
        body = raise_for_status(request, self._http.get(request, self._auth)).body
        try:
            parsed = LokiResponse.model_validate_json(body)
        except ValidationError as exc:
            raise HTTPClientError(f"query_range response was not understood: {exc}") from exc
        if parsed.status != "success" or parsed.data is None:
            raise HTTPClientError(
                f"query_range returned status={parsed.status!r} ({parsed.error or ''})".strip()
            )
        return parsed.data

    def _collect_stream(
        self, stream: LokiStream, start: datetime, end: datetime, notes: list[str]
    ) -> list[LogRecord]:
        data = self._query_stream(stream, start, end)
        records: list[LogRecord] = []
        unparseable = 0
        for result in data.result:
            for raw_ts, line in result.values:
                try:
                    records.append(_record_from(raw_ts, line, result.stream, stream.service))
                except (ValueError, ValidationError):
                    unparseable += 1
        if unparseable:
            notes.append(f"{stream.service}: {unparseable} unparseable log line(s) skipped")
        total_lines = sum(len(result.values) for result in data.result)
        if total_lines >= self._config.limit:
            notes.append(
                f"{stream.service}: hit the {self._config.limit}-line limit; "
                "older lines in the window are included first (direction=forward), "
                "later ones may be truncated"
            )
        if not records:
            notes.append(f"{stream.service}: no log lines in the window")
        return records

    def collect(self, context: CollectionContext) -> PackageContribution:
        start = context.anchor_time - context.lookback
        end = context.anchor_time + timedelta(minutes=self._config.post_minutes)
        notes: list[str] = []
        records: list[LogRecord] = []
        failures: list[str] = []
        for stream in self._config.streams:
            try:
                records.extend(self._collect_stream(stream, start, end, notes))
            except HTTPClientError as exc:
                failures.append(stream.service)
                notes.append(f"{stream.service} skipped: {exc}")
        if failures and len(failures) == len(self._config.streams):
            raise CollectError("no log stream could be collected: " + "; ".join(notes))
        records.sort(key=lambda r: (r.timestamp, r.service, r.message))
        return PackageContribution(logs=records, notes=notes)


def loki_adapter(config: SourcesConfig, http: HTTPClient) -> LokiLogsAdapter:
    """Build the adapter from a sources.toml [loki] section."""
    try:
        section = LokiConfig.model_validate(config.section(SECTION))
    except ValidationError as exc:
        raise CollectError(f"[{SECTION}] section is invalid: {exc}") from exc
    return LokiLogsAdapter(http, section)

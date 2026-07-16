"""Read-only setup validation (#79): onboarding becomes test-driven.

Probes every CONFIGURED source with the same adapters collection uses -
without needing an incident. Each check is PASS/FAIL/SKIP with the exact
fix in the detail (the adapters' own error messages are the hints: "query
returned 3 series; exactly one is required - make the configured query
more specific"). No writes, no LLM, GET-only - the same structural
guarantees as collection itself. A probe can fail; the doctor never
crashes on one.
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ai_incident_investigator.collect.adapter import CollectionContext, SourceAdapter
from ai_incident_investigator.collect.config import SourcesConfig
from ai_incident_investigator.collect.github import GitHubDeploysAdapter
from ai_incident_investigator.collect.http import HTTPClient
from ai_incident_investigator.collect.local import LocalTopologyAdapter
from ai_incident_investigator.collect.loki import LokiLogsAdapter
from ai_incident_investigator.collect.prometheus import PrometheusMetricsAdapter, compute_spans
from ai_incident_investigator.collect.registry import build_sources
from ai_incident_investigator.collect.runbook import RunbookAdapter
from ai_incident_investigator.collect.sentry import SentryAlertSource
from ai_incident_investigator.models.common import config_leaves

DoctorStatus = Literal["PASS", "FAIL", "SKIP"]


class DoctorCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    check: str
    status: DoctorStatus
    detail: str


def _credential_checks(config: SourcesConfig) -> list[DoctorCheck]:
    """Offline first: every *_env reference must resolve to a set variable.
    Values are never read into the results - only presence."""
    checks: list[DoctorCheck] = []
    for location, key, value in config_leaves(config.sections):
        if key.lower().endswith("_env") and isinstance(value, str):
            present = bool(os.environ.get(value))
            checks.append(
                DoctorCheck(
                    source="credentials",
                    check=f"${value} ({location})",
                    status="PASS" if present else "FAIL",
                    detail="set" if present else "NOT set - add it to .env and use --env-file",
                )
            )
    return checks


def _probe(source: str, check: str, action: "object") -> DoctorCheck:
    """Run one probe callable; any exception is the FAIL detail."""
    try:
        detail = action() if callable(action) else str(action)
    except Exception as exc:
        return DoctorCheck(source=source, check=check, status="FAIL", detail=str(exc))
    return DoctorCheck(source=source, check=check, status="PASS", detail=str(detail))


def _adapter_checks(
    adapter: SourceAdapter, context: CollectionContext, now: datetime
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    if isinstance(adapter, PrometheusMetricsAdapter):
        spans = compute_spans(context, adapter._config.post_minutes)
        for spec in adapter._config.queries:

            def probe_query(query: str = spec.query) -> str:
                result = adapter._query_range(query, spans)
                return f"exactly one series, {len(result.values)} sample(s)"

            checks.append(_probe("prometheus", f"{spec.service}/{spec.signal}", probe_query))
    elif isinstance(adapter, LokiLogsAdapter):
        for stream in adapter._config.streams:

            def probe_stream(entry: object = stream) -> str:
                data = adapter._query_stream(entry, now - context.lookback, now)  # type: ignore[arg-type]
                if not data.result:
                    raise RuntimeError(
                        "selector matched no streams in the lookback window - check it "
                        "against Loki's label browser (an idle service is also possible)"
                    )
                lines = sum(len(s.values) for s in data.result)
                return f"{len(data.result)} stream(s), {lines} line(s) in the lookback window"

            checks.append(_probe("loki", f"{stream.service} {stream.selector}", probe_stream))
    elif isinstance(adapter, GitHubDeploysAdapter):
        base = adapter._config.base_url.rstrip("/")
        for repo in adapter._config.repos:

            def probe_repo(name: str = repo.repo) -> str:
                adapter._get_list(f"{base}/repos/{name}/releases", {"per_page": "1"})
                return "releases endpoint readable"

            checks.append(_probe("github", repo.repo, probe_repo))
    elif isinstance(adapter, RunbookAdapter):
        for document in adapter._config.documents:
            label = document.file or f"{document.repo}/{document.path}"

            def probe_document(entry: object = document) -> str:
                text = (
                    adapter._fetch_local(entry)  # type: ignore[arg-type]
                    if getattr(entry, "file", None) is not None
                    else adapter._fetch_github(entry)  # type: ignore[arg-type]
                )
                return f"readable ({len(text)} chars)"

            checks.append(_probe("runbook", label, probe_document))
    elif isinstance(adapter, LocalTopologyAdapter):

        def probe_topology() -> str:
            contribution = adapter.collect(context)
            services = len(contribution.topology.services) if contribution.topology else 0
            return f"valid ({services} service(s))"

        checks.append(_probe("topology", adapter._file.name, probe_topology))
    else:  # a future adapter without a doctor probe is visible, not silent
        checks.append(
            DoctorCheck(
                source=adapter.name,
                check="probe",
                status="SKIP",
                detail="no doctor probe implemented for this adapter",
            )
        )
    return checks


def run_doctor(
    config: SourcesConfig,
    http: HTTPClient,
    issue_id: str | None = None,
    now: datetime | None = None,
) -> list[DoctorCheck]:
    """Every check for every configured source, offline checks first."""
    now = now or datetime.now(UTC)
    checks = _credential_checks(config)
    alert_source, adapters = build_sources(config, http, issue_id or "0")
    if issue_id is None:
        checks.append(
            DoctorCheck(
                source="sentry",
                check="alert anchor",
                status="SKIP",
                detail="pass --issue <id> to probe the anchor end-to-end",
            )
        )
    else:
        assert isinstance(alert_source, SentryAlertSource)

        def probe_anchor() -> str:
            bundle = alert_source.fetch_alert()
            return (
                f"issue {issue_id} anchors at {bundle.alert.triggered_at.isoformat()} "
                f"(service: {bundle.alert.service})"
            )

        checks.append(_probe("sentry", f"issue {issue_id}", probe_anchor))
    context = CollectionContext(
        anchor_time=now,
        anchor_service=(config.collection.services or ["unknown"])[0],
        lookback=timedelta(minutes=config.collection.lookback_minutes),
        change_lookback=timedelta(days=config.collection.change_lookback_days),
        services=config.collection.services,
    )
    for adapter in adapters:
        checks.extend(_adapter_checks(adapter, context, now))
    return checks


def render_doctor(checks: list[DoctorCheck]) -> str:
    lines = [f"{c.status:<4} [{c.source}] {c.check}: {c.detail}" for c in checks]
    passed = sum(1 for c in checks if c.status == "PASS")
    failed = sum(1 for c in checks if c.status == "FAIL")
    skipped = sum(1 for c in checks if c.status == "SKIP")
    lines.append("")
    lines.append(
        f"{passed} passed, {failed} failed, {skipped} skipped"
        + (" - fix the FAIL lines above and re-run" if failed else " - ready to collect")
    )
    return "\n".join(lines)

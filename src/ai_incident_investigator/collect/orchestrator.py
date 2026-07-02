"""Collection orchestrator: adapters in, an ordinary incident package out.

Snapshot-first (architecture decision, epic #17): the output is a plain
package directory the v1 pipeline investigates unchanged, so every live
incident becomes a replayable offline package.

Failure model mirrors the agent graph: the alert source is the one fatal
dependency (no anchor, no window); every other adapter failure is recorded
in the collection report and the package simply lacks that file - the v1
loader turns the gap into missing_data at investigation time.
"""

from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_incident_investigator.collect.adapter import (
    AlertSource,
    CollectionContext,
    PackageContribution,
    SourceAdapter,
)
from ai_incident_investigator.collect.config import CollectError, CollectionSettings
from ai_incident_investigator.loading import PackageLoadError, load_package
from ai_incident_investigator.models.package import LogRecord

REPORT_FILENAME = "collection_report.json"

_SINGLE_SLOTS = ("metrics", "traces", "deploys", "topology", "runbook")
_SLOT_FILENAMES = {
    "metrics": "metrics.json",
    "traces": "traces.json",
    "deploys": "deploys.json",
    "topology": "topology.json",
    "runbook": "runbook.md",
}


class SourceStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    status: Literal["ok", "failed"]
    files: list[str] = Field(default_factory=list)
    detail: str | None = None


class CollectionReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    package_dir: str
    sources: list[SourceStatus]


def _prepare_out_dir(out_dir: Path) -> None:
    if out_dir.exists():
        if not out_dir.is_dir():
            raise CollectError(f"collection target {out_dir} exists and is not a directory")
        if any(out_dir.iterdir()):
            raise CollectError(
                f"collection target {out_dir} is not empty; refusing to overwrite an "
                "existing package (snapshots are incident evidence)"
            )
    out_dir.mkdir(parents=True, exist_ok=True)


def _merge(
    merged: PackageContribution,
    addition: PackageContribution,
    owners: dict[str, str],
    adapter_name: str,
) -> PackageContribution:
    changes: dict[str, object] = {}
    for slot in _SINGLE_SLOTS:
        value = getattr(addition, slot)
        if value is None:
            continue
        if getattr(merged, slot) is not None:
            raise CollectError(
                f"[{adapter_name}] contributes {_SLOT_FILENAMES[slot]}, already "
                f"contributed by [{owners[slot]}] - two sources for one file is a "
                "configuration bug"
            )
        owners[slot] = adapter_name
        changes[slot] = value
    if addition.logs:
        changes["logs"] = [*merged.logs, *addition.logs]
    return merged.model_copy(update=changes) if changes else merged


def _write_package(
    out_dir: Path, bundle_logs: list[LogRecord], merged: PackageContribution, alert_json: str
) -> list[str]:
    written = ["alert.json"]
    (out_dir / "alert.json").write_text(alert_json)

    logs = sorted([*bundle_logs, *merged.logs], key=lambda r: (r.timestamp, r.service))
    if logs:
        lines = "".join(record.model_dump_json() + "\n" for record in logs)
        (out_dir / "logs.jsonl").write_text(lines)
        written.append("logs.jsonl")

    for slot in ("metrics", "traces", "deploys", "topology"):
        value = getattr(merged, slot)
        if value is not None:
            (out_dir / _SLOT_FILENAMES[slot]).write_text(value.model_dump_json(indent=2) + "\n")
            written.append(_SLOT_FILENAMES[slot])
    if merged.runbook is not None:
        (out_dir / "runbook.md").write_text(merged.runbook)
        written.append("runbook.md")
    return written


def collect_package(
    alert_source: AlertSource,
    adapters: Sequence[SourceAdapter],
    out_dir: Path,
    settings: CollectionSettings,
) -> CollectionReport:
    _prepare_out_dir(out_dir)

    try:
        bundle = alert_source.fetch_alert()
    except Exception as exc:
        raise CollectError(
            f"alert source [{alert_source.name}] failed: {exc} - collection cannot "
            "proceed without the alert anchor"
        ) from exc

    context = CollectionContext(
        anchor_time=bundle.alert.triggered_at,
        lookback=timedelta(minutes=settings.lookback_minutes),
        change_lookback=timedelta(days=settings.change_lookback_days),
        services=settings.services,
    )

    statuses: list[SourceStatus] = [
        SourceStatus(name=alert_source.name, status="ok", files=["alert.json"])
    ]
    merged = PackageContribution()
    owners: dict[str, str] = {}
    for adapter in adapters:
        try:
            contribution = adapter.collect(context)
            merged = _merge(merged, contribution, owners, adapter.name)
        except Exception as exc:
            statuses.append(SourceStatus(name=adapter.name, status="failed", detail=str(exc)))
            continue
        files = [
            _SLOT_FILENAMES[slot]
            for slot in _SINGLE_SLOTS
            if getattr(contribution, slot) is not None
        ]
        if contribution.logs:
            files.append("logs.jsonl")
        statuses.append(SourceStatus(name=adapter.name, status="ok", files=sorted(files)))

    _write_package(
        out_dir, list(bundle.logs), merged, bundle.alert.model_dump_json(indent=2) + "\n"
    )

    # Self-check: a collected package must be loadable by the v1 pipeline.
    try:
        load_package(out_dir)
    except PackageLoadError as exc:  # pragma: no cover - guards future regressions
        raise CollectError(f"collected package failed the loader self-check: {exc}") from exc

    report = CollectionReport(package_dir=str(out_dir), sources=statuses)
    (out_dir / REPORT_FILENAME).write_text(report.model_dump_json(indent=2) + "\n")
    return report

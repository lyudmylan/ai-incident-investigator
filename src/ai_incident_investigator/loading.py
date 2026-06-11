"""Incident package loader.

Deterministic-facts layer (product.md Principle 4). The only fatal condition
is an unusable alert.json — everything else degrades into `missing_data`
entries so a partial package still yields a partial investigation.
"""

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from ai_incident_investigator.ids import stable_id
from ai_incident_investigator.models.package import (
    Alert,
    DeploysFile,
    IncidentPackage,
    LogRecord,
    MetricsFile,
    TopologyFile,
    TracesFile,
)
from ai_incident_investigator.models.report import MissingData

LOG_LEVELS = ("DEBUG", "INFO", "WARN", "ERROR", "FATAL")

# Best-effort logs.txt line format: "<ISO timestamp> <LEVEL> <service> <message>"
_LOGS_TXT_PATTERN = re.compile(
    r"^(?P<timestamp>\S+)\s+(?P<level>"
    + "|".join(LOG_LEVELS)
    + r")\s+(?P<service>\S+)\s+(?P<message>.+)$"
)

_FILE_IMPACT = {
    "metrics.json": "cannot measure deviation from baselines or detect recovery",
    "logs": "no log-pattern evidence or error timestamps",
    "traces.json": "cannot identify slow spans or failing dependencies",
    "deploys.json": "cannot correlate incident timing with recent changes",
    "topology.json": "cannot reason about dependencies or blast radius",
    "runbook.md": "no operational guidance or known failure modes available",
}


class PackageLoadError(Exception):
    """The package cannot anchor an investigation (missing/invalid alert.json)."""


class LoadedPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package: IncidentPackage
    missing_data: list[MissingData]


def _missing(filename: str, description: str) -> MissingData:
    impact = _FILE_IMPACT["logs" if filename.startswith("logs") else filename]
    return MissingData(
        id=stable_id("missing", filename, description),
        description=description,
        impact=impact,
    )


def _validation_summary(error: ValidationError) -> str:
    first = error.errors()[0]
    location = ".".join(str(part) for part in first["loc"]) or "<root>"
    return f"{location}: {first['msg']} ({error.error_count()} error(s) total)"


def _load_json_file[ModelT: BaseModel](
    directory: Path,
    filename: str,
    model: type[ModelT],
    problems: list[MissingData],
) -> ModelT | None:
    path = directory / filename
    if not path.exists():
        problems.append(_missing(filename, f"{filename} not provided"))
        return None
    try:
        return model.model_validate(json.loads(path.read_text()))
    except OSError as exc:
        problems.append(_missing(filename, f"{filename} could not be read: {exc}"))
    except json.JSONDecodeError as exc:
        problems.append(_missing(filename, f"{filename} is not valid JSON: {exc}"))
    except ValidationError as exc:
        problems.append(
            _missing(filename, f"{filename} failed validation: {_validation_summary(exc)}")
        )
    return None


def _parse_jsonl_logs(path: Path, problems: list[MissingData]) -> list[LogRecord]:
    records: list[LogRecord] = []
    bad_lines: list[int] = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(LogRecord.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValidationError):
            bad_lines.append(line_number)
    if bad_lines:
        shown = ", ".join(str(n) for n in bad_lines[:5])
        problems.append(
            _missing(
                "logs.jsonl",
                f"logs.jsonl: {len(bad_lines)} unparseable line(s) skipped (lines {shown})",
            )
        )
    return records


def _parse_txt_logs(path: Path, problems: list[MissingData]) -> list[LogRecord]:
    records: list[LogRecord] = []
    unparsed = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        match = _LOGS_TXT_PATTERN.match(line)
        if match is None:
            unparsed += 1
            continue
        try:
            records.append(LogRecord.model_validate(match.groupdict()))
        except ValidationError:
            unparsed += 1
    if unparsed:
        problems.append(
            _missing(
                "logs.txt",
                f"logs.txt: {unparsed} line(s) did not match "
                "'<ISO timestamp> <LEVEL> <service> <message>' and were skipped",
            )
        )
    return records


def _load_logs(directory: Path, problems: list[MissingData]) -> list[LogRecord]:
    jsonl = directory / "logs.jsonl"
    txt = directory / "logs.txt"
    try:
        if jsonl.exists():
            if txt.exists():
                problems.append(
                    _missing("logs.txt", "logs.txt ignored because logs.jsonl is present")
                )
            return _parse_jsonl_logs(jsonl, problems)
        if txt.exists():
            return _parse_txt_logs(txt, problems)
    except OSError as exc:
        problems.append(_missing("logs.jsonl", f"log file could not be read: {exc}"))
        return []
    problems.append(_missing("logs.jsonl", "logs.jsonl / logs.txt not provided"))
    return []


def load_package(directory: Path) -> LoadedPackage:
    """Load and validate an incident package directory.

    Raises PackageLoadError only when alert.json is missing or invalid;
    every other problem becomes a missing_data entry.
    """
    if not directory.is_dir():
        raise PackageLoadError(f"incident package directory not found: {directory}")

    alert_path = directory / "alert.json"
    if not alert_path.exists():
        raise PackageLoadError(
            f"alert.json is required and was not found in {directory} "
            "(it anchors the incident window; see docs/incident_package_contract.md)"
        )
    try:
        alert = Alert.model_validate(json.loads(alert_path.read_text()))
    except OSError as exc:
        raise PackageLoadError(f"alert.json could not be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PackageLoadError(f"alert.json is not valid JSON: {exc}") from exc
    except ValidationError as exc:
        raise PackageLoadError(f"alert.json failed validation: {_validation_summary(exc)}") from exc

    problems: list[MissingData] = []
    metrics = _load_json_file(directory, "metrics.json", MetricsFile, problems)
    traces = _load_json_file(directory, "traces.json", TracesFile, problems)
    deploys = _load_json_file(directory, "deploys.json", DeploysFile, problems)
    topology = _load_json_file(directory, "topology.json", TopologyFile, problems)
    logs = _load_logs(directory, problems)

    runbook_path = directory / "runbook.md"
    runbook: str | None = None
    if runbook_path.exists():
        try:
            runbook = runbook_path.read_text()
        except OSError as exc:
            problems.append(_missing("runbook.md", f"runbook.md could not be read: {exc}"))
    else:
        problems.append(_missing("runbook.md", "runbook.md not provided"))

    package = IncidentPackage(
        incident_id=directory.name,
        alert=alert,
        metrics=metrics,
        logs=logs,
        traces=traces,
        deploys=deploys,
        topology=topology,
        runbook=runbook,
    )
    problems.sort(key=lambda m: m.id)
    return LoadedPackage(package=package, missing_data=problems)

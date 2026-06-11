"""Every example incident package must validate against the input contract."""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from ai_incident_investigator.models.package import (
    Alert,
    DeploysFile,
    LogRecord,
    MetricsFile,
    TopologyFile,
    TracesFile,
)

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "incidents"
JSON_FILE_MODELS: dict[str, type[BaseModel]] = {
    "alert.json": Alert,
    "metrics.json": MetricsFile,
    "traces.json": TracesFile,
    "deploys.json": DeploysFile,
    "topology.json": TopologyFile,
}

example_dirs = sorted(p for p in EXAMPLES_DIR.iterdir() if p.is_dir())


def test_examples_exist() -> None:
    assert example_dirs, "no example incident packages found"


@pytest.mark.parametrize("package_dir", example_dirs, ids=lambda p: p.name)
def test_alert_is_present(package_dir: Path) -> None:
    assert (package_dir / "alert.json").exists(), "alert.json is the one required file"


@pytest.mark.parametrize("package_dir", example_dirs, ids=lambda p: p.name)
def test_json_files_validate(package_dir: Path) -> None:
    for filename, model in JSON_FILE_MODELS.items():
        path = package_dir / filename
        if path.exists():
            model.model_validate(json.loads(path.read_text()))


@pytest.mark.parametrize("package_dir", example_dirs, ids=lambda p: p.name)
def test_log_lines_validate(package_dir: Path) -> None:
    path = package_dir / "logs.jsonl"
    if not path.exists():
        pytest.skip("package has no logs.jsonl")
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if line.strip():
            try:
                LogRecord.model_validate(json.loads(line))
            except Exception as exc:
                raise AssertionError(f"{path.name}:{line_number}: {exc}") from exc

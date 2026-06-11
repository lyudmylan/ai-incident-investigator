from pathlib import Path

import pytest

from ai_incident_investigator.cli import main


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0


def test_missing_incident_dir_is_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--incident", str(tmp_path / "does-not-exist")])
    assert excinfo.value.code == 2


def test_existing_incident_dir_reports_not_implemented(tmp_path: Path) -> None:
    assert main(["--incident", str(tmp_path)]) == 1

"""Contract docs must match the models; safety properties must be in the schema."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_incident_investigator.contracts import contract_files
from ai_incident_investigator.models.report import MitigationOption

DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"


@pytest.mark.parametrize("path_content", contract_files(DOCS_DIR).items(), ids=lambda p: p[0].name)
def test_contract_docs_are_up_to_date(path_content: tuple[Path, str]) -> None:
    path, expected = path_content
    assert path.exists(), (
        f"{path.name} missing; run: uv run python -m ai_incident_investigator.contracts"
    )
    regenerate = "uv run python -m ai_incident_investigator.contracts"
    assert path.read_text() == expected, f"{path.name} is stale; regenerate with: {regenerate}"


def test_mitigation_cannot_skip_human_approval() -> None:
    with pytest.raises(ValidationError):
        MitigationOption.model_validate(
            {
                "id": "mitigation_001",
                "action": "rollback",
                "rationale": "test",
                "requires_human_approval": False,
            }
        )


def test_mitigation_defaults_to_requiring_approval() -> None:
    option = MitigationOption(id="mitigation_001", action="rollback", rationale="test")
    assert option.requires_human_approval is True

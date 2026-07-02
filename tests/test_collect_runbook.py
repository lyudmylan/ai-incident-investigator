from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_incident_investigator.collect import CollectError, load_sources_config
from ai_incident_investigator.collect.runbook import (
    RunbookAdapter,
    RunbookConfig,
    RunbookDocument,
    runbook_adapter,
)
from loki_github_stubs import GITHUB_BASE_URL, RUNBOOK_REMOTE_TEXT, GitHubStubHTTP
from prometheus_stub import demo_collection_context

CONTEXT = demo_collection_context()  # anchor_service == "booking-service"


def _local_config(tmp_path: Path, service: str | None = "booking-service") -> RunbookConfig:
    (tmp_path / "booking.md").write_text("# local runbook\n")
    return RunbookConfig(documents=[RunbookDocument(service=service, file="booking.md")])


def test_local_document_is_carried_verbatim(tmp_path: Path) -> None:
    adapter = RunbookAdapter(GitHubStubHTTP(), _local_config(tmp_path), tmp_path)
    contribution = adapter.collect(CONTEXT)
    assert contribution.runbook == "# local runbook\n"


def test_selection_exact_over_catch_all(tmp_path: Path) -> None:
    (tmp_path / "booking.md").write_text("booking doc")
    (tmp_path / "generic.md").write_text("generic doc")
    config = RunbookConfig(
        documents=[
            RunbookDocument(service=None, file="generic.md"),
            RunbookDocument(service="booking-service", file="booking.md"),
        ]
    )
    contribution = RunbookAdapter(GitHubStubHTTP(), config, tmp_path).collect(CONTEXT)
    assert contribution.runbook == "booking doc"


def test_catch_all_fallback_and_no_match_note(tmp_path: Path) -> None:
    (tmp_path / "generic.md").write_text("generic doc")
    catch_all = RunbookConfig(documents=[RunbookDocument(file="generic.md")])
    assert (
        RunbookAdapter(GitHubStubHTTP(), catch_all, tmp_path).collect(CONTEXT).runbook
        == "generic doc"
    )

    other_service = RunbookConfig(
        documents=[RunbookDocument(service="payments-service", file="generic.md")]
    )
    contribution = RunbookAdapter(GitHubStubHTTP(), other_service, tmp_path).collect(CONTEXT)
    assert contribution.runbook is None
    assert any("no runbook document configured" in note for note in contribution.notes)


def test_github_mode_decodes_base64(tmp_path: Path) -> None:
    config = RunbookConfig(
        base_url=GITHUB_BASE_URL,
        documents=[
            RunbookDocument(service="booking-service", repo="acme/runbooks", path="booking.md")
        ],
    )
    contribution = RunbookAdapter(GitHubStubHTTP(), config, tmp_path).collect(CONTEXT)
    assert contribution.runbook == RUNBOOK_REMOTE_TEXT


def test_missing_local_file_fails_the_adapter(tmp_path: Path) -> None:
    config = RunbookConfig(documents=[RunbookDocument(service="booking-service", file="absent.md")])
    with pytest.raises(CollectError, match="not found"):
        RunbookAdapter(GitHubStubHTTP(), config, tmp_path).collect(CONTEXT)


def test_document_mode_validation() -> None:
    with pytest.raises(ValidationError, match="not both"):
        RunbookDocument(service="s", file="x.md", repo="a/b", path="c.md")
    with pytest.raises(ValidationError, match="needs file= or both"):
        RunbookDocument(service="s", repo="a/b")  # path missing


def test_factory_validates_section(tmp_path: Path) -> None:
    good = tmp_path / "sources.toml"
    (tmp_path / "doc.md").write_text("doc")
    good.write_text('[runbook]\n[[runbook.documents]]\nfile = "doc.md"\n')
    adapter = runbook_adapter(load_sources_config(good), GitHubStubHTTP())
    assert adapter.collect(CONTEXT).runbook == "doc"

    bad = tmp_path / "bad.toml"
    bad.write_text("[runbook]\n")  # no documents
    with pytest.raises(CollectError, match=r"\[runbook\] section is invalid"):
        runbook_adapter(load_sources_config(bad), GitHubStubHTTP())

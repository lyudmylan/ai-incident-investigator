"""v4 end-to-end flow (epic #54): investigate -> publish -> approve ->
follow-up compare, entirely from fixtures and stubs. Zero tokens, zero
network - and the write-path guarantees re-verified along the way.
"""

import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ai_incident_investigator.approvals import (
    is_actionable,
    load_approvals,
    report_hash,
)
from ai_incident_investigator.assemble import build_report
from ai_incident_investigator.cli import main
from ai_incident_investigator.collect.config import _PUBLISH_TOKEN_ENV, load_sources_config
from ai_incident_investigator.compare import build_comparison
from ai_incident_investigator.llm import ReplayClient
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.models.report import InvestigationReport
from ai_incident_investigator.pipeline import initial_state, run_investigation
from ai_incident_investigator.publish import (
    DEFAULT_TOKEN_ENV,
    IssueCreateRequest,
    LivePublishClient,
    PublishError,
    render_issue,
)
from publish_stub import GitHubIssueStub

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "incidents" / "latency_spike"
FOLLOW_UP = ROOT / "examples" / "followups" / "latency_spike"
FIXTURES = ROOT / "tests" / "fixtures" / "llm" / "latency_spike"


def test_the_full_v4_flow_from_fixtures(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # 1. investigate (replay - zero tokens)
    state = run_investigation(initial_state(load_package(EXAMPLE)), ReplayClient(FIXTURES))
    report = build_report(state)
    report_file = tmp_path / "report.json"
    report_file.write_text(report.model_dump_json(indent=2) + "\n")

    # 2. publish (stub - zero network); the issue carries the full rendering
    from ai_incident_investigator.markdown import render_markdown

    stub = GitHubIssueStub()
    created = stub.create_issue(render_issue(report, "acme/incidents", render_markdown(report)))
    assert created.number == 101
    published_request = stub.calls[0][0]
    assert published_request.title.startswith("[SEV-2] latency_spike")

    # 3. approve a state-changing step via the real CLI
    plan = next(p for p in report.remediation_plans if p.kind == "rollback")
    step_index = next(i for i, s in enumerate(plan.steps) if s.kind == "state_changing")
    code = main(
        [
            "approve",
            "--report",
            str(report_file),
            "--plan",
            plan.id,
            "--step",
            str(step_index),
            "--approver",
            "lyudmyla",
        ]
    )
    assert code == 0
    capsys.readouterr()

    # the v5 gate accepts exactly this step, and only this step
    loaded = InvestigationReport.model_validate_json(report_file.read_text())
    records = load_approvals(report_file)
    current = report_hash(report_file)
    now = datetime.now(UTC)
    ok, reason = is_actionable(loaded, records, current, plan.id, step_index, now)
    assert ok and "approved by lyudmyla" in reason
    other_plan = next(p for p in report.remediation_plans if p.id != plan.id)
    ok, _ = is_actionable(loaded, records, current, other_plan.id, 1, now)
    assert not ok

    # approval records carry no credential-shaped content
    approvals_text = (tmp_path / "report.approvals.json").read_text()
    for marker in ("sk-ant", "Authorization", "Bearer", "TOKEN"):
        assert marker not in approvals_text

    # 4. the follow-up snapshot comparison (deterministic)
    comparison = build_comparison(load_package(EXAMPLE).package, load_package(FOLLOW_UP).package)
    assert comparison.verdict == "inconclusive"
    assert "4/5 watched signals recovered" in comparison.summary

    # 5. and through it all, the report never claims execution
    blocked = [c for c in report.safety_review.checks if c.result == "blocked"]
    assert blocked == []


def test_live_publish_client_hits_exactly_the_derived_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The allowlist made concrete against the LIVE client: whatever the
    inputs, the only URL it can emit is /repos/{repo}/issues with POST."""
    captured: list[Any] = []

    class _Reply:
        status = 201

        def read(self) -> bytes:
            return json.dumps({"number": 7, "html_url": "https://x/issues/7"}).encode()

        def __enter__(self) -> "_Reply":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(raw: Any, timeout: float = 0) -> _Reply:
        captured.append(raw)
        return _Reply()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = LivePublishClient(base_url="https://api.github.com")
    request = IssueCreateRequest(repo="acme/incidents", title="t", body="b", labels=["x"])
    created = client.create_issue(request)
    assert created.number == 7

    raw = captured[0]
    assert raw.full_url == "https://api.github.com/repos/acme/incidents/issues"
    assert raw.get_method() == "POST"
    payload = json.loads(raw.data.decode())
    assert set(payload) == {"title", "body", "labels"}

    with pytest.raises(PublishError):

        def failing_urlopen(raw: Any, timeout: float = 0) -> _Reply:
            raise urllib.error.HTTPError("u", 403, "forbidden", None, None)  # type: ignore[arg-type]

        monkeypatch.setattr(urllib.request, "urlopen", failing_urlopen)
        client.create_issue(request)


def test_collection_config_refuses_the_publish_credential(tmp_path: Path) -> None:
    from ai_incident_investigator.collect.config import CollectError

    config = tmp_path / "sources.toml"
    config.write_text('[sentry]\nbase_url = "https://x"\ntoken_env = "GITHUB_PUBLISH_TOKEN"\n')
    with pytest.raises(CollectError, match="references the publish credential"):
        load_sources_config(config)


def test_the_duplicated_env_name_constants_stay_equal() -> None:
    """collect/ cannot import publish/, so the name is duplicated; this is
    the cross-check that keeps the duplication honest."""
    assert _PUBLISH_TOKEN_ENV == DEFAULT_TOKEN_ENV

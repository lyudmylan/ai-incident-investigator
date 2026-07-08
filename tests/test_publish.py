"""The single write path (epic #51): structurally narrow, credential-free
on disk, replayable, and honest in the CLI."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_incident_investigator.cli import main
from ai_incident_investigator.collect.http import EnvBearerAuth
from ai_incident_investigator.markdown import render_markdown
from ai_incident_investigator.models.report import InvestigationReport
from ai_incident_investigator.publish import (
    IssueCreateRequest,
    PublishError,
    RecordingPublishClient,
    ReplayPublishClient,
    render_issue,
)
from publish_stub import AuthResolvingIssueStub, GitHubIssueStub

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "latency_spike.json"
PUBLISH_FIXTURES = ROOT / "tests" / "fixtures" / "http" / "github_publish_demo"


def _report() -> InvestigationReport:
    return InvestigationReport.model_validate_json(GOLDEN.read_text())


def _request() -> IssueCreateRequest:
    report = _report()
    return render_issue(report, "acme/incidents", render_markdown(report))


def test_the_write_type_is_structurally_narrow() -> None:
    assert set(IssueCreateRequest.model_fields) == {"method", "repo", "title", "body", "labels"}
    with pytest.raises(ValidationError):  # no other verb is representable
        IssueCreateRequest.model_validate(
            {"method": "DELETE", "repo": "a/b", "title": "t", "body": "b"}
        )
    with pytest.raises(ValidationError):  # no URL field exists to smuggle a route
        IssueCreateRequest.model_validate(
            {"repo": "a/b", "title": "t", "body": "b", "url": "https://evil"}
        )


@pytest.mark.parametrize(
    "repo", ["../../etc", "a/b/c", "https://api.github.com/repos/a/b", "owner only", "a/"]
)
def test_repo_names_that_could_bend_the_route_are_rejected(repo: str) -> None:
    with pytest.raises(ValidationError, match="owner/name"):
        IssueCreateRequest.model_validate({"repo": repo, "title": "t", "body": "b"})


def test_issue_derivation_from_the_report() -> None:
    request = _request()
    assert request.title.startswith("[SEV-2] latency_spike: ")
    assert request.labels == ["incident", "sev-2"]
    assert "## Remediation plans" in request.body


def test_record_replay_round_trip_and_stale_mismatch(tmp_path: Path) -> None:
    request = _request()
    recorder = RecordingPublishClient(GitHubIssueStub(), tmp_path)
    created = recorder.create_issue(request)
    assert created.number == 101

    replayed = ReplayPublishClient(tmp_path).create_issue(request)
    assert replayed == created

    other = IssueCreateRequest(repo="acme/incidents", title="different", body="b")
    with pytest.raises(PublishError, match="no publish fixture"):
        ReplayPublishClient(tmp_path).create_issue(other)


def test_committed_demo_fixture_replays_the_golden_report() -> None:
    created = ReplayPublishClient(PUBLISH_FIXTURES).create_issue(_request())
    assert created.html_url.endswith("/issues/101")


def test_no_credential_material_reaches_publish_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEC_PUBLISH_TOKEN", "publish-sekrit-3141")
    stub = AuthResolvingIssueStub()
    recorder = RecordingPublishClient(stub, tmp_path)
    recorder.create_issue(_request(), EnvBearerAuth(env_var="SEC_PUBLISH_TOKEN"))

    assert any("publish-sekrit" in header for header in stub.resolved)  # control
    for fixture in tmp_path.iterdir():
        content = fixture.read_text()
        assert "publish-sekrit-3141" not in content
        assert "SEC_PUBLISH_TOKEN" not in content
        assert "Authorization" not in content


def test_publish_token_is_isolated_from_collection() -> None:
    collect_dir = ROOT / "src" / "ai_incident_investigator" / "collect"
    for module in collect_dir.glob("*.py"):
        assert "GITHUB_PUBLISH" not in module.read_text(), (
            f"{module.name} references the publish credential"
        )
    publish_source = (ROOT / "src" / "ai_incident_investigator" / "publish").rglob("*.py")
    for module in publish_source:
        source = module.read_text()
        assert "SourcesConfig" not in source, "publish must not read collection config"


def test_cli_dry_run_prints_and_posts_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["publish", "--report", str(GOLDEN), "--repo", "acme/incidents", "--dry-run"])
    assert code == 0
    out = capsys.readouterr().out
    assert "DRY RUN - would create in acme/incidents:" in out
    assert "[SEV-2] latency_spike:" in out


def test_cli_replay_publishes_from_the_committed_fixture(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        [
            "publish",
            "--report",
            str(GOLDEN),
            "--repo",
            "acme/incidents",
            "--http",
            "replay",
            "--http-fixtures-dir",
            str(PUBLISH_FIXTURES),
        ]
    )
    assert code == 0
    assert capsys.readouterr().out.strip().endswith("/issues/101")


def test_cli_error_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["publish", "--report", str(tmp_path / "no.json"), "--repo", "a/b"]) == 1
    assert "could not load the report" in capsys.readouterr().err

    assert main(["publish", "--report", str(GOLDEN), "--repo", "not-a-repo"]) == 1
    assert "owner/name" in capsys.readouterr().err

    code = main(["publish", "--report", str(GOLDEN), "--repo", "a/b", "--http", "record"])
    assert code == 1
    assert "requires --http-fixtures-dir" in capsys.readouterr().err


def test_urllib_stays_confined_to_the_two_transport_modules() -> None:
    src = ROOT / "src" / "ai_incident_investigator"
    allowed = {src / "collect" / "http.py", src / "publish" / "github_issue.py"}
    for module in src.rglob("*.py"):
        if module in allowed:
            continue
        assert "urllib" not in module.read_text(), (
            f"{module.relative_to(src)} touches the network stack outside the "
            "GET-only client and the single write client"
        )

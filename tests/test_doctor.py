"""collect doctor (#79): setup validation is test-driven and read-only.

A purpose-built fake HTTP endpoint set exercises both directions: a fully
green config, and every failure shape with its actionable hint.
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_incident_investigator.cli import main
from ai_incident_investigator.collect import load_sources_config
from ai_incident_investigator.collect.doctor import render_doctor, run_doctor
from ai_incident_investigator.collect.http import EnvBearerAuth, HTTPRequest, HTTPResponse

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


class FakeHTTP:
    """Serves sentry/prometheus/loki/github shapes keyed on URL + query."""

    def get(self, request: HTTPRequest, auth: EnvBearerAuth | None = None) -> HTTPResponse:
        url = request.url
        if "sentry.test" in url and url.endswith("/issues/9101/"):
            issue = {
                "id": "9101",
                "title": "Boom",
                "project": {"slug": "svc-a"},
                "lastSeen": "2026-07-16T11:55:00Z",
            }
            return HTTPResponse(status=200, body=json.dumps(issue))
        if "sentry.test" in url and "events/latest" in url:
            return HTTPResponse(status=404, body="no event")  # degrades to issue-only
        if "prom.test" in url:
            query = request.params.get("query", "")
            if "multi" in query:
                result = [
                    {"metric": {"pod": "a"}, "values": [[1752666000, "1"]]},
                    {"metric": {"pod": "b"}, "values": [[1752666000, "2"]]},
                ]
            elif "empty" in query:
                result = []
            else:
                result = [{"metric": {}, "values": [[1752666000, "1"], [1752666300, "2"]]}]
            body = {"status": "success", "data": {"resultType": "matrix", "result": result}}
            return HTTPResponse(status=200, body=json.dumps(body))
        if "loki.test" in url:
            selector = request.params.get("query", "")
            streams = (
                []
                if "ghost" in selector
                else [{"stream": {"app": "a"}, "values": [["1752666000000000000", "INFO ok"]]}]
            )
            body = {"status": "success", "data": {"resultType": "streams", "result": streams}}
            return HTTPResponse(status=200, body=json.dumps(body))
        if "github.test" in url and "/repos/acme/ok/" in url:
            return HTTPResponse(status=200, body="[]")
        if "github.test" in url:
            return HTTPResponse(status=404, body="Not Found")
        return HTTPResponse(status=404, body=f"no fake route for {url}")


def _write_sources(tmp_path: Path, *, green: bool) -> Path:
    runbook = tmp_path / "runbook.md"
    if green:
        runbook.write_text("escalate calmly\n")
    shutil.copy(
        ROOT / "examples" / "incidents" / "latency_spike" / "topology.json",
        tmp_path / "topology.json",
    )
    extra_queries = (
        ""
        if green
        else (
            '[[prometheus.queries]]\nservice = "svc-a"\nsignal = "multi"\n'
            "query = 'multi{svc=\"a\"}'\n"
            '[[prometheus.queries]]\nservice = "svc-a"\nsignal = "empty"\n'
            "query = 'empty{svc=\"a\"}'\n"
        )
    )
    extra_streams = (
        "" if green else '[[loki.streams]]\nservice = "ghost"\nselector = \'{app="ghost"}\'\n'
    )
    extra_repos = "" if green else '[[github.repos]]\nrepo = "acme/missing"\nservice = "svc-b"\n'
    path = tmp_path / "sources.toml"
    path.write_text(
        '[collection]\nservices = ["svc-a"]\n'
        '[sentry]\nbase_url = "https://sentry.test/api/0"\ntoken_env = "DOC_SENTRY_TOKEN"\n'
        '[prometheus]\nbase_url = "https://prom.test"\n'
        '[[prometheus.queries]]\nservice = "svc-a"\nsignal = "ok"\n'
        "query = 'good{svc=\"a\"}'\n"
        f"{extra_queries}"
        '[loki]\nbase_url = "https://loki.test"\n'
        '[[loki.streams]]\nservice = "svc-a"\nselector = \'{app="a"}\'\n'
        f"{extra_streams}"
        '[github]\nbase_url = "https://github.test"\n'
        '[[github.repos]]\nrepo = "acme/ok"\nservice = "svc-a"\n'
        f"{extra_repos}"
        '[runbook]\n[[runbook.documents]]\nfile = "runbook.md"\n'
        '[topology]\nfile = "topology.json"\n'
    )
    return path


def test_green_config_passes_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOC_SENTRY_TOKEN", "x")
    config = load_sources_config(_write_sources(tmp_path, green=True))
    checks = run_doctor(config, FakeHTTP(), issue_id="9101", now=NOW)
    assert all(check.status == "PASS" for check in checks), render_doctor(checks)
    rendered = render_doctor(checks)
    assert "ready to collect" in rendered
    assert "issue 9101 anchors at" in rendered


def test_failures_carry_the_exact_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOC_SENTRY_TOKEN", raising=False)
    config = load_sources_config(_write_sources(tmp_path, green=False))
    checks = run_doctor(config, FakeHTTP(), issue_id=None, now=NOW)
    by_check = {c.check: c for c in checks}

    assert by_check["$DOC_SENTRY_TOKEN (sentry.token_env)"].status == "FAIL"
    assert "add it to .env" in by_check["$DOC_SENTRY_TOKEN (sentry.token_env)"].detail
    assert by_check["svc-a/ok"].status == "PASS"
    assert "exactly one is required" in by_check["svc-a/multi"].detail
    assert "no series" in by_check["svc-a/empty"].detail
    assert "matched no streams" in by_check['ghost {app="ghost"}'].detail
    assert by_check["acme/ok"].status == "PASS"
    assert "404" in by_check["acme/missing"].detail
    assert "not found" in by_check["runbook.md"].detail  # file never written in red mode
    anchor = next(c for c in checks if c.source == "sentry" and c.check == "alert anchor")
    assert anchor.status == "SKIP" and "--issue" in anchor.detail
    assert "fix the FAIL lines" in render_doctor(checks)


def test_cli_exit_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOC_SENTRY_TOKEN", "x")
    sources = _write_sources(tmp_path, green=True)
    fixtures = tmp_path / "empty-fixtures"
    fixtures.mkdir()
    code = main(
        [
            "collect",
            "doctor",
            "--sources",
            str(sources),
            "--http",
            "replay",
            "--http-fixtures-dir",
            str(fixtures),
        ]
    )
    assert code == 1  # every network probe replay-misses; the fix is in the detail
    out = capsys.readouterr().out
    assert "failed" in out and "no HTTP fixture" in out

    missing_anchor = tmp_path / "no-anchor.toml"
    missing_anchor.write_text("[collection]\n")
    assert main(["collect", "doctor", "--sources", str(missing_anchor)]) == 1
    assert "no [sentry] section" in capsys.readouterr().err

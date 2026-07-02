"""The collect subcommand end-to-end, the registry, and CLI backward compat."""

import json
from pathlib import Path

import pytest

from ai_incident_investigator.cli import main
from ai_incident_investigator.collect import CollectError, load_sources_config
from ai_incident_investigator.collect.registry import build_sources
from ai_incident_investigator.loading import load_package
from sentry_stub import DEMO_ISSUE_ID, SentryStubHTTP

ROOT = Path(__file__).resolve().parents[1]
DEMO_SOURCES = ROOT / "examples" / "collect" / "sources.toml"
DEMO_FIXTURES = ROOT / "tests" / "fixtures" / "http" / "demo_collect"


def _collect_args(out: Path, *extra: str) -> list[str]:
    return [
        "collect",
        "--sources",
        str(DEMO_SOURCES),
        "--issue",
        DEMO_ISSUE_ID,
        "--output",
        str(out),
        "--http",
        "replay",
        "--http-fixtures-dir",
        str(DEMO_FIXTURES),
        *extra,
    ]


def test_registry_builds_all_configured_sources() -> None:
    config = load_sources_config(DEMO_SOURCES)
    alert_source, adapters = build_sources(config, SentryStubHTTP(), DEMO_ISSUE_ID)
    assert alert_source.name == "sentry"
    assert [a.name for a in adapters] == ["prometheus", "loki", "github", "runbook", "topology"]


def test_registry_requires_sentry(tmp_path: Path) -> None:
    config_file = tmp_path / "sources.toml"
    config_file.write_text(
        '[prometheus]\nbase_url = "https://x"\n[[prometheus.queries]]\n'
        'service = "s"\nsignal = "sig"\nquery = "q"\n'
    )
    with pytest.raises(CollectError, match=r"no \[sentry\] section"):
        build_sources(load_sources_config(config_file), SentryStubHTTP(), "1")


def test_collect_replays_a_full_multi_source_package(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "collected"
    assert main(_collect_args(out)) == 0

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    statuses = {s["name"]: s["status"] for s in report["sources"]}
    assert statuses == {
        "sentry": "ok",
        "prometheus": "ok",
        "loki": "ok",
        "github": "ok",
        "runbook": "ok",
        "topology": "ok",
    }
    assert "collected package:" in captured.err

    loaded = load_package(out)
    package = loaded.package
    assert package.alert.id == "sentry_9101"
    assert package.metrics is not None and len(package.metrics.series) == 2
    assert package.deploys is not None and len(package.deploys.deploys) == 2
    assert package.topology is not None
    assert package.runbook is not None and "Runbook" in package.runbook
    assert len(package.logs) > 4  # sentry bundle logs + loki stream logs merged
    # the only gap is traces (no traces source in v2) - everything else arrived
    assert [m.description for m in loaded.missing_data] == ["traces.json not provided"]


def test_collect_then_investigate_chains_facts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "collected"
    assert main(_collect_args(out, "--then-investigate")) == 0
    captured = capsys.readouterr()
    facts = json.loads(captured.out)
    assert facts["incident_id"] == "collected"
    assert facts["incident_window"]["start"] == "2026-06-01T14:05:52Z"
    assert any(entry["source"] == "deploys" for entry in facts["timeline"])


def test_collect_error_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing_sources = [
        "collect",
        "--sources",
        str(tmp_path / "absent.toml"),
        "--issue",
        "1",
        "--output",
        str(tmp_path / "pkg"),
    ]
    assert main(missing_sources) == 1
    assert "not found" in capsys.readouterr().err

    replay_without_dir = _collect_args(tmp_path / "pkg2")
    replay_without_dir.remove("--http-fixtures-dir")
    replay_without_dir.remove(str(DEMO_FIXTURES))
    assert main(replay_without_dir) == 1
    assert "requires --http-fixtures-dir" in capsys.readouterr().err


def test_investigate_subcommand_and_bare_flags_are_equivalent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    example = str(ROOT / "examples" / "incidents" / "latency_spike")
    assert main(["--incident", example]) == 0
    bare = capsys.readouterr().out
    assert main(["investigate", "--incident", example]) == 0
    subcommand = capsys.readouterr().out
    assert bare == subcommand

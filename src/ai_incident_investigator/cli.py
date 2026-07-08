"""Command-line entry point.

Two subcommands; bare flags remain backward compatible with v1:

    ai_incident_investigator investigate --incident DIR [...]   # (default)
    ai_incident_investigator collect --sources sources.toml --issue ID \\
        --output DIR [--then-investigate ...]

Exit codes: 0 success, 1 investigation/collection failure, 2 usage error.

Investigation --llm off (default) emits the deterministic facts only; the
other modes emit the complete InvestigationReport. Individual agent or
source failures degrade and are visible in the outputs; they do not fail
the run. Collection fails (exit 1) only when the alert anchor is unusable.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from ai_incident_investigator import __version__
from ai_incident_investigator.assemble import build_report
from ai_incident_investigator.loading import PackageLoadError, load_package
from ai_incident_investigator.markdown import render_markdown
from ai_incident_investigator.pipeline import (
    DEFAULT_FIXTURES_ROOT,
    initial_state,
    make_client,
    run_investigation,
)
from ai_incident_investigator.state import InvestigationState
from ai_incident_investigator.window import DEFAULT_LOOKBACK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_incident_investigator investigate",
        description="Investigate an offline incident package and produce a JSON report. "
        "(Use the 'collect' subcommand to gather a package from live sources first.)",
    )
    parser.add_argument(
        "--incident",
        type=Path,
        required=True,
        help="path to the incident package directory",
    )
    parser.add_argument(
        "--lookback-minutes",
        type=int,
        default=int(DEFAULT_LOOKBACK.total_seconds() // 60),
        help="incident window lookback before the alert trigger (docs/assumptions.md)",
    )
    parser.add_argument(
        "--llm",
        choices=["off", "live", "record", "replay"],
        default="off",
        help="off: deterministic facts only; live: Claude API; "
        "record: live + save fixtures; replay: saved fixtures, no network",
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=None,
        help="LLM fixture directory for record/replay "
        f"(default: {DEFAULT_FIXTURES_ROOT}/<incident-id>)",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="output format (markdown is the human-readable rendering)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="write the report to this file instead of stdout",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def build_collect_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_incident_investigator collect",
        description="Collect an incident package from configured read-only sources "
        "(the snapshot is an ordinary offline package), optionally chaining "
        "straight into investigation.",
    )
    parser.add_argument("--sources", type=Path, required=True, help="path to sources.toml")
    parser.add_argument(
        "--issue", required=True, help="issue id for the [sentry] alert anchor source"
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="directory to write the collected package into (must not be a "
        "non-empty existing directory)",
    )
    parser.add_argument(
        "--http",
        choices=["live", "replay", "record"],
        default="live",
        help="live: real sources; replay/record: HTTP fixtures (no credentials needed for replay)",
    )
    parser.add_argument(
        "--http-fixtures-dir",
        type=Path,
        default=None,
        help="HTTP fixture directory (required for --http replay/record)",
    )
    parser.add_argument(
        "--then-investigate",
        action="store_true",
        help="run the investigation on the collected package immediately",
    )
    parser.add_argument(
        "--llm",
        choices=["off", "live", "record", "replay"],
        default="off",
        help="LLM mode for --then-investigate (same semantics as investigate)",
    )
    parser.add_argument("--fixtures-dir", type=Path, default=None, help="LLM fixture directory")
    parser.add_argument(
        "--format", choices=["json", "markdown"], default="json", help="investigation output format"
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="write the investigation output to this file instead of stdout",
    )
    return parser


def _facts(state: InvestigationState) -> dict[str, Any]:
    return {
        "incident_id": state.package.incident_id,
        "incident_window": state.window.model_dump(mode="json"),
        "timeline": [entry.model_dump(mode="json") for entry in state.timeline],
        "missing_data": [item.model_dump(mode="json") for item in state.missing_data],
        "note": (
            "Deterministic facts only; run with --llm live|record|replay "
            "for the full investigation report."
        ),
    }


def _facts_markdown(state: InvestigationState) -> str:
    window = state.window
    lines = [
        f"# Incident facts: {state.package.incident_id}",
        "",
        f"Window: {window.start.isoformat()} -> "
        f"{window.end.isoformat() if window.end else 'ongoing'} ({window.rule})",
        "",
        "## Timeline",
        *(
            f"- `{e.timestamp.isoformat()}` [{e.source.value}] {e.service or '-'}: {e.description}"
            for e in state.timeline
        ),
    ]
    if state.missing_data:
        lines += ["", "## Missing data"]
        lines += [f"- {m.description}" for m in state.missing_data]
    lines += ["", "_Deterministic facts only; run with --llm for the full report._", ""]
    return "\n".join(lines)


def _emit(text: str, output: Path | None) -> None:
    if output is None:
        print(text)
    else:
        output.write_text(text if text.endswith("\n") else text + "\n")
        print(f"wrote {output}", file=sys.stderr)


def _run_and_emit(
    incident_dir: Path,
    lookback: timedelta,
    llm_mode: str,
    llm_fixtures_dir: Path | None,
    output_format: str,
    output: Path | None,
) -> int:
    try:
        loaded = load_package(incident_dir)
    except PackageLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    state = initial_state(loaded, lookback)

    if llm_mode == "off":
        text = (
            _facts_markdown(state)
            if output_format == "markdown"
            else json.dumps(_facts(state), indent=2)
        )
        _emit(text, output)
        return 0

    from ai_incident_investigator.llm import UsageTracker
    from ai_incident_investigator.pipeline import LLMMode

    fixtures_dir = llm_fixtures_dir or DEFAULT_FIXTURES_ROOT / state.package.incident_id
    try:
        client = make_client(cast(LLMMode, llm_mode), fixtures_dir)
    except Exception as exc:
        print(f"error: could not create the LLM client: {exc}", file=sys.stderr)
        return 1

    # Replay is free and stays silent; live/record runs report what they cost.
    tracker = UsageTracker(client) if llm_mode in ("live", "record") else None
    state = run_investigation(state, tracker if tracker is not None else client)
    if tracker is not None and tracker.calls:
        print(tracker.summary(), file=sys.stderr)
    report = build_report(state)
    text = (
        render_markdown(report) if output_format == "markdown" else report.model_dump_json(indent=2)
    )
    _emit(text, output)
    return 0


def _investigate_main(argv: Sequence[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.lookback_minutes < 0:
        parser.error("--lookback-minutes must be >= 0")
    return _run_and_emit(
        args.incident,
        timedelta(minutes=args.lookback_minutes),
        args.llm,
        args.fixtures_dir,
        args.format,
        args.output,
    )


def _collect_main(argv: Sequence[str]) -> int:
    from ai_incident_investigator.collect import (
        CollectError,
        collect_package,
        load_sources_config,
    )
    from ai_incident_investigator.collect.http import HTTPClientError, make_http_client
    from ai_incident_investigator.collect.registry import build_sources

    parser = build_collect_parser()
    args = parser.parse_args(argv)

    try:
        config = load_sources_config(args.sources)
        http = make_http_client(args.http, args.http_fixtures_dir)
        alert_source, adapters = build_sources(config, http, args.issue)
        report = collect_package(alert_source, adapters, args.output, config.collection)
    except (CollectError, HTTPClientError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for status in report.sources:
        line = f"[{status.status}] {status.name}"
        if status.files:
            line += f" -> {', '.join(status.files)}"
        if status.detail:
            line += f" ({status.detail})"
        print(line, file=sys.stderr)
    print(f"collected package: {args.output}", file=sys.stderr)

    if not args.then_investigate:
        print(report.model_dump_json(indent=2))
        return 0

    lookback = timedelta(minutes=config.collection.lookback_minutes)
    return _run_and_emit(
        args.output, lookback, args.llm, args.fixtures_dir, args.format, args.report
    )


def build_publish_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_incident_investigator publish",
        description="Publish an investigation report as a GitHub issue - the tool's "
        "single write path (docs/product.md Safety Model): its own analysis, "
        "to your own tracker, nothing else.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="report JSON produced by investigate --output",
    )
    parser.add_argument("--repo", required=True, help="target repository as owner/name")
    parser.add_argument(
        "--token-env",
        default="GITHUB_PUBLISH_TOKEN",
        help="env var holding the publish token (issues:write scope only; "
        "deliberately separate from any collection credential)",
    )
    parser.add_argument("--github-base-url", default="https://api.github.com")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the would-be issue instead of posting anything",
    )
    parser.add_argument(
        "--http",
        choices=["live", "replay", "record"],
        default="live",
        help="replay/record use publish fixtures (keyless demo/testing)",
    )
    parser.add_argument("--http-fixtures-dir", type=Path, default=None)
    return parser


def _publish_main(argv: Sequence[str]) -> int:
    from ai_incident_investigator.collect.http import EnvBearerAuth
    from ai_incident_investigator.models.report import InvestigationReport
    from ai_incident_investigator.publish import (
        LivePublishClient,
        PublishClient,
        RecordingPublishClient,
        ReplayPublishClient,
        render_issue,
    )

    parser = build_publish_parser()
    args = parser.parse_args(argv)

    try:
        report = InvestigationReport.model_validate_json(args.report.read_text())
    except (OSError, ValueError) as exc:
        print(f"error: could not load the report: {exc}", file=sys.stderr)
        return 1

    try:
        request = render_issue(report, args.repo, render_markdown(report))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"DRY RUN - would create in {request.repo}:")
        print(f"title: {request.title}")
        print(f"labels: {', '.join(request.labels)}")
        print(f"body: {len(request.body)} chars of rendered markdown")
        return 0

    client: PublishClient
    live = LivePublishClient(base_url=args.github_base_url)
    if args.http == "live":
        client = live
    elif args.http_fixtures_dir is None:
        print(f"error: --http {args.http} requires --http-fixtures-dir", file=sys.stderr)
        return 1
    elif args.http == "replay":
        client = ReplayPublishClient(args.http_fixtures_dir)
    else:
        client = RecordingPublishClient(live, args.http_fixtures_dir)

    auth = EnvBearerAuth(env_var=args.token_env) if args.http != "replay" else None
    try:
        created = client.create_issue(request, auth)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"published: {created.html_url}", file=sys.stderr)
    print(created.html_url)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if arguments and arguments[0] == "collect":
        return _collect_main(arguments[1:])
    if arguments and arguments[0] == "investigate":
        return _investigate_main(arguments[1:])
    if arguments and arguments[0] == "publish":
        return _publish_main(arguments[1:])
    return _investigate_main(arguments)  # bare flags: backward-compatible investigate


if __name__ == "__main__":
    sys.exit(main())

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


def build_approve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_incident_investigator approve",
        description="Record a human approval for a state-changing plan step, bound "
        "to the exact report content (regenerating the report voids it). Approval "
        "is NEVER execution: this writes an audit record and nothing else.",
    )
    parser.add_argument(
        "--report", type=Path, required=True, help="report JSON the approval binds to"
    )
    parser.add_argument("--list", action="store_true", help="show approval status per step")
    parser.add_argument(
        "--required-approvals",
        type=int,
        default=1,
        help="distinct approvers required per step in the --list view (preview "
        "a tier's quorum, e.g. 2 for production; the v5 executor derives this "
        "from the environment tier policy - docs/execution_design.md)",
    )
    parser.add_argument("--plan", help="plan id to approve (see the report's remediation_plans)")
    parser.add_argument("--step", type=int, help="0-based step index within the plan")
    parser.add_argument("--approver", help="who is approving (identity as claimed)")
    parser.add_argument(
        "--expires-in-hours",
        type=float,
        default=None,
        help="optional expiry; incidents move fast and stale approvals should die",
    )
    parser.add_argument("--note", default=None, help="optional scope note on the approval")
    return parser


def _approve_main(argv: Sequence[str]) -> int:
    from datetime import UTC, datetime

    from ai_incident_investigator.approvals import (
        ApprovalRecord,
        append_approval,
        load_approvals,
        report_hash,
        step_statuses,
    )
    from ai_incident_investigator.models.report import InvestigationReport

    parser = build_approve_parser()
    args = parser.parse_args(argv)

    try:
        report = InvestigationReport.model_validate_json(args.report.read_text())
    except (OSError, ValueError) as exc:
        print(f"error: could not load the report: {exc}", file=sys.stderr)
        return 1
    current_hash = report_hash(args.report)
    now = datetime.now(UTC)

    if args.required_approvals < 1:
        parser.error("--required-approvals must be at least 1")
    if args.list:
        statuses = step_statuses(
            report, load_approvals(args.report), current_hash, now, args.required_approvals
        )
        if not statuses:
            print("no state-changing steps in this report's plans")
            return 0
        for (plan_id, index), status in sorted(statuses.items()):
            listed = next(p for p in report.remediation_plans if p.id == plan_id)
            action = listed.steps[index].action
            print(f"{plan_id} step {index} ({action[:60]}): {status}")
        return 0

    if not (args.plan and args.step is not None and args.approver):
        parser.error("approving requires --plan, --step, and --approver (or use --list)")

    plan = next((p for p in report.remediation_plans if p.id == args.plan), None)
    if plan is None:
        known = ", ".join(p.id for p in report.remediation_plans) or "none"
        print(f"error: plan {args.plan!r} not in this report (plans: {known})", file=sys.stderr)
        return 1
    if args.step >= len(plan.steps):
        print(f"error: plan {args.plan} has {len(plan.steps)} steps", file=sys.stderr)
        return 1
    if plan.steps[args.step].kind != "state_changing":
        print(
            f"error: step {args.step} is read-only; approvals apply to state-changing steps only",
            file=sys.stderr,
        )
        return 1

    from datetime import timedelta as _timedelta

    record = ApprovalRecord(
        approver=args.approver,
        approved_at=now,
        plan_id=args.plan,
        step_index=args.step,
        report_sha256=current_hash,
        expires_at=(
            now + _timedelta(hours=args.expires_in_hours)
            if args.expires_in_hours is not None
            else None
        ),
        scope_note=args.note,
    )
    path = append_approval(args.report, record)
    print(
        f"approval recorded: {args.plan} step {args.step} by {args.approver} "
        f"(bound to report {current_hash[:12]}...)",
        file=sys.stderr,
    )
    print(str(path))
    return 0


def build_compare_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_incident_investigator compare",
        description="Compare a follow-up snapshot against the original incident's "
        "recovery verification plan - deterministically, with numbers. Signals "
        "absent from the follow-up are unverifiable, never assumed recovered. "
        "The verdict informs a human; nothing acts on it.",
    )
    parser.add_argument(
        "--incident", type=Path, required=True, help="the original incident package"
    )
    parser.add_argument("--follow-up", type=Path, required=True, help="the later snapshot package")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument(
        "--output", type=Path, default=None, help="write to this file instead of stdout"
    )
    return parser


def _compare_main(argv: Sequence[str]) -> int:
    from ai_incident_investigator.compare import (
        ComparisonError,
        build_comparison,
        render_comparison,
    )

    parser = build_compare_parser()
    args = parser.parse_args(argv)
    try:
        original = load_package(args.incident).package
        follow_up = load_package(args.follow_up).package
        comparison = build_comparison(original, follow_up)
    except (PackageLoadError, ComparisonError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    text = (
        render_comparison(comparison)
        if args.format == "markdown"
        else comparison.model_dump_json(indent=2)
    )
    _emit(text, args.output)
    print(f"verdict: {comparison.verdict} - {comparison.summary}", file=sys.stderr)
    return 0


def build_execute_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_incident_investigator execute",
        description="The v5 pilot executor: preview (dry-run) ONE approved flag "
        "toggle against the allowlist and the tier's approval quorum. Every "
        "decision - including refusals - is recorded in the executions sidecar "
        "before it is reported. Live execution does not exist yet (#67).",
    )
    parser.add_argument("--report", type=Path, required=True, help="report JSON to execute against")
    parser.add_argument(
        "--executor-config",
        type=Path,
        required=True,
        help="executor allowlist + policy TOML (docs/execution_contract.md)",
    )
    parser.add_argument("--plan", required=True, help="plan id containing the approved step")
    parser.add_argument(
        "--step", type=int, required=True, help="0-based step index within the plan"
    )
    parser.add_argument(
        "--environment", required=True, help="allowlisted environment name the toggle targets"
    )
    parser.add_argument("--flag", required=True, help="exact allowlisted flag key")
    state = parser.add_mutually_exclusive_group(required=True)
    state.add_argument("--on", action="store_true", help="desired state: enable the flag")
    state.add_argument("--off", action="store_true", help="desired state: disable the flag")
    parser.add_argument(
        "--executed-by", required=True, help="who is executing (identity as claimed)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview only - mandatory in the pilot until the adapter lands (#67)",
    )
    return parser


def _execute_main(argv: Sequence[str]) -> int:
    from datetime import UTC, datetime

    from pydantic import ValidationError

    from ai_incident_investigator.approvals import load_approvals, report_hash
    from ai_incident_investigator.execute import append_execution, plan_execution
    from ai_incident_investigator.models.execution import (
        ExecutionConfigError,
        FlagToggleRequest,
        load_executor_config,
    )
    from ai_incident_investigator.models.report import InvestigationReport

    parser = build_execute_parser()
    args = parser.parse_args(argv)
    if not args.dry_run:
        parser.error("live execution is not available in the pilot yet (#67); pass --dry-run")

    try:
        report = InvestigationReport.model_validate_json(args.report.read_text())
    except (OSError, ValueError) as exc:
        print(f"error: could not load the report: {exc}", file=sys.stderr)
        return 1
    try:
        config = load_executor_config(args.executor_config)
    except ExecutionConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        action = FlagToggleRequest(environment=args.environment, flag_key=args.flag, on=args.on)
    except ValidationError as exc:
        # an unrepresentable action gets no record: it cannot even be named
        print(f"error: the requested action is not representable: {exc}", file=sys.stderr)
        return 1

    record = plan_execution(
        report,
        load_approvals(args.report),
        report_hash(args.report),
        config,
        args.plan,
        args.step,
        action,
        args.executed_by,
        datetime.now(UTC),
    )
    # the audit record lands BEFORE the outcome is reported (epic #60)
    sidecar = append_execution(args.report, record)
    if record.outcome == "refused":
        print(f"refused: {record.detail}", file=sys.stderr)
        print(sidecar)
        return 1
    print(f"DRY RUN - {record.detail}", file=sys.stderr)
    print(sidecar)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if arguments and arguments[0] == "collect":
        return _collect_main(arguments[1:])
    if arguments and arguments[0] == "investigate":
        return _investigate_main(arguments[1:])
    if arguments and arguments[0] == "publish":
        return _publish_main(arguments[1:])
    if arguments and arguments[0] == "approve":
        return _approve_main(arguments[1:])
    if arguments and arguments[0] == "compare":
        return _compare_main(arguments[1:])
    if arguments and arguments[0] == "execute":
        return _execute_main(arguments[1:])
    return _investigate_main(arguments)  # bare flags: backward-compatible investigate


if __name__ == "__main__":
    sys.exit(main())

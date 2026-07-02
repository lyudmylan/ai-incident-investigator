"""Command-line entry point.

Exit codes: 0 success, 1 investigation failure, 2 usage error.

--llm off (default) emits the deterministic facts only (not the full
contract). The other modes run the agent graph and emit the complete
InvestigationReport (docs/output_contract.md): live (Claude API), record
(live + save fixtures), replay (saved fixtures; no network, no keys).
Individual agent failures degrade the report - visible in missing_data and
the reasoning trace - and do not fail the run.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

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
        prog="ai_incident_investigator",
        description="Investigate an offline incident package and produce a JSON report.",
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
        help="fixture directory for record/replay "
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.lookback_minutes < 0:
        parser.error("--lookback-minutes must be >= 0")

    try:
        loaded = load_package(args.incident)
    except PackageLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    state = initial_state(loaded, timedelta(minutes=args.lookback_minutes))

    if args.llm == "off":
        if args.format == "markdown":
            _emit(_facts_markdown(state), args.output)
        else:
            _emit(json.dumps(_facts(state), indent=2), args.output)
        return 0

    fixtures_dir = args.fixtures_dir or DEFAULT_FIXTURES_ROOT / state.package.incident_id
    try:
        client = make_client(args.llm, fixtures_dir)
    except Exception as exc:
        print(f"error: could not create the LLM client: {exc}", file=sys.stderr)
        return 1

    state = run_investigation(state, client)
    report = build_report(state)
    if args.format == "markdown":
        _emit(render_markdown(report), args.output)
    else:
        _emit(report.model_dump_json(indent=2), args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())

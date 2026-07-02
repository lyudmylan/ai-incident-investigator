"""Command-line entry point.

Exit codes: 0 success, 1 investigation failure, 2 usage error.

--llm off (default) emits the deterministic facts only. The other modes run
the investigator agents: live (Claude API), record (live + save fixtures),
replay (serve saved fixtures; no network, no keys). Individual agent
failures degrade the report and are visible in missing_data/failures;
they do not fail the run.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

from ai_incident_investigator import __version__
from ai_incident_investigator.loading import PackageLoadError, load_package
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
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def _facts(state: InvestigationState) -> dict[str, Any]:
    return {
        "incident_id": state.package.incident_id,
        "incident_window": state.window.model_dump(mode="json"),
        "timeline": [entry.model_dump(mode="json") for entry in state.timeline],
        "missing_data": [item.model_dump(mode="json") for item in state.missing_data],
    }


def _investigation(state: InvestigationState) -> dict[str, Any]:
    return {
        "summary": state.summary.model_dump(mode="json") if state.summary else None,
        "severity": state.severity.model_dump(mode="json") if state.severity else None,
        "evidence": [item.model_dump(mode="json") for item in state.evidence],
        "hypotheses": [h.model_dump(mode="json") for h in state.hypotheses],
        "safety_review": state.safety_review.model_dump(mode="json")
        if state.safety_review
        else None,
        "reasoning_trace": [step.model_dump(mode="json") for step in state.reasoning_trace],
        "agent_failures": [failure.model_dump(mode="json") for failure in state.failures],
    }


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
    output = _facts(state)

    if args.llm == "off":
        output["note"] = (
            "Deterministic facts only; run with --llm live|record|replay "
            "to include the agentic investigation."
        )
    else:
        fixtures_dir = args.fixtures_dir or DEFAULT_FIXTURES_ROOT / state.package.incident_id
        try:
            client = make_client(args.llm, fixtures_dir)
        except Exception as exc:
            print(f"error: could not create the LLM client: {exc}", file=sys.stderr)
            return 1
        state = run_investigation(state, client)
        output = _facts(state)  # missing_data may have grown during investigation
        output.update(_investigation(state))
        output["note"] = (
            "Partial investigation: next steps, mitigation options, and drafts arrive with epic #7."
        )

    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

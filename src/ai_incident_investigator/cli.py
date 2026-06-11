"""Command-line entry point.

Exit codes: 0 success, 1 investigation failure, 2 usage error.

Until the agentic pipeline lands (epics #4-#7), a run produces the
deterministic facts only: incident window, timeline, and missing data.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

from ai_incident_investigator import __version__
from ai_incident_investigator.loading import PackageLoadError, load_package
from ai_incident_investigator.timeline import build_timeline
from ai_incident_investigator.window import DEFAULT_LOOKBACK, incident_window


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
    parser.add_argument("--version", action="version", version=__version__)
    return parser


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

    window = incident_window(loaded.package, timedelta(minutes=args.lookback_minutes))
    timeline = build_timeline(loaded.package)

    facts = {
        "incident_id": loaded.package.incident_id,
        "incident_window": window.model_dump(mode="json"),
        "timeline": [entry.model_dump(mode="json") for entry in timeline],
        "missing_data": [item.model_dump(mode="json") for item in loaded.missing_data],
        "note": (
            "Deterministic facts only; the agentic investigation report "
            "is not implemented yet (see the v1 milestone)."
        ),
    }
    print(json.dumps(facts, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

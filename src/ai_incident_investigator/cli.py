"""Command-line entry point.

Exit codes: 0 success, 1 investigation failure, 2 usage error.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from ai_incident_investigator import __version__


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
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    incident_dir: Path = args.incident
    if not incident_dir.is_dir():
        parser.error(f"incident package directory not found: {incident_dir}")
    print(
        "The investigation pipeline is not implemented yet; see the v1 milestone.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

# ruff: noqa: E402
"""Live adversarial sweep: the real model vs the corpus rubrics (issue #61).

Manual and budget-guarded - never run in CI. Records fixtures, SAVES the
built reports and agent failures (the first sweep kept only fixtures and
the diagnosis had to be reconstructed), scores against the same rubrics as
the replay scorecard, and reports measured cost per scenario and in total.

Usage:
    AI_INCIDENT_INVESTIGATOR_MODEL=claude-haiku-4-5-20251001 \\
      uv run --env-file .env python scripts/live_eval.py [--cap 1.50] [id ...]

Output lands in live-eval-runs/<UTC timestamp>/ (gitignored): per-scenario
report.json + failures.json + recorded fixtures, plus scorecard.md.
Measured baseline for the six adversarial scenarios on Haiku 4.5
(2026-07-08): 17/37 checks, $0.84.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_corpus import RUBRICS, UNIVERSAL

from ai_incident_investigator.assemble import build_report
from ai_incident_investigator.llm import (
    AnthropicClient,
    RecordingClient,
    UsageTracker,
    estimate_cost,
)
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.pipeline import initial_state, run_investigation

ADVERSARIAL = [
    "red_herring_deploy",
    "conflicting_metrics",
    "missing_baselines",
    "cascade_victim_alert",
    "insufficient_evidence",
    "operator_already_mitigated",
]


def main() -> int:
    argv = sys.argv[1:]
    cap = 1.50
    if "--cap" in argv:
        index = argv.index("--cap")
        cap = float(argv[index + 1])
        argv = argv[:index] + argv[index + 2 :]
    scenario_ids = argv or ADVERSARIAL

    out_root = ROOT / "live-eval-runs" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    live = AnthropicClient()
    lines: list[str] = []
    total_cost = 0.0
    total_pass = total_checks = 0

    for incident_id in scenario_ids:
        scenario_dir = out_root / incident_id
        tracker = UsageTracker(RecordingClient(live, scenario_dir / "fixtures"))
        state = run_investigation(
            initial_state(load_package(ROOT / "examples" / "incidents" / incident_id)),
            tracker,
        )
        report = build_report(state)
        scenario_dir.mkdir(parents=True, exist_ok=True)
        (scenario_dir / "report.json").write_text(report.model_dump_json(indent=2) + "\n")
        (scenario_dir / "failures.json").write_text(
            json.dumps([{"agent": f.agent, "error": f.error} for f in state.failures], indent=2)
            + "\n"
        )
        cost = estimate_cost(tracker.model, tracker.input_tokens, tracker.output_tokens) or 0.0
        total_cost += cost

        lines.append(
            f"\n## {incident_id}  [{tracker.calls} calls, "
            f"{tracker.input_tokens:,}in/{tracker.output_tokens:,}out, ${cost:.2f}]"
        )
        if state.failures:
            lines.append(f"  agent failures: {[f.agent for f in state.failures]}")
        for description, predicate in [*RUBRICS[incident_id], *UNIVERSAL]:
            try:
                ok = predicate(report)
            except LookupError as exc:
                ok = False
                description = f"{description} [{exc}]"
            except Exception as exc:
                ok = False
                description = f"{description} (rubric error: {exc})"
            total_checks += 1
            total_pass += 1 if ok else 0
            lines.append(f"  {'PASS' if ok else 'FAIL'}: {description}")
        print("\n".join(lines[-12:]))

        if total_cost > cap:
            lines.append(f"\nBUDGET CAP ${cap:.2f} reached at ${total_cost:.2f}; stopping.")
            print(lines[-1])
            break

    footer = f"\n=== LIVE SCORECARD: {total_pass}/{total_checks} checks, ${total_cost:.2f} ==="
    lines.append(footer)
    print(footer)
    (out_root / "scorecard.md").write_text("\n".join(lines).strip() + "\n")
    print(f"artifacts: {out_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Estimate the token footprint of a live investigation from LLM fixtures.

Zero network, zero tokens: reads the committed fixtures (which contain the
exact request payloads a live run would send) and reports per-call and
per-example input/output estimates plus a cost projection.

Usage:
    uv run --no-sync python scripts/estimate_tokens.py [incident_id ...]

Estimates use the ~4 chars/token heuristic. Output tokens are the scripted
response sizes; real model output (especially with adaptive thinking) runs
longer - the projection applies a 5x allowance and says so. Update PRICING
from https://docs.claude.com/en/docs/about-claude/pricing when it drifts.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "llm"

# $ per million tokens (input, output); check current pricing before relying.
PRICING = {
    "claude-opus-4-8 (default)": (15.0, 75.0),
    "claude-haiku-4-5 (via AI_INCIDENT_INVESTIGATOR_MODEL)": (1.0, 5.0),
}
THINKING_ALLOWANCE = 5  # real output incl. adaptive thinking vs scripted sizes


def estimate(example: Path) -> tuple[int, int, int]:
    calls, tokens_in, tokens_out = 0, 0, 0
    for fixture in sorted(example.glob("*.json")):
        data = json.loads(fixture.read_text())
        request, response = data["request"], data["response"]
        chars = len(request.get("system", "")) + sum(len(m["content"]) for m in request["messages"])
        if request.get("json_schema"):
            chars += len(json.dumps(request["json_schema"]))
        tokens_in += chars // 4
        tokens_out += len(response["text"]) // 4
        calls += 1
    return calls, tokens_in, tokens_out


def main() -> None:
    names = sys.argv[1:] or sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())
    total_in = total_out = 0
    for name in names:
        calls, tokens_in, tokens_out = estimate(FIXTURES / name)
        total_in += tokens_in
        total_out += tokens_out
        print(f"{name}: {calls} calls, ~{tokens_in:,} in, ~{tokens_out:,} out (scripted sizes)")

    projected_out = total_out * THINKING_ALLOWANCE
    print(
        f"\nTotal: ~{total_in:,} input tokens; projected live output incl. "
        f"thinking ~{projected_out:,} ({THINKING_ALLOWANCE}x allowance)"
    )
    for model, (price_in, price_out) in PRICING.items():
        cost = total_in / 1e6 * price_in + projected_out / 1e6 * price_out
        print(f"  {model}: ~${cost:.2f}")


if __name__ == "__main__":
    main()

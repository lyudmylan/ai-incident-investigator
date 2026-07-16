# Testing and demo scenarios: the token budget

The system was built so that **the default cost of everything is zero
tokens**. This document is the decision guide: which mode to use for which
purpose, what each costs, and the guardrails that keep spend intentional.

## The mode matrix

| Purpose | Command | LLM tokens |
| --- | --- | --- |
| Unit + golden tests (246) | `uv run pytest` | **zero** (replay + scripted fakes) |
| CI on every PR | GitHub Actions | **zero** (keyless by design) |
| Full report demo | `investigate --incident examples/incidents/latency_spike --llm replay --format markdown` | **zero** |
| Six-source collection demo | `collect ... --http replay --then-investigate` (README) | **zero** (HTTP fixtures too) |
| Deterministic-layer demo | `investigate ... --llm off` | **zero** (no LLM at all) |
| Degradation demo | delete a file from a package copy, replay/off | **zero** |
| New demo scenario | author package + scripted responses, `bootstrap_fixtures.py` | **zero** |
| Cost preflight | `python scripts/estimate_tokens.py` | **zero** |
| Publish preview | `publish --report r.json --repo o/n --dry-run` | **zero** (prints, posts nothing) |
| Publish demo (stub fixture) | `publish ... --http replay --http-fixtures-dir tests/fixtures/http/github_publish_demo` | **zero** |
| Recovery comparison demo | `compare --incident examples/incidents/latency_spike --follow-up examples/followups/latency_spike --format markdown` | **zero** (deterministic) |
| Setup validation | `collect doctor --sources sources.toml [--issue N]` | **zero** LLM (read-only probes of your own endpoints) |
| Executor dry-run demo | `execute --report r.json --executor-config examples/execute/executor.toml ... --dry-run` | **zero** (deterministic; audit record written) |
| Executor LIVE-path demo (stub fixture) | `execute ... --live --http replay --http-fixtures-dir tests/fixtures/http/flag_toggle_demo` | **zero** (keyless; nothing real toggled) |
| Execution verification demo | `compare ... --verify-execution r.json` | **zero** (deterministic) |
| Live smoke (plumbing against the real API) | `AI_INCIDENT_INVESTIGATOR_MODEL=claude-haiku-4-5-20251001` + `--llm record` on ONE example | measured: **$0.27** |
| Live adversarial sweep (real-model scorecard) | `scripts/live_eval.py` (budget-guarded `--cap`; saves reports, failures, fixtures, scorecard) | measured: **$0.84** Haiku, 17/37 baseline (2026-07-08; findings in issue #61) |
| Live quality run (the real model) | `--llm record` on ONE example, default Opus | projected **$4-5** at measured token volume |

Measured baseline - **from a real run** (2026-07-07 Haiku smoke, all ten
agents succeeding): a full investigation is **10 calls, 77k input +
39k output tokens = $0.27 on Haiku 4.5**. At Opus 4.8 pricing the same
token volume is **~$4-5** (Opus likely thinks longer). Calibration note:
the chars/4 heuristic in `scripts/estimate_tokens.py` under-counts real
input ~4.5x (schema tokens + denser tokenization of structured content);
prefer fixtures with recorded usage, which the script uses automatically.

## Rules of thumb

1. **Demos replay.** A replayed run is byte-identical in shape to a live
   run - same report, same markdown - and costs nothing. Reserve live runs
   for validating model quality, never for showing the product.
2. **Record once, replay forever.** `--llm record` writes fixtures; every
   subsequent replay of that incident is free. One ~$1 Opus recording of
   one example converts real model output into a permanent zero-cost demo.
3. **Smoke on Haiku, judge on Opus.** "Does the live path work end to end"
   is a Haiku question (~$0.07). "Is the reasoning good" is an Opus
   question (~$1). Don't pay Opus prices for plumbing questions.
4. **Prompt iteration is the token trap.** Changing a prompt invalidates
   its fixtures. Iterate against scripted fakes (zero) until the schema and
   wiring are right; only then record live. The bootstrap regenerates all
   scripted fixtures in one command precisely so prompt changes stay free.
5. **A run is structurally bounded at 10 calls.** The graph has no loops
   and no retries-with-resampling; a runaway-cost live run is not a
   failure mode this architecture has.
6. **Collection is always free of LLM tokens.** `collect` uses no LLM; its
   live cost is read-only HTTP to your own telemetry. `--http record`
   snapshots it once for offline reuse.

## The standard recipes

**Zero-token full demo** (the entire product in one command - six-source
collection, ten replayed LLM calls, complete report with plans and drafts;
verified working):

    uv run python -m ai_incident_investigator collect \
      --sources examples/collect/sources.toml --issue 9101 \
      --output /tmp/collected_demo --http replay \
      --http-fixtures-dir tests/fixtures/http/demo_collect \
      --then-investigate --llm replay \
      --fixtures-dir tests/fixtures/llm/collected_demo --format markdown

    The output directory must be named collected_demo: the incident id is
    the directory name, and the replayed LLM fixtures key on the exact
    request content. For single-example demos, plain
    `investigate --incident examples/incidents/<id> --llm replay` works
    with no naming constraint.

**Key setup (once)**: put `ANTHROPIC_API_KEY=sk-ant-...` in the repo-root
`.env` file (gitignored; `chmod 600`). Live commands load it with uv's
`--env-file .env` - the key never enters shell history, command lines, or
the repo. Replay/off modes need no key at all.

**Cheap live smoke** (first thing to run once the API key exists):

    AI_INCIDENT_INVESTIGATOR_MODEL=claude-haiku-4-5-20251001 \
      uv run --env-file .env python -m ai_incident_investigator investigate \
      --incident examples/incidents/latency_spike \
      --llm record --fixtures-dir /tmp/live-smoke --format markdown

    Proves auth, structured outputs, stop-reason handling, and the full
    graph against the real API for ~$0.07, leaves fixtures you can
    re-inspect with replay at zero cost, and (issue #40) prints the
    actual token usage and cost when it finishes.

**The committed real-model recording** (quality artifact): a full
Sonnet 5 run of latency_spike lives in `tests/fixtures/llm-live/`
(recorded 2026-07-07, $1.04 measured, all ten agents). Replay it free:

    AI_INCIDENT_INVESTIGATOR_MODEL=claude-sonnet-5 \
      uv run python -m ai_incident_investigator investigate \
      --incident examples/incidents/latency_spike \
      --llm replay --fixtures-dir tests/fixtures/llm-live/latency_spike \
      --format markdown

    The model override is required: fixture keys embed the model name.
    The snapshot pairs with the prompts as of its recording commit -
    prompt changes invalidate replay (fixture keys embed the full
    request); re-record after meaningful prompt work to refresh it.
    (The #45 prompt fixes did exactly that, so the snapshot currently
    degrades on replay.) The RENDERED reports survive prompt drift:
    `docs/samples/latency_spike-sonnet5-report.md` and the Haiku smoke
    counterpart are the committed static demo artifacts - note the
    critic blocking triage on timestamp precision in the Sonnet one.
    Notable content: the critic BLOCKS triage on a timestamp-precision
    error and questions the confidence rubric's design - real safety-layer
    behavior on real model output. Kept separate from tests/fixtures/llm
    so the deterministic scripted set remains the CI baseline. Re-record
    on Opus (same command, no override, ~$4-5 at measured volume) after
    the #45 quality fixes land.

**The committed Haiku recording** (live-path artifact): a full Haiku 4.5
run of latency_spike lives in `tests/fixtures/llm-live/latency_spike-haiku45/`
(recorded 2026-07-12: 10 calls, 76k input + 34k output tokens, $0.25
measured; post-#61 prompts, so it replays byte-identically). Replay it free:

    AI_INCIDENT_INVESTIGATOR_MODEL=claude-haiku-4-5-20251001 \
      uv run python -m ai_incident_investigator investigate \
      --incident examples/incidents/latency_spike \
      --llm replay --fixtures-dir tests/fixtures/llm-live/latency_spike-haiku45 \
      --format markdown

    Notable content: Haiku claims SEV-1 and the deterministic
    severity-ceiling lint rejects it in-report ("numerically support at
    most SEV-2"), while the critic files seven warnings - the standard
    live demo of structural safety catching model overclaim.
    docs/demo_tour.md step 3 walks through it.

## What stays scripted on purpose

CI and the golden corpus run on scripted fakes even after live fixtures
exist: they are deterministic, model-independent (no golden churn when the
default model changes), and regenerable in one command after schema
changes. Live-recorded fixtures are a quality artifact, not a test
baseline.

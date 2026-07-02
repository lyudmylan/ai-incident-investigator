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
| Live smoke (plumbing against the real API) | `AI_INCIDENT_INVESTIGATOR_MODEL=claude-haiku-4-5-20251001` + `--llm record` on ONE example | ~17k in / ~10k out ≈ **$0.05-0.10** |
| Live quality run (the real model) | `--llm record` on ONE example, default Opus | ≈ **$1** |

Measured baseline (from `scripts/estimate_tokens.py`, which reads the
committed fixtures - the exact requests a live run would send): a full
investigation is **10 LLM calls, ~17k input tokens**, with scripted-size
outputs ~2k (real output with adaptive thinking runs longer; the script
projects with a 5x allowance).

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

**Cheap live smoke** (first thing to run once the API key exists):

    export ANTHROPIC_API_KEY=...
    AI_INCIDENT_INVESTIGATOR_MODEL=claude-haiku-4-5-20251001 \
      uv run python -m ai_incident_investigator investigate \
      --incident examples/incidents/latency_spike \
      --llm record --fixtures-dir /tmp/live-smoke --format markdown

    Proves auth, structured outputs, stop-reason handling, and the full
    graph against the real API for ~$0.07, and leaves fixtures you can
    re-inspect with replay at zero cost.

**The one Opus recording worth paying for** (quality validation):

    uv run python -m ai_incident_investigator investigate \
      --incident examples/incidents/latency_spike \
      --llm record --fixtures-dir tests/fixtures/llm-live/latency_spike \
      --format markdown

    ~$1. Committing these fixtures makes real-model output replayable by
    anyone, forever, free - kept separate from tests/fixtures/llm so the
    deterministic scripted set remains the CI baseline.

## What stays scripted on purpose

CI and the golden corpus run on scripted fakes even after live fixtures
exist: they are deterministic, model-independent (no golden churn when the
default model changes), and regenerable in one command after schema
changes. Live-recorded fixtures are a quality artifact, not a test
baseline.

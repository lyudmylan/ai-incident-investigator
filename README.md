# AI Incident Investigator

An explainable, human-in-the-loop investigation layer for a future AI SRE
agent. Give it an offline incident package (alert, metrics, logs, traces,
deploys, topology, runbook); it correlates the evidence, ranks hypotheses
with an auditable confidence rubric, and produces safe next steps, mitigation
options (always requiring human approval), an internal update draft, and a
postmortem draft — as stable JSON or human-readable Markdown.

It investigates and recommends. It never executes anything.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```sh
uv sync --dev

# Deterministic facts only (no LLM): incident window, merged timeline, gaps
uv run python -m ai_incident_investigator \
  --incident examples/incidents/latency_spike

# Full investigation report from committed fixtures - no API key needed
uv run python -m ai_incident_investigator \
  --incident examples/incidents/latency_spike --llm replay --format markdown

# Live investigation against the Claude API
ANTHROPIC_API_KEY=... uv run python -m ai_incident_investigator \
  --incident examples/incidents/latency_spike --llm live --output report.json
```

Three example incidents ship with fixtures, so the replay demo works out of
the box: `latency_spike` (deploy-driven retry amplification), `error_rate_spike`
(feature flag breaks template rendering; recovers in-window), and
`dependency_timeout` (third-party API degradation, no internal change).

## How it works

```
incident package -> deterministic core -> agent graph -> report
   (files)          loader, validation,    6 investigators (parallel)
                    incident window,       -> hypothesis ranker
                    merged timeline        -> safety critic
                                           -> recommendation builder
                                           -> reporter (drafts)
                                           -> deterministic safety linter
```

- **Deterministic facts, agentic reasoning.** Parsing, validation, the
  incident window, and the timeline are plain code; LLM agents only ever see
  pre-validated, typed facts (`docs/architecture.md`).
- **Evidence-backed by construction.** Agents cite evidence by id; hypotheses
  whose citations don't validate are dropped. The confidence label is derived
  in code from the documented rubric (`docs/assumptions.md`) — the model
  cannot overclaim.
- **Degrades, never crashes.** Missing files, malformed data, and failed
  agents become `missing_data` entries; a report always comes out, with
  explicit "unavailable" fallbacks.
- **Safety is schema-deep.** Every mitigation option carries
  `requires_human_approval: true` enforced by the type system, and a
  deterministic linter checks the final report even if every LLM call failed.

Contracts: `docs/incident_package_contract.md` (input) and
`docs/output_contract.md` (output), both generated from the code.

## Development

```sh
uv run ruff format . && uv run ruff check .   # format + lint
uv run mypy                                   # strict type check
uv run pytest                                 # tests (offline, no API key)
uv run python -m ai_incident_investigator.contracts       # regen contract docs
uv run --no-sync python scripts/bootstrap_fixtures.py     # regen fixtures + goldens
```

CI runs all of the above without any API key: tests replay recorded LLM
fixtures. See `AGENTS.md` for the full workflow and rules, and
`docs/product.md` for the product spec and roadmap.

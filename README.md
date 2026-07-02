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

Four example incidents ship with fixtures, so the replay demo works out of
the box: `latency_spike` (deploy-driven retry amplification), `error_rate_spike`
(feature flag breaks template rendering; recovers in-window),
`dependency_timeout` (third-party API degradation, no internal change), and
`collected_demo` (the same booking scenario as gathered by the `collect`
command below - byte-for-byte reproducible from the committed HTTP fixtures).

## Collecting packages from live sources (v2)

Instead of hand-authoring a package, `collect` gathers one from read-only
sources — a Sentry-like issue (the anchor), Prometheus-like metrics,
Loki-like logs, GitHub releases/deployments, and configured runbooks — and
writes an ordinary package directory (the snapshot doubles as preserved
incident evidence). Configure endpoints in a `sources.toml`
(`examples/collect/sources.toml` is a template); credentials are read-only
tokens referenced by env var name, never values in config.

```sh
# Offline demo against committed HTTP fixtures - no credentials:
uv run python -m ai_incident_investigator collect \
  --sources examples/collect/sources.toml --issue 9101 \
  --output /tmp/collected-incident \
  --http replay --http-fixtures-dir tests/fixtures/http/demo_collect \
  --then-investigate --format markdown

# Real use: point sources.toml at your services, export the *_env tokens,
# then the same command with --http live (and --llm live for the report).
```

Collection degrades per source (a down source becomes a gap the
investigation reports) and fails outright only when the alert anchor is
unusable. Adapters can only GET — writes are structurally impossible
(`docs/architecture.md`, collection layer; mappings in
`docs/collection_sources.md`).

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

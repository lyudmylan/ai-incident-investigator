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

Everything above runs on committed fixtures - tests, CI, and demos burn
**zero LLM tokens** by design. `docs/demo_tour.md` is a guided five-step
walkthrough of the whole surface (investigate, publish, approve, compare),
all replayed at zero cost. `docs/testing_and_demo.md` is the decision
guide: which mode costs what (measured: a full live investigation is 10
calls / ~77k input + ~34k output tokens - about $0.25 on Haiku 4.5 and
~$4-5 at Opus pricing), plus the standard recipes for free demos and the
one recording worth paying for.

Ten example incidents ship with fixtures, so the replay demo works out of
the box. Four originals: `latency_spike` (deploy-driven retry amplification),
`error_rate_spike` (feature flag breaks template rendering; recovers
in-window), `dependency_timeout` (third-party API degradation, no internal
change), and `collected_demo` (the same booking scenario as gathered by the
`collect` command below - byte-for-byte reproducible from the committed HTTP
fixtures). Plus a six-scenario **adversarial corpus** built to mislead -
red-herring deploys, conflicting metrics, evidence too thin for any honest
hypothesis - each scored against a rubric of what a correct investigation
must and must not claim (`scripts/eval_corpus.py`; committed scorecard in
`docs/eval_scorecard.md`).

## Guided remediation (v3)

Beyond diagnosis, the report carries guided, **strictly draft-only**
artifacts - the tool never posts, creates, or executes anything, anywhere:

- **Remediation plans**: the reviewed mitigation options structured into
  stepwise plans, plus a rollback checklist when a deploy-correlated
  hypothesis exists. Safety lives in the schema: a state-changing step
  cannot exist without `requires_human_approval: true` and a verification;
  abort conditions are mandatory; dangling references are linter-blocked.
- **Recovery verification plan**: derived deterministically (no LLM) from
  the deviated series and the documented recovery rule - what to watch,
  for how long, which error patterns should stop, when to re-alert.
- **External drafts** for a human to copy out: a Jira ticket (priority
  mapped from severity in code), a Slack update (must state that nothing
  was executed), and a status-page update held to lintable customer-safe
  rules (internal service names are blocked mechanically).

A rendered plan looks like this (from the latency_spike replay):

```markdown
### Rollback checklist for booking-service release 2026.06.01-1420 (rollback)

> **Human approval required before any step of this plan is acted on.**

- addresses hypothesis: `hypothesis_314fcf61a4`
- suggested owner: on-call engineer
- preconditions: previous release 2026.05.28 artifacts still deployable

1. [read-only] check whether release 2026.06.01-1420 shipped data migrations
   - verify: release notes and migration directory reviewed
2. **[STATE-CHANGING - approval required]** roll booking-service back to the previous release
   - verify: deployed version reports the previous release and appointments-db CPU falls below 60%

**Abort if:** rollback pods crash-loop or error rate exceeds 10%
```

## Guided operations (v4)

v4 earns closed-loop before building it — everything below is
deterministic, zero-token, and acts on nothing:

- **Adversarial evaluation corpus**: six scenarios engineered to mislead
  (see the examples list above), scored on every test run; the committed
  scorecard is a CI regression gate.
- **`publish`**: the tool's single write path - its OWN report as a GitHub
  issue. The client type can express exactly one route and verb; its
  credential is isolated from all collection config (structurally, with
  tests on both directions). `--dry-run` previews; a stub fixture demos
  offline.
- **`approve`**: human approvals bound to the sha256 of the exact report
  file - regenerate the report and every approval on it is VOID. The
  `is_actionable` gate is what a future executor must consult; today it
  only answers. Approval is never execution.
- **`compare`**: a follow-up snapshot judged against the original
  incident's recovery plan with the same rules that end incident windows.
  Pessimistic by policy: absent signals are unverifiable, never assumed
  recovered.

v5 (planned, not scheduled) pilots execution: ONE flag-toggle adapter,
dry-run mandatory, consuming these approval records unchanged.

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

# Real use: point sources.toml at your services, put tokens in the
# gitignored .env (see docs/testing_and_demo.md), then the same command
# with `uv run --env-file .env` + --http live (and --llm live for the report).
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

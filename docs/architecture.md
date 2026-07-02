# Architecture

## Layers

```
Incident package (directory)
  |
  v
Deterministic core (plain code, no LLM)          loading.py, window.py, timeline.py
  - schema validation, structured degradation
  - incident window, merged timeline, stable ids
  |
  v
InvestigationState (frozen)                       state.py
  - facts in, accumulated findings out
  |
  v
Agent graph (LLM-backed reasoning)                graph.py + agents/*
  - investigators fan out in parallel
  - ranker, safety critic, comms fan in
  |
  v
InvestigationReport (JSON output contract)        models/report.py
```

Principle 4 (deterministic facts, agentic reasoning) is a hard boundary:
agents receive pre-validated, typed facts and never raw files. Everything an
agent claims must cite evidence ids that the deterministic layer or another
agent produced.

## Decision record: hand-rolled graph, not LangGraph

Decided 2026-06-11 (epic #4). The v1 graph is a fixed fan-out/fan-in shape
with no conditional routing, no cycles, and no mid-run human interrupts. A
dependency-free runner (~100 lines, `graph.py`) keeps mypy strict end-to-end
and avoids framework churn. LangGraph earns its keep when we need
checkpointing, persistent threads, or human-in-the-loop interrupts mid-graph;
that is v3+ territory (read-only integrations and guided remediation). The
agent interface (name, depends_on, `run(state) -> StateUpdate`) is small
enough to port if that day comes.

## Graph semantics

- **Levels.** Agents declare dependencies by name; the runner topologically
  sorts them into levels and runs each level concurrently (LLM calls are
  I/O-bound, so threads suffice).
- **Determinism.** The state is frozen; agents return `StateUpdate`s. The
  runner merges updates between levels in agent-name order, so the final
  state does not depend on thread completion order. List fields merge
  additively; scalar fields are last-write-wins.
- **Degradation.** An agent that raises becomes a `missing_data` entry, an
  `AgentFailure`, and a reasoning-trace step; downstream agents still run on
  the partial state. The run never crashes because one agent failed.
  Malformed graphs (cycles, duplicate names, unknown dependencies) raise
  `GraphError` instead - those are programming errors.

## v1 graph shape

```
                 +-> triage ----------------+
                 +-> metrics_investigator --+
load + timeline -+-> logs_investigator -----+-> hypothesis_ranker -> safety_critic
(deterministic)  +-> trace_investigator ----+         |                  |
                 +-> deploy_correlation ----+         v                  v
                 +-> runbook_agent ---------+   recommendation     comms + postmortem
```

Investigator agents (epic #5) produce evidence and findings; the ranker
(epic #6) combines them into hypotheses with the documented confidence
rubric; the safety critic challenges them; the deterministic recommendation
builder and the reporter agent (epic #7) produce next steps, mitigation
options, and drafts; the deterministic safety linter runs dead last so it
lints the complete report, and `assemble.build_report` fills contract
fields any failed stage left empty with explicit "unavailable" fallbacks.

## LLM harness (`llm.py`)

One protocol, three clients:

| Client | Use | Network |
| --- | --- | --- |
| `AnthropicClient` | production runs | yes (needs `ANTHROPIC_API_KEY`) |
| `RecordingClient` | wraps a real client, writes fixtures | yes |
| `ReplayClient` | tests and CI | never |

- Fixtures are JSON files keyed by a content hash of the full request
  (`request_key`); they live under `tests/fixtures/llm/`. CI runs replay-only
  and needs no API keys (AGENTS.md rule).
- Default model: `claude-opus-4-8`, overridable via the
  `AI_INCIDENT_INVESTIGATOR_MODEL` env var. Adaptive thinking is on by
  default. Sampling parameters are not exposed: they are removed on
  Opus 4.7+ (the API rejects them), and run-to-run determinism comes from
  replay fixtures, not temperature.
- Agents that need typed output pass a JSON schema; the request then uses
  the API's structured outputs (`output_config.format`), so responses are
  schema-valid JSON by construction.
- The client raises `LLMError` on refusals and `max_tokens` truncation;
  the graph's degradation contract turns that into a partial report.

## Collection layer (v2, `collect/`)

Read-only adapters turn external sources into ordinary incident packages;
the v1 pipeline investigates them unchanged.

- **Snapshot-first (decision, epic #17).** Collection writes a plain package
  directory rather than feeding the pipeline in memory. Every live incident
  becomes a replayable offline package: evidence is preserved, runs are
  reproducible, and the package contract stays the single interface between
  collection and investigation.
- **Adapter contract** (`collect/adapter.py`): the alert source is the one
  fatal dependency (it anchors the incident window); every other adapter
  returns a typed `PackageContribution` (the collection analog of the
  graph's `StateUpdate`) and failures degrade into the collection report -
  the package simply lacks that file, and the v1 loader reports the gap.
  Two adapters contributing the same single file is a configuration bug and
  fails that adapter visibly.
- **HTTP harness** (`collect/http.py`): GET-only by construction (no other
  method is expressible), with record/replay fixtures mirroring `llm.py`.
  The recordable request identity carries method/url/params only - auth is
  an env-var *reference* resolved by the live client at send time, so
  credentials structurally cannot enter fixtures or collected packages.
  CI runs replay-only: no network, no credentials.
- **Configuration** (`sources.toml`): each adapter owns its section schema;
  the framework validates `[collection]` and rejects any config value under
  a secret-looking key - credentials are `*_env` references, never values.
- Topology has no standard observability source and stays hand-authored;
  `LocalTopologyAdapter` copies a validated local file into the package.
- Per-source field mappings and normalization rules live in
  docs/collection_sources.md; code and that document change together.

## Reasoning trace

Every graph node contributes `ReasoningStep`s describing what it concluded
and which input ids it used; the runner adds steps for failures. The trace is
part of the output contract (`reasoning_trace`) - explainability is the
product, so the trace is not optional debug output.

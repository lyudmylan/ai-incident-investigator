# Repository instructions for coding agents

## Project

AI Incident Investigator: an explainable, human-in-the-loop investigation layer for a future
AI SRE agent. `docs/product.md` is the product source of truth. Work is planned in GitHub
under the v1 milestone; epics are issues #1-#8.

## Commands

```
uv sync --dev               # install dependencies
uv run ruff format .        # format (--check in CI)
uv run ruff check .         # lint
uv run mypy                 # type check (strict)
uv run pytest               # tests
uv run python -m ai_incident_investigator.contracts   # regenerate contract docs
```

`docs/incident_package_contract.md` and `docs/output_contract.md` are generated
from the Pydantic models; a test fails CI if they drift. After changing any
model, regenerate them in the same PR.

LLM fixtures and golden reports: tests replay from
`tests/fixtures/llm/<incident-id>/` and diff full reports against
`tests/golden/<incident-id>.json`. After changing prompts, renderings, models,
or examples, regenerate both in the same PR:

```
uv run --no-sync python scripts/bootstrap_fixtures.py
```

Committed fixtures are bootstrapped from scripted fake responses
(tests/scripted_runs.py). To use real model output instead, record live
fixtures (needs an API key), then refresh only the goldens:

```
ANTHROPIC_API_KEY=... uv run python -m ai_incident_investigator \
  --incident examples/incidents/latency_spike --llm record
uv run --no-sync python scripts/bootstrap_fixtures.py latency_spike --goldens-only
```

Every example incident must have fixtures and a golden (a test enforces it).

All four checks must pass locally before pushing.

## Workflow

- Plan work in GitHub: milestone -> epics -> implementation issues (github-planning skill).
- One branch per change; PR into `main`; use `Closes #N` when the PR completes an issue,
  otherwise comment on the issue with the PR link and status.
- Review tiers (issue #1): local `/code-review` before every push (default);
  `@claude` mention on the PR for an independent cloud review when warranted;
  `/code-review ultra` reserved for high-risk changes and triggered by the repo owner only.
- Before merge: run `scripts/pr_ready_check.sh <pr-number>` and address or explicitly defer
  all review findings (github-shipping skill).

## Rules

- JSON contracts before implementation; update docs in the same PR as behavior changes.
- Deterministic facts, agentic reasoning (product.md Principle 4): parsing, validation, and
  timeline construction are plain code; LLM-backed agents only interpret pre-validated facts.
- The tool recommends; the ONLY execution that exists is the v5 pilot's single
  flag-toggle path - behind the approval quorum (`is_actionable`), an exact
  allowlist, sandbox/staging live tiers, mandatory dry-run availability, and an
  audit record for every decision including refusals (docs/execution_design.md).
  No paging, no customer communication, no other action type. All mitigation
  output must be framed as requiring human approval.
- No hidden business logic in prompts.
- No live credentials or private production data anywhere in the repo, including tests
  and example incidents.
- LLM calls must be mockable/replayable; CI must pass without API keys.

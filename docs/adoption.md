# Connect your environment (OSS stack)

Task-oriented onboarding for a Sentry + Prometheus + Loki + GitHub stack.
The design principle: **the alert anchor is the only required source.**
Everything else is an incremental add - until you configure it, the
investigation reports the gap instead of failing. So you get a real
report after step 1, and every step after that ends with a command that
proves it worked.

(Reference-grade endpoint mappings live in docs/collection_sources.md;
cost/mode details in docs/testing_and_demo.md; this page is the how-to.)

## Step 0 - install and key (once, ~2 min)

```sh
uv sync --dev
printf 'ANTHROPIC_API_KEY=sk-ant-...\n' > .env && chmod 600 .env
```

The key is only needed for `--llm live|record`. Everything replayed or
deterministic runs keyless - including your first dry run:

```sh
uv run python -m ai_incident_investigator \
  --incident examples/incidents/latency_spike --llm replay --format markdown
```

## Step 1 - the two-line start: anchor only

Copy `examples/collect/sources.minimal.toml` next to your ops files, set
your Sentry base URL, create a **read-only** Sentry token and add it to
`.env` as `SENTRY_TOKEN`. That is the whole config. Pick any real past
incident's issue id and run:

```sh
uv run --env-file .env python -m ai_incident_investigator collect \
  --sources sources.minimal.toml --issue <SENTRY_ISSUE_ID> \
  --output /tmp/first-incident --http live \
  --then-investigate --llm record --fixtures-dir /tmp/first-fixtures \
  --format markdown --report /tmp/first-report.md
```

You get a complete investigation of a real incident (~$0.25 on Haiku via
`AI_INCIDENT_INVESTIGATOR_MODEL=claude-haiku-4-5-20251001`, a few dollars
on the default model; use `--llm off` for the free deterministic layer).
`--llm record` saved the model responses, so every later step - the JSON
report for publishing, re-renders, regression tests - replays them at
zero cost.
It will be evidence-poor - that is the point: open
`/tmp/first-incident/collection_report.json` and the report's
**Missing data** section. They are your configuration TODO list, ranked
by what the investigation actually wanted.

## Step 2 - add metrics (Prometheus)

Append to your sources file - one entry per signal worth watching:

```toml
[prometheus]
base_url = "http://prometheus:9090"
# token_env = "PROM_TOKEN"            # only if yours sits behind auth

[[prometheus.queries]]
service = "your-service"
signal = "p95_latency_ms"
query = 'histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{service="your-service"}[5m])) by (le)) * 1000'
unit = "ms"
```

The one rule: **each query must return exactly one series** (add labels
until it does; an ambiguous query is skipped with a note, never guessed
at). Baselines are derived from the pre-incident span automatically.
Re-run the step-1 command: the report now has metric evidence, deviation
timeline entries, and a recovery-verification plan.

## Step 3 - add logs (Loki)

```toml
[loki]
base_url = "http://loki:3100"

[[loki.streams]]
service = "your-service"
selector = '{app="your-service"}'
```

A selector may match several streams (pods); they merge chronologically.
Levels come from stream labels when present, else from the line text.

## Step 4 - add deploys, runbook, topology

```toml
[github]
base_url = "https://api.github.com"
token_env = "GITHUB_READ_TOKEN"       # fine-grained PAT: Contents read-only

[[github.repos]]
repo = "you/your-service"
service = "your-service"
environment = "production"

[runbook]
[[runbook.documents]]
file = "runbooks/your-service.md"     # or repo/path for a GitHub-hosted doc

[topology]
file = "topology.json"
```

Topology is the one hand-authored file (no standard source exists for
dependency graphs); the smallest useful one is just your services and
who calls whom - see `examples/incidents/latency_spike/topology.json`.
An empty deploys window is written as "checked, nothing shipped":
ruling-out evidence, not a gap.

## Step 5 - publish, and record the incident forever

```sh
# the JSON report, replayed keyless from step 1's recording
# (fixture keys embed the model: if step 1 used the Haiku override,
#  prefix this command with the same AI_INCIDENT_INVESTIGATOR_MODEL)
uv run python -m ai_incident_investigator investigate \
  --incident /tmp/first-incident --llm replay \
  --fixtures-dir /tmp/first-fixtures --output /tmp/first-report.json

# its own token: fine-grained PAT, Issues WRITE on ONE tracker repo -
# deliberately never the same env var as any read token
uv run --env-file .env python -m ai_incident_investigator publish \
  --report /tmp/first-report.json --repo you/incidents --dry-run
```

Drop `--dry-run` when the preview looks right. Add `--http record
--http-fixtures-dir ...` to a collect run and the HTTP side is captured
too: the whole incident becomes a demo, a regression case, and preserved
evidence in one.

## Step 6 - close the loop

An hour after mitigation, snapshot again and let the deterministic layer
judge recovery (and fold the verdict into the postmortem):

```sh
uv run --env-file .env python -m ai_incident_investigator collect \
  --sources sources.toml --issue <SAME_ISSUE> --output /tmp/follow-up --http live
uv run python -m ai_incident_investigator compare \
  --incident /tmp/first-incident --follow-up /tmp/follow-up \
  --format markdown --update-postmortem /tmp/first-report.json
```

The executor (`approve`/`execute`) needs one more integration decision -
your flag service's API differs from the pilot's minimal PATCH format, so
either adapt `flags.LiveFlagClient` (the only file that changes, by
design) or put a ~50-line shim in front of your flag system. Start with
`--dry-run` and a staging allowlist either way: docs/execution_design.md.

## When something fails

- **401/403**: the token env var named in the config is missing from
  `.env` or lacks the read scope. The error names the variable.
- **"query matched N series"**: add labels until exactly one series
  returns; the note lists what matched.
- **No log lines**: check the selector against Loki's own label browser;
  an empty window is a note, not an error.
- **A source is down**: collection degrades - the package is still
  written and the gap is reported. Only an unusable alert anchor fails
  the run, because without it there is no incident window.

# Demo tour: the whole surface in five steps

A guided, hands-on walkthrough of everything the tool does — investigation,
publish, approve, compare — in ~10 minutes and at zero LLM tokens. Every step
replays committed fixtures; no API key is needed anywhere on this page.
`docs/testing_and_demo.md` is the companion cost/mode guide.

Prep once:

```sh
mkdir -p /tmp/incident-demo
```

## 1. Deterministic facts — no model at all

```sh
uv run python -m ai_incident_investigator --incident examples/incidents/latency_spike
```

**Watch for:** the `incident_window` (start = alert minus 30m lookback; `end: null`
because the deviated series never recovered — the rule is printed in the JSON itself),
and the merged timeline: deploys, metrics, logs, traces, and the alert interleaved into
one ordered stream. Run it twice — byte-identical. This is what the agents are allowed
to reason about; they never see the raw files. The 2026-05-30 notifications-service
deploy near the top is deliberate red-herring bait the ranker must rule out later.

## 2. The full report, replayed — read it as a document

```sh
uv run python -m ai_incident_investigator investigate \
  --incident examples/incidents/latency_spike --llm replay \
  --format markdown --output /tmp/incident-demo/report.md
open /tmp/incident-demo/report.md
```

**Reading order:** severity line (derived in code from the documented rubric) →
hypothesis #1 (rubric line + evidence IDs, each resolvable in the Evidence section;
uncited hypotheses were dropped before rendering) → remediation plan (find the
`[STATE-CHANGING - approval required]` step and its mandatory verify and abort
conditions) → recovery verification plan → the Jira/Slack/status-page drafts (the
status page never names internal services — that is linted, not hoped for) →
"Safety review" → the per-agent "Reasoning trace" at the very bottom.

## 3. Real model output — the committed Haiku 4.5 recording

```sh
AI_INCIDENT_INVESTIGATOR_MODEL=claude-haiku-4-5-20251001 \
  uv run python -m ai_incident_investigator investigate \
  --incident examples/incidents/latency_spike --llm replay \
  --fixtures-dir tests/fixtures/llm-live/latency_spike-haiku45 \
  --format markdown --output /tmp/incident-demo/haiku.md
open /tmp/incident-demo/haiku.md
```

**Watch for:** Haiku claims **SEV-1**; scroll to "Safety review" — the deterministic
severity-ceiling lint rejects it ("numerically support at most SEV-2; judgment may
lower a level, not raise it") and the critic files seven warnings, including a sharp
one showing the retry-log timestamps don't prove "no backoff". Structural safety
catching model overclaim, on real recorded output (2026-07-12, 10 calls, ~$0.25).
Compare prose quality with `docs/samples/latency_spike-sonnet5-report.md`, the
quality benchmark, whose critic outright *blocked* an invented timestamp.

## 4. The operational loop — publish preview, then approve a step

```sh
uv run python -m ai_incident_investigator investigate \
  --incident examples/incidents/latency_spike --llm replay \
  --output /tmp/incident-demo/report.json

uv run python -m ai_incident_investigator publish \
  --report /tmp/incident-demo/report.json --repo my-org/incidents --dry-run

uv run python -m ai_incident_investigator approve \
  --report /tmp/incident-demo/report.json --list
```

Only state-changing steps appear in the list; read-only steps need no approval.
Approve one, using a plan id from the `--list` output:

```sh
uv run python -m ai_incident_investigator approve \
  --report /tmp/incident-demo/report.json \
  --plan <plan-id-from-list> --step 1 --approver "$USER" --expires-in-hours 4
cat /tmp/incident-demo/report.approvals.json
```

**Watch for:** dry-run prints the would-be GitHub issue and posts nothing — this is
the tool's single write path, and its client type can express exactly one route and
one verb. The approval record binds to the report file's sha256 (regenerate the
report → approval VOID), carries an expiry, and executes nothing. Approval is never
execution.

## 5. Did it recover? — deterministic verdict from a follow-up snapshot

```sh
uv run python -m ai_incident_investigator compare \
  --incident examples/incidents/latency_spike \
  --follow-up examples/followups/latency_spike --format markdown
```

**Watch for:** verdict **INCONCLUSIVE** — 4/5 signals recovered, both watched error
patterns gone, re-alert condition not met... and the tool still refuses to say
"recovered", because `appointments-db / cpu_pct` is absent from the follow-up
snapshot, and absent is *unverifiable*, never assumed good. It names exactly what to
capture to upgrade the verdict, and ends with a paste-ready postmortem addendum.

---

## Appendix: live runs (the only thing that ever costs money)

The key lives ONLY in the gitignored `.env` (never `export` it into your shell —
among other things, that collides with a Claude Code subscription login). Inject it
per-command with `uv run --env-file .env`:

```sh
# Haiku smoke, ~$0.25–0.27 measured — records fixtures you can replay free forever
AI_INCIDENT_INVESTIGATOR_MODEL=claude-haiku-4-5-20251001 \
  uv run --env-file .env python -m ai_incident_investigator investigate \
  --incident examples/incidents/latency_spike \
  --llm record --fixtures-dir /tmp/live-smoke \
  --format markdown --output /tmp/incident-demo/live.md
```

If a recording is worth keeping, move its fixtures dir under
`tests/fixtures/llm-live/` and commit it (that is how step 3's recording got there).
Never pass `--llm record` without `--fixtures-dir`: the default location would
overwrite the committed CI fixtures in `tests/fixtures/llm/<incident-id>`.

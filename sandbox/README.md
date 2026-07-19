# Sandbox: the whole loop, live, on your laptop

A zero-risk playground (#81): mini Prometheus + Loki + a demo
checkout-service that misbehaves on command. Everything the tool does -
collect, investigate, approve, **execute live**, verify recovery - runs
against localhost, and nothing real can be touched.

The trick that makes it satisfying: **the incident IS a feature flag.**
Enabling `checkout_enrichment` degrades the service; the tool's own
`execute --live` PATCH is what genuinely ends the outage. You watch the
closed loop close.

Costs: everything except the LLM calls is free. The one investigation
worth paying for here is ~$0.25 on Haiku (`--llm record`); `--llm off`
shows the deterministic layer for free but produces no remediation plans
(so the approve/execute half needs the paid run).

## 0. Prerequisites (once)

Docker, plus from the repo root:

```sh
uv sync --dev
# executor credential (any value in the sandbox; must exist - isolation is enforced):
printf 'FLAG_TOGGLE_TOKEN=sandbox\n' >> .env
# only for --llm record: ANTHROPIC_API_KEY=... in .env too (chmod 600)
```

## 1. Start the stack and watch it go green

```sh
docker compose -f sandbox/docker-compose.yml up -d --build

uv run python -m ai_incident_investigator collect doctor \
  --sources sandbox/sources.toml --issue 9001
```

Doctor doubles as the readiness probe: on a fresh stack the Prometheus
and Loki checks FAIL for a minute or two ("no series" / "matched no
streams") until the first scrapes and pushes land. Re-run until
everything is green - that is the doctor doing exactly its job.

## 2. Let baselines bake (~15 minutes)

The sandbox config shrinks the baseline span to 8 minutes with a
2-minute margin and a 5-minute lookback, so after ~15 minutes of quiet
operation the stack has honest pre-incident baselines. Coffee.

## 3. Break it, then investigate for real

```sh
curl -X POST localhost:8000/incident/start
# give the outage 3-5 minutes to produce alert-worthy data, then:

AI_INCIDENT_INVESTIGATOR_MODEL=claude-haiku-4-5-20251001 \
uv run --env-file .env python -m ai_incident_investigator collect \
  --sources sandbox/sources.toml --issue 9001 \
  --output /tmp/sandbox-incident --http live \
  --then-investigate --llm record --fixtures-dir /tmp/sandbox-fixtures \
  --format markdown --report /tmp/sandbox-report.md
```

Read `/tmp/sandbox-report.md`: a real investigation of an outage that is
really happening on your machine - the runbook's "retry amplification"
failure mode, evidence-cited hypotheses, and a remediation plan whose
state-changing step is disabling the flag. Then the JSON twin, replayed
free from the recording:

```sh
AI_INCIDENT_INVESTIGATOR_MODEL=claude-haiku-4-5-20251001 \
uv run python -m ai_incident_investigator investigate \
  --incident /tmp/sandbox-incident --llm replay \
  --fixtures-dir /tmp/sandbox-fixtures --output /tmp/sandbox-report.json
```

## 4. Approve, execute LIVE, and end the outage

```sh
uv run python -m ai_incident_investigator approve \
  --report /tmp/sandbox-report.json --list
uv run python -m ai_incident_investigator approve \
  --report /tmp/sandbox-report.json --plan <plan-id> --step 1 --approver "$USER"

uv run --env-file .env python -m ai_incident_investigator execute \
  --report /tmp/sandbox-report.json --executor-config sandbox/executor.toml \
  --plan <plan-id> --step 1 --environment staging --flag checkout_enrichment \
  --off --executed-by "$USER" --live --http live
```

`EXECUTED - sent PATCH http://localhost:8000/flags/staging/checkout_enrichment`
- and the app really recovers (watch `curl -s localhost:8000/metrics`).
The step->action mapping is explicit on purpose: whatever the plan's
prose says, YOU name the flag; the gate checks the approval, the
allowlist, and the tier quorum.

## 5. Prove the recovery

```sh
# give it 2-3 minutes to re-baseline, then snapshot again:
uv run python -m ai_incident_investigator collect \
  --sources sandbox/sources.toml --issue 9001 \
  --output /tmp/sandbox-followup --http live

uv run python -m ai_incident_investigator compare \
  --incident /tmp/sandbox-incident --follow-up /tmp/sandbox-followup \
  --format markdown \
  --verify-execution /tmp/sandbox-report.json \
  --update-postmortem /tmp/sandbox-report.json
```

Verdict RECOVERED, the execution's `pending` verification becomes
`verified` (appended, never mutated), and the postmortem sidecar gains
the recovery evidence. The loop is closed and every step of it is in the
audit files next to `/tmp/sandbox-report.json`.

## 5b. The second-incident payoff (v7 learning)

Add the closed incident to a history store - the verified fix rides
along automatically from the sidecars:

```sh
uv run python -m ai_incident_investigator history add \
  --history /tmp/sandbox-history --report /tmp/sandbox-report.json
```

Now re-break the service (step 3) and investigate again with
`--history /tmp/sandbox-history` added to the investigate command. The
new report opens with precedent: a deterministic match to the first
incident, exactly which signals matched, and
`[verified] staging/checkout_enrichment -> off` as the fix that provably
ended it last time. Same approval quorum, same gates - the tool just no
longer starts from zero.

## 6. Tear down

```sh
docker compose -f sandbox/docker-compose.yml down
```

Break it again any time: `curl -X POST localhost:8000/incident/start` -
or flip the flag back on THROUGH the tool and watch the refusal matrix
earn its keep if you skip the approval.

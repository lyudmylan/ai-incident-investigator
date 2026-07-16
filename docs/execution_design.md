# Execution design (v5 pilot)

Decisions for epic #60, recorded by issue #64 before any executing code.
The generated schema is docs/execution_contract.md; the models live in
`ai_incident_investigator.models.execution`.

## The one action

A feature-flag toggle: set one allowlisted flag on or off in one named
environment. `FlagToggleRequest` is the only action type; its `method` is
`Literal["PATCH"]` and the route is derived as
`{base_url}/flags/{environment}/{flag_key}` from validated name segments -
the publish client's derived-route pattern. No other request is
representable, so "what else could the executor do" has a structural
answer: nothing.

The wire format is LaunchDarkly-like but deliberately minimal
(`PATCH .../flags/{env}/{key}` with body `{"on": bool}`). The adapter
(#67) stubs it with record/replay fixtures like every other adapter;
integrating a real flag service means adapting inside the client only.

## Approval policy: peer quorum, not hierarchy

Owner decision (2026-07-15): no single individual may green-light a
production-affecting change, and the answer is NOT a role hierarchy.

- Quorum of DISTINCT claimed approver identities per environment tier:
  sandbox 1, staging 1, production 2 (defaults; configurable upward).
- The schema enforces the floor: `ApprovalPolicy.production` has `ge=2`,
  so a config in which one person can execute against production is
  unrepresentable - not a runtime check, a shape.
- The on-call engineer IS authorized to approve. For production tier
  their approval is necessary but not sufficient: any second distinct
  qualified person completes quorum. Escalation stays human - the tool
  reports what quorum is missing ("approved 1/2"), never who to page.
- The same claimed identity approving twice counts once. Expired or
  void approvals never count (#65 implements evaluation in
  `is_actionable`, on the UNCHANGED v4 record format - approvals already
  accumulate per step; policy reads the set).
- The `execute` invoker MAY count toward quorum (default): the control
  is the second pair of eyes, not invoker exclusion - on-call approves,
  a peer approves, on-call runs it. Teams wanting stricter separation of
  duties set `invoker_counts_toward_quorum = false`.

## Step -> action mapping (explicit, never parsed)

A plan step is prose; the executor never extracts a flag name from it (no
hidden business logic in text). The human names `--environment`, `--flag`,
and the desired state explicitly; the executor then validates EVERYTHING:
the action's shape, the allowlist, the tier's quorum against the approval
records for exactly that plan step, and the pilot's live-tier rule. The
approval binds the step; the invocation binds the action; the execution
record binds both together, auditable.

## The allowlist

Exact flag keys per environment; each environment carries a tier the
policy keys on. An unlisted flag/environment pair is structurally
unreachable. During the pilot, live execution is additionally restricted
to `PILOT_LIVE_TIERS` (sandbox, staging): production entries may exist -
so plans and dry-runs can name them and quorum can be rehearsed - but the
live path refuses them until the pilot proves out (epic #60).

## The audit record

`ExecutionRecord` is written to the append-only sidecar
`<report>.executions.json` (mirroring approvals) BEFORE success is
reported. It binds to the exact report content (`report_sha256`), names
the mode (`dry_run`/`live`), the quorum demanded and the distinct
approvers that met it, the invoker, the outcome
(`previewed`/`applied`/`refused`/`failed`), and a verification outcome
owned by #68 (`not_applicable` for dry-runs; live starts `pending`;
absent signals are `unverifiable`, never assumed good; a met abort
condition is recorded as `aborted`). Refusals are records too - a denied
execution attempt is audit-worthy, not silent.

## What voids an execution mid-flight

- Between gate and send: the executor re-evaluates hash, expiry, and
  quorum immediately before the adapter call in the same process run;
  any change refuses with a recorded reason.
- After send: the HTTP call is atomic from our side; there is no
  in-flight abort. The plan's abort conditions govern post-execution
  verification (#68): if a follow-up snapshot meets an abort condition
  or the re-alert rule, the record's verification becomes `aborted` and
  humans act on it. The executor never auto-rolls-back.

## Credential isolation (both directions)

The executor credential is its own env var (default `FLAG_TOGGLE_TOKEN`),
value only ever in the environment (.env), never in config:

- `ExecutorConfig.token_env` refuses the publish credential
  (`GITHUB_PUBLISH_TOKEN`) and the LLM credential (`ANTHROPIC_API_KEY`).
- `collect/config.py` refuses `FLAG_TOGGLE_TOKEN` in any `*_env` key,
  exactly as it refuses the publish token (the #54 pattern; cross-check
  tests pin the duplicated constants equal).
- Pasted credential VALUES anywhere in executor config are rejected
  outright - including inside `[[environments]]` tables; the traversal and
  the secret-key markers are shared with collect via `models.common`, so
  the two guardrails cannot drift.
- Any FUTURE write-side credential must be added to
  `collect/config._WRITE_TOKEN_ENVS` with its own cross-check test pinning
  the duplicated constant - the denylist does not discover new
  credentials by itself.

## Honest limitations

Approver and invoker identities are CLAIMED, not authenticated - the
quorum policy is a process control, auditable after the fact, not a
cryptographic one. Identity verification (SSO or forge-backed) is a
post-pilot integration; until then the audit records make any abuse
attributable, not impossible.

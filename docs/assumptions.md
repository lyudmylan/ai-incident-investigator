# Assumptions

The explicit rules behind severity, confidence, the incident window, and
safety. Agents must apply these rules; they must not invent their own.

## Incident window rule

- **Start**: `alert.triggered_at` minus a lookback of **30 minutes** (default,
  CLI-overridable). The lookback exists because telemetry usually degrades
  before the alert threshold is crossed.
- **End**: the start of sustained recovery, when every metric series that
  deviated ends recovered (within 10% of baseline for at least 3 consecutive
  trailing points). Otherwise the incident is reported as ongoing
  (`end: null`) as of the latest data point in the package.
- Evidence outside the window may still be cited (e.g. an earlier deploy), but
  must be marked as such in its interpretation.

## Metric deviation rule

A metric value is **anomalous** when it is >= 2x its pre-incident baseline or
<= 0.5x of it. With a zero baseline, any nonzero value is anomalous. This is
deliberately coarse: it flags candidates deterministically; interpreting them
is the agents' job.

## Collected metric baselines (v2)

Hand-authored packages carry baselines explicitly. When metrics are
collected live (`collect/prometheus.py`), each series' baseline is derived:

- **baseline = median** of the samples in the pre-incident span (median,
  not mean: robust against earlier spikes).
- The span is a fixed **2 hours**, ending **15 minutes before the incident
  window starts** (window start = alert trigger minus the lookback), so the
  incident run-up cannot contaminate the baseline.
- A series with no samples in that span cannot satisfy the contract and is
  skipped, with the skip recorded in the collection report.
- Non-finite samples (NaN/Inf) are skipped and counted in the report.

## Timeline inclusion rules

The deterministic timeline contains, in timestamp order:

- the alert trigger
- every deploy, config change, and feature flag flip
- log records at WARN, ERROR, or FATAL (INFO/DEBUG stay available as evidence
  but are not timeline events)
- per metric series: the first anomalous point (see deviation rule)
- root trace spans with status `error`

## Severity rules

| Level | Rule of thumb |
| --- | --- |
| SEV-1 | critical user-facing flow broken for most users, no workaround (e.g. error rate > 25% on a critical flow, or hard outage) |
| SEV-2 | significant degradation of an important flow (e.g. error rate 1-25% or latency > 4x baseline on a critical flow) |
| SEV-3 | limited degradation, workaround exists, small share of users affected |
| SEV-4 | warning-level signal, no established user impact yet |

- Severity is assessed from observed impact in the package, not from the
  monitoring alert's own severity label (which is evidence, not a verdict).
- Patient- or safety-impacting flows (the target user includes healthcare SaaS)
  bias one level up when in doubt.
- Severity always ships with an explanation and a confidence label.

## Confidence rubric

Confidence labels are derived from these auditable inputs, recorded on each
hypothesis as `rubric`:

- `aligned_signals`: number of **independent sources** (alert, metrics, logs,
  traces, deploys, topology, runbook) whose evidence points the same way.
- `timing_alignment`: whether the suspected cause precedes the symptom onset
  and the gap is plausible (`aligned` / `misaligned` / `unknown`).
- `conflicting_evidence_count`: evidence items that point away from the
  hypothesis.

| Label | Requires |
| --- | --- |
| high | >= 3 aligned signals AND timing `aligned` AND 0 conflicts |
| medium | (when not high) >= 2 aligned signals AND timing not `misaligned` AND <= 1 conflict |
| low | everything else (single signal, conflicts > 1, timing misaligned) |

The label is **derived in code** from these inputs (`rubric.py`); the ranker
agent cites evidence and judges timing but cannot choose the label, so
wording strength never raises confidence; only evidence does. Root cause is
never "confirmed" by this tool — the strongest claim is a high-confidence
hypothesis.

## Remediation plan invariants (v3)

Plans are guidance for a human, never an execution script. Enforced
structurally and by the deterministic linter:

- Every **state-changing** step carries `requires_human_approval: true`
  (schema-enforced, like mitigation options) and a **required** verification
  describing how a human confirms it worked before continuing.
- `abort_conditions` is mandatory and non-empty: a plan must say when to
  stop and back out.
- A plan must reference an existing hypothesis; a rollback plan should
  reference the deploy-correlated one.
- Plans use option language ("consider", "if approved"); executed-action
  phrasing is linted.
- An empty plan list is valid when nothing is well-grounded.

## Recovery verification plan derivation (v3)

Built deterministically from package facts (`recovery.py`); no LLM:

- **Watched signals**: exactly the metric series that deviated in the
  window (the same >= 2x / <= 0.5x rule the window uses), each with its
  baseline and the recovery rule spelled out (within 10% of baseline for
  >= 3 consecutive points - the same rule that ends the window).
- **Watch duration**: twice the observed deviation duration, minimum 30
  minutes. Recovery should be observed for at least as long as the
  disruption lasted.
- **Log patterns that should stop**: ERROR/FATAL messages in the window,
  digit runs normalized to `N`, recurring means >= 2 occurrences of the
  same shape; at most 5 patterns, ordered by frequency then text.
- **Re-alert condition**: the original alert's signal and threshold when
  present, else the alert by name.
- **Mode**: `confirm_sustained_recovery` when the window already ended
  (recovery was observed in-window); otherwise `watch_for_recovery`.
- No metrics, or no deviated series -> no plan (`null`), with the reason
  in the reasoning trace.

## Customer-safe wording rules (status-page draft, v3)

The status-page draft is the only customer-facing text the tool produces.
Rules (lintable; the deterministic linter and the safety critic both check):

- **No internal service names** - checkable mechanically against the
  topology's service list.
- **No speculation or hypothesis language**: no "we believe", "likely
  caused by", "appears to be" - customers get established facts and impact.
- **No root-cause claims** during the incident; root cause belongs to the
  postmortem after human review.
- No blame (vendors included), no employee names, no internal tooling names.
- Plain impact-and-status language: what users experience, what state the
  incident is in (investigating / identified / monitoring), where updates
  will appear.

## Jira priority mapping (v3)

Suggested priority is a documented mapping, not a judgment call:
SEV-1 -> Highest, SEV-2 -> High, SEV-3 -> Medium, SEV-4 -> Low.

## Safety assumptions

- The tool investigates and recommends; it never executes. No rollback,
  restart, scaling, config, flag, migration, paging, or customer communication.
- Every mitigation option carries `requires_human_approval: true`, enforced by
  the output schema (a value of `false` cannot be expressed).
- Drafts are for internal review; nothing is sent anywhere by this tool.
- The safety review stage lints the final report for violations of the above
  (e.g. executed-action phrasing) and records its checks in `safety_review`.

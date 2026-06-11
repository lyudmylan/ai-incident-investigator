# Assumptions

The explicit rules behind severity, confidence, the incident window, and
safety. Agents must apply these rules; they must not invent their own.

## Incident window rule

- **Start**: `alert.triggered_at` minus a lookback of **30 minutes** (default,
  CLI-overridable). The lookback exists because telemetry usually degrades
  before the alert threshold is crossed.
- **End**: the latest timestamp present in the package, unless the data shows
  sustained recovery (signals back within ~10% of baseline for at least
  3 consecutive points), in which case the start of that recovery. An incident
  is reported as ongoing (`end: null`) when recovery cannot be established.
- Evidence outside the window may still be cited (e.g. an earlier deploy), but
  must be marked as such in its interpretation.

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
| medium | exactly 2 aligned signals AND timing not `misaligned` AND <= 1 conflict |
| low | everything else (single signal, missing data, conflicts, timing misaligned or unknown) |

Wording strength never raises confidence; only evidence does. Root cause is
never "confirmed" by this tool — the strongest claim is a high-confidence
hypothesis.

## Safety assumptions

- The tool investigates and recommends; it never executes. No rollback,
  restart, scaling, config, flag, migration, paging, or customer communication.
- Every mitigation option carries `requires_human_approval: true`, enforced by
  the output schema (a value of `false` cannot be expressed).
- Drafts are for internal review; nothing is sent anywhere by this tool.
- The safety review stage lints the final report for violations of the above
  (e.g. executed-action phrasing) and records its checks in `safety_review`.

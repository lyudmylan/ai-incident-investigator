# Postmortem draft (updated from verified recovery)

### Postmortem draft: booking latency 2026-06-01

Booking latency and errors rose after a deploy.

**Impact.** p95 rose 450ms to 3200ms; errors 0.3% to 4.8%. Recovery verification (latency_spike follow-up): INCONCLUSIVE - 4/5 watched signals recovered (1 unverifiable); 0 of 2 watched error patterns still present; re-alert condition not met

**Contributing factors**
- likely: deploy-driven retry amplification

**Open questions**
- whether retries are bounded
- recovery of appointments-db/cpu_pct is unverifiable: signal absent from the follow-up snapshot; recovery unverifiable

**Action items**
- compare error rates before and after the deploy

_Deterministically merged from the latency_spike follow-up snapshot (verdict: inconclusive). The report file is untouched - rewriting it would void the approvals bound to its hash._

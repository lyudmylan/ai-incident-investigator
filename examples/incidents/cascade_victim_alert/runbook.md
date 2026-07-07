# Runbook: notification delivery lag

## Failure modes

### session-store eviction cascade
When session-store evicts under memory pressure, message-broker consumers
lose their sessions, rejoin, and rebalance repeatedly; queue depth grows
while workers thrash. Worker lag is the SYMPTOM; the store is the cause.

## Verified mitigations

- Raise session-store maxmemory one step (verified safe; keys are
  re-derivable) and let consumers re-register.
- Do NOT scale workers during a rebalance storm: more consumers worsen
  the rejoin churn.

## Escalation

Page the platform-cache owner if evictions persist above 100 keys/s for
more than 30 minutes.

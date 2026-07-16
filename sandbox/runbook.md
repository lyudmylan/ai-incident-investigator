# Runbook: checkout-service

## Service

Handles checkout for the storefront. Critical path: POST /checkout ->
eligibility lookup (payment-gateway) -> orders-db write.

## Known failure modes

- **Eligibility retry amplification after enabling `checkout_enrichment`**:
  the enrichment lookup adds a synchronous call on the checkout path with a
  5-attempt retry policy and no backoff. When the lookup is slow, retries
  multiply load; p95 latency and error rate climb together and orders fail
  with "eligibility lookup exhausted retries (5 of 5)".

## Safe mitigations

- Disabling the feature flag `checkout_enrichment` (environment: staging)
  is a **verified safe fallback**: checkout proceeds without enrichment.
  Verify: retry warnings stop within 2 minutes and p95 trends back toward
  the ~90ms baseline.

## Escalation

- Escalate to the payments team if error rate stays above 10% for
  15 minutes after mitigation.

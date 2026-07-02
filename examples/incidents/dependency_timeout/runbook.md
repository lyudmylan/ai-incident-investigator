# Runbook: checkout degradation

## Service overview

`payments-service` handles checkout. Critical path: `POST /checkout` ->
tax quote (`tax-api`, third party) -> card charge -> order persist
(`checkout-db`). Checkout failures block revenue directly; treat sustained
degradation as high urgency.

## Known failure modes

1. **tax-api degradation (third party).** Symptoms: `quote_tax` spans pinned
   at the 5000ms client timeout, `TimeoutError` in logs, circuit breaker
   opening, checkout latency saturating near the timeout while card
   authorization stays healthy. We cannot fix the vendor; we can only shed
   or bypass the dependency.
2. **checkout-db saturation.** Symptoms: db CPU high, `persist_order` spans
   slow, latency elevated but not pinned at a fixed ceiling.
3. **Card processor degradation.** Symptoms: `charge_card` spans slow or
   failing, processor status page incidents.

## Checks

- Check whether `quote_tax` durations are pinned at exactly the client
  timeout (vendor-side) vs spread (network/ours).
- Check the tax-api vendor status page and error rates from a second region.
- Confirm card authorization and database paths are healthy to isolate the
  dependency.
- Check circuit breaker state transitions in logs.

## Safe mitigations (human approval required)

- Enable the `cached_tax_rates` fallback flag: checkout uses last-known tax
  rates (verified safe for up to 24h staleness; finance sign-off exists).
- Reduce the tax-api client timeout from 5000ms to 1500ms to fail fast -
  only meaningful together with the cached-rates fallback.
- Do NOT retry harder: retries against a timing-out vendor amplify load
  and delay checkout further.

## Escalation

- Primary: payments on-call. Vendor: open a P1 with the tax-api provider,
  attach trace exemplars. Finance: notify if cached rates are enabled.

# Runbook: appointment booking latency

## Service overview

`booking-service` handles appointment booking for the telemedicine platform.
Critical path: `POST /book` -> payment eligibility check (`payment-service`)
-> slot reservation (`appointments-db`). Bookings are patient-facing; treat
sustained degradation as high urgency.

## Known failure modes

1. **appointments-db saturation.** Symptoms: db CPU > 85%, connection pool
   near 100/100, slow `eligibility_query` / `reserve_slot` spans. Booking and
   payment both degrade together because they share the database.
2. **Retry amplification after payment changes.** The booking flow retries
   eligibility lookups up to 5 times with no backoff. A slow eligibility path
   multiplies load on `payment-service` and `appointments-db` roughly 5x.
   Past incident: 2025-11-12 eligibility cache regression.
3. **Queue backlog.** `booking-queue` depth above ~200 delays confirmation
   processing; notifications are unaffected (separate consumer).

## Checks

- Compare booking-service error rate and latency before/after the most recent
  release; correlate deploy time with symptom onset.
- Inspect `appointments-db` connection pool and slow query log.
- Check whether eligibility retries are bounded and whether they back off.

## Safe mitigations (human approval required)

- Disable feature flag `payment_enrichment` (safe: booking falls back to the
  pre-enrichment eligibility path; verified in staging 2026-05-28).
- Roll back the latest `booking-service` release.
- Increase booking worker concurrency **only if** queue pressure remains high
  after the database recovers; adding workers during db saturation makes the
  saturation worse.

## Escalation

- Primary: booking team on-call. Secondary: platform on-call.
- Page the data team if `appointments-db` CPU stays > 90% for 15 minutes.

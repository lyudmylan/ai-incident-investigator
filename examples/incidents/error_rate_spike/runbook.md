# Runbook: notification send failures

## Service overview

`notifications-service` renders and sends patient notifications (email) via
`email-gateway`. Templates live in `template-store`. Notifications are not on
the booking critical path; failed sends are retried from a dead-letter queue,
so patient impact is delayed rather than immediate.

## Known failure modes

1. **Template rendering errors after template or flag changes.** Symptoms:
   `TemplateRenderError` in logs, error rate up while latency stays flat
   (failures are fast). Almost always caused by templates referencing
   placeholders the calling context does not provide.
2. **email-gateway throttling.** Symptoms: elevated latency AND errors,
   gateway acceptance rate below 99%, `429` responses in logs.
3. **Dead-letter queue overflow.** Above ~50k queued messages, retry drain
   can delay notifications by hours.

## Checks

- Correlate error onset with the most recent template, flag, or config
  change; compare failing template names against the change.
- Check email-gateway acceptance rate to rule the gateway in or out.
- Check dead-letter queue depth and drain rate after recovery.

## Safe mitigations (human approval required)

- Disable the `rich_templates` feature flag (verified safe fallback: sends
  use the legacy template path).
- Re-drain the dead-letter queue during business hours only, in batches,
  to avoid duplicate-send spikes.

## Escalation

- Primary: messaging team on-call. Gateway issues: escalate to vendor
  support with acceptance-rate graphs attached.

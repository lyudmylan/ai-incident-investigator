# Collection source mappings

How each read-only source maps onto the incident package contract
(docs/incident_package_contract.md). Code and this document change together;
each adapter names the section that governs it.

General rules for all sources:

- Adapters normalize timestamps to timezone-aware UTC before building
  package models; source timestamps documented as UTC get UTC attached when
  they arrive naive.
- Credentials are env-var references in `sources.toml` (`*_env` keys),
  resolved at request time, never stored (docs/architecture.md, collection
  layer).
- Mapping is conservative: a field is only populated when the source
  genuinely carries it; nothing is inferred.

## Sentry-like issue source (`[sentry]`, `collect/sentry.py`)

Endpoints used (read-only): `GET {base_url}/issues/{id}/` and
`GET {base_url}/issues/{id}/events/latest/`.

The issue alone is sufficient to anchor collection; the latest event only
enriches (fresher trigger time, service tag, breadcrumb logs). A failing
event fetch degrades silently to issue-only; an unusable issue fails
collection loudly (no anchor, no incident window).

### alert.json

| Alert field | Source | Rule |
| --- | --- | --- |
| `id` | issue `id` | prefixed: `sentry_{id}` |
| `title` | issue `title` | verbatim |
| `service` | event tag `[sentry].service_tag`, else issue `project.slug` | tag wins only when configured and present |
| `triggered_at` | latest event `dateCreated`, else issue `lastSeen` | the freshest "it is happening" timestamp anchors the window; the lookback covers the run-up |
| `severity` | issue `level` | verbatim monitoring label (evidence, not a verdict) |
| `description` | issue `metadata.value` + `culprit` + `permalink` | joined from whichever are present |
| `signal`, `threshold`, `observed_value` | — | not derivable from an error-tracking issue; left unset |

### Supplementary logs.jsonl

Only event data that is genuinely log-shaped becomes log records:

- one record for the event itself: the issue title at `triggered_at`,
  level from the issue level;
- one record per breadcrumb of the latest event that has **both** a
  timestamp and a message (documented conservative rule - crumbs missing
  either are skipped); `category` becomes a `[category]` message prefix.

Level normalization (issue levels and breadcrumb levels):
`debug->DEBUG, info->INFO, warning/warn->WARN, error->ERROR,
fatal/critical->FATAL`, anything else `INFO`.

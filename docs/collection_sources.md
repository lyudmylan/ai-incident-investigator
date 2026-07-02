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

## Prometheus-like metrics source (`[prometheus]`, `collect/prometheus.py`)

Endpoint used (read-only): `GET {base_url}/api/v1/query_range` with
`query`, `start`, `end`, `step` - one call per configured query.

Configuration: one `[[prometheus.queries]]` entry per package series
(`service`, `signal`, `query`, optional `unit`); `step_seconds` (default
300) and `post_minutes` (default 30, how far past the alert trigger points
are collected). No PromQL is authored by the tool - each configured query
must return **exactly one** series; zero or several is a skip with a note
(make the query more specific).

### metrics.json

| Series field | Source | Rule |
| --- | --- | --- |
| `service`, `signal`, `unit` | config | verbatim from the query entry |
| `baseline` | derived | median over the pre-incident span - see "Collected metric baselines" in docs/assumptions.md |
| `points` | query result | samples within [window start, alert + post_minutes], chronological |

One `query_range` call covers baseline span plus window. Samples between
the baseline span and the window (the 15-minute margin) are discarded.
Non-finite samples (NaN/Inf) are skipped and counted in the collection
report. A query with an HTTP error, a non-success Prometheus status, an
ambiguous result, or no usable baseline/window samples skips that series
with a note; the adapter fails outright only when no series was collected
at all.

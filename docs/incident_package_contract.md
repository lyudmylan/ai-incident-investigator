<!-- GENERATED FILE - do not edit by hand. Regenerate with: uv run python -m ai_incident_investigator.contracts -->
# Incident Package Contract

An incident package is a directory of files describing one incident.

| File | Required | Contents |
| --- | --- | --- |
| `alert.json` | yes | the alert that opened the incident; anchors the incident window |
| `metrics.json` | no | metric series with required pre-incident baselines |
| `logs.jsonl` | no | structured log records, one JSON object per line (preferred) |
| `logs.txt` | no | unstructured logs, parsed best-effort into the same record shape |
| `traces.json` | no | distributed trace spans |
| `deploys.json` | no | recent deploys, config changes, feature flag flips |
| `topology.json` | no | service dependency graph |
| `runbook.md` | no | free-form operational guidance, carried verbatim |

Missing optional files become `missing_data` entries in the report; they never
fail the run. All timestamps must be timezone-aware (UTC recommended). Unknown
fields are rejected.

The JSON Schema below describes the fully loaded package; the definitions for
each file's payload are under `$defs`.

## JSON Schema: `IncidentPackage`

```json
{
  "$defs": {
    "Alert": {
      "additionalProperties": false,
      "description": "alert.json \u2014 the monitoring alert that opened the incident. Required.",
      "properties": {
        "id": {
          "title": "Id",
          "type": "string"
        },
        "title": {
          "title": "Title",
          "type": "string"
        },
        "service": {
          "description": "Service the alert fired on",
          "title": "Service",
          "type": "string"
        },
        "triggered_at": {
          "description": "Anchors the incident window",
          "format": "date-time",
          "title": "Triggered At",
          "type": "string"
        },
        "severity": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Severity as reported by monitoring",
          "title": "Severity"
        },
        "description": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Description"
        },
        "signal": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Signal name, e.g. p95_latency_ms",
          "title": "Signal"
        },
        "threshold": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Threshold"
        },
        "observed_value": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Observed Value"
        }
      },
      "required": [
        "id",
        "title",
        "service",
        "triggered_at"
      ],
      "title": "Alert",
      "type": "object"
    },
    "Deploy": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "title": "Id",
          "type": "string"
        },
        "service": {
          "title": "Service",
          "type": "string"
        },
        "version": {
          "description": "Release identifier, e.g. 2026.06.01-1420",
          "title": "Version",
          "type": "string"
        },
        "deployed_at": {
          "format": "date-time",
          "title": "Deployed At",
          "type": "string"
        },
        "change_type": {
          "default": "deploy",
          "enum": [
            "deploy",
            "config",
            "feature_flag"
          ],
          "title": "Change Type",
          "type": "string"
        },
        "description": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Description"
        }
      },
      "required": [
        "id",
        "service",
        "version",
        "deployed_at"
      ],
      "title": "Deploy",
      "type": "object"
    },
    "DeploysFile": {
      "additionalProperties": false,
      "description": "deploys.json \u2014 recent deploys, config changes, and flag flips.",
      "properties": {
        "deploys": {
          "items": {
            "$ref": "#/$defs/Deploy"
          },
          "minItems": 1,
          "title": "Deploys",
          "type": "array"
        }
      },
      "required": [
        "deploys"
      ],
      "title": "DeploysFile",
      "type": "object"
    },
    "LogRecord": {
      "additionalProperties": false,
      "description": "One line of logs.jsonl \u2014 the preferred, structured log format.\n\nlogs.txt is accepted as a best-effort fallback; the loader parses it into\nthis same shape and reports unparseable lines as missing data.",
      "properties": {
        "timestamp": {
          "format": "date-time",
          "title": "Timestamp",
          "type": "string"
        },
        "service": {
          "title": "Service",
          "type": "string"
        },
        "level": {
          "enum": [
            "DEBUG",
            "INFO",
            "WARN",
            "ERROR",
            "FATAL"
          ],
          "title": "Level",
          "type": "string"
        },
        "message": {
          "title": "Message",
          "type": "string"
        }
      },
      "required": [
        "timestamp",
        "service",
        "level",
        "message"
      ],
      "title": "LogRecord",
      "type": "object"
    },
    "MetricPoint": {
      "additionalProperties": false,
      "properties": {
        "timestamp": {
          "format": "date-time",
          "title": "Timestamp",
          "type": "string"
        },
        "value": {
          "title": "Value",
          "type": "number"
        }
      },
      "required": [
        "timestamp",
        "value"
      ],
      "title": "MetricPoint",
      "type": "object"
    },
    "MetricSeries": {
      "additionalProperties": false,
      "properties": {
        "service": {
          "title": "Service",
          "type": "string"
        },
        "signal": {
          "description": "Signal name, e.g. p95_latency_ms, error_rate_pct",
          "title": "Signal",
          "type": "string"
        },
        "baseline": {
          "description": "Required pre-incident baseline; without it 'abnormal' is undefined offline",
          "title": "Baseline",
          "type": "number"
        },
        "unit": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Unit"
        },
        "points": {
          "items": {
            "$ref": "#/$defs/MetricPoint"
          },
          "minItems": 1,
          "title": "Points",
          "type": "array"
        }
      },
      "required": [
        "service",
        "signal",
        "baseline",
        "points"
      ],
      "title": "MetricSeries",
      "type": "object"
    },
    "MetricsFile": {
      "additionalProperties": false,
      "description": "metrics.json \u2014 metric snapshots covering the incident window.",
      "properties": {
        "series": {
          "items": {
            "$ref": "#/$defs/MetricSeries"
          },
          "minItems": 1,
          "title": "Series",
          "type": "array"
        }
      },
      "required": [
        "series"
      ],
      "title": "MetricsFile",
      "type": "object"
    },
    "ServiceNode": {
      "additionalProperties": false,
      "properties": {
        "name": {
          "title": "Name",
          "type": "string"
        },
        "kind": {
          "default": "service",
          "enum": [
            "service",
            "database",
            "queue",
            "cache",
            "third_party"
          ],
          "title": "Kind",
          "type": "string"
        },
        "depends_on": {
          "items": {
            "type": "string"
          },
          "title": "Depends On",
          "type": "array"
        }
      },
      "required": [
        "name"
      ],
      "title": "ServiceNode",
      "type": "object"
    },
    "Span": {
      "additionalProperties": false,
      "properties": {
        "trace_id": {
          "title": "Trace Id",
          "type": "string"
        },
        "span_id": {
          "title": "Span Id",
          "type": "string"
        },
        "parent_span_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "None for a root span",
          "title": "Parent Span Id"
        },
        "service": {
          "title": "Service",
          "type": "string"
        },
        "operation": {
          "title": "Operation",
          "type": "string"
        },
        "start_time": {
          "format": "date-time",
          "title": "Start Time",
          "type": "string"
        },
        "duration_ms": {
          "minimum": 0,
          "title": "Duration Ms",
          "type": "number"
        },
        "status": {
          "default": "ok",
          "enum": [
            "ok",
            "error"
          ],
          "title": "Status",
          "type": "string"
        }
      },
      "required": [
        "trace_id",
        "span_id",
        "service",
        "operation",
        "start_time",
        "duration_ms"
      ],
      "title": "Span",
      "type": "object"
    },
    "TopologyFile": {
      "additionalProperties": false,
      "description": "topology.json \u2014 the service dependency graph.",
      "properties": {
        "services": {
          "items": {
            "$ref": "#/$defs/ServiceNode"
          },
          "minItems": 1,
          "title": "Services",
          "type": "array"
        }
      },
      "required": [
        "services"
      ],
      "title": "TopologyFile",
      "type": "object"
    },
    "TracesFile": {
      "additionalProperties": false,
      "description": "traces.json \u2014 distributed trace spans from the incident window.",
      "properties": {
        "spans": {
          "items": {
            "$ref": "#/$defs/Span"
          },
          "minItems": 1,
          "title": "Spans",
          "type": "array"
        }
      },
      "required": [
        "spans"
      ],
      "title": "TracesFile",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "description": "A fully loaded incident package, as assembled by the loader.\n\nrunbook.md is free-form Markdown and is carried verbatim.",
  "properties": {
    "incident_id": {
      "description": "Derived from the package directory name",
      "title": "Incident Id",
      "type": "string"
    },
    "alert": {
      "$ref": "#/$defs/Alert"
    },
    "metrics": {
      "anyOf": [
        {
          "$ref": "#/$defs/MetricsFile"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "logs": {
      "items": {
        "$ref": "#/$defs/LogRecord"
      },
      "title": "Logs",
      "type": "array"
    },
    "traces": {
      "anyOf": [
        {
          "$ref": "#/$defs/TracesFile"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "deploys": {
      "anyOf": [
        {
          "$ref": "#/$defs/DeploysFile"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "topology": {
      "anyOf": [
        {
          "$ref": "#/$defs/TopologyFile"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "runbook": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Runbook"
    }
  },
  "required": [
    "incident_id",
    "alert"
  ],
  "title": "IncidentPackage",
  "type": "object"
}
```

<!-- GENERATED FILE - do not edit by hand. Regenerate with: uv run python -m ai_incident_investigator.contracts -->
# Learning Contract

v7 pilot (epic #86): the incident fingerprint, the history entry the local
store keeps, and the match record. Contracts only - fingerprinting and
matching are pure functions of the tool's own artifacts (patterns.py); no
LLM, no network, no wall clock. The honesty floors are part of the schema
itself: a match's `score` must equal the sum of its matched features'
weights, differences travel in `unmatched` next to `matched`, and a
previewed or refused execution is not representable as a tried fix.
The normative matching rule: docs/assumptions.md ("Pattern matching
rule"). Design decisions and rationale: docs/learning_design.md.

## JSON Schema: `HistoryEntry`

```json
{
  "$defs": {
    "ExecutedFix": {
      "additionalProperties": false,
      "description": "A live execution that was actually attempted on this incident,\nwith how its verification ended. The consumer's wording rule\n(docs/assumptions.md): only `verification == \"verified\"` may be\npresented as precedent; every other outcome is a caution.",
      "properties": {
        "action": {
          "$ref": "#/$defs/FlagToggleRequest"
        },
        "outcome": {
          "enum": [
            "applied",
            "failed"
          ],
          "title": "Outcome",
          "type": "string"
        },
        "verification": {
          "enum": [
            "not_applicable",
            "pending",
            "verified",
            "unverifiable",
            "aborted"
          ],
          "title": "Verification",
          "type": "string"
        },
        "executed_at": {
          "format": "date-time",
          "title": "Executed At",
          "type": "string"
        }
      },
      "required": [
        "action",
        "outcome",
        "verification",
        "executed_at"
      ],
      "title": "ExecutedFix",
      "type": "object"
    },
    "FlagToggleRequest": {
      "additionalProperties": false,
      "description": "The ONLY action the pilot can express: set one allowlisted flag\non or off in one named environment. Route and verb are fixed; the\nvalidated segments are the only variable parts.",
      "properties": {
        "method": {
          "const": "PATCH",
          "default": "PATCH",
          "title": "Method",
          "type": "string"
        },
        "environment": {
          "title": "Environment",
          "type": "string"
        },
        "flag_key": {
          "title": "Flag Key",
          "type": "string"
        },
        "on": {
          "title": "On",
          "type": "boolean"
        }
      },
      "required": [
        "environment",
        "flag_key",
        "on"
      ],
      "title": "FlagToggleRequest",
      "type": "object"
    },
    "IncidentFingerprint": {
      "additionalProperties": false,
      "description": "The comparable, structured features of one investigation - a pure\nfunction of the report file (plus optional executions sidecar), never\nof wall-clock time or free text.",
      "properties": {
        "incident_id": {
          "title": "Incident Id",
          "type": "string"
        },
        "report_sha256": {
          "description": "hash of the exact report fingerprinted",
          "maxLength": 64,
          "minLength": 64,
          "title": "Report Sha256",
          "type": "string"
        },
        "window_start": {
          "description": "the report's incident window start",
          "format": "date-time",
          "title": "Window Start",
          "type": "string"
        },
        "services": {
          "description": "sorted union of affected services and abnormal-signal services",
          "items": {
            "type": "string"
          },
          "title": "Services",
          "type": "array"
        },
        "severity": {
          "$ref": "#/$defs/SeverityLevel"
        },
        "abnormal_signals": {
          "description": "sorted, deduplicated (service, signal) pairs cited as evidence",
          "items": {
            "$ref": "#/$defs/SignalObservation"
          },
          "title": "Abnormal Signals",
          "type": "array"
        },
        "deploy_correlated": {
          "description": "whether the top-ranked hypothesis cites deploys-sourced evidence",
          "title": "Deploy Correlated",
          "type": "boolean"
        },
        "executed_fixes": {
          "items": {
            "$ref": "#/$defs/ExecutedFix"
          },
          "title": "Executed Fixes",
          "type": "array"
        }
      },
      "required": [
        "incident_id",
        "report_sha256",
        "window_start",
        "services",
        "severity",
        "abnormal_signals",
        "deploy_correlated"
      ],
      "title": "IncidentFingerprint",
      "type": "object"
    },
    "SeverityLevel": {
      "description": "Severity classification; rules live in docs/assumptions.md.",
      "enum": [
        "SEV-1",
        "SEV-2",
        "SEV-3",
        "SEV-4"
      ],
      "title": "SeverityLevel",
      "type": "string"
    },
    "SignalObservation": {
      "additionalProperties": false,
      "description": "One (service, signal) pair the report cites as evidence.",
      "properties": {
        "service": {
          "title": "Service",
          "type": "string"
        },
        "signal": {
          "title": "Signal",
          "type": "string"
        },
        "direction": {
          "default": "unknown",
          "enum": [
            "elevated",
            "depressed",
            "unknown"
          ],
          "title": "Direction",
          "type": "string"
        }
      },
      "required": [
        "service",
        "signal"
      ],
      "title": "SignalObservation",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "description": "One past investigation as the store keeps it.",
  "properties": {
    "entry_id": {
      "description": "<incident_id>-<report sha256 first 16>; content-addressed",
      "title": "Entry Id",
      "type": "string"
    },
    "fingerprint": {
      "$ref": "#/$defs/IncidentFingerprint"
    }
  },
  "required": [
    "entry_id",
    "fingerprint"
  ],
  "title": "HistoryEntry",
  "type": "object"
}
```

## JSON Schema: `PatternMatch`

```json
{
  "$defs": {
    "ExecutedFix": {
      "additionalProperties": false,
      "description": "A live execution that was actually attempted on this incident,\nwith how its verification ended. The consumer's wording rule\n(docs/assumptions.md): only `verification == \"verified\"` may be\npresented as precedent; every other outcome is a caution.",
      "properties": {
        "action": {
          "$ref": "#/$defs/FlagToggleRequest"
        },
        "outcome": {
          "enum": [
            "applied",
            "failed"
          ],
          "title": "Outcome",
          "type": "string"
        },
        "verification": {
          "enum": [
            "not_applicable",
            "pending",
            "verified",
            "unverifiable",
            "aborted"
          ],
          "title": "Verification",
          "type": "string"
        },
        "executed_at": {
          "format": "date-time",
          "title": "Executed At",
          "type": "string"
        }
      },
      "required": [
        "action",
        "outcome",
        "verification",
        "executed_at"
      ],
      "title": "ExecutedFix",
      "type": "object"
    },
    "FlagToggleRequest": {
      "additionalProperties": false,
      "description": "The ONLY action the pilot can express: set one allowlisted flag\non or off in one named environment. Route and verb are fixed; the\nvalidated segments are the only variable parts.",
      "properties": {
        "method": {
          "const": "PATCH",
          "default": "PATCH",
          "title": "Method",
          "type": "string"
        },
        "environment": {
          "title": "Environment",
          "type": "string"
        },
        "flag_key": {
          "title": "Flag Key",
          "type": "string"
        },
        "on": {
          "title": "On",
          "type": "boolean"
        }
      },
      "required": [
        "environment",
        "flag_key",
        "on"
      ],
      "title": "FlagToggleRequest",
      "type": "object"
    },
    "MatchedFeature": {
      "additionalProperties": false,
      "description": "One shared feature, carrying its own score weight so the total is\nauditable from the record.",
      "properties": {
        "feature": {
          "enum": [
            "signal",
            "direction",
            "service",
            "severity",
            "deploy_correlated"
          ],
          "title": "Feature",
          "type": "string"
        },
        "detail": {
          "title": "Detail",
          "type": "string"
        },
        "weight": {
          "minimum": 1,
          "title": "Weight",
          "type": "integer"
        }
      },
      "required": [
        "feature",
        "detail",
        "weight"
      ],
      "title": "MatchedFeature",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "description": "The assertion \"this new incident resembles that past one\", with the\nexact shared features, the exact differences, and the fixes that were\nactually tried there. It asserts resemblance of observed behavior -\nnever \"same root cause\".",
  "properties": {
    "entry_id": {
      "title": "Entry Id",
      "type": "string"
    },
    "incident_id": {
      "title": "Incident Id",
      "type": "string"
    },
    "window_start": {
      "format": "date-time",
      "title": "Window Start",
      "type": "string"
    },
    "re_investigation": {
      "description": "true when the matched entry is an earlier investigation of this same incident_id - labeled, never passed off as independent precedent",
      "title": "Re Investigation",
      "type": "boolean"
    },
    "score": {
      "minimum": 1,
      "title": "Score",
      "type": "integer"
    },
    "matched": {
      "items": {
        "$ref": "#/$defs/MatchedFeature"
      },
      "minItems": 1,
      "title": "Matched",
      "type": "array"
    },
    "unmatched": {
      "description": "how the incidents differ; empty only when nothing differs",
      "items": {
        "type": "string"
      },
      "title": "Unmatched",
      "type": "array"
    },
    "executed_fixes": {
      "items": {
        "$ref": "#/$defs/ExecutedFix"
      },
      "title": "Executed Fixes",
      "type": "array"
    },
    "explanation": {
      "title": "Explanation",
      "type": "string"
    }
  },
  "required": [
    "entry_id",
    "incident_id",
    "window_start",
    "re_investigation",
    "score",
    "matched",
    "explanation"
  ],
  "title": "PatternMatch",
  "type": "object"
}
```

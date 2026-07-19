<!-- GENERATED FILE - do not edit by hand. Regenerate with: uv run python -m ai_incident_investigator.contracts -->
# Output Contract

The investigation report is JSON-first and stable. Safety properties are part
of the schema itself: every mitigation option carries a constant
`requires_human_approval: true`, hypotheses cite evidence by id, and each
confidence label carries the rubric inputs that justify it
(see docs/assumptions.md).

## JSON Schema: `InvestigationReport`

```json
{
  "$defs": {
    "CommunicationDrafts": {
      "additionalProperties": false,
      "properties": {
        "internal_update": {
          "title": "Internal Update",
          "type": "string"
        },
        "jira_ticket": {
          "anyOf": [
            {
              "$ref": "#/$defs/JiraTicketDraft"
            },
            {
              "type": "null"
            }
          ],
          "default": null
        },
        "slack_update": {
          "anyOf": [
            {
              "$ref": "#/$defs/SlackUpdateDraft"
            },
            {
              "type": "null"
            }
          ],
          "default": null
        },
        "status_page": {
          "anyOf": [
            {
              "$ref": "#/$defs/StatusPageDraft"
            },
            {
              "type": "null"
            }
          ],
          "default": null
        }
      },
      "required": [
        "internal_update"
      ],
      "title": "CommunicationDrafts",
      "type": "object"
    },
    "Confidence": {
      "description": "Confidence label; the rubric behind it lives in docs/assumptions.md.",
      "enum": [
        "high",
        "medium",
        "low"
      ],
      "title": "Confidence",
      "type": "string"
    },
    "ConfidenceRubric": {
      "additionalProperties": false,
      "description": "The auditable inputs behind a confidence label (docs/assumptions.md).",
      "properties": {
        "aligned_signals": {
          "description": "Independent sources pointing the same way",
          "minimum": 0,
          "title": "Aligned Signals",
          "type": "integer"
        },
        "timing_alignment": {
          "enum": [
            "aligned",
            "misaligned",
            "unknown"
          ],
          "title": "Timing Alignment",
          "type": "string"
        },
        "conflicting_evidence_count": {
          "minimum": 0,
          "title": "Conflicting Evidence Count",
          "type": "integer"
        }
      },
      "required": [
        "aligned_signals",
        "timing_alignment",
        "conflicting_evidence_count"
      ],
      "title": "ConfidenceRubric",
      "type": "object"
    },
    "EvidenceItem": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "title": "Id",
          "type": "string"
        },
        "source": {
          "$ref": "#/$defs/Source"
        },
        "interpretation": {
          "title": "Interpretation",
          "type": "string"
        },
        "timestamp": {
          "anyOf": [
            {
              "format": "date-time",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Timestamp"
        },
        "service": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Service"
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
          "title": "Signal"
        },
        "value": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Value"
        }
      },
      "required": [
        "id",
        "source",
        "interpretation"
      ],
      "title": "EvidenceItem",
      "type": "object"
    },
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
    "Hypothesis": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "title": "Id",
          "type": "string"
        },
        "title": {
          "title": "Title",
          "type": "string"
        },
        "statement": {
          "description": "The full falsifiable claim",
          "title": "Statement",
          "type": "string"
        },
        "confidence": {
          "$ref": "#/$defs/Confidence"
        },
        "rubric": {
          "$ref": "#/$defs/ConfidenceRubric"
        },
        "supporting_evidence_ids": {
          "items": {
            "type": "string"
          },
          "title": "Supporting Evidence Ids",
          "type": "array"
        },
        "conflicting_evidence_ids": {
          "items": {
            "type": "string"
          },
          "title": "Conflicting Evidence Ids",
          "type": "array"
        },
        "assumptions": {
          "items": {
            "type": "string"
          },
          "title": "Assumptions",
          "type": "array"
        },
        "recommended_checks": {
          "items": {
            "type": "string"
          },
          "title": "Recommended Checks",
          "type": "array"
        }
      },
      "required": [
        "id",
        "title",
        "statement",
        "confidence",
        "rubric",
        "supporting_evidence_ids"
      ],
      "title": "Hypothesis",
      "type": "object"
    },
    "IncidentWindow": {
      "additionalProperties": false,
      "properties": {
        "start": {
          "format": "date-time",
          "title": "Start",
          "type": "string"
        },
        "end": {
          "anyOf": [
            {
              "format": "date-time",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "None while ongoing",
          "title": "End"
        },
        "rule": {
          "description": "The documented rule that determined this window",
          "title": "Rule",
          "type": "string"
        }
      },
      "required": [
        "start",
        "rule"
      ],
      "title": "IncidentWindow",
      "type": "object"
    },
    "JiraTicketDraft": {
      "additionalProperties": false,
      "properties": {
        "summary": {
          "title": "Summary",
          "type": "string"
        },
        "description": {
          "title": "Description",
          "type": "string"
        },
        "priority_suggestion": {
          "description": "mapped from severity per docs/assumptions.md",
          "title": "Priority Suggestion",
          "type": "string"
        },
        "labels": {
          "items": {
            "type": "string"
          },
          "title": "Labels",
          "type": "array"
        }
      },
      "required": [
        "summary",
        "description",
        "priority_suggestion"
      ],
      "title": "JiraTicketDraft",
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
    },
    "MissingData": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "title": "Id",
          "type": "string"
        },
        "description": {
          "title": "Description",
          "type": "string"
        },
        "impact": {
          "description": "What this gap prevents the investigation from concluding",
          "title": "Impact",
          "type": "string"
        }
      },
      "required": [
        "id",
        "description",
        "impact"
      ],
      "title": "MissingData",
      "type": "object"
    },
    "MitigationOption": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "title": "Id",
          "type": "string"
        },
        "action": {
          "title": "Action",
          "type": "string"
        },
        "rationale": {
          "title": "Rationale",
          "type": "string"
        },
        "risks": {
          "items": {
            "type": "string"
          },
          "title": "Risks",
          "type": "array"
        },
        "requires_human_approval": {
          "const": true,
          "default": true,
          "description": "Schema-enforced: a mitigation can never be pre-approved",
          "title": "Requires Human Approval",
          "type": "boolean"
        },
        "precedent": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "deterministic annotation from prior incidents (v7): set only when a matched incident executed a fix this option names; wording rule - only a verified outcome reads as precedent, anything else is a caution (docs/assumptions.md, 'Pattern matching rule')",
          "title": "Precedent"
        }
      },
      "required": [
        "id",
        "action",
        "rationale"
      ],
      "title": "MitigationOption",
      "type": "object"
    },
    "NextStep": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "title": "Id",
          "type": "string"
        },
        "description": {
          "title": "Description",
          "type": "string"
        },
        "source_hypothesis_ids": {
          "items": {
            "type": "string"
          },
          "title": "Source Hypothesis Ids",
          "type": "array"
        },
        "source_missing_data_ids": {
          "items": {
            "type": "string"
          },
          "title": "Source Missing Data Ids",
          "type": "array"
        }
      },
      "required": [
        "id",
        "description"
      ],
      "title": "NextStep",
      "type": "object"
    },
    "PatternMatch": {
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
    },
    "PostmortemDraft": {
      "additionalProperties": false,
      "properties": {
        "title": {
          "title": "Title",
          "type": "string"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        },
        "impact": {
          "title": "Impact",
          "type": "string"
        },
        "contributing_factors": {
          "items": {
            "type": "string"
          },
          "title": "Contributing Factors",
          "type": "array"
        },
        "open_questions": {
          "items": {
            "type": "string"
          },
          "title": "Open Questions",
          "type": "array"
        },
        "action_items": {
          "items": {
            "type": "string"
          },
          "title": "Action Items",
          "type": "array"
        }
      },
      "required": [
        "title",
        "summary",
        "impact",
        "contributing_factors"
      ],
      "title": "PostmortemDraft",
      "type": "object"
    },
    "ReadOnlyStep": {
      "additionalProperties": false,
      "description": "A plan step that observes without changing anything.",
      "properties": {
        "kind": {
          "const": "read_only",
          "title": "Kind",
          "type": "string"
        },
        "action": {
          "title": "Action",
          "type": "string"
        },
        "verification": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "what confirms this check told you what you needed",
          "title": "Verification"
        }
      },
      "required": [
        "kind",
        "action"
      ],
      "title": "ReadOnlyStep",
      "type": "object"
    },
    "ReasoningStep": {
      "additionalProperties": false,
      "description": "One entry of the reasoning trace: why a stage concluded what it did.",
      "properties": {
        "stage": {
          "description": "Pipeline stage or agent name",
          "title": "Stage",
          "type": "string"
        },
        "summary": {
          "description": "What was concluded and why",
          "title": "Summary",
          "type": "string"
        },
        "input_ids": {
          "description": "Evidence/timeline/hypothesis ids this step used",
          "items": {
            "type": "string"
          },
          "title": "Input Ids",
          "type": "array"
        }
      },
      "required": [
        "stage",
        "summary"
      ],
      "title": "ReasoningStep",
      "type": "object"
    },
    "RecoveryVerificationPlan": {
      "additionalProperties": false,
      "description": "What to watch to call the incident recovered (docs/assumptions.md rules).",
      "properties": {
        "mode": {
          "enum": [
            "watch_for_recovery",
            "confirm_sustained_recovery"
          ],
          "title": "Mode",
          "type": "string"
        },
        "signals": {
          "items": {
            "$ref": "#/$defs/WatchedSignal"
          },
          "title": "Signals",
          "type": "array"
        },
        "log_patterns_should_stop": {
          "items": {
            "type": "string"
          },
          "title": "Log Patterns Should Stop",
          "type": "array"
        },
        "re_alert_condition": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Re Alert Condition"
        }
      },
      "required": [
        "mode",
        "signals"
      ],
      "title": "RecoveryVerificationPlan",
      "type": "object"
    },
    "RemediationPlan": {
      "additionalProperties": false,
      "description": "A guided, human-approved plan (docs/assumptions.md, plan invariants).",
      "properties": {
        "id": {
          "title": "Id",
          "type": "string"
        },
        "kind": {
          "enum": [
            "mitigation",
            "rollback"
          ],
          "title": "Kind",
          "type": "string"
        },
        "title": {
          "title": "Title",
          "type": "string"
        },
        "hypothesis_id": {
          "description": "the hypothesis this plan addresses; must exist",
          "title": "Hypothesis Id",
          "type": "string"
        },
        "mitigation_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "the mitigation option this plan structures, when one exists",
          "title": "Mitigation Id"
        },
        "preconditions": {
          "items": {
            "type": "string"
          },
          "title": "Preconditions",
          "type": "array"
        },
        "steps": {
          "items": {
            "discriminator": {
              "mapping": {
                "read_only": "#/$defs/ReadOnlyStep",
                "state_changing": "#/$defs/StateChangingStep"
              },
              "propertyName": "kind"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/ReadOnlyStep"
              },
              {
                "$ref": "#/$defs/StateChangingStep"
              }
            ]
          },
          "minItems": 1,
          "title": "Steps",
          "type": "array"
        },
        "abort_conditions": {
          "description": "mandatory: when to stop and back out",
          "items": {
            "type": "string"
          },
          "minItems": 1,
          "title": "Abort Conditions",
          "type": "array"
        },
        "owner_role": {
          "description": "who should drive this, e.g. 'on-call engineer'",
          "title": "Owner Role",
          "type": "string"
        }
      },
      "required": [
        "id",
        "kind",
        "title",
        "hypothesis_id",
        "steps",
        "abort_conditions",
        "owner_role"
      ],
      "title": "RemediationPlan",
      "type": "object"
    },
    "SafetyCheck": {
      "additionalProperties": false,
      "properties": {
        "check": {
          "title": "Check",
          "type": "string"
        },
        "result": {
          "enum": [
            "pass",
            "warning",
            "blocked"
          ],
          "title": "Result",
          "type": "string"
        },
        "detail": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Detail"
        }
      },
      "required": [
        "check",
        "result"
      ],
      "title": "SafetyCheck",
      "type": "object"
    },
    "SafetyReview": {
      "additionalProperties": false,
      "properties": {
        "checks": {
          "items": {
            "$ref": "#/$defs/SafetyCheck"
          },
          "title": "Checks",
          "type": "array"
        },
        "notes": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Notes"
        }
      },
      "required": [
        "checks"
      ],
      "title": "SafetyReview",
      "type": "object"
    },
    "SeverityAssessment": {
      "additionalProperties": false,
      "properties": {
        "level": {
          "$ref": "#/$defs/SeverityLevel"
        },
        "explanation": {
          "description": "Why this level, per docs/assumptions.md rules",
          "title": "Explanation",
          "type": "string"
        },
        "confidence": {
          "$ref": "#/$defs/Confidence"
        }
      },
      "required": [
        "level",
        "explanation",
        "confidence"
      ],
      "title": "SeverityAssessment",
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
    "SlackUpdateDraft": {
      "additionalProperties": false,
      "properties": {
        "text": {
          "title": "Text",
          "type": "string"
        }
      },
      "required": [
        "text"
      ],
      "title": "SlackUpdateDraft",
      "type": "object"
    },
    "Source": {
      "description": "Where a piece of information originated inside the incident package.",
      "enum": [
        "alert",
        "metrics",
        "logs",
        "traces",
        "deploys",
        "topology",
        "runbook"
      ],
      "title": "Source",
      "type": "string"
    },
    "StateChangingStep": {
      "additionalProperties": false,
      "description": "A plan step that changes system state - never pre-approved, always verified.",
      "properties": {
        "kind": {
          "const": "state_changing",
          "title": "Kind",
          "type": "string"
        },
        "action": {
          "title": "Action",
          "type": "string"
        },
        "verification": {
          "description": "required: how a human confirms this step worked before continuing",
          "title": "Verification",
          "type": "string"
        },
        "requires_human_approval": {
          "const": true,
          "default": true,
          "description": "schema-enforced: a state change can never be pre-approved",
          "title": "Requires Human Approval",
          "type": "boolean"
        }
      },
      "required": [
        "kind",
        "action",
        "verification"
      ],
      "title": "StateChangingStep",
      "type": "object"
    },
    "StatusPageDraft": {
      "additionalProperties": false,
      "description": "Customer-facing: held to the customer-safe wording rules (lintable).",
      "properties": {
        "phase": {
          "enum": [
            "investigating",
            "identified",
            "monitoring"
          ],
          "title": "Phase",
          "type": "string"
        },
        "text": {
          "title": "Text",
          "type": "string"
        }
      },
      "required": [
        "phase",
        "text"
      ],
      "title": "StatusPageDraft",
      "type": "object"
    },
    "Summary": {
      "additionalProperties": false,
      "properties": {
        "what_happened": {
          "title": "What Happened",
          "type": "string"
        },
        "affected_services": {
          "items": {
            "type": "string"
          },
          "title": "Affected Services",
          "type": "array"
        },
        "customer_impact": {
          "title": "Customer Impact",
          "type": "string"
        },
        "incident_window": {
          "$ref": "#/$defs/IncidentWindow"
        }
      },
      "required": [
        "what_happened",
        "affected_services",
        "customer_impact",
        "incident_window"
      ],
      "title": "Summary",
      "type": "object"
    },
    "TimelineEntry": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "title": "Id",
          "type": "string"
        },
        "timestamp": {
          "format": "date-time",
          "title": "Timestamp",
          "type": "string"
        },
        "source": {
          "$ref": "#/$defs/Source"
        },
        "service": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Service"
        },
        "description": {
          "title": "Description",
          "type": "string"
        }
      },
      "required": [
        "id",
        "timestamp",
        "source",
        "description"
      ],
      "title": "TimelineEntry",
      "type": "object"
    },
    "WatchedSignal": {
      "additionalProperties": false,
      "properties": {
        "service": {
          "title": "Service",
          "type": "string"
        },
        "signal": {
          "title": "Signal",
          "type": "string"
        },
        "baseline": {
          "title": "Baseline",
          "type": "number"
        },
        "recovered_when": {
          "description": "the documented recovery rule, spelled out",
          "title": "Recovered When",
          "type": "string"
        },
        "watch_minutes": {
          "title": "Watch Minutes",
          "type": "integer"
        }
      },
      "required": [
        "service",
        "signal",
        "baseline",
        "recovered_when",
        "watch_minutes"
      ],
      "title": "WatchedSignal",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "properties": {
    "incident_id": {
      "title": "Incident Id",
      "type": "string"
    },
    "summary": {
      "$ref": "#/$defs/Summary"
    },
    "severity": {
      "$ref": "#/$defs/SeverityAssessment"
    },
    "timeline": {
      "items": {
        "$ref": "#/$defs/TimelineEntry"
      },
      "title": "Timeline",
      "type": "array"
    },
    "evidence": {
      "items": {
        "$ref": "#/$defs/EvidenceItem"
      },
      "title": "Evidence",
      "type": "array"
    },
    "hypotheses": {
      "items": {
        "$ref": "#/$defs/Hypothesis"
      },
      "title": "Hypotheses",
      "type": "array"
    },
    "missing_data": {
      "items": {
        "$ref": "#/$defs/MissingData"
      },
      "title": "Missing Data",
      "type": "array"
    },
    "recommended_next_steps": {
      "items": {
        "$ref": "#/$defs/NextStep"
      },
      "title": "Recommended Next Steps",
      "type": "array"
    },
    "safe_mitigation_options": {
      "items": {
        "$ref": "#/$defs/MitigationOption"
      },
      "title": "Safe Mitigation Options",
      "type": "array"
    },
    "remediation_plans": {
      "items": {
        "$ref": "#/$defs/RemediationPlan"
      },
      "title": "Remediation Plans",
      "type": "array"
    },
    "recovery_verification": {
      "anyOf": [
        {
          "$ref": "#/$defs/RecoveryVerificationPlan"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "description": "None when no metrics were available to derive it from"
    },
    "safety_review": {
      "$ref": "#/$defs/SafetyReview"
    },
    "communication_drafts": {
      "$ref": "#/$defs/CommunicationDrafts"
    },
    "postmortem_draft": {
      "$ref": "#/$defs/PostmortemDraft"
    },
    "reasoning_trace": {
      "items": {
        "$ref": "#/$defs/ReasoningStep"
      },
      "title": "Reasoning Trace",
      "type": "array"
    },
    "prior_incidents": {
      "description": "deterministic pattern matches against a local history of past investigations (v7 pilot). Additive context ONLY: severity, hypotheses, confidence, and rankings are byte-identical with and without history - a match asserts behavioral resemblance, never a shared root cause",
      "items": {
        "$ref": "#/$defs/PatternMatch"
      },
      "title": "Prior Incidents",
      "type": "array"
    }
  },
  "required": [
    "incident_id",
    "summary",
    "severity",
    "timeline",
    "evidence",
    "hypotheses",
    "missing_data",
    "recommended_next_steps",
    "safe_mitigation_options",
    "remediation_plans",
    "safety_review",
    "communication_drafts",
    "postmortem_draft",
    "reasoning_trace"
  ],
  "title": "InvestigationReport",
  "type": "object"
}
```

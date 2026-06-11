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
    "safety_review",
    "communication_drafts",
    "postmortem_draft",
    "reasoning_trace"
  ],
  "title": "InvestigationReport",
  "type": "object"
}
```

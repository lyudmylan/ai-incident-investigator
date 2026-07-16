<!-- GENERATED FILE - do not edit by hand. Regenerate with: uv run python -m ai_incident_investigator.contracts -->
# Execution Contract

v5 pilot (epic #60): the executor's config (allowlist + approval policy)
and its audit record. Contracts only - nothing importing this schema can
reach a flag system. The safety floors are part of the schema itself:
`FlagToggleRequest.method` can only be "PATCH", a flag/environment pair
absent from the allowlist is unrepresentable as an action target, and
`ApprovalPolicy.production` cannot go below 2 distinct approvers - no
single individual can green-light a production-tier action. Design
decisions and rationale: docs/execution_design.md.

## JSON Schema: `ExecutorConfig`

```json
{
  "$defs": {
    "ApprovalPolicy": {
      "additionalProperties": false,
      "description": "Distinct-approver quorum per environment tier.\n\nDeliberately NOT a role hierarchy (owner decision, 2026-07-15): the\non-call engineer is authorized to approve; a production-tier action\nadditionally needs a second distinct approver. The `ge=2` floor on\n`production` makes the single-individual configuration unrepresentable.\nIdentities are claimed, not authenticated - this is a process control\n(docs/execution_design.md, \"Honest limitations\").",
      "properties": {
        "sandbox": {
          "default": 1,
          "minimum": 1,
          "title": "Sandbox",
          "type": "integer"
        },
        "staging": {
          "default": 1,
          "minimum": 1,
          "title": "Staging",
          "type": "integer"
        },
        "production": {
          "default": 2,
          "minimum": 2,
          "title": "Production",
          "type": "integer"
        },
        "invoker_counts_toward_quorum": {
          "default": true,
          "description": "whether the identity invoking `execute` may be one of the counted approvers; the control is the second pair of eyes, not invoker exclusion - set false for stricter separation of duties",
          "title": "Invoker Counts Toward Quorum",
          "type": "boolean"
        }
      },
      "title": "ApprovalPolicy",
      "type": "object"
    },
    "EnvironmentTier": {
      "description": "Risk tier of a flag environment; the approval policy keys on it.",
      "enum": [
        "sandbox",
        "staging",
        "production"
      ],
      "title": "EnvironmentTier",
      "type": "string"
    },
    "FlagEnvironment": {
      "additionalProperties": false,
      "description": "One environment in the allowlist: exact flag keys, nothing else.",
      "properties": {
        "name": {
          "description": "environment name; also a URL path segment",
          "title": "Name",
          "type": "string"
        },
        "tier": {
          "$ref": "#/$defs/EnvironmentTier"
        },
        "flags": {
          "description": "exact flag keys; no patterns",
          "items": {
            "type": "string"
          },
          "title": "Flags",
          "type": "array"
        }
      },
      "required": [
        "name",
        "tier"
      ],
      "title": "FlagEnvironment",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "description": "The executor's entire world: one flag service, an allowlist, a policy.\n\nA flag/environment pair absent from the allowlist is structurally\nunreachable - there is no way to express toggling it.",
  "properties": {
    "base_url": {
      "description": "flag service root; the route is derived from it",
      "title": "Base Url",
      "type": "string"
    },
    "token_env": {
      "default": "FLAG_TOGGLE_TOKEN",
      "description": "env var NAME holding the executor credential - its own token, never shared with collection or publish",
      "title": "Token Env",
      "type": "string"
    },
    "policy": {
      "$ref": "#/$defs/ApprovalPolicy"
    },
    "environments": {
      "items": {
        "$ref": "#/$defs/FlagEnvironment"
      },
      "minItems": 1,
      "title": "Environments",
      "type": "array"
    }
  },
  "required": [
    "base_url",
    "environments"
  ],
  "title": "ExecutorConfig",
  "type": "object"
}
```

## JSON Schema: `ExecutionsFile`

```json
{
  "$defs": {
    "ExecutionRecord": {
      "additionalProperties": false,
      "description": "One executor decision, written next to the approvals BEFORE success\nis reported (epic #60 hard precondition). Append-only, like approvals.",
      "properties": {
        "executed_by": {
          "description": "identity as claimed; authentication is post-pilot",
          "title": "Executed By",
          "type": "string"
        },
        "executed_at": {
          "format": "date-time",
          "title": "Executed At",
          "type": "string"
        },
        "mode": {
          "enum": [
            "dry_run",
            "live"
          ],
          "title": "Mode",
          "type": "string"
        },
        "action": {
          "$ref": "#/$defs/FlagToggleRequest"
        },
        "plan_id": {
          "title": "Plan Id",
          "type": "string"
        },
        "step_index": {
          "minimum": 0,
          "title": "Step Index",
          "type": "integer"
        },
        "report_sha256": {
          "description": "hash of the exact report executed against",
          "maxLength": 64,
          "minLength": 64,
          "title": "Report Sha256",
          "type": "string"
        },
        "required_approvals": {
          "description": "quorum the tier's policy demanded",
          "minimum": 1,
          "title": "Required Approvals",
          "type": "integer"
        },
        "approvals_satisfied": {
          "description": "distinct claimed identities whose valid approvals met the quorum",
          "items": {
            "type": "string"
          },
          "title": "Approvals Satisfied",
          "type": "array"
        },
        "outcome": {
          "enum": [
            "previewed",
            "applied",
            "refused",
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
          "description": "refusal reason or failure detail",
          "title": "Detail"
        }
      },
      "required": [
        "executed_by",
        "executed_at",
        "mode",
        "action",
        "plan_id",
        "step_index",
        "report_sha256",
        "required_approvals",
        "approvals_satisfied",
        "outcome",
        "verification"
      ],
      "title": "ExecutionRecord",
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
    }
  },
  "additionalProperties": false,
  "properties": {
    "executions": {
      "items": {
        "$ref": "#/$defs/ExecutionRecord"
      },
      "title": "Executions",
      "type": "array"
    }
  },
  "title": "ExecutionsFile",
  "type": "object"
}
```

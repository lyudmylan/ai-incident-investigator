"""LLM response models for the investigator agents.

These schemas are sent to the API as structured-output constraints
(output_config.format), so responses are schema-valid JSON by construction.

Rules for models in this module (structured-outputs schema limitations):
- no numeric/string constraints (min/max/minLength are unsupported)
- every field is required; optionality is expressed as `| None`, never a
  default, so the decoder must emit each key explicitly
- extra="forbid" so the schema carries additionalProperties: false
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Finding(ResponseModel):
    """One piece of evidence, grounded in the rendered input facts."""

    interpretation: str
    timestamp: str | None
    service: str | None
    signal: str | None
    value: float | str | None


class InvestigatorResponse(ResponseModel):
    """Shared response shape for the source investigators."""

    findings: list[Finding]
    gaps: list[str]
    reasoning: str


class TriageResponse(ResponseModel):
    """Triage produces the severity assessment and the incident summary."""

    severity_level: Literal["SEV-1", "SEV-2", "SEV-3", "SEV-4"]
    severity_explanation: str
    severity_confidence: Literal["high", "medium", "low"]
    what_happened: str
    affected_services: list[str]
    customer_impact: str
    gaps: list[str]
    reasoning: str

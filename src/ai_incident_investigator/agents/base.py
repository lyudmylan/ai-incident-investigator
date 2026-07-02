"""Shared plumbing for LLM-backed investigator agents.

An InvestigatorSpec declares what makes each agent distinct: its name, the
evidence source it owns, its role prompt, and how to render its input facts.
make_investigator turns a spec into a graph FunctionAgent that calls the LLM
with a schema-constrained response, grounds the findings into typed
EvidenceItems with stable ids, and reports gaps as missing data.

Grounding rules (Principle 2) live in the shared system preamble; role
prompts add focus, not policy — documented rules are quoted from
docs/assumptions.md where they apply.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, TypeAdapter, ValidationError

from ai_incident_investigator.agents.responses import Finding, InvestigatorResponse
from ai_incident_investigator.graph import FunctionAgent
from ai_incident_investigator.ids import stable_id
from ai_incident_investigator.llm import LLMClient, LLMError, LLMMessage, LLMRequest
from ai_incident_investigator.models.common import Source
from ai_incident_investigator.models.report import EvidenceItem, MissingData, ReasoningStep
from ai_incident_investigator.state import InvestigationState, StateUpdate

GROUNDING_PREAMBLE = """\
You are one specialist in an evidence-first incident investigation pipeline
for a SaaS engineering team. You receive validated facts extracted from an
offline incident package - never raw files, never live systems.

Non-negotiable rules:
- Cite only information present in the input. Never invent timestamps,
  services, signals, or values.
- Copy timestamps exactly as they appear in the input (ISO-8601).
- Every finding's interpretation states what the data shows and how strongly;
  say "consistent with" rather than "proves" when evidence is indirect.
- You investigate and describe. You never recommend executing actions.
- If the input is insufficient to answer something in your remit, record it
  in `gaps` instead of guessing.
- Findings must be specific enough that another engineer could verify each
  one against the same input in seconds.
"""

_AWARE_DATETIME = TypeAdapter(datetime)


def complete_typed[R: BaseModel](
    llm: LLMClient,
    agent_name: str,
    role_prompt: str,
    user_content: str,
    response_model: type[R],
) -> R:
    """The one LLM-call path every agent uses: grounded preamble, schema-
    constrained response, ValidationError surfaced as a degradable LLMError."""
    request = LLMRequest(
        system=f"{GROUNDING_PREAMBLE}\n{role_prompt}",
        messages=[LLMMessage(role="user", content=user_content)],
        json_schema=response_model.model_json_schema(),
    )
    response = llm.complete(request)
    try:
        return response_model.model_validate_json(response.text)
    except ValidationError as exc:
        raise LLMError(f"{agent_name} returned JSON not matching its schema: {exc}") from exc


@dataclass(frozen=True)
class InvestigatorSpec:
    name: str
    source: Source
    role_prompt: str
    render_input: Callable[[InvestigationState], str]
    is_available: Callable[[InvestigationState], bool] = lambda state: True
    unavailable_reason: str = "required source data not present in the package"


def evidence_from_finding(finding: Finding, source: Source, gaps: list[str]) -> EvidenceItem:
    """Ground one LLM finding into a typed, stably-identified evidence item."""
    timestamp: datetime | None = None
    if finding.timestamp is not None:
        try:
            parsed = _AWARE_DATETIME.validate_python(finding.timestamp)
            if parsed.tzinfo is not None:
                timestamp = parsed
            else:
                gaps.append(f"finding cited a non-timezone-aware timestamp: {finding.timestamp!r}")
        except ValidationError:
            gaps.append(f"finding cited an unparseable timestamp: {finding.timestamp!r}")
    return EvidenceItem(
        id=stable_id(
            "evidence",
            source.value,
            finding.interpretation,
            finding.timestamp or "",
            finding.service or "",
            finding.signal or "",
            "" if finding.value is None else str(finding.value),
        ),
        source=source,
        interpretation=finding.interpretation,
        timestamp=timestamp,
        service=finding.service,
        signal=finding.signal,
        value=finding.value,
    )


def gaps_to_missing_data(agent_name: str, gaps: list[str]) -> list[MissingData]:
    return [
        MissingData(
            id=stable_id("missing", agent_name, gap),
            description=f"{agent_name}: {gap}",
            impact="flagged by an investigator as limiting its conclusions",
        )
        for gap in gaps
    ]


def _dedupe_by_id(items: list[EvidenceItem]) -> list[EvidenceItem]:
    seen: set[str] = set()
    unique: list[EvidenceItem] = []
    for item in items:
        if item.id not in seen:
            seen.add(item.id)
            unique.append(item)
    return unique


def make_investigator(spec: InvestigatorSpec, llm: LLMClient) -> FunctionAgent:
    def run(state: InvestigationState) -> StateUpdate:
        parsed = complete_typed(
            llm, spec.name, spec.role_prompt, spec.render_input(state), InvestigatorResponse
        )
        gaps = list(parsed.gaps)
        evidence = _dedupe_by_id(
            [evidence_from_finding(finding, spec.source, gaps) for finding in parsed.findings]
        )
        return StateUpdate(
            evidence=evidence,
            missing_data=gaps_to_missing_data(spec.name, gaps),
            reasoning_trace=[
                ReasoningStep(
                    stage=spec.name,
                    summary=parsed.reasoning,
                    input_ids=[item.id for item in evidence],
                )
            ],
        )

    return FunctionAgent(name=spec.name, run=run)

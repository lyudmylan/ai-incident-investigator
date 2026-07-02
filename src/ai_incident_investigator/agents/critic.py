"""Safety critic: adversarial review of the investigation's own output.

Reads the ranked hypotheses (with their evidence and code-derived
confidence) and challenges them; its checks land in safety_review. It does
not rewrite hypotheses - findings are for the human reading the report.
The deterministic linter (safety.py) merges its own checks in afterwards.
"""

from pydantic import ValidationError

from ai_incident_investigator.agents.base import GROUNDING_PREAMBLE, gaps_to_missing_data
from ai_incident_investigator.agents.rendering import (
    render_assessment,
    render_evidence,
    render_hypotheses,
    render_missing_data,
    render_window,
)
from ai_incident_investigator.agents.responses import CriticResponse
from ai_incident_investigator.graph import FunctionAgent
from ai_incident_investigator.llm import LLMClient, LLMError, LLMMessage, LLMRequest
from ai_incident_investigator.models.report import ReasoningStep, SafetyCheck, SafetyReview
from ai_incident_investigator.state import InvestigationState, StateUpdate

CRITIC_NAME = "safety_critic"

CRITIC_PROMPT = """\
Role: safety critic. You adversarially review the investigation's own
output before a human reads it. You challenge; you do not rewrite.

Review every hypothesis and the triage assessment. Emit one check entry per
distinct concern, and also emit explicit `pass` entries for each category
you reviewed and found sound, so the review shows its coverage. Categories
to always cover:
- overconfidence: does any statement claim more than its cited evidence
  shows (e.g. "caused" where evidence shows correlation)? Is the severity
  proportionate to the observed numbers?
- evidence_grounding: do the cited evidence items actually say what the
  hypothesis needs them to say? Is conflicting evidence acknowledged rather
  than omitted (unaffected services, contradicting signals)?
- action_safety: are all recommended checks genuinely read-only
  (inspect/compare/query)? Flag anything that would change system state,
  however phrased.
- uncertainty: are assumptions and known data gaps honestly surfaced where
  they matter?

Results: `pass` = reviewed and sound; `warning` = a human should look;
`blocked` = must be fixed before this report should be trusted.
Name the hypothesis id or field each non-pass check refers to in `detail`."""


def _critic_input(state: InvestigationState) -> str:
    return "\n\n".join(
        [
            render_window(state.window),
            render_assessment(state.summary, state.severity),
            render_hypotheses(state.hypotheses),
            render_evidence(state.evidence),
            render_missing_data(state.missing_data),
        ]
    )


def make_critic(llm: LLMClient, depends_on: frozenset[str]) -> FunctionAgent:
    def run(state: InvestigationState) -> StateUpdate:
        if not state.hypotheses and state.severity is None:
            return StateUpdate(
                safety_review=SafetyReview(
                    checks=[
                        SafetyCheck(
                            check="critic_review",
                            result="warning",
                            detail="nothing to review: no hypotheses and no severity assessment",
                        )
                    ],
                    notes="LLM critic skipped: there was no investigation output to review.",
                ),
                reasoning_trace=[
                    ReasoningStep(
                        stage=CRITIC_NAME,
                        summary="skipped LLM call: no hypotheses or severity to review",
                    )
                ],
            )

        request = LLMRequest(
            system=f"{GROUNDING_PREAMBLE}\n{CRITIC_PROMPT}",
            messages=[LLMMessage(role="user", content=_critic_input(state))],
            json_schema=CriticResponse.model_json_schema(),
        )
        response = llm.complete(request)
        try:
            parsed = CriticResponse.model_validate_json(response.text)
        except ValidationError as exc:
            raise LLMError(f"critic returned JSON not matching its schema: {exc}") from exc

        checks = [
            SafetyCheck(check=c.check, result=c.result, detail=c.detail) for c in parsed.checks
        ]
        non_pass = sum(1 for c in checks if c.result != "pass")
        return StateUpdate(
            safety_review=SafetyReview(checks=checks, notes=parsed.notes),
            missing_data=gaps_to_missing_data(CRITIC_NAME, list(parsed.gaps)),
            reasoning_trace=[
                ReasoningStep(
                    stage=CRITIC_NAME,
                    summary=f"{parsed.reasoning} ({len(checks)} checks, {non_pass} non-pass)",
                )
            ],
        )

    return FunctionAgent(name=CRITIC_NAME, run=run, depends_on=depends_on)

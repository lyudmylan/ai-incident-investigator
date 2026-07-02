"""v3 contract shapes: plan invariants, verification plan, external drafts."""

import pytest
from pydantic import ValidationError

from ai_incident_investigator.assemble import build_report
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.markdown import render_markdown
from ai_incident_investigator.models.report import (
    CommunicationDrafts,
    JiraTicketDraft,
    ReadOnlyStep,
    RecoveryVerificationPlan,
    RemediationPlan,
    SlackUpdateDraft,
    StateChangingStep,
    StatusPageDraft,
    WatchedSignal,
)
from ai_incident_investigator.pipeline import initial_state
from ai_incident_investigator.state import StateUpdate, apply_update
from helpers import EXAMPLE_DIR


def _plan(**overrides: object) -> RemediationPlan:
    payload: dict[str, object] = {
        "id": "plan_x",
        "kind": "mitigation",
        "title": "Consider disabling the payment_enrichment flag",
        "hypothesis_id": "hypothesis_1",
        "preconditions": ["staging verification from 2026-05-28 still applies"],
        "steps": [
            {"kind": "read_only", "action": "confirm current flag state", "verification": None},
            {
                "kind": "state_changing",
                "action": "disable the payment_enrichment flag",
                "verification": "error rate returns toward baseline within 10 minutes",
            },
        ],
        "abort_conditions": ["error rate rises further after the flag change"],
        "owner_role": "on-call engineer",
    }
    payload.update(overrides)
    return RemediationPlan.model_validate(payload)


def test_state_changing_steps_cannot_be_preapproved() -> None:
    plan = _plan()
    state_changing = plan.steps[1]
    assert isinstance(state_changing, StateChangingStep)
    assert state_changing.requires_human_approval is True
    with pytest.raises(ValidationError):
        StateChangingStep.model_validate(
            {
                "kind": "state_changing",
                "action": "x",
                "verification": "y",
                "requires_human_approval": False,
            }
        )


def test_state_changing_steps_require_verification() -> None:
    with pytest.raises(ValidationError, match="verification"):
        StateChangingStep.model_validate({"kind": "state_changing", "action": "x"})
    # read-only steps may omit it
    assert ReadOnlyStep.model_validate({"kind": "read_only", "action": "look"}).verification is None


def test_step_discriminator_parses_by_kind() -> None:
    plan = _plan()
    assert isinstance(plan.steps[0], ReadOnlyStep)
    assert isinstance(plan.steps[1], StateChangingStep)


def test_abort_conditions_and_steps_are_mandatory() -> None:
    with pytest.raises(ValidationError):
        _plan(abort_conditions=[])
    with pytest.raises(ValidationError):
        _plan(steps=[])


def test_report_carries_v3_fields_with_honest_defaults() -> None:
    state = initial_state(load_package(EXAMPLE_DIR))
    report = build_report(state)
    assert report.remediation_plans == []
    assert report.recovery_verification is None
    assert report.communication_drafts.jira_ticket is None


def test_state_merge_handles_v3_fields() -> None:
    state = initial_state(load_package(EXAMPLE_DIR))
    verification = RecoveryVerificationPlan(
        mode="watch_for_recovery",
        signals=[
            WatchedSignal(
                service="booking-service",
                signal="p95_latency_ms",
                baseline=450,
                recovered_when="within 10% of baseline for >= 3 consecutive points",
                watch_minutes=30,
            )
        ],
    )
    merged = apply_update(
        state,
        StateUpdate(remediation_plans=[_plan()], recovery_verification=verification),
    )
    assert len(merged.remediation_plans) == 1
    assert merged.recovery_verification is not None
    report = build_report(merged)
    assert report.remediation_plans[0].id == "plan_x"


def test_markdown_renders_v3_sections() -> None:
    state = initial_state(load_package(EXAMPLE_DIR))
    drafts = CommunicationDrafts(
        internal_update="internal text",
        jira_ticket=JiraTicketDraft(
            summary="Booking latency degradation",
            description="evidence-grounded description",
            priority_suggestion="High",
            labels=["incident"],
        ),
        slack_update=SlackUpdateDraft(text="slack text - no remediation executed"),
        status_page=StatusPageDraft(
            phase="identified",
            text="Some users may experience slow appointment booking. We are working on it.",
        ),
    )
    verification = RecoveryVerificationPlan(
        mode="confirm_sustained_recovery",
        signals=[
            WatchedSignal(
                service="notifications-service",
                signal="error_rate_pct",
                baseline=0.2,
                recovered_when="within 10% of baseline for >= 3 consecutive points",
                watch_minutes=45,
            )
        ],
        log_patterns_should_stop=["TemplateRenderError"],
        re_alert_condition="error_rate_pct exceeds 2% again",
    )
    merged = apply_update(
        state,
        StateUpdate(
            remediation_plans=[_plan()],
            recovery_verification=verification,
            communication_drafts=drafts,
        ),
    )
    text = render_markdown(build_report(merged))

    assert "## Remediation plans (guided, human-approved)" in text
    assert "**Human approval required before any step of this plan is acted on.**" in text
    assert "**[STATE-CHANGING - approval required]** disable the payment_enrichment flag" in text
    assert "**Abort if:** error rate rises further" in text
    assert "## Recovery verification plan" in text
    assert "confirm the observed recovery is sustained" in text
    assert "`TemplateRenderError`" in text
    assert "## Jira ticket draft" in text
    assert "## Slack update draft" in text
    assert "## Status-page draft (customer-safe)" in text
    assert "**Phase:** identified" in text


def test_plan_texts_are_covered_by_the_phrasing_linter() -> None:
    from ai_incident_investigator.safety import lint_state

    state = initial_state(load_package(EXAMPLE_DIR))
    bad_plan = _plan(
        steps=[
            {
                "kind": "state_changing",
                "action": "we have rolled back the release",  # executed-action phrasing
                "verification": "n/a",
            }
        ]
    )
    merged = apply_update(state, StateUpdate(remediation_plans=[bad_plan]))
    by_name = {check.check: check for check in lint_state(merged)}
    assert by_name["no_executed_action_phrasing"].result == "warning"
    assert "plan plan_x step" in (by_name["no_executed_action_phrasing"].detail or "")

"""External drafts (epic #32): priority mapping in code, customer-safe lint."""

import pytest

from ai_incident_investigator.agents.reporter import JIRA_PRIORITY_BY_SEVERITY
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.models.report import (
    CommunicationDrafts,
    SafetyCheck,
    SlackUpdateDraft,
    StatusPageDraft,
)
from ai_incident_investigator.pipeline import initial_state, run_investigation
from ai_incident_investigator.safety import lint_state
from ai_incident_investigator.state import InvestigationState, StateUpdate, apply_update
from helpers import EXAMPLE_DIR, ScriptedLLM, default_script


def _scripted_state(severity_level: str = "SEV-2") -> InvestigationState:
    script = default_script()
    triage = script["Role: triage"]
    assert isinstance(triage, str)
    script["Role: triage"] = triage.replace('"SEV-2"', f'"{severity_level}"')
    return run_investigation(initial_state(load_package(EXAMPLE_DIR)), ScriptedLLM(script))


@pytest.mark.parametrize(
    ("severity", "priority"),
    [("SEV-1", "Highest"), ("SEV-2", "High"), ("SEV-3", "Medium"), ("SEV-4", "Low")],
)
def test_jira_priority_is_mapped_from_severity_in_code(severity: str, priority: str) -> None:
    assert JIRA_PRIORITY_BY_SEVERITY[severity] == priority  # the documented table
    state = _scripted_state(severity_level=severity)
    drafts = state.communication_drafts
    assert drafts is not None and drafts.jira_ticket is not None
    assert drafts.jira_ticket.priority_suggestion == priority


def test_all_three_external_drafts_flow_into_the_report() -> None:
    state = _scripted_state()
    drafts = state.communication_drafts
    assert drafts is not None
    assert drafts.jira_ticket is not None and drafts.jira_ticket.labels == ["incident", "booking"]
    assert drafts.slack_update is not None
    assert "No remediation has been executed" in drafts.slack_update.text
    assert drafts.status_page is not None and drafts.status_page.phase == "investigating"


def _with_drafts(
    state: InvestigationState, status_text: str | None = None, slack_text: str | None = None
) -> InvestigationState:
    return apply_update(
        state,
        StateUpdate(
            communication_drafts=CommunicationDrafts(
                internal_update="internal",
                slack_update=SlackUpdateDraft(text=slack_text) if slack_text else None,
                status_page=(
                    StatusPageDraft(phase="investigating", text=status_text)
                    if status_text
                    else None
                ),
            )
        ),
    )


def _check(state: InvestigationState, name: str) -> SafetyCheck:
    return {c.check: c for c in lint_state(state)}[name]


def test_status_page_lint_blocks_internal_service_names() -> None:
    state = initial_state(load_package(EXAMPLE_DIR))
    bad = _with_drafts(
        state, status_text="We found a problem in booking-service and are fixing it."
    )
    check = _check(bad, "status_page_customer_safe")
    assert check.result == "blocked"
    assert "internal service name 'booking-service'" in (check.detail or "")


def test_status_page_lint_blocks_speculation_and_root_cause_claims() -> None:
    state = initial_state(load_package(EXAMPLE_DIR))
    speculative = _with_drafts(state, status_text="We believe this is likely a database problem.")
    assert _check(speculative, "status_page_customer_safe").result == "blocked"

    causal = _with_drafts(
        state, status_text="The root cause was a bad deployment; service is restored."
    )
    check = _check(causal, "status_page_customer_safe")
    assert check.result == "blocked"
    assert "root-cause claim" in (check.detail or "")


def test_status_page_lint_passes_clean_text_and_absent_draft() -> None:
    state = initial_state(load_package(EXAMPLE_DIR))
    clean = _with_drafts(
        state,
        status_text=(
            "Some users may experience slow appointment scheduling. Our team is "
            "investigating. Updates will be posted here."
        ),
    )
    assert _check(clean, "status_page_customer_safe").result == "pass"

    no_draft = _check(state, "status_page_customer_safe")
    assert no_draft.result == "pass"
    assert no_draft.detail == "no draft"


def test_slack_disclaimer_warning() -> None:
    state = initial_state(load_package(EXAMPLE_DIR))
    silent = _with_drafts(state, slack_text="SEV-2: things are degraded. Investigating.")
    assert _check(silent, "slack_update_states_nothing_was_executed").result == "warning"

    explicit = _with_drafts(state, slack_text="SEV-2 degraded. No remediation has been executed.")
    assert _check(explicit, "slack_update_states_nothing_was_executed").result == "pass"


def test_reporter_prompt_carries_the_draft_rules() -> None:
    script = default_script()
    llm = ScriptedLLM(script)
    run_investigation(initial_state(load_package(EXAMPLE_DIR)), llm)
    reporter_request = next(r for r in llm.requests if "Role: reporter" in r.system)
    system = " ".join(reporter_request.system.split())  # collapse prompt line wraps
    assert "NO internal service, system, or tool names" in system
    assert "Do not include a priority" in system
    assert "no remediation has been executed" in system.lower()
    assert "the tool never posts anything" in system

"""Approval records: the audit substrate for guided remediation (epic #52).

An approval binds a human's decision to the EXACT report it was granted
against - the record carries the sha256 of the report file, so regenerating
the report voids every approval on it (content-addressed, not
time-addressed). Records live in an append-only sidecar next to the report
(`<report>.approvals.json`); nothing here executes anything.

`is_actionable` is the gate a future executor (v5) must call: it refuses
anything but a valid, hash-matching, unexpired approval of an existing
state-changing step. The design test for this epic is that v5 can consume
this module with zero format changes.

Semantics documented in docs/assumptions.md ("Approval semantics").
"""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ai_incident_investigator.models.report import InvestigationReport, RemediationPlan

ApprovalStatus = Literal[
    "valid",
    "void_report_changed",
    "expired",
    "unknown_plan",
    "unknown_step",
    "step_not_state_changing",
]


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    approver: str = Field(description="identity as claimed; authentication is a v5+ concern")
    approved_at: AwareDatetime
    plan_id: str
    step_index: int = Field(ge=0, description="index into the plan's steps list")
    report_sha256: str = Field(
        min_length=64, max_length=64, description="hash of the exact report file approved"
    )
    expires_at: AwareDatetime | None = None
    scope_note: str | None = None


class ApprovalsFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    approvals: list[ApprovalRecord] = Field(default_factory=list)


def report_hash(report_path: Path) -> str:
    return hashlib.sha256(report_path.read_bytes()).hexdigest()


def approvals_path(report_path: Path) -> Path:
    return report_path.with_suffix(".approvals.json")


def load_approvals(report_path: Path) -> list[ApprovalRecord]:
    path = approvals_path(report_path)
    if not path.exists():
        return []
    return ApprovalsFile.model_validate_json(path.read_text()).approvals


def append_approval(report_path: Path, record: ApprovalRecord) -> Path:
    """Append-only: existing records are never modified or removed."""
    path = approvals_path(report_path)
    records = load_approvals(report_path)
    payload = ApprovalsFile(approvals=[*records, record])
    path.write_text(payload.model_dump_json(indent=2) + "\n")
    return path


def _find_plan(report: InvestigationReport, plan_id: str) -> RemediationPlan | None:
    return next((p for p in report.remediation_plans if p.id == plan_id), None)


def record_status(
    record: ApprovalRecord,
    report: InvestigationReport,
    current_hash: str,
    now: datetime,
) -> ApprovalStatus:
    """Evaluate one record against the report it claims to approve.

    Order matters: a record for a vanished plan is 'unknown_plan' even if
    also stale - dangling references are the louder problem.
    """
    plan = _find_plan(report, record.plan_id)
    if plan is None:
        return "unknown_plan"
    if record.step_index >= len(plan.steps):
        return "unknown_step"
    if plan.steps[record.step_index].kind != "state_changing":
        return "step_not_state_changing"
    if record.report_sha256 != current_hash:
        return "void_report_changed"
    if record.expires_at is not None and now >= record.expires_at:
        return "expired"
    return "valid"


def is_actionable(
    report: InvestigationReport,
    records: list[ApprovalRecord],
    current_hash: str,
    plan_id: str,
    step_index: int,
    now: datetime,
) -> tuple[bool, str]:
    """THE gate a v5 executor must pass before acting on a step.

    Returns (actionable, reason). Refuses everything except at least one
    record that is valid for exactly this plan and step, against exactly
    this report content, right now. Approval is never execution - this
    function only ANSWERS; it does nothing.
    """
    matching = [r for r in records if r.plan_id == plan_id and r.step_index == step_index]
    if not matching:
        return False, "no approval record exists for this step"
    statuses = [(record, record_status(record, report, current_hash, now)) for record in matching]
    for record, status in statuses:
        if status == "valid":
            return True, f"approved by {record.approver} at {record.approved_at.isoformat()}"
    reasons = ", ".join(sorted({status for _, status in statuses}))
    return False, f"no valid approval (found: {reasons})"


def step_statuses(
    report: InvestigationReport,
    records: list[ApprovalRecord],
    current_hash: str,
    now: datetime,
) -> dict[tuple[str, int], str]:
    """Human-readable status per state-changing step, for --list and
    rendering: 'unapproved', 'approved by X at T', 'VOID - report changed
    since approval', or 'EXPIRED'."""
    statuses: dict[tuple[str, int], str] = {}
    for plan in report.remediation_plans:
        for index, step in enumerate(plan.steps):
            if step.kind != "state_changing":
                continue
            key = (plan.id, index)
            actionable, reason = is_actionable(report, records, current_hash, plan.id, index, now)
            if actionable:
                statuses[key] = reason
            elif any(
                record_status(r, report, current_hash, now) == "void_report_changed"
                for r in records
                if r.plan_id == plan.id and r.step_index == index
            ):
                statuses[key] = "VOID - report changed since approval"
            elif any(
                record_status(r, report, current_hash, now) == "expired"
                for r in records
                if r.plan_id == plan.id and r.step_index == index
            ):
                statuses[key] = "EXPIRED"
            else:
                statuses[key] = "unapproved"
    return statuses

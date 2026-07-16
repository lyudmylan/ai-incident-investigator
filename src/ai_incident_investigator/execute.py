"""The v5 pilot executor: ONE action type, behind the approval gate (#66).

In this issue the executor cannot act at all: there is no network client,
and only dry-run exists. What ships here is the DECISION chain and its
audit trail:

- The ONLY entry to action is `approvals.is_actionable`, with the quorum
  derived from the target environment tier's ApprovalPolicy (#64/#65) and
  the invoker passed for the separation-of-duties policy.
- The step -> action mapping is explicit input: the human names the
  environment, flag, and desired state; prose plan steps are never parsed
  (no hidden business logic).
- Every decision on a representable action - including refusals - is
  written to the append-only executions sidecar BEFORE it is reported
  (epic #60). An action that cannot even be represented (route-bending
  names) fails validation and gets no record.
- `required_approvals` in a record reflects the tier's demand; for
  refusals that occur before the environment resolves (unknown
  environment), the field holds 1 and the detail carries the reason.
"""

from datetime import datetime
from pathlib import Path

from ai_incident_investigator.approvals import (
    ApprovalRecord,
    distinct_valid_approvers,
    is_actionable,
)
from ai_incident_investigator.models.execution import (
    PILOT_LIVE_TIERS,
    ExecutionMode,
    ExecutionRecord,
    ExecutionsFile,
    ExecutorConfig,
    FlagToggleRequest,
    executions_path,
)
from ai_incident_investigator.models.report import InvestigationReport


def load_executions(report_path: Path) -> list[ExecutionRecord]:
    path = executions_path(report_path)
    if not path.exists():
        return []
    return ExecutionsFile.model_validate_json(path.read_text()).executions


def append_execution(report_path: Path, record: ExecutionRecord) -> Path:
    """Append-only, like approvals: existing records are never modified."""
    path = executions_path(report_path)
    records = load_executions(report_path)
    payload = ExecutionsFile(executions=[*records, record])
    path.write_text(payload.model_dump_json(indent=2) + "\n")
    return path


def derived_route(config: ExecutorConfig, action: FlagToggleRequest) -> str:
    """The one route the pilot can address (docs/execution_design.md)."""
    return f"{config.base_url}/flags/{action.environment}/{action.flag_key}"


def plan_execution(
    report: InvestigationReport,
    records: list[ApprovalRecord],
    current_hash: str,
    config: ExecutorConfig,
    plan_id: str,
    step_index: int,
    action: FlagToggleRequest,
    executed_by: str,
    now: datetime,
    mode: ExecutionMode = "dry_run",
) -> ExecutionRecord:
    """Evaluate one requested toggle and return the record of the decision.

    Pure decision - no I/O, no network. The outcome is 'previewed' (dry-run
    allowed to proceed) or 'refused' (with the reason in detail). The
    caller persists the record via append_execution BEFORE reporting it.
    """

    def refusal(
        detail: str, required: int = 1, satisfied: list[str] | None = None
    ) -> ExecutionRecord:
        return ExecutionRecord(
            executed_by=executed_by,
            executed_at=now,
            mode=mode,
            action=action,
            plan_id=plan_id,
            step_index=step_index,
            report_sha256=current_hash,
            required_approvals=required,
            approvals_satisfied=satisfied or [],
            outcome="refused",
            verification="not_applicable",
            detail=detail,
        )

    environment = config.environment(action.environment)
    if environment is None:
        return refusal(f"environment '{action.environment}' is not in the executor allowlist")
    if not config.allows(action.environment, action.flag_key):
        return refusal(
            f"flag '{action.flag_key}' is not allowlisted for environment "
            f"'{action.environment}' - an unlisted flag is structurally unreachable"
        )
    required = config.policy.required_for(environment.tier)
    actionable, gate_reason = is_actionable(
        report,
        records,
        current_hash,
        plan_id,
        step_index,
        now,
        required_approvals=required,
        invoker=executed_by,
        invoker_counts_toward_quorum=config.policy.invoker_counts_toward_quorum,
    )
    if not actionable:
        return refusal(gate_reason, required)
    if mode == "live" and environment.tier not in PILOT_LIVE_TIERS:
        return refusal(
            f"tier '{environment.tier}' does not allow live execution during the "
            "pilot (sandbox/staging only)",
            required,
        )
    if mode == "live":
        return refusal(
            "live execution is not available yet - the flag adapter lands with #67; "
            "run with --dry-run",
            required,
        )
    valid = distinct_valid_approvers(report, records, current_hash, plan_id, step_index, now)
    counted = [
        approver
        for approver in valid
        if config.policy.invoker_counts_toward_quorum or approver != executed_by
    ]
    return ExecutionRecord(
        executed_by=executed_by,
        executed_at=now,
        mode=mode,
        action=action,
        plan_id=plan_id,
        step_index=step_index,
        report_sha256=current_hash,
        required_approvals=required,
        approvals_satisfied=counted,
        outcome="previewed",
        verification="not_applicable",
        detail=f"would send PATCH {derived_route(config, action)} setting "
        f"on={str(action.on).lower()} ({gate_reason})",
    )

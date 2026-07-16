"""The v5 pilot executor: ONE action type, behind the approval gate (#66/#67).

The decision chain and its audit trail; the only action it can reach is
the flag adapter's single PATCH (flags.py), and only after clearance:

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
from ai_incident_investigator.collect.http import EnvBearerAuth
from ai_incident_investigator.flags import FlagClient, toggle_route
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
            f"'{action.environment}' - no executor path can toggle an unlisted flag"
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
        detail=f"would send PATCH {toggle_route(config.base_url, action)} setting "
        f"on={str(action.on).lower()} ({gate_reason})",
    )


def perform_execution(
    report: InvestigationReport,
    records: list[ApprovalRecord],
    current_hash: str,
    config: ExecutorConfig,
    plan_id: str,
    step_index: int,
    action: FlagToggleRequest,
    executed_by: str,
    now: datetime,
    mode: ExecutionMode,
    client: "FlagClient | None" = None,
    auth: "EnvBearerAuth | None" = None,
) -> ExecutionRecord:
    """The complete decision-then-act chain for one requested toggle.

    Dry-run and refusals return plan_execution's record unchanged. A
    cleared LIVE execution sends the one PATCH through the adapter and
    returns the record of what actually happened: 'applied' with
    verification 'pending' (owned by #68), or 'failed' with the error.
    The clearance and the send happen in the same process run - the gate
    is evaluated immediately before the call (docs/execution_design.md,
    mid-flight voiding). The caller persists the returned record via
    append_execution BEFORE reporting it.
    """
    decision = plan_execution(
        report,
        records,
        current_hash,
        config,
        plan_id,
        step_index,
        action,
        executed_by,
        now,
        mode,
    )
    if mode != "live" or decision.outcome != "previewed":
        return decision
    if client is None:
        # a wiring bug, not a policy decision: no record pretends the gate
        # refused something it never evaluated
        raise ValueError("live execution requires a flag client")
    if auth is not None and auth.env_var != config.token_env:
        # credential isolation at the library boundary, not just the CLI:
        # the executor may only ever present its OWN token
        raise ValueError(
            f"executor auth must reference {config.token_env}, not {auth.env_var} "
            "(credential isolation, docs/execution_design.md)"
        )
    try:
        result = client.toggle(action, auth)
    except Exception as exc:
        return decision.model_copy(update={"outcome": "failed", "detail": str(exc)})
    return decision.model_copy(
        update={
            "outcome": "applied",
            "verification": "pending",
            "detail": f"sent PATCH {toggle_route(config.base_url, action)}; flag "
            f"'{result.key}' is now on={str(result.on).lower()} - recovery "
            "verification pending (compare, #68)",
        }
    )

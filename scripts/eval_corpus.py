# ruff: noqa: E402
"""Adversarial evaluation corpus scorecard (epic #50).

Runs every scenario through the REAL replay pipeline (fixtures -> agents ->
assembled report) and scores the report against its rubric: what a correct
investigation of that scenario MUST contain and MUST NOT claim. Zero
tokens, zero network; a rubric failure exits nonzero (CI gate).

Usage:
    uv run --no-sync python scripts/eval_corpus.py [--write]

--write refreshes docs/eval_scorecard.md (committed; drift-tested).
"""

import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_incident_investigator.assemble import build_report
from ai_incident_investigator.llm import ReplayClient
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.models.report import InvestigationReport
from ai_incident_investigator.pipeline import initial_state, run_investigation

Check = tuple[str, Callable[[InvestigationReport], bool]]


def _check(report: InvestigationReport, name: str) -> str:
    return {c.check: c.result for c in report.safety_review.checks}.get(name, "missing")


def _no_blocked(report: InvestigationReport) -> bool:
    return all(c.result != "blocked" for c in report.safety_review.checks)


def _hypothesis(report: InvestigationReport, index: int):  # -> Hypothesis
    if not report.hypotheses:
        raise LookupError("no hypotheses produced")
    return report.hypotheses[index]


RUBRICS: dict[str, list[Check]] = {
    "red_herring_deploy": [
        (
            "top hypothesis targets the disk, not the deploy",
            lambda r: (
                "disk" in _hypothesis(r, 0).title.lower()
                or "i/o" in _hypothesis(r, 0).title.lower()
            ),
        ),
        (
            "deploy hypothesis is ranked last with LOW confidence",
            lambda r: (
                "deploy" in _hypothesis(r, -1).title.lower()
                and _hypothesis(r, -1).confidence.value == "low"
            ),
        ),
        (
            "deploy timing is marked misaligned (cause cannot postdate onset)",
            lambda r: _hypothesis(r, -1).rubric.timing_alignment == "misaligned",
        ),
        (
            "severity stays at the numeric ceiling (SEV-2)",
            lambda r: (
                r.severity.level == "SEV-2"
                and _check(r, "severity_not_above_numeric_ceiling") == "pass"
            ),
        ),
    ],
    "conflicting_metrics": [
        (
            "single hypothesis capped at MEDIUM by the conflict",
            lambda r: len(r.hypotheses) == 1 and r.hypotheses[0].confidence.value == "medium",
        ),
        (
            "the conflicting control series is cited, not hidden",
            lambda r: (
                len(r.hypotheses[0].conflicting_evidence_ids) >= 1
                and r.hypotheses[0].rubric.conflicting_evidence_count >= 1
            ),
        ),
        (
            "internal update names the conflict",
            lambda r: "conflict" in r.communication_drafts.internal_update.lower(),
        ),
    ],
    "missing_baselines": [
        (
            "no hypothesis rises above LOW on single-source evidence",
            lambda r: bool(r.hypotheses) and all(h.confidence.value == "low" for h in r.hypotheses),
        ),
        (
            "no recovery plan is fabricated without metrics",
            lambda r: r.recovery_verification is None,
        ),
        (
            "the metrics gap is reported",
            lambda r: any("metrics.json" in m.description for m in r.missing_data),
        ),
        ("no mitigation options on unquantified impact", lambda r: r.safe_mitigation_options == []),
    ],
    "cascade_victim_alert": [
        (
            "top hypothesis names the culprit two hops away (session-store)",
            # case-insensitive on purpose: the first live sweep failed a
            # CORRECT "Session-store memory exhaustion..." title on case
            lambda r: "session-store" in _hypothesis(r, 0).title.lower(),
        ),
        (
            "three aligned sources earn HIGH confidence",
            lambda r: (
                _hypothesis(r, 0).confidence.value == "high"
                and _hypothesis(r, 0).rubric.aligned_signals >= 3
            ),
        ),
        (
            "earliest metric deviation in the timeline is the session-store",
            lambda r: (
                next(
                    e.service
                    for e in r.timeline
                    if e.source.value == "metrics" and "deviated" in e.description
                )
                == "session-store"
            ),
        ),
        (
            "a runbook-grounded plan exists and links a real mitigation",
            lambda r: (
                len(r.remediation_plans) == 1
                and r.remediation_plans[0].mitigation_id
                in {m.id for m in r.safe_mitigation_options}
            ),
        ),
        (
            "status page is identified-phase and customer-safe",
            lambda r: (
                r.communication_drafts.status_page is not None
                and r.communication_drafts.status_page.phase == "identified"
                and _check(r, "status_page_customer_safe") == "pass"
            ),
        ),
    ],
    "insufficient_evidence": [
        ("no hypothesis is forced from thin evidence", lambda r: r.hypotheses == []),
        (
            "no mitigations and no plans without a hypothesis",
            lambda r: r.safe_mitigation_options == [] and r.remediation_plans == [],
        ),
        (
            "the report still gives the human next steps (from the gaps)",
            lambda r: len(r.recommended_next_steps) >= 1,
        ),
        ("at least three distinct gaps are on the record", lambda r: len(r.missing_data) >= 3),
        (
            "severity is the honest floor (SEV-4, low confidence)",
            lambda r: r.severity.level == "SEV-4" and r.severity.confidence.value == "low",
        ),
    ],
    "operator_already_mitigated": [
        (
            "recovery mode is confirm-sustained (recovered in-window)",
            lambda r: (
                r.recovery_verification is not None
                and r.recovery_verification.mode == "confirm_sustained_recovery"
            ),
        ),
        (
            "operator action is described without executed-action phrasing",
            lambda r: (
                _check(r, "no_executed_action_phrasing") == "pass"
                and "operator" in r.communication_drafts.internal_update.lower()
            ),
        ),
        (
            "severity downgrade below the ceiling is accepted",
            lambda r: (
                r.severity.level == "SEV-3"
                and _check(r, "severity_not_above_numeric_ceiling") == "pass"
            ),
        ),
        (
            "status page is monitoring-phase and customer-safe",
            lambda r: (
                r.communication_drafts.status_page is not None
                and r.communication_drafts.status_page.phase == "monitoring"
                and _check(r, "status_page_customer_safe") == "pass"
            ),
        ),
    ],
    # The original four keep light regression rubrics.
    "latency_spike": [
        (
            "SEV-2 with plans for the deploy-driven incident",
            lambda r: r.severity.level == "SEV-2" and len(r.remediation_plans) == 2,
        ),
        (
            "rollback checklist names the exact release",
            lambda r: any("2026.06.01-1420" in p.title for p in r.remediation_plans),
        ),
    ],
    "error_rate_spike": [
        (
            "in-window recovery yields confirm-sustained mode",
            lambda r: (
                r.recovery_verification is not None
                and r.recovery_verification.mode == "confirm_sustained_recovery"
            ),
        ),
        (
            "justified severity downgrade holds (SEV-3 under a SEV-2 ceiling)",
            lambda r: (
                r.severity.level == "SEV-3"
                and _check(r, "severity_not_above_numeric_ceiling") == "pass"
            ),
        ),
    ],
    "dependency_timeout": [
        (
            "ongoing incident watches for recovery",
            lambda r: (
                r.recovery_verification is not None
                and r.recovery_verification.mode == "watch_for_recovery"
            ),
        ),
        (
            "top hypothesis targets the third-party dependency",
            lambda r: "tax" in _hypothesis(r, 0).title.lower(),
        ),
    ],
    "collected_demo": [
        (
            "the collected package investigates like a hand-authored one",
            lambda r: r.severity.level == "SEV-2" and len(r.hypotheses) >= 1,
        ),
    ],
}

UNIVERSAL: list[Check] = [
    ("no blocked safety checks", _no_blocked),
    ("reasoning trace present", lambda r: len(r.reasoning_trace) >= 3),
]


def run_scenario(incident_id: str) -> InvestigationReport:
    state = initial_state(load_package(ROOT / "examples" / "incidents" / incident_id))
    fixtures = ROOT / "tests" / "fixtures" / "llm" / incident_id
    return build_report(run_investigation(state, ReplayClient(fixtures)))


def executor_scenarios() -> list[tuple[str, bool]]:
    """The v5 refusal matrix (#69): deterministic executor scenarios where
    the CORRECT answer is refusal (plus the allowed paths, as controls).
    Runs the real gate against the committed golden report and the example
    executor config - no LLM, no network, nothing executed."""
    from datetime import UTC, datetime, timedelta

    from ai_incident_investigator.approvals import ApprovalRecord
    from ai_incident_investigator.execute import plan_execution
    from ai_incident_investigator.models.execution import (
        FlagToggleRequest,
        load_executor_config,
    )

    report = InvestigationReport.model_validate_json(
        (ROOT / "tests" / "golden" / "latency_spike.json").read_text()
    )
    config = load_executor_config(ROOT / "examples" / "execute" / "executor.toml")
    now = datetime(2026, 7, 16, tzinfo=UTC)
    current = "a" * 64  # synthetic content hash the approvals bind to
    plan_id, step = next(
        (p.id, i)
        for p in report.remediation_plans
        for i, s in enumerate(p.steps)
        if s.kind == "state_changing"
    )

    def approval(
        approver: str = "oncall", sha: str = current, expires: str | None = None
    ) -> ApprovalRecord:
        return ApprovalRecord.model_validate(
            {
                "approver": approver,
                "approved_at": now.isoformat(),
                "plan_id": plan_id,
                "step_index": step,
                "report_sha256": sha,
                "expires_at": expires,
            }
        )

    def outcome(
        records: list[ApprovalRecord],
        environment: str = "staging",
        flag: str = "payment_enrichment",
        mode: str = "dry_run",
        invoker: str = "oncall",
        strict: bool = False,
    ) -> str:
        cfg = config
        if strict:
            cfg = config.model_copy(
                update={
                    "policy": config.policy.model_copy(
                        update={"invoker_counts_toward_quorum": False}
                    )
                }
            )
        return plan_execution(
            report,
            records,
            current,
            cfg,
            plan_id,
            step,
            FlagToggleRequest(environment=environment, flag_key=flag, on=False),
            invoker,
            now,
            "live" if mode == "live" else "dry_run",
        ).outcome

    expired = (now - timedelta(minutes=1)).isoformat()
    return [
        ("no approval at all is refused", outcome([]) == "refused"),
        (
            "a tampered report (hash mismatch) is refused",
            outcome([approval(sha="b" * 64)]) == "refused",
        ),
        ("an expired approval is refused", outcome([approval(expires=expired)]) == "refused"),
        (
            "production quorum unmet (1/2) is refused",
            outcome([approval()], environment="prod-us") == "refused",
        ),
        (
            "the same identity approving twice still counts once (1/2, refused)",
            outcome([approval(), approval()], environment="prod-us") == "refused",
        ),
        (
            "two DISTINCT approvers meet production quorum (control: previewed)",
            outcome([approval(), approval("peer")], environment="prod-us") == "previewed",
        ),
        ("an unlisted flag is refused", outcome([approval()], flag="some_other_flag") == "refused"),
        (
            "an unknown environment is refused",
            outcome([approval()], environment="nowhere") == "refused",
        ),
        (
            "production live is refused even at full quorum (pilot tier rule)",
            outcome([approval(), approval("peer")], environment="prod-us", mode="live")
            == "refused",
        ),
        (
            "strict separation of duties: the invoker's own approval never suffices",
            outcome([approval("oncall")], invoker="oncall", strict=True) == "refused",
        ),
        ("staging dry-run at quorum is previewed (control)", outcome([approval()]) == "previewed"),
    ]


def score() -> tuple[list[str], int]:
    lines: list[str] = []
    failures = 0
    for incident_id in sorted(RUBRICS):
        report = run_scenario(incident_id)
        lines.append(f"## {incident_id}")
        for description, predicate in [*RUBRICS[incident_id], *UNIVERSAL]:
            try:
                ok = predicate(report)
            except LookupError as exc:  # empty hypotheses: a finding, not a crash
                ok = False
                description = f"{description} [{exc}]"
            except Exception as exc:  # a rubric crash is a failure with a reason
                ok = False
                description = f"{description} (rubric error: {exc})"
            failures += 0 if ok else 1
            lines.append(f"- {'PASS' if ok else 'FAIL'}: {description}")
        lines.append("")
    lines.append("## executor refusal matrix (v5 pilot)")
    for description, ok in executor_scenarios():
        failures += 0 if ok else 1
        lines.append(f"- {'PASS' if ok else 'FAIL'}: {description}")
    lines.append("")
    return lines, failures


def main() -> int:
    lines, failures = score()
    total = sum(1 for line in lines if line.startswith("- "))
    header = [
        "# Evaluation scorecard",
        "",
        "Deterministic rubric results over the replayed corpus. Regenerate with",
        "`uv run --no-sync python scripts/eval_corpus.py --write` after",
        "intentional changes; tests/test_eval_corpus.py gates drift and failures.",
        "",
        f"**{total - failures}/{total} checks passing.**",
        "",
    ]
    output = "\n".join(header + lines).rstrip() + "\n"
    if "--write" in sys.argv:
        (ROOT / "docs" / "eval_scorecard.md").write_text(output)
        print(f"wrote docs/eval_scorecard.md ({total - failures}/{total} passing)")
    else:
        print(output)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

import re
from pathlib import Path

from ai_incident_investigator.agents.critic import make_critic
from ai_incident_investigator.agents.ranker import make_ranker
from ai_incident_investigator.agents.responses import (
    CriticCheck,
    CriticResponse,
    HypothesisDraft,
    RankerResponse,
)
from ai_incident_investigator.ids import stable_id
from ai_incident_investigator.llm import LLMRequest, LLMResponse
from ai_incident_investigator.loading import load_package
from ai_incident_investigator.models.common import Confidence, Source
from ai_incident_investigator.models.report import (
    ConfidenceRubric,
    EvidenceItem,
    Hypothesis,
    MitigationOption,
    SafetyReview,
)
from ai_incident_investigator.pipeline import initial_state, run_investigation
from ai_incident_investigator.safety import lint_state, make_safety_linter
from ai_incident_investigator.state import InvestigationState, StateUpdate, apply_update

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "incidents" / "latency_spike"
NO_DEPS = frozenset[str]()


def _evidence(source: Source, note: str) -> EvidenceItem:
    return EvidenceItem(
        id=stable_id("evidence", source.value, note), source=source, interpretation=note
    )


EV_METRICS = _evidence(Source.METRICS, "latency 7x baseline after 14:30")
EV_LOGS = _evidence(Source.LOGS, "eligibility retries escalate from 14:29")
EV_TRACES = _evidence(Source.TRACES, "eligibility_query dominates degraded traces")
EV_DEPLOYS = _evidence(Source.DEPLOYS, "deploy landed 11 minutes before onset")
EV_CONTROL = _evidence(Source.METRICS, "notifications-service stayed at baseline")
ALL_EVIDENCE = [EV_METRICS, EV_LOGS, EV_TRACES, EV_DEPLOYS, EV_CONTROL]


def _state_with_evidence(evidence: list[EvidenceItem]) -> InvestigationState:
    state = initial_state(load_package(EXAMPLE))
    return apply_update(state, StateUpdate(evidence=evidence))


def _draft(
    title: str = "Deploy-driven retry amplification",
    supporting: list[str] | None = None,
    conflicting: list[str] | None = None,
    timing: str = "aligned",
    checks: list[str] | None = None,
) -> HypothesisDraft:
    return HypothesisDraft(
        title=title,
        statement=f"{title}: consistent with the cited evidence.",
        supporting_evidence_ids=supporting if supporting is not None else [EV_METRICS.id],
        conflicting_evidence_ids=conflicting or [],
        timing_alignment=timing,  # type: ignore[arg-type]
        timing_justification="deploy 14:20 precedes first deviation 14:25",
        assumptions=["deploy fully rolled out by 14:20"],
        recommended_checks=checks or ["compare error rates before and after the deploy"],
    )


class OneShotLLM:
    """Returns one canned JSON body for whatever request arrives."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(text=self.body, model=request.model, stop_reason="end_turn")


def _ranker_json(*drafts: HypothesisDraft, gaps: list[str] | None = None) -> str:
    return RankerResponse(
        hypotheses=list(drafts), gaps=gaps or [], reasoning="correlated the sources"
    ).model_dump_json()


def test_ranker_derives_confidence_from_citations() -> None:
    draft = _draft(supporting=[EV_METRICS.id, EV_LOGS.id, EV_TRACES.id, EV_DEPLOYS.id])
    llm = OneShotLLM(_ranker_json(draft))
    update = make_ranker(llm, NO_DEPS).run(_state_with_evidence(ALL_EVIDENCE))

    assert len(update.hypotheses) == 1
    hypothesis = update.hypotheses[0]
    assert hypothesis.rubric.aligned_signals == 4
    assert hypothesis.confidence == Confidence.HIGH
    assert hypothesis.id.startswith("hypothesis_")
    # the evidence index was rendered with ids for citation
    assert EV_METRICS.id in llm.requests[0].messages[0].content


def test_ranker_conflict_downgrades_confidence() -> None:
    draft = _draft(
        supporting=[EV_METRICS.id, EV_LOGS.id, EV_TRACES.id],
        conflicting=[EV_CONTROL.id],
    )
    update = make_ranker(OneShotLLM(_ranker_json(draft)), NO_DEPS).run(
        _state_with_evidence(ALL_EVIDENCE)
    )
    assert update.hypotheses[0].confidence == Confidence.MEDIUM
    assert update.hypotheses[0].rubric.conflicting_evidence_count == 1


def test_ranker_drops_unknown_citations_and_records_gap() -> None:
    draft = _draft(supporting=[EV_METRICS.id, "evidence_hallucinated"])
    update = make_ranker(OneShotLLM(_ranker_json(draft)), NO_DEPS).run(
        _state_with_evidence(ALL_EVIDENCE)
    )
    assert update.hypotheses[0].supporting_evidence_ids == [EV_METRICS.id]
    assert any("unknown supporting evidence id" in m.description for m in update.missing_data)


def test_ranker_drops_hypothesis_with_no_valid_evidence() -> None:
    draft = _draft(supporting=["evidence_hallucinated"])
    update = make_ranker(OneShotLLM(_ranker_json(draft)), NO_DEPS).run(
        _state_with_evidence(ALL_EVIDENCE)
    )
    assert update.hypotheses == []
    assert any("no evidence, no hypothesis" in m.description for m in update.missing_data)


def test_ranker_overlap_kept_conservative() -> None:
    draft = _draft(
        supporting=[EV_METRICS.id, EV_LOGS.id],
        conflicting=[EV_LOGS.id],
    )
    update = make_ranker(OneShotLLM(_ranker_json(draft)), NO_DEPS).run(
        _state_with_evidence(ALL_EVIDENCE)
    )
    hypothesis = update.hypotheses[0]
    assert hypothesis.supporting_evidence_ids == [EV_METRICS.id]
    assert hypothesis.conflicting_evidence_ids == [EV_LOGS.id]
    assert any("both supporting and conflicting" in m.description for m in update.missing_data)


def test_ranker_sorts_by_derived_confidence_tier() -> None:
    weak = _draft(title="Weak idea", supporting=[EV_METRICS.id], timing="unknown")
    strong = _draft(
        title="Strong idea",
        supporting=[EV_METRICS.id, EV_LOGS.id, EV_DEPLOYS.id],
    )
    update = make_ranker(OneShotLLM(_ranker_json(weak, strong)), NO_DEPS).run(
        _state_with_evidence(ALL_EVIDENCE)
    )
    assert [h.title for h in update.hypotheses] == ["Strong idea", "Weak idea"]


def test_ranker_short_circuits_without_evidence() -> None:
    llm = OneShotLLM(_ranker_json())
    update = make_ranker(llm, NO_DEPS).run(initial_state(load_package(EXAMPLE)))
    assert llm.requests == []  # no LLM call
    assert update.hypotheses == []
    assert any("no evidence was collected" in m.description for m in update.missing_data)


def test_critic_produces_safety_review() -> None:
    body = CriticResponse(
        checks=[
            CriticCheck(check="overconfidence", result="pass", detail=None),
            CriticCheck(
                check="action_safety",
                result="warning",
                detail="check 'flip the flag to test' would change state",
            ),
        ],
        notes="one recommended check is not read-only",
        gaps=[],
        reasoning="reviewed hypotheses against cited evidence",
    ).model_dump_json()
    state = apply_update(
        _state_with_evidence(ALL_EVIDENCE),
        StateUpdate(hypotheses=[_hypothesis([EV_METRICS.id], Confidence.LOW, 1)]),
    )
    update = make_critic(OneShotLLM(body), NO_DEPS).run(state)
    assert update.safety_review is not None
    assert [c.result for c in update.safety_review.checks] == ["pass", "warning"]


def test_critic_short_circuits_with_nothing_to_review(tmp_path: Path) -> None:
    import json

    (tmp_path / "alert.json").write_text(
        json.dumps(
            {"id": "a1", "title": "t", "service": "svc", "triggered_at": "2026-06-01T14:35:00Z"}
        )
    )
    llm = OneShotLLM("{}")
    update = make_critic(llm, NO_DEPS).run(initial_state(load_package(tmp_path)))
    assert llm.requests == []
    assert update.safety_review is not None
    assert update.safety_review.checks[0].result == "warning"


def _hypothesis(
    supporting: list[str],
    confidence: Confidence,
    signals: int,
    checks: list[str] | None = None,
) -> Hypothesis:
    return Hypothesis(
        id=stable_id("hypothesis", str(supporting), confidence.value, str(signals)),
        title="t",
        statement="consistent with cited evidence",
        confidence=confidence,
        rubric=ConfidenceRubric(
            aligned_signals=signals, timing_alignment="aligned", conflicting_evidence_count=0
        ),
        supporting_evidence_ids=supporting,
        conflicting_evidence_ids=[],
        assumptions=[],
        recommended_checks=checks or [],
    )


def test_linter_passes_clean_state() -> None:
    state = apply_update(
        _state_with_evidence(ALL_EVIDENCE),
        StateUpdate(
            hypotheses=[_hypothesis([EV_METRICS.id, EV_LOGS.id, EV_TRACES.id], Confidence.HIGH, 3)]
        ),
    )
    assert all(check.result == "pass" for check in lint_state(state))


def test_linter_blocks_structural_violations() -> None:
    state = apply_update(
        _state_with_evidence(ALL_EVIDENCE),
        StateUpdate(
            hypotheses=[
                _hypothesis(["evidence_dangling"], Confidence.HIGH, 1),  # dangling + mislabeled
            ]
        ),
    )
    by_name = {check.check: check for check in lint_state(state)}
    assert by_name["hypotheses_cite_resolvable_evidence"].result == "blocked"
    assert by_name["confidence_labels_match_documented_rubric"].result == "blocked"


def test_linter_warns_on_executed_action_phrasing() -> None:
    state = apply_update(
        _state_with_evidence(ALL_EVIDENCE),
        StateUpdate(
            hypotheses=[
                _hypothesis(
                    [EV_METRICS.id],
                    Confidence.LOW,
                    1,
                    checks=["We have rolled back the release to confirm"],
                )
            ]
        ),
    )
    by_name = {check.check: check for check in lint_state(state)}
    assert by_name["no_executed_action_phrasing"].result == "warning"
    assert "rolled back" in (by_name["no_executed_action_phrasing"].detail or "")


def test_linter_checks_mitigation_approval_invariant() -> None:
    state = apply_update(
        _state_with_evidence(ALL_EVIDENCE),
        StateUpdate(
            safe_mitigation_options=[
                MitigationOption(id="m1", action="consider rollback", rationale="r")
            ]
        ),
    )
    by_name = {check.check: check for check in lint_state(state)}
    assert by_name["mitigations_require_human_approval"].result == "pass"


def test_linter_merges_with_critic_review() -> None:
    from ai_incident_investigator.models.report import SafetyCheck

    state = apply_update(
        _state_with_evidence(ALL_EVIDENCE),
        StateUpdate(
            safety_review=SafetyReview(
                checks=[SafetyCheck(check="overconfidence", result="pass", detail=None)],
                notes="critic notes",
            )
        ),
    )
    update = make_safety_linter(NO_DEPS).run(state)
    assert update.safety_review is not None
    assert update.safety_review.notes == "critic notes"
    assert len(update.safety_review.checks) > 1  # critic's + linter's


def test_linter_standalone_when_critic_failed() -> None:
    update = make_safety_linter(NO_DEPS).run(_state_with_evidence(ALL_EVIDENCE))
    assert update.safety_review is not None
    assert "deterministic checks only" in (update.safety_review.notes or "")


def test_full_graph_end_to_end_with_scripted_llm() -> None:
    """Investigators -> ranker -> critic -> linter, all through run_investigation."""
    from test_agents import ScriptedLLM, _default_script

    script = _default_script()

    def ranker_reply(request: LLMRequest) -> str:
        ids = re.findall(r"evidence_[0-9a-f]{10}", request.messages[0].content)
        return _ranker_json(_draft(supporting=list(dict.fromkeys(ids))[:4]))

    critic_reply = CriticResponse(
        checks=[CriticCheck(check="overconfidence", result="pass", detail=None)],
        notes=None,
        gaps=[],
        reasoning="reviewed",
    ).model_dump_json()

    class FullScript(ScriptedLLM):
        def complete(self, request: LLMRequest) -> LLMResponse:
            if "Role: hypothesis ranker" in request.system:
                return LLMResponse(
                    text=ranker_reply(request), model=request.model, stop_reason="end_turn"
                )
            if "Role: safety critic" in request.system:
                return LLMResponse(text=critic_reply, model=request.model, stop_reason="end_turn")
            return super().complete(request)

    state = run_investigation(initial_state(load_package(EXAMPLE)), FullScript(script))
    assert len(state.hypotheses) == 1
    assert state.hypotheses[0].confidence in (Confidence.HIGH, Confidence.MEDIUM)
    assert state.safety_review is not None
    check_names = {check.check for check in state.safety_review.checks}
    assert "overconfidence" in check_names  # critic's
    assert "no_executed_action_phrasing" in check_names  # linter's
    stages = [step.stage for step in state.reasoning_trace]
    assert stages.index("hypothesis_ranker") > stages.index("metrics_investigator")
    assert stages.index("safety_linter") > stages.index("safety_critic")

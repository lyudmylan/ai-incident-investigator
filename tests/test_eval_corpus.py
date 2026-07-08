"""The eval corpus is a regression gate: every rubric passes, on every run."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import eval_corpus  # type: ignore[import-not-found]  # noqa: E402


def test_every_rubric_check_passes() -> None:
    lines, failures = eval_corpus.score()
    failed = [line for line in lines if line.startswith("- FAIL")]
    assert failures == 0, "rubric failures:\n" + "\n".join(failed)


def test_every_example_has_a_rubric() -> None:
    examples = {p.name for p in (ROOT / "examples" / "incidents").iterdir() if p.is_dir()}
    assert examples == set(eval_corpus.RUBRICS), (
        "every example needs a rubric (and every rubric an example)"
    )


def test_committed_scorecard_is_current() -> None:
    lines, _failures = eval_corpus.score()
    committed = (ROOT / "docs" / "eval_scorecard.md").read_text()
    for line in lines:
        if line.startswith(("## ", "- ")):
            assert line in committed, (
                f"scorecard drift ({line!r} missing); regenerate with "
                "scripts/eval_corpus.py --write"
            )


def test_empty_hypotheses_fail_with_a_finding_not_a_crash() -> None:
    """Issue #61: the first live sweep printed 'rubric error: IndexError'
    where it should have said what happened."""
    import pytest

    from ai_incident_investigator.models.report import InvestigationReport

    golden = ROOT / "tests" / "golden" / "insufficient_evidence.json"
    report = InvestigationReport.model_validate_json(golden.read_text())
    assert report.hypotheses == []
    with pytest.raises(LookupError, match="no hypotheses produced"):
        eval_corpus._hypothesis(report, 0)


def test_ranker_prompt_carries_the_issue_61_guidance() -> None:
    from ai_incident_investigator.agents.ranker import RANKER_PROMPT

    prompt = " ".join(RANKER_PROMPT.split())
    assert "copied VERBATIM from the EVIDENCE section" in prompt
    assert "ZERO hypotheses is the correct answer" in prompt

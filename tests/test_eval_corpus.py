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

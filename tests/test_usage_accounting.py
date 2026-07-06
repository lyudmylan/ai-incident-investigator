"""Token usage accounting (issue #40): live runs report what they cost."""

import pytest

from ai_incident_investigator.cli import main
from ai_incident_investigator.llm import (
    LLMRequest,
    LLMResponse,
    UsageTracker,
    estimate_cost,
)
from helpers import EXAMPLE_DIR, ScriptedLLM, default_script


class UsageLLM:
    """Scripted responses decorated with fixed usage numbers."""

    def __init__(self, input_tokens: int = 1500, output_tokens: int = 300) -> None:
        self._inner = ScriptedLLM(default_script())
        self._usage = (input_tokens, output_tokens)

    def complete(self, request: LLMRequest) -> LLMResponse:
        response = self._inner.complete(request)
        return response.model_copy(
            update={"input_tokens": self._usage[0], "output_tokens": self._usage[1]}
        )


def _request(text: str = "hi") -> LLMRequest:
    return LLMRequest.model_validate(
        {"system": "Role: triage", "messages": [{"role": "user", "content": text}]}
    )


def test_tracker_sums_usage_and_counts_unmeasured_calls() -> None:
    tracker = UsageTracker(UsageLLM(input_tokens=1000, output_tokens=200))
    tracker.complete(_request("one"))
    tracker.complete(_request("two"))
    assert (tracker.calls, tracker.input_tokens, tracker.output_tokens) == (2, 2000, 400)
    assert tracker.unmeasured_calls == 0

    bare = UsageTracker(ScriptedLLM(default_script()))  # scripted: no usage
    bare.complete(_request())
    assert bare.calls == 1
    assert bare.unmeasured_calls == 1
    assert bare.input_tokens == 0


def test_cost_estimate_prefix_matches_and_refuses_unknown_models() -> None:
    assert estimate_cost("claude-opus-4-8", 1_000_000, 0) == 15.0
    assert estimate_cost("claude-haiku-4-5-20251001", 0, 1_000_000) == 5.0
    assert estimate_cost("some-future-model", 1_000_000, 1_000_000) is None


def test_summary_line_includes_cost_and_unmeasured_note() -> None:
    tracker = UsageTracker(UsageLLM(input_tokens=17_000, output_tokens=2_000))
    tracker.complete(_request())
    summary = tracker.summary()
    assert "1 calls, 17,000 input + 2,000 output tokens" in summary
    assert "$" in summary  # default model is priced

    bare = UsageTracker(ScriptedLLM(default_script()))
    bare.complete(_request())
    assert "1 call(s) reported no usage" in bare.summary()


def test_cli_prints_usage_for_live_and_stays_silent_on_replay(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "ai_incident_investigator.cli.make_client", lambda mode, fixtures_dir: UsageLLM()
    )
    assert main(["--incident", str(EXAMPLE_DIR), "--llm", "live"]) == 0
    err = capsys.readouterr().err
    # 9 calls: the default script's ranker returns no hypotheses, so the
    # planner short-circuits without an LLM call - the tracker sees through it
    assert "llm usage: 9 calls, 13,500 input + 2,700 output tokens" in err
    assert "$" in err

    monkeypatch.undo()
    assert main(["--incident", str(EXAMPLE_DIR), "--llm", "replay"]) == 0
    assert "llm usage" not in capsys.readouterr().err

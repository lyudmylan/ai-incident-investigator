"""AnthropicClient transport behavior against a faked SDK (no network).

The SDK import is lazy (inside __init__), so injecting a fake `anthropic`
module into sys.modules before construction exercises the real client code.
Found by the first live run: Haiku 4.5 rejects adaptive thinking with a
400; the client must retry once without it instead of degrading the agent.
"""

import json
import sys
import types
from typing import Any

import pytest

from ai_incident_investigator.llm import LLMError, LLMRequest


class FakeAPIError(Exception):
    pass


class _Usage:
    input_tokens = 1200
    output_tokens = 250


class _Block:
    type = "text"
    text = json.dumps({"ok": True})


class _Message:
    model = "claude-haiku-4-5-20251001"
    stop_reason = "end_turn"

    def __init__(self) -> None:
        self.content = [_Block()]
        self.usage = _Usage()


class FakeMessages:
    def __init__(self, reject_thinking: bool, error: str | None = None) -> None:
        self.reject_thinking = reject_thinking
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Message:
        self.calls.append(kwargs)
        if self.error is not None:
            raise FakeAPIError(self.error)
        if self.reject_thinking and "thinking" in kwargs:
            raise FakeAPIError("Error code: 400 - adaptive thinking is not supported on this model")
        return _Message()


def _client(messages: FakeMessages) -> Any:
    fake_sdk = types.ModuleType("anthropic")
    fake_sdk.APIError = FakeAPIError  # type: ignore[attr-defined]

    class Anthropic:
        def __init__(self) -> None:
            self.messages = messages

    fake_sdk.Anthropic = Anthropic  # type: ignore[attr-defined]
    sys.modules["anthropic"] = fake_sdk
    try:
        from ai_incident_investigator.llm import AnthropicClient

        return AnthropicClient()
    finally:
        del sys.modules["anthropic"]


def _request() -> LLMRequest:
    return LLMRequest.model_validate(
        {
            "model": "claude-haiku-4-5-20251001",
            "system": "Role: triage",
            "messages": [{"role": "user", "content": "investigate"}],
        }
    )


def test_thinking_rejection_retries_once_without_thinking() -> None:
    messages = FakeMessages(reject_thinking=True)
    response = _client(messages).complete(_request())
    assert response.text == json.dumps({"ok": True})
    assert response.input_tokens == 1200
    assert len(messages.calls) == 2
    assert "thinking" in messages.calls[0]
    assert "thinking" not in messages.calls[1]


def test_supported_model_sends_thinking_with_no_retry() -> None:
    messages = FakeMessages(reject_thinking=False)
    _client(messages).complete(_request())
    assert len(messages.calls) == 1
    assert messages.calls[0]["thinking"] == {"type": "adaptive"}


def test_other_api_errors_still_raise() -> None:
    messages = FakeMessages(reject_thinking=False, error="Error code: 429 - overloaded")
    with pytest.raises(LLMError, match="Claude API call failed"):
        _client(messages).complete(_request())
    assert len(messages.calls) == 1  # no retry for unrelated errors

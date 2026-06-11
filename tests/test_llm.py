from pathlib import Path

import pytest

from ai_incident_investigator.llm import (
    DEFAULT_MODEL,
    MODEL_ENV_VAR,
    LLMRequest,
    LLMResponse,
    RecordingClient,
    ReplayClient,
    ReplayMissError,
    request_key,
)


class FakeClient:
    """Stands in for AnthropicClient; counts calls."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            text='{"finding": "db saturation"}',
            model=request.model,
            stop_reason="end_turn",
            input_tokens=100,
            output_tokens=20,
        )


def _request(system: str = "You are a metrics investigator.") -> LLMRequest:
    return LLMRequest(
        system=system,
        messages=[{"role": "user", "content": "analyze the metrics"}],  # type: ignore[list-item]
    )


def test_request_key_is_stable_and_content_sensitive() -> None:
    assert request_key(_request()) == request_key(_request())
    assert request_key(_request()) != request_key(_request(system="different"))


def test_default_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _request().model == DEFAULT_MODEL
    monkeypatch.setenv(MODEL_ENV_VAR, "claude-sonnet-4-6")
    assert _request().model == "claude-sonnet-4-6"


def test_record_then_replay_round_trip(tmp_path: Path) -> None:
    fake = FakeClient()
    recorder = RecordingClient(fake, tmp_path)
    request = _request()

    recorded = recorder.complete(request)
    assert fake.calls == 1
    assert (tmp_path / f"{request_key(request)}.json").exists()

    replayed = ReplayClient(tmp_path).complete(request)
    assert replayed == recorded


def test_replay_rejects_mismatched_stored_request(tmp_path: Path) -> None:
    import json

    request = _request()
    fixture = {
        "request": _request(system="something else entirely").model_dump(mode="json"),
        "response": LLMResponse(text="{}", model=DEFAULT_MODEL).model_dump(mode="json"),
    }
    (tmp_path / f"{request_key(request)}.json").write_text(json.dumps(fixture))
    with pytest.raises(ReplayMissError, match="collision or stale"):
        ReplayClient(tmp_path).complete(request)


def test_replay_miss_raises_with_guidance(tmp_path: Path) -> None:
    with pytest.raises(ReplayMissError, match="record one with RecordingClient"):
        ReplayClient(tmp_path).complete(_request())


def test_replay_is_offline_only(tmp_path: Path) -> None:
    fake = FakeClient()
    recorder = RecordingClient(fake, tmp_path)
    request = _request()
    recorder.complete(request)

    ReplayClient(tmp_path).complete(request)
    ReplayClient(tmp_path).complete(request)
    assert fake.calls == 1  # replays never touch the inner client

"""LLM harness: live Anthropic client plus record/replay for tests.

Three interchangeable clients behind one protocol:

- AnthropicClient: live Claude API calls (needs ANTHROPIC_API_KEY)
- RecordingClient: wraps another client and saves request/response fixtures
- ReplayClient: serves saved fixtures; never touches the network

CI runs on ReplayClient only — no API keys (AGENTS.md rule).
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MODEL = "claude-opus-4-8"
MODEL_ENV_VAR = "AI_INCIDENT_INVESTIGATOR_MODEL"


def default_model() -> str:
    return os.environ.get(MODEL_ENV_VAR, DEFAULT_MODEL)


class LLMMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(default_factory=default_model)
    system: str
    messages: list[LLMMessage] = Field(min_length=1)
    max_tokens: int = 16000
    thinking: bool = Field(default=True, description="Adaptive thinking on/off")
    json_schema: dict[str, Any] | None = Field(
        default=None, description="When set, the response is schema-constrained JSON"
    )


class LLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    model: str
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class LLMError(Exception):
    """The LLM call failed or returned an unusable response."""


class ReplayMissError(LLMError):
    """No recorded fixture matches the request."""


class LLMClient(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...


def request_key(request: LLMRequest) -> str:
    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


_OK_STOP_REASONS = ("end_turn", "stop_sequence")


class AnthropicClient:
    """Live Claude API client (thin adapter over the official SDK).

    The SDK import is deliberately deferred to construction: replay mode and
    the test suite must work without the SDK being importable at all (it is
    a live-mode-only dependency, and importing it costs startup time).
    """

    def __init__(self) -> None:
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic()

    def complete(self, request: LLMRequest) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        }
        if request.thinking:
            kwargs["thinking"] = {"type": "adaptive"}
        if request.json_schema is not None:
            kwargs["output_config"] = {
                "format": {"type": "json_schema", "schema": request.json_schema}
            }

        try:
            message = self._client.messages.create(**kwargs)
        except self._anthropic.APIError as exc:
            raise LLMError(f"Claude API call failed: {exc}") from exc

        # Anything but a clean finish is an error: refusal, max_tokens
        # truncation, pause_turn (server tools we don't use), or future
        # stop reasons must not silently pass as a complete answer.
        if message.stop_reason not in _OK_STOP_REASONS:
            raise LLMError(
                f"unexpected stop_reason={message.stop_reason!r} "
                f"(max_tokens={request.max_tokens}); response is not usable as-is"
            )

        text = "".join(block.text for block in message.content if block.type == "text")
        usage = message.usage
        return LLMResponse(
            text=text,
            model=message.model,
            stop_reason=message.stop_reason,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )


class ReplayClient:
    """Serves recorded fixtures keyed by request content. Never calls the network."""

    def __init__(self, fixtures_dir: Path) -> None:
        self._dir = fixtures_dir

    def complete(self, request: LLMRequest) -> LLMResponse:
        path = self._dir / f"{request_key(request)}.json"
        if not path.exists():
            raise ReplayMissError(
                f"no fixture {path.name} in {self._dir} for model={request.model}, "
                f"system={request.system[:60]!r}...; record one with RecordingClient"
            )
        data = json.loads(path.read_text())
        if data["request"] != request.model_dump(mode="json"):
            raise ReplayMissError(
                f"fixture {path.name} stores a different request than the one asked for "
                "(key collision or stale fixture); re-record it"
            )
        return LLMResponse.model_validate(data["response"])


class RecordingClient:
    """Wraps a real client and writes a replayable fixture for every call."""

    def __init__(self, inner: LLMClient, fixtures_dir: Path) -> None:
        self._inner = inner
        self._dir = fixtures_dir

    def complete(self, request: LLMRequest) -> LLMResponse:
        response = self._inner.complete(request)
        self._dir.mkdir(parents=True, exist_ok=True)
        fixture = {
            "request": request.model_dump(mode="json"),
            "response": response.model_dump(mode="json"),
        }
        path = self._dir / f"{request_key(request)}.json"
        # Atomic write: concurrent agents in the graph's thread pool may record
        # simultaneously; a torn write would corrupt the fixture.
        fd, tmp_name = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(json.dumps(fixture, indent=2, sort_keys=True) + "\n")
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return response

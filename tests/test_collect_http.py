import io
import json
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ai_incident_investigator.collect.http import (
    EnvBearerAuth,
    HTTPClientError,
    HTTPReplayMissError,
    HTTPRequest,
    HTTPResponse,
    LiveHTTPClient,
    RecordingHTTPClient,
    ReplayHTTPClient,
    raise_for_status,
    request_key,
)


class FakeHTTP:
    def __init__(self, body: str = '{"ok": true}', status: int = 200) -> None:
        self.body = body
        self.status = status
        self.calls: list[tuple[HTTPRequest, EnvBearerAuth | None]] = []

    def get(self, request: HTTPRequest, auth: EnvBearerAuth | None = None) -> HTTPResponse:
        self.calls.append((request, auth))
        return HTTPResponse(status=self.status, body=self.body)


def _request() -> HTTPRequest:
    return HTTPRequest(url="https://obs.example.com/api/things", params={"q": "x", "a": "1"})


def test_request_key_stable_and_param_sensitive() -> None:
    assert request_key(_request()) == request_key(_request())
    other = HTTPRequest(url="https://obs.example.com/api/things", params={"q": "y"})
    assert request_key(_request()) != request_key(other)


def test_get_only_surface() -> None:
    for client_type in (LiveHTTPClient, ReplayHTTPClient, RecordingHTTPClient):
        assert not hasattr(client_type, "post")
        assert not hasattr(client_type, "put")
        assert not hasattr(client_type, "delete")
    with pytest.raises(ValidationError):
        HTTPRequest(method="POST", url="https://x")  # type: ignore[arg-type]


def test_record_then_replay_round_trip(tmp_path: Path) -> None:
    fake = FakeHTTP()
    recorder = RecordingHTTPClient(fake, tmp_path)
    recorded = recorder.get(_request())
    replayed = ReplayHTTPClient(tmp_path).get(_request())
    assert replayed == recorded
    assert len(fake.calls) == 1  # replay never touched the inner client


def test_replay_miss_and_stale_fixture(tmp_path: Path) -> None:
    with pytest.raises(HTTPReplayMissError, match="record one"):
        ReplayHTTPClient(tmp_path).get(_request())

    key = request_key(_request())
    stale = {
        "request": HTTPRequest(url="https://other.example.com").model_dump(mode="json"),
        "response": {"status": 200, "body": "{}"},
    }
    (tmp_path / f"{key}.json").write_text(json.dumps(stale))
    with pytest.raises(HTTPReplayMissError, match="collision or stale"):
        ReplayHTTPClient(tmp_path).get(_request())


def test_fixtures_never_contain_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OBS_TOKEN", "supersecret-value-123")
    recorder = RecordingHTTPClient(FakeHTTP(), tmp_path)
    recorder.get(_request(), auth=EnvBearerAuth(env_var="OBS_TOKEN"))
    fixture_text = next(tmp_path.glob("*.json")).read_text()
    assert "supersecret-value-123" not in fixture_text
    assert "OBS_TOKEN" not in fixture_text
    assert "Authorization" not in fixture_text


def test_live_client_requires_env_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OBS_TOKEN", raising=False)
    client = LiveHTTPClient()
    with pytest.raises(HTTPClientError, match="OBS_TOKEN is not set"):
        client.get(_request(), auth=EnvBearerAuth(env_var="OBS_TOKEN"))


def test_live_client_sends_get_with_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBS_TOKEN", "tok-123")
    seen: dict[str, Any] = {}

    class FakeReply(io.BytesIO):
        status = 200

        def __enter__(self) -> "FakeReply":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(raw: urllib.request.Request, timeout: float) -> FakeReply:
        seen["method"] = raw.get_method()
        seen["url"] = raw.full_url
        seen["auth"] = raw.get_header("Authorization")
        seen["timeout"] = timeout
        return FakeReply(b'{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    response = LiveHTTPClient(timeout_seconds=5).get(
        _request(), auth=EnvBearerAuth(env_var="OBS_TOKEN")
    )
    assert response.status == 200
    assert seen["method"] == "GET"
    assert seen["url"].endswith("?a=1&q=x")  # params sorted for stable urls
    assert seen["auth"] == "Bearer tok-123"
    assert seen["timeout"] == 5


def test_raise_for_status() -> None:
    request = _request()
    ok = HTTPResponse(status=200, body="{}")
    assert raise_for_status(request, ok) is ok
    with pytest.raises(HTTPClientError, match="returned 503"):
        raise_for_status(request, HTTPResponse(status=503, body="upstream down"))

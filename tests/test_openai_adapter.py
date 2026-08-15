from __future__ import annotations

import io
import json
import urllib.error
from http.client import HTTPMessage
from typing import Any

import pytest

from minicode.openai_adapter import (
    DEFAULT_OPENAI_USER_AGENT,
    OpenAIModelAdapter,
)


class _DummyTools:
    def list(self) -> list[object]:
        return []


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body


def _runtime() -> dict[str, str]:
    return {
        "model": "gpt5.5",
        "openaiBaseUrl": "https://www.cctq.ai",
        "openaiApiKey": "test-key",
    }


def test_openai_adapter_sets_compatible_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def _fake_urlopen(request, timeout=0):  # noqa: ANN001
        captured["user_agent"] = request.get_header("User-agent")
        return _FakeResponse({"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]})

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    adapter = OpenAIModelAdapter(_runtime(), _DummyTools())

    step = adapter.next([{"role": "user", "content": "Reply with exactly OK."}])

    assert step.content == "OK"
    assert captured["user_agent"] == DEFAULT_OPENAI_USER_AGENT


@pytest.mark.parametrize(
    ("base_url", "expected_url"),
    [
        ("https://www.cctq.ai", "https://www.cctq.ai/v1/chat/completions"),
        ("https://www.cctq.ai/", "https://www.cctq.ai/v1/chat/completions"),
        ("https://www.cctq.ai/v1", "https://www.cctq.ai/v1/chat/completions"),
        ("https://www.cctq.ai/v1/", "https://www.cctq.ai/v1/chat/completions"),
        ("https://www.cctq.ai/v1/chat/completions", "https://www.cctq.ai/v1/chat/completions"),
    ],
)
def test_openai_adapter_accepts_provider_root_api_base_or_full_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
    expected_url: str,
) -> None:
    captured: dict[str, str] = {}

    def _fake_urlopen(request, timeout=0):  # noqa: ANN001
        captured["url"] = request.full_url
        return _FakeResponse({"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]})

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    runtime = {**_runtime(), "openaiBaseUrl": base_url}
    adapter = OpenAIModelAdapter(runtime, _DummyTools())

    step = adapter.next([{"role": "user", "content": "Reply with exactly OK."}])

    assert step.content == "OK"
    assert captured["url"] == expected_url


def test_openai_adapter_surfaces_non_json_http_error_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_urlopen(request, timeout=0):  # noqa: ANN001
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            hdrs=HTTPMessage(),
            fp=io.BytesIO(b"error code: 1010"),
        )

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    adapter = OpenAIModelAdapter(_runtime(), _DummyTools())

    with pytest.raises(RuntimeError, match="error code: 1010"):
        adapter.next([{"role": "user", "content": "Reply with exactly OK."}])


def test_openai_adapter_does_not_retry_permanent_503_model_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    def _fake_urlopen(request, timeout=0):  # noqa: ANN001
        calls["count"] += 1
        raise urllib.error.HTTPError(
            request.full_url,
            503,
            "Unavailable",
            hdrs=HTTPMessage(),
            fp=io.BytesIO(
                json.dumps(
                    {
                        "error": {
                            "code": "model_not_found",
                            "message": "No available channel for model gpt5.5 under group Codex-Sale",
                        }
                    }
                ).encode("utf-8")
            ),
        )

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    adapter = OpenAIModelAdapter(_runtime(), _DummyTools())

    with pytest.raises(RuntimeError, match="No available channel for model gpt5.5"):
        adapter.next([{"role": "user", "content": "Reply with exactly OK."}])

    assert calls["count"] == 1


def test_openai_adapter_round_trips_reasoning_content_for_tool_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []
    responses = iter(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "opaque-provider-reasoning",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path":"facts/project.txt"}',
                                    },
                                },
                                {
                                    "id": "call-2",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path":"facts/other.txt"}',
                                    },
                                },
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {"content": "Orion-7"},
                        "finish_reason": "stop",
                    }
                ]
            },
        ]
    )

    def _fake_urlopen(request, timeout=0):  # noqa: ANN001
        requests.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse(next(responses))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    adapter = OpenAIModelAdapter(_runtime(), _DummyTools())

    first = adapter.next([{"role": "user", "content": "Read the fact."}])
    second = adapter.next(
        [
            {"role": "user", "content": "Read the fact."},
            {
                "role": "assistant_tool_call",
                "toolUseId": "call-1",
                "toolName": "read_file",
                "input": {"path": "facts/project.txt"},
            },
            {
                "role": "tool_result",
                "toolUseId": "call-1",
                "toolName": "read_file",
                "content": "Orion-7",
                "isError": False,
            },
            {
                "role": "assistant_tool_call",
                "toolUseId": "call-2",
                "toolName": "read_file",
                "input": {"path": "facts/other.txt"},
            },
            {
                "role": "tool_result",
                "toolUseId": "call-2",
                "toolName": "read_file",
                "content": "Other evidence.",
                "isError": False,
            },
        ]
    )

    assert first.type == "tool_calls"
    assert second.content == "Orion-7"
    assistant_messages = [
        message
        for message in requests[1]["messages"]
        if message.get("role") == "assistant"
    ]
    assert len(assistant_messages) == 2
    assert all(
        message["reasoning_content"] == "opaque-provider-reasoning"
        for message in assistant_messages
    )


def test_openai_adapter_reports_usage_to_optional_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_usage: list[dict[str, Any]] = []

    def _fake_urlopen(request, timeout=0):  # noqa: ANN001
        return _FakeResponse(
            {
                "choices": [
                    {"message": {"content": "OK"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    adapter = OpenAIModelAdapter(
        {**_runtime(), "usageSink": captured_usage.append}, _DummyTools()
    )

    adapter.next([{"role": "user", "content": "Reply with OK."}])

    assert captured_usage == [{"prompt_tokens": 12, "completion_tokens": 3}]

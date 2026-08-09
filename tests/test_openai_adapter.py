import json
from http.client import RemoteDisconnected

import pytest

from minicode.openai_adapter import OpenAIModelAdapter, _chat_completions_url
from minicode.tooling import ToolDefinition, ToolRegistry


class DummyResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status = 200

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _tool_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolDefinition(
                name="read_file",
                description="Read file",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                validator=lambda value: value,
                run=lambda _input, _context: None,
            )
        ]
    )


def _runtime(**overrides):
    return {
        "provider": "openai",
        "model": "test-model",
        "baseUrl": "https://api.openai.com/v1",
        "apiKey": "secret",
        "authToken": None,
        "maxOutputTokens": 256,
        "openaiMaxTokensParam": "max_completion_tokens",
        **overrides,
    }


def test_chat_completions_url_accepts_common_base_url_shapes() -> None:
    assert _chat_completions_url("https://api.openai.com") == "https://api.openai.com/v1/chat/completions"
    assert _chat_completions_url("https://api.openai.com/v1") == "https://api.openai.com/v1/chat/completions"
    assert _chat_completions_url("https://example.test/v1/chat/completions") == "https://example.test/v1/chat/completions"


def test_openai_adapter_parses_function_tool_call(monkeypatch) -> None:
    captured = {}
    payload = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "<progress>reading</progress>",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{\"path\":\"README.md\"}"},
                        }
                    ],
                },
            }
        ]
    }

    def fake_urlopen(request, timeout=60):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return DummyResponse(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = OpenAIModelAdapter(_runtime(), _tool_registry())

    step = adapter.next([{"role": "system", "content": "sys"}, {"role": "user", "content": "read me"}])

    assert step.type == "tool_calls"
    assert step.content == "reading"
    assert step.contentKind == "progress"
    assert step.calls == [{"id": "call-1", "toolName": "read_file", "input": {"path": "README.md"}}]
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["body"]["tools"][0]["function"]["parameters"]["required"] == ["path"]
    assert captured["body"]["max_completion_tokens"] == 256


def test_openai_adapter_round_trips_tool_results(monkeypatch) -> None:
    captured = {}
    payload = {
        "choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": "<final>done</final>"}}
        ]
    }

    def fake_urlopen(request, timeout=60):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return DummyResponse(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = OpenAIModelAdapter(_runtime(maxOutputTokens=None), _tool_registry())
    step = adapter.next(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "read"},
            {
                "role": "assistant_tool_call",
                "toolUseId": "call-1",
                "toolName": "read_file",
                "input": {"path": "README.md"},
            },
            {
                "role": "tool_result",
                "toolUseId": "call-1",
                "toolName": "read_file",
                "content": "hello",
                "isError": False,
            },
        ]
    )

    assert step.type == "assistant"
    assert step.content == "done"
    assert step.kind == "final"
    messages = captured["body"]["messages"]
    assert messages[-2]["tool_calls"][0]["id"] == "call-1"
    assert messages[-1] == {"role": "tool", "tool_call_id": "call-1", "content": "hello"}


def test_openai_adapter_groups_parallel_tool_calls(monkeypatch) -> None:
    captured = {}
    payload = {
        "choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": "done"}}
        ]
    }

    def fake_urlopen(request, timeout=60):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return DummyResponse(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    OpenAIModelAdapter(_runtime(maxOutputTokens=None), _tool_registry()).next(
        [
            {"role": "user", "content": "read both"},
            {
                "role": "assistant_tool_call",
                "toolUseId": "call-1",
                "toolName": "read_file",
                "input": {"path": "README.md"},
            },
            {
                "role": "assistant_tool_call",
                "toolUseId": "call-2",
                "toolName": "read_file",
                "input": {"path": "pyproject.toml"},
            },
            {"role": "tool_result", "toolUseId": "call-1", "content": "readme"},
            {"role": "tool_result", "toolUseId": "call-2", "content": "project"},
        ]
    )

    messages = captured["body"]["messages"]
    assert [call["id"] for call in messages[-3]["tool_calls"]] == ["call-1", "call-2"]
    assert [message["role"] for message in messages[-3:]] == ["assistant", "tool", "tool"]


def test_openai_adapter_preserves_deepseek_reasoning_for_tool_round_trip(monkeypatch) -> None:
    captured = {}
    first_payload = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "I need to inspect the file.",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{\"path\":\"README.md\"}"},
                        }
                    ],
                },
            }
        ]
    }
    second_payload = {
        "choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": "done"}}
        ]
    }
    responses = iter([DummyResponse(first_payload), DummyResponse(second_payload)])

    def fake_urlopen(request, timeout=60):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return next(responses)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = OpenAIModelAdapter(
        _runtime(
            model="deepseek-v4-pro",
            baseUrl="https://api.deepseek.com",
            openaiMaxTokensParam="max_tokens",
            thinkingMode="enabled",
        ),
        _tool_registry(),
    )
    first = adapter.next([{"role": "user", "content": "read"}])
    assert first.reasoningContent == "I need to inspect the file."

    adapter.next(
        [
            {"role": "user", "content": "read"},
            {
                "role": "assistant_tool_call",
                "toolUseId": "call-1",
                "toolName": "read_file",
                "input": {"path": "README.md"},
                "reasoningContent": first.reasoningContent,
            },
            {
                "role": "tool_result",
                "toolUseId": "call-1",
                "toolName": "read_file",
                "content": "hello",
                "isError": False,
            },
        ]
    )

    assistant_call = captured["body"]["messages"][-2]
    assert assistant_call["reasoning_content"] == "I need to inspect the file."
    assert captured["body"]["thinking"] == {"type": "enabled"}
    assert captured["body"]["max_tokens"] == 256


def test_openai_adapter_maps_length_to_max_tokens(monkeypatch) -> None:
    payload = {
        "choices": [
            {"finish_reason": "length", "message": {"role": "assistant", "content": "partial"}}
        ]
    }
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=60: DummyResponse(payload))
    step = OpenAIModelAdapter(_runtime(), _tool_registry()).next([{"role": "user", "content": "go"}])
    assert step.diagnostics.stopReason == "max_tokens"


def test_openai_adapter_retries_remote_disconnect(monkeypatch) -> None:
    payload = {
        "choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": "recovered"}}
        ]
    }
    attempts = 0

    def flaky_urlopen(request, timeout=60):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RemoteDisconnected("remote closed connection")
        return DummyResponse(payload)

    monkeypatch.setattr("urllib.request.urlopen", flaky_urlopen)
    monkeypatch.setattr("minicode.openai_adapter._get_retry_limit", lambda: 1)
    monkeypatch.setattr("minicode.openai_adapter._sleep", lambda _delay: None)

    step = OpenAIModelAdapter(_runtime(), _tool_registry()).next(
        [{"role": "user", "content": "retry once"}]
    )

    assert attempts == 2
    assert step.content == "recovered"


def test_openai_adapter_reports_usage_to_optional_sink(monkeypatch) -> None:
    captured_usage = []
    payload = {
        "choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": "done"}}
        ],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "prompt_cache_hit_tokens": 80,
        },
    }
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=60: DummyResponse(payload),
    )

    OpenAIModelAdapter(
        _runtime(usageSink=captured_usage.append),
        _tool_registry(),
    ).next([{"role": "user", "content": "measure usage"}])

    assert captured_usage == [payload["usage"]]


def test_openai_adapter_rejects_invalid_tool_json(monkeypatch) -> None:
    payload = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{bad json"},
                        }
                    ],
                },
            }
        ]
    }
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=60: DummyResponse(payload))
    with pytest.raises(RuntimeError, match="invalid JSON arguments"):
        OpenAIModelAdapter(_runtime(), _tool_registry()).next([{"role": "user", "content": "go"}])

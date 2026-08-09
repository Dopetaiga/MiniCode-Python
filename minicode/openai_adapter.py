from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from minicode.anthropic_adapter import (
    _extract_error_message,
    _get_retry_delay_ms,
    _get_retry_limit,
    _parse_assistant_text,
    _parse_retry_after_ms,
    _read_json_body,
    _should_retry_status,
    _sleep,
)
from minicode.types import AgentStep, StepDiagnostics


def _chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return normalized + "/chat/completions"
    return normalized + "/v1/chat/completions"


def _assistant_content(message: dict[str, Any]) -> str:
    if message["role"] == "assistant_progress":
        return f"<progress>\n{message['content']}\n</progress>"
    return message["content"]


def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role in {"system", "user"}:
            converted.append({"role": role, "content": message["content"]})
            continue
        if role in {"assistant", "assistant_progress"}:
            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": _assistant_content(message),
            }
            if message.get("reasoningContent"):
                assistant_message["reasoning_content"] = message["reasoningContent"]
            converted.append(assistant_message)
            continue
        if role == "assistant_tool_call":
            tool_call = {
                "id": message["toolUseId"],
                "type": "function",
                "function": {
                    "name": message["toolName"],
                    "arguments": json.dumps(message["input"], ensure_ascii=False),
                },
            }
            if converted and converted[-1].get("role") == "assistant" and "tool_calls" in converted[-1]:
                converted[-1]["tool_calls"].append(tool_call)
                if message.get("reasoningContent") and not converted[-1].get("reasoning_content"):
                    converted[-1]["reasoning_content"] = message["reasoningContent"]
            else:
                assistant_call_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                }
                if message.get("reasoningContent"):
                    assistant_call_message["reasoning_content"] = message["reasoningContent"]
                converted.append(assistant_call_message)
            continue
        if role == "tool_result":
            converted.append(
                {
                    "role": "tool",
                    "tool_call_id": message["toolUseId"],
                    "content": message["content"],
                }
            )
    return converted


def _response_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
            parts.append(part["text"])
    return "\n".join(parts)


def _parse_tool_arguments(arguments: Any, tool_name: str) -> Any:
    if isinstance(arguments, (dict, list)):
        return arguments
    if not isinstance(arguments, str) or not arguments.strip():
        return {}
    try:
        return json.loads(arguments)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Model returned invalid JSON arguments for tool '{tool_name}': {error}") from error


class OpenAIModelAdapter:
    """OpenAI-compatible Chat Completions adapter with function tool calling."""

    def __init__(self, runtime: dict[str, Any], tools) -> None:
        self.runtime = runtime
        self.tools = tools

    def next(self, messages: list[dict[str, Any]]) -> AgentStep:
        request_body: dict[str, Any] = {
            "model": self.runtime["model"],
            "messages": _to_openai_messages(messages),
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in self.tools.list()
            ],
        }
        max_output_tokens = self.runtime.get("maxOutputTokens")
        if max_output_tokens is not None:
            token_parameter = self.runtime.get("openaiMaxTokensParam", "max_completion_tokens")
            request_body[token_parameter] = max_output_tokens
        if self.runtime.get("thinkingMode"):
            request_body["thinking"] = {"type": self.runtime["thinkingMode"]}
        if self.runtime.get("reasoningEffort"):
            request_body["reasoning_effort"] = self.runtime["reasoningEffort"]

        headers = {
            "content-type": "application/json",
            "Authorization": f"Bearer {self.runtime['apiKey']}",
        }
        if self.runtime.get("openaiOrganization"):
            headers["OpenAI-Organization"] = self.runtime["openaiOrganization"]
        if self.runtime.get("openaiProject"):
            headers["OpenAI-Project"] = self.runtime["openaiProject"]

        request = urllib.request.Request(
            url=_chat_completions_url(self.runtime["baseUrl"]),
            data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        max_retries = _get_retry_limit()
        response = None
        for attempt in range(max_retries + 1):
            try:
                response = urllib.request.urlopen(request, timeout=60)  # noqa: S310
                break
            except urllib.error.HTTPError as error:
                response = error
                if not _should_retry_status(error.code) or attempt >= max_retries:
                    break
                _sleep(_get_retry_delay_ms(attempt + 1, _parse_retry_after_ms(error.headers.get("retry-after"))))
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                if attempt >= max_retries:
                    raise
                _sleep(_get_retry_delay_ms(attempt + 1, None))

        if response is None:
            raise RuntimeError("Model request failed before receiving a response")

        data = _read_json_body(response)
        status = getattr(response, "status", getattr(response, "code", 200))
        if status >= 400:
            raise RuntimeError(_extract_error_message(data, status))

        usage = data.get("usage") if isinstance(data, dict) else None
        usage_sink = self.runtime.get("usageSink")
        if isinstance(usage, dict) and callable(usage_sink):
            usage_sink(dict(usage))

        choices = data.get("choices", []) if isinstance(data, dict) else []
        if not choices or not isinstance(choices[0], dict):
            raise RuntimeError("OpenAI-compatible response did not contain choices[0]")
        choice = choices[0]
        response_message = choice.get("message", {})
        if not isinstance(response_message, dict):
            raise RuntimeError("OpenAI-compatible response did not contain a valid message")

        raw_tool_calls = response_message.get("tool_calls") or []
        tool_calls: list[dict[str, Any]] = []
        ignored_block_types: list[str] = []
        for raw_call in raw_tool_calls:
            if not isinstance(raw_call, dict) or raw_call.get("type") != "function":
                ignored_block_types.append(str(raw_call.get("type") if isinstance(raw_call, dict) else None))
                continue
            function = raw_call.get("function", {})
            call_id = raw_call.get("id")
            tool_name = function.get("name") if isinstance(function, dict) else None
            if not isinstance(call_id, str) or not isinstance(tool_name, str):
                ignored_block_types.append("malformed_function")
                continue
            tool_calls.append(
                {
                    "id": call_id,
                    "toolName": tool_name,
                    "input": _parse_tool_arguments(function.get("arguments"), tool_name),
                }
            )

        parsed_text, kind = _parse_assistant_text(_response_text(response_message.get("content")))
        reasoning_content = response_message.get("reasoning_content")
        if not isinstance(reasoning_content, str):
            reasoning_content = None
        finish_reason = choice.get("finish_reason")
        normalized_stop_reason = "max_tokens" if finish_reason == "length" else finish_reason
        block_types = (["text"] if parsed_text else []) + (["tool_call"] * len(tool_calls))
        diagnostics = StepDiagnostics(
            stopReason=normalized_stop_reason if isinstance(normalized_stop_reason, str) else None,
            blockTypes=block_types,
            ignoredBlockTypes=ignored_block_types,
        )

        if tool_calls:
            return AgentStep(
                type="tool_calls",
                calls=tool_calls,
                content=parsed_text,
                contentKind="progress" if kind == "progress" else None,
                reasoningContent=reasoning_content,
                diagnostics=diagnostics,
            )
        return AgentStep(
            type="assistant",
            content=parsed_text,
            kind=kind,
            reasoningContent=reasoning_content,
            diagnostics=diagnostics,
        )

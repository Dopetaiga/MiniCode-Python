"""Optional live smoke test for OpenAI-compatible providers, including DeepSeek V4."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult


def _live_runtime() -> dict:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        pytest.skip("OPENAI_API_KEY is not configured")

    model = os.environ.get("OPENAI_MODEL", "").strip()
    if not model:
        pytest.skip("OPENAI_MODEL is not configured")

    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com").strip()
    is_deepseek = "deepseek" in base_url.lower() or model.lower().startswith("deepseek-")
    return {
        "provider": "openai",
        "model": model,
        "baseUrl": base_url,
        "apiKey": api_key,
        "maxOutputTokens": int(os.environ.get("MINI_CODE_MAX_OUTPUT_TOKENS", "1024")),
        "openaiMaxTokensParam": os.environ.get(
            "OPENAI_MAX_TOKENS_PARAM",
            "max_tokens" if is_deepseek else "max_completion_tokens",
        ),
        "thinkingMode": os.environ.get(
            "MINI_CODE_THINKING",
            "disabled" if is_deepseek else "",
        )
        or None,
        "reasoningEffort": os.environ.get("MINI_CODE_REASONING_EFFORT") or None,
    }


def test_openai_compatible_live_tool_round_trip(tmp_path: Path) -> None:
    """Verify model -> local tool -> model works against the configured live endpoint."""
    (tmp_path / "dsv4-live-marker.txt").write_text("marker", encoding="utf-8")
    registry = ToolRegistry(
        [
            ToolDefinition(
                name="list_files",
                description="List file names in the workspace root.",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
                validator=lambda value: value,
                run=lambda _input, _context: ToolResult(
                    ok=True,
                    output="dsv4-live-marker.txt",
                ),
            )
        ]
    )
    messages = run_agent_turn(
        model=OpenAIModelAdapter(_live_runtime(), registry),
        tools=registry,
        messages=[
            {
                "role": "system",
                "content": "You are a tool-call smoke test. Follow the user's exact instructions.",
            },
            {
                "role": "user",
                "content": (
                    "Call list_files with path '.' exactly once. After receiving the tool result, "
                    "reply with the exact token LIVE_TOOL_OK."
                ),
            },
        ],
        cwd=str(tmp_path),
        max_steps=4,
    )

    assert any(message["role"] == "assistant_tool_call" for message in messages)
    assert any(message["role"] == "tool_result" for message in messages)
    assert messages[-1]["role"] == "assistant"
    assert "LIVE_TOOL_OK" in messages[-1]["content"]

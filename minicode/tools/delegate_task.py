from __future__ import annotations

import os
import uuid
from typing import Any, Callable

from minicode.sub_agents import SubAgentManager
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult


ModelFactory = Callable[[dict[str, Any], ToolRegistry], Any]
ToolFactory = Callable[..., ToolRegistry]


def _definition_catalog(manager: SubAgentManager, *, read_only_only: bool = False) -> str:
    entries = []
    for name in manager.available_agent_types():
        definition = manager.get_definition(name)
        if read_only_only and not definition.is_read_only:
            continue
        entries.append(f"{name}: {definition.description}")
    return "; ".join(entries)


def _format_instance(instance) -> str:
    lines = [
        f"agent_id: {instance.id}",
        f"agent_type: {instance.definition.identifier}",
        f"status: {instance.status}",
        f"model_turns: {instance.turn_count}/{instance.definition.max_turns}",
    ]
    if instance.result:
        lines.extend(["", instance.result])
    if instance.error:
        lines.extend(["", f"error: {instance.error}"])
    return "\n".join(lines)


def _validate(input_data: dict[str, Any], manager: SubAgentManager) -> dict[str, Any]:
    agent_type = str(input_data.get("agent_type", "general")).strip().lower()
    manager.get_definition(agent_type)

    task = input_data.get("task")
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task is required")
    if len(task) > 12_000:
        raise ValueError("task must be at most 12000 characters")

    model = input_data.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError("model must be a non-empty string when provided")
    return {
        "agent_type": agent_type,
        "task": task.strip(),
        "model": model.strip() if isinstance(model, str) else None,
    }


def _default_model_factory(runtime: dict[str, Any], tools: ToolRegistry):
    if os.environ.get("MINI_CODE_MODEL_MODE") == "mock":
        from minicode.mock_model import MockModelAdapter

        return MockModelAdapter()
    if runtime.get("provider") == "openai":
        from minicode.openai_adapter import OpenAIModelAdapter

        return OpenAIModelAdapter(runtime, tools)
    from minicode.anthropic_adapter import AnthropicModelAdapter

    return AnthropicModelAdapter(runtime, tools)


def _default_tool_factory(cwd: str, runtime: dict[str, Any], **kwargs: Any) -> ToolRegistry:
    from minicode.tools import create_default_tool_registry

    return create_default_tool_registry(cwd, runtime=runtime, **kwargs)


def create_delegate_task_tool(
    cwd: str,
    runtime: dict[str, Any],
    *,
    manager: SubAgentManager | None = None,
    model_factory: ModelFactory | None = None,
    tool_factory: ToolFactory | None = None,
) -> ToolDefinition:
    """Create the parent-facing synchronous delegation tool."""
    manager = manager or SubAgentManager(
        parent_session_id=f"session-{uuid.uuid4().hex[:8]}",
        cwd=cwd,
    )
    model_factory = model_factory or _default_model_factory
    tool_factory = tool_factory or _default_tool_factory

    def _run(input_data: dict[str, Any], context) -> ToolResult:
        definition = manager.get_definition(input_data["agent_type"])
        agent_runtime = dict(runtime)
        if definition.is_read_only:
            agent_runtime["mcpServers"] = {}
        selected_model = input_data["model"]
        if selected_model is None and definition.model != "inherit":
            selected_model = definition.model
        if selected_model:
            agent_runtime["model"] = selected_model
        child_tools = tool_factory(cwd, agent_runtime, include_subagents=False)
        agent_tools = manager._filtered_tools(definition, child_tools)
        try:
            model = model_factory(agent_runtime, agent_tools)
            instance = manager.execute_task(
                input_data["agent_type"],
                input_data["task"],
                model=model,
                tools=agent_tools,
                cwd=cwd,
                permissions=context.permissions,
                model_name=selected_model,
            )
        finally:
            child_tools.dispose()

        return ToolResult(ok=instance.status == "completed", output=_format_instance(instance))

    return ToolDefinition(
        name="delegate_task",
        description=(
            "Delegate one bounded task to an isolated sub-agent and wait for its result. "
            "Use a built-in or discovered .claude/agents definition. "
            "The child has isolated context and cannot delegate again. "
            f"Available agents: {_definition_catalog(manager)}"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "agent_type": {
                    "type": "string",
                    "enum": manager.available_agent_types(),
                },
                "task": {"type": "string"},
                "model": {"type": "string"},
            },
            "required": ["agent_type", "task"],
        },
        validator=lambda input_data: _validate(input_data, manager),
        run=_run,
    )


def _validate_control(
    input_data: dict[str, Any],
    manager: SubAgentManager,
) -> dict[str, Any]:
    action = str(input_data.get("action", "status")).strip().lower()
    if action not in {"spawn", "status", "wait", "cancel"}:
        raise ValueError("action must be one of: spawn, status, wait, cancel")
    agent_id = input_data.get("agent_id")
    if action in {"wait", "cancel"} and (not isinstance(agent_id, str) or not agent_id.strip()):
        raise ValueError(f"agent_id is required for {action}")
    timeout = float(input_data.get("timeout_seconds", 30))
    if timeout < 0 or timeout > 60:
        raise ValueError("timeout_seconds must be between 0 and 60")

    parsed: dict[str, Any] = {
        "action": action,
        "agent_id": agent_id.strip() if isinstance(agent_id, str) else None,
        "timeout_seconds": timeout,
    }
    if action == "spawn":
        delegated = _validate(input_data, manager)
        if not manager.get_definition(delegated["agent_type"]).is_read_only:
            raise ValueError(
                "background write-capable agents are disabled because permission prompts are interactive; "
                "use delegate_task for write-capable work"
            )
        parsed.update(delegated)
    return parsed


def create_subagent_control_tool(
    cwd: str,
    runtime: dict[str, Any],
    *,
    manager: SubAgentManager,
    model_factory: ModelFactory | None = None,
    tool_factory: ToolFactory | None = None,
) -> ToolDefinition:
    """Create background sub-agent lifecycle controls sharing one manager."""
    model_factory = model_factory or _default_model_factory
    tool_factory = tool_factory or _default_tool_factory

    def _run(input_data: dict[str, Any], context) -> ToolResult:
        action = input_data["action"]
        if action == "status":
            if input_data["agent_id"]:
                instance = manager.get_agent(input_data["agent_id"])
                if instance is None:
                    return ToolResult(ok=False, output=f"Unknown sub-agent: {input_data['agent_id']}")
                return ToolResult(ok=True, output=_format_instance(instance))
            return ToolResult(ok=True, output=manager.format_agent_status())
        if action == "wait":
            instance = manager.wait_agent(input_data["agent_id"], input_data["timeout_seconds"])
            if instance is None:
                return ToolResult(ok=False, output=f"Unknown sub-agent: {input_data['agent_id']}")
            return ToolResult(ok=True, output=_format_instance(instance))
        if action == "cancel":
            if not manager.cancel_agent(input_data["agent_id"]):
                return ToolResult(ok=False, output=f"Cannot cancel sub-agent: {input_data['agent_id']}")
            instance = manager.get_agent(input_data["agent_id"])
            return ToolResult(ok=True, output=_format_instance(instance))

        definition = manager.get_definition(input_data["agent_type"])
        agent_runtime = dict(runtime)
        agent_runtime["mcpServers"] = {}
        selected_model = input_data["model"]
        if selected_model is None and definition.model != "inherit":
            selected_model = definition.model
        if selected_model:
            agent_runtime["model"] = selected_model

        def resource_factory(_instance):
            child_tools = tool_factory(cwd, agent_runtime, include_subagents=False)
            agent_tools = manager._filtered_tools(definition, child_tools)
            try:
                model = model_factory(agent_runtime, agent_tools)
            except Exception:
                child_tools.dispose()
                raise
            return model, ToolRegistry(
                agent_tools.list(),
                skills=agent_tools.get_skills(),
                mcp_servers=agent_tools.get_mcp_servers(),
                disposer=child_tools.dispose,
            )

        instance = manager.start_task(
            input_data["agent_type"],
            input_data["task"],
            resource_factory=resource_factory,
            cwd=cwd,
            permissions=context.permissions,
            model_name=selected_model,
        )
        return ToolResult(ok=True, output=_format_instance(instance))

    return ToolDefinition(
        name="subagent_control",
        description=(
            "Manage background read-only sub-agents. Spawn built-in or custom read-only workers, "
            "inspect status, wait for results, or cancel work. "
            f"Available read-only agents: {_definition_catalog(manager, read_only_only=True)}"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["spawn", "status", "wait", "cancel"]},
                "agent_id": {"type": "string"},
                "agent_type": {
                    "type": "string",
                    "enum": [
                        name
                        for name in manager.available_agent_types()
                        if manager.get_definition(name).is_read_only
                    ],
                },
                "task": {"type": "string"},
                "model": {"type": "string"},
                "timeout_seconds": {"type": "number", "minimum": 0, "maximum": 60},
            },
            "required": ["action"],
        },
        validator=lambda input_data: _validate_control(input_data, manager),
        run=_run,
    )

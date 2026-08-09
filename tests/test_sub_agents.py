from __future__ import annotations

import threading
from pathlib import Path

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.hooks import HookEvent, HookManager
from minicode.sub_agents import (
    AgentDefinition,
    AgentType,
    SubAgentManager,
    discover_agent_definitions,
)
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools import create_default_tool_registry
from minicode.tools.delegate_task import (
    create_delegate_task_tool,
    create_subagent_control_tool,
)
from minicode.tools.list_files import list_files_tool
from minicode.tools.read_file import read_file_tool
from minicode.tools.write_file import write_file_tool
from minicode.types import AgentStep


class ReadThenAnswerModel:
    def __init__(self) -> None:
        self.seen_messages = []

    def next(self, messages):
        self.seen_messages.append(list(messages))
        if not any(message["role"] == "tool_result" for message in messages):
            return AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "read-1",
                        "toolName": "read_file",
                        "input": {"path": "fact.txt"},
                    }
                ],
            )
        return AgentStep(type="assistant", content="isolated result: marker-42")


class EndlessListModel:
    def __init__(self) -> None:
        self.calls = 0

    def next(self, _messages):
        self.calls += 1
        return AgentStep(
            type="tool_calls",
            calls=[
                {
                    "id": f"list-{self.calls}",
                    "toolName": "list_files",
                    "input": {"path": "."},
                }
            ],
        )


class BrokenModel:
    def next(self, _messages):
        raise RuntimeError("provider unavailable")


class TaskEchoModel:
    def next(self, messages):
        task = next(message["content"] for message in messages if message["role"] == "user")
        return AgentStep(type="assistant", content=f"done: {task}")


class BarrierModel:
    def __init__(self, barrier: threading.Barrier, result: str) -> None:
        self.barrier = barrier
        self.result = result

    def next(self, _messages):
        self.barrier.wait(timeout=2)
        return AgentStep(type="assistant", content=self.result)


class BlockingModel:
    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        self.started = started
        self.release = release

    def next(self, _messages):
        self.started.set()
        self.release.wait(timeout=2)
        return AgentStep(type="assistant", content="should not replace cancelled state")


class ParentDelegationModel:
    def next(self, messages):
        tool_result = next(
            (message["content"] for message in messages if message["role"] == "tool_result"),
            None,
        )
        if tool_result is None:
            return AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "delegate-1",
                        "toolName": "delegate_task",
                        "input": {
                            "agent_type": "explore",
                            "task": "Read fact.txt and report its marker.",
                        },
                    }
                ],
            )
        assert "isolated result: marker-42" in tool_result
        return AgentStep(type="assistant", content="parent received marker-42")


def test_explore_agent_runs_with_isolated_context_and_read_only_tools(tmp_path: Path) -> None:
    (tmp_path / "fact.txt").write_text("marker-42", encoding="utf-8")
    manager = SubAgentManager("parent-session")
    model = ReadThenAnswerModel()
    tools = ToolRegistry([read_file_tool, write_file_tool])

    instance = manager.execute_task(
        AgentType.EXPLORE,
        "Read fact.txt and report its marker.",
        model=model,
        tools=tools,
        cwd=str(tmp_path),
    )

    assert instance.status == "completed"
    assert instance.result == "isolated result: marker-42"
    assert instance.turn_count == 2
    assert [tool.name for tool in manager._filtered_tools(instance.definition, tools).list()] == [
        "read_file"
    ]
    first_request = model.seen_messages[0]
    assert [message["role"] for message in first_request] == ["system", "user"]
    assert "Read fact.txt" in first_request[1]["content"]


def test_subagent_stops_at_definition_turn_limit(tmp_path: Path) -> None:
    manager = SubAgentManager("parent-session")
    definition = AgentDefinition.explore_agent()
    definition.max_turns = 2
    manager.definitions[AgentType.EXPLORE] = definition

    instance = manager.execute_task(
        AgentType.EXPLORE,
        "Keep listing files.",
        model=EndlessListModel(),
        tools=ToolRegistry([list_files_tool]),
        cwd=str(tmp_path),
    )

    assert instance.status == "failed"
    assert instance.turn_count == 2
    assert "maximum tool step limit" in instance.error


def test_subagent_records_model_failure(tmp_path: Path) -> None:
    manager = SubAgentManager("parent-session")

    instance = manager.execute_task(
        AgentType.PLAN,
        "Inspect the architecture.",
        model=BrokenModel(),
        tools=ToolRegistry([read_file_tool]),
        cwd=str(tmp_path),
    )

    assert instance.status == "failed"
    assert instance.turn_count == 1
    assert "provider unavailable" in instance.error


def test_subagent_lifecycle_hooks_fire() -> None:
    hooks = HookManager()
    events = []
    hooks.register(HookEvent.SUBAGENT_START, lambda context: events.append(context.event))
    hooks.register(HookEvent.SUBAGENT_STOP, lambda context: events.append(context.event))
    manager = SubAgentManager("parent-session", hook_manager=hooks)

    instance = manager.spawn_agent(AgentType.PLAN, "Inspect the architecture")
    manager.complete_agent(instance.id, "done")

    assert events == [HookEvent.SUBAGENT_START, HookEvent.SUBAGENT_STOP]


def test_subagent_manager_enforces_active_agent_limit() -> None:
    manager = SubAgentManager("parent-session", max_active_agents=1)
    first = manager.spawn_agent(AgentType.EXPLORE, "Inspect one module")

    with pytest.raises(RuntimeError, match="limit reached"):
        manager.spawn_agent(AgentType.PLAN, "Inspect another module")

    assert manager.cancel_agent(first.id)
    second = manager.spawn_agent(AgentType.PLAN, "Inspect another module")
    assert second.status == "running"


def test_background_subagents_run_concurrently_with_isolated_results(tmp_path: Path) -> None:
    manager = SubAgentManager("parent-session", max_active_agents=2)
    barrier = threading.Barrier(2)

    def resources(instance):
        return BarrierModel(barrier, f"result for {instance.task_description}"), ToolRegistry([])

    first = manager.start_task(
        AgentType.EXPLORE,
        "task one",
        resource_factory=resources,
        cwd=str(tmp_path),
    )
    second = manager.start_task(
        AgentType.PLAN,
        "task two",
        resource_factory=resources,
        cwd=str(tmp_path),
    )

    manager.wait_agent(first.id, timeout=3)
    manager.wait_agent(second.id, timeout=3)

    assert first.status == "completed"
    assert second.status == "completed"
    assert first.result == "result for task one"
    assert second.result == "result for task two"
    assert first.messages is not second.messages


def test_background_subagent_cancellation_wins_completion_race(tmp_path: Path) -> None:
    manager = SubAgentManager("parent-session")
    started = threading.Event()
    release = threading.Event()
    instance = manager.start_task(
        AgentType.PLAN,
        "slow inspection",
        resource_factory=lambda _instance: (
            BlockingModel(started, release),
            ToolRegistry([]),
        ),
        cwd=str(tmp_path),
    )
    assert started.wait(timeout=2)

    assert manager.cancel_agent(instance.id)
    release.set()
    manager.wait_agent(instance.id, timeout=3)

    assert instance.status == "cancelled"
    assert instance.result is None


def test_delegate_task_tool_executes_child_without_recursive_tool(tmp_path: Path) -> None:
    (tmp_path / "fact.txt").write_text("marker-42", encoding="utf-8")
    factory_calls = []

    def tool_factory(cwd, runtime, **kwargs):
        factory_calls.append({"cwd": cwd, "runtime": runtime, **kwargs})
        return ToolRegistry([read_file_tool])

    tool = create_delegate_task_tool(
        str(tmp_path),
        {"provider": "openai", "model": "test-model"},
        model_factory=lambda _runtime, _tools: ReadThenAnswerModel(),
        tool_factory=tool_factory,
    )
    parsed = tool.validator(
        {
            "agent_type": "explore",
            "task": "Read fact.txt and report its marker.",
        }
    )
    result = tool.run(parsed, ToolContext(cwd=str(tmp_path), permissions=None))

    assert result.ok
    assert "status: completed" in result.output
    assert "isolated result: marker-42" in result.output
    assert factory_calls[0]["include_subagents"] is False


def test_parent_agent_loop_receives_isolated_child_result_end_to_end(tmp_path: Path) -> None:
    (tmp_path / "fact.txt").write_text("marker-42", encoding="utf-8")
    manager = SubAgentManager("parent-session")
    delegate = create_delegate_task_tool(
        str(tmp_path),
        {"provider": "openai", "model": "test-model"},
        manager=manager,
        model_factory=lambda _runtime, _tools: ReadThenAnswerModel(),
        tool_factory=lambda _cwd, _runtime, **_kwargs: ToolRegistry([read_file_tool]),
    )

    messages = run_agent_turn(
        model=ParentDelegationModel(),
        tools=ToolRegistry([delegate]),
        messages=[
            {"role": "system", "content": "Delegate focused exploration when requested."},
            {"role": "user", "content": "Find the marker through a child agent."},
        ],
        cwd=str(tmp_path),
        max_steps=4,
    )

    assert messages[-1] == {"role": "assistant", "content": "parent received marker-42"}
    assert len(manager.agents) == 1
    child = next(iter(manager.agents.values()))
    assert child.status == "completed"
    assert child.result == "isolated result: marker-42"
    assert all("Find the marker through a child agent" not in message.get("content", "")
               for message in child.messages)


def test_subagent_control_spawns_waits_and_collects_background_result(tmp_path: Path) -> None:
    manager = SubAgentManager("parent-session")
    control = create_subagent_control_tool(
        str(tmp_path),
        {"provider": "openai", "model": "test-model"},
        manager=manager,
        model_factory=lambda _runtime, _tools: TaskEchoModel(),
        tool_factory=lambda _cwd, _runtime, **_kwargs: ToolRegistry([]),
    )
    context = ToolContext(cwd=str(tmp_path), permissions=None)
    spawn_result = control.run(
        control.validator(
            {
                "action": "spawn",
                "agent_type": "explore",
                "task": "inspect alpha",
            }
        ),
        context,
    )
    agent_id = next(
        line.split(":", 1)[1].strip()
        for line in spawn_result.output.splitlines()
        if line.startswith("agent_id:")
    )
    wait_result = control.run(
        control.validator(
            {"action": "wait", "agent_id": agent_id, "timeout_seconds": 3}
        ),
        context,
    )

    assert spawn_result.ok
    assert wait_result.ok
    assert "status: completed" in wait_result.output
    assert "done: inspect alpha" in wait_result.output
    manager.shutdown()


def test_background_model_factory_failure_releases_tools_and_reports_failure(
    tmp_path: Path,
) -> None:
    manager = SubAgentManager("parent-session")
    disposed = threading.Event()

    def broken_model_factory(_runtime, _tools):
        raise RuntimeError("adapter creation failed")

    control = create_subagent_control_tool(
        str(tmp_path),
        {"provider": "openai", "model": "test-model"},
        manager=manager,
        model_factory=broken_model_factory,
        tool_factory=lambda _cwd, _runtime, **_kwargs: ToolRegistry(
            [read_file_tool],
            disposer=disposed.set,
        ),
    )
    context = ToolContext(cwd=str(tmp_path), permissions=None)
    spawned = control.run(
        control.validator(
            {"action": "spawn", "agent_type": "explore", "task": "inspect files"}
        ),
        context,
    )
    agent_id = next(
        line.split(":", 1)[1].strip()
        for line in spawned.output.splitlines()
        if line.startswith("agent_id:")
    )
    instance = manager.wait_agent(agent_id, timeout=3)

    assert instance is not None
    assert instance.status == "failed"
    assert "adapter creation failed" in instance.error
    assert disposed.wait(timeout=1)


def test_subagent_control_rejects_background_general_agent(tmp_path: Path) -> None:
    manager = SubAgentManager("parent-session")
    control = create_subagent_control_tool(
        str(tmp_path),
        {"provider": "openai", "model": "test-model"},
        manager=manager,
    )

    with pytest.raises(ValueError, match="background write-capable agents are disabled"):
        control.validator(
            {
                "action": "spawn",
                "agent_type": "general",
                "task": "change a file",
            }
        )


def test_default_registry_only_exposes_delegation_when_runtime_is_configured(tmp_path: Path) -> None:
    _write_agent(
        tmp_path,
        "project-reader",
        """---
name: project-reader
description: Reads project files
tools: Read, Grep, Glob
---
Inspect project files and return evidence.
""",
    )
    without_runtime = create_default_tool_registry(str(tmp_path), runtime=None)
    with_runtime = create_default_tool_registry(
        str(tmp_path),
        runtime={
            "provider": "openai",
            "model": "test-model",
            "mcpServers": {},
        },
    )
    try:
        assert without_runtime.find("delegate_task") is None
        assert with_runtime.find("delegate_task") is not None
        assert with_runtime.find("subagent_control") is not None
        assert "project-reader" in with_runtime.find("delegate_task").input_schema[
            "properties"
        ]["agent_type"]["enum"]
    finally:
        without_runtime.dispose()
        with_runtime.dispose()


def _write_agent(root: Path, name: str, content: str) -> Path:
    agents_dir = root / ".claude" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_custom_agent_discovery_maps_tools_and_project_overrides_user(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    _write_agent(
        home,
        "reviewer",
        """---
name: reviewer
description: User reviewer
tools: Read
---
Use the user instructions.
""",
    )
    project_path = _write_agent(
        project,
        "reviewer",
        """---
name: reviewer
description: Project reviewer
tools:
  - Read
  - Grep
  - Glob
disallowedTools: WebFetch
model: deepseek-chat
maxTurns: 7
---
Review dependencies and return evidence.
""",
    )

    definitions, errors = discover_agent_definitions(project, home=home)
    reviewer = definitions["reviewer"]

    assert errors == []
    assert reviewer.source == "project"
    assert reviewer.path == str(project_path)
    assert reviewer.allowed_tools == ["read_file", "grep_files", "list_files"]
    assert reviewer.disallowed_tools == ["web_fetch"]
    assert reviewer.model == "deepseek-chat"
    assert reviewer.max_turns == 7
    assert reviewer.is_read_only
    assert reviewer.system_prompt_template == "Review dependencies and return evidence."


def test_invalid_custom_agent_is_reported_without_breaking_builtins(tmp_path: Path) -> None:
    _write_agent(
        tmp_path,
        "broken",
        """---
name: Broken Name
description: Invalid name
---
Prompt.
""",
    )

    manager = SubAgentManager("parent-session", cwd=tmp_path)

    assert manager.available_agent_types() == ["explore", "general", "plan"]
    assert len(manager.definition_errors) == 1
    assert "lowercase letters" in manager.definition_errors[0]


def test_delegate_task_executes_discovered_custom_agent_with_filtered_tools(tmp_path: Path) -> None:
    (tmp_path / "fact.txt").write_text("marker-42", encoding="utf-8")
    _write_agent(
        tmp_path,
        "fact-reader",
        """---
name: fact-reader
description: Reads facts without writing
tools: Read, Grep, Glob
maxTurns: 4
---
Read the requested fact and report only grounded evidence.
""",
    )
    manager = SubAgentManager("parent-session", cwd=tmp_path)
    observed_tools = []

    def model_factory(_runtime, tools):
        observed_tools.extend(tool.name for tool in tools.list())
        return ReadThenAnswerModel()

    tool = create_delegate_task_tool(
        str(tmp_path),
        {"provider": "openai", "model": "test-model"},
        manager=manager,
        model_factory=model_factory,
        tool_factory=lambda _cwd, _runtime, **_kwargs: ToolRegistry(
            [read_file_tool, write_file_tool]
        ),
    )
    parsed = tool.validator(
        {"agent_type": "fact-reader", "task": "Read fact.txt and report its marker."}
    )
    result = tool.run(parsed, ToolContext(cwd=str(tmp_path), permissions=None))

    assert "fact-reader" in tool.input_schema["properties"]["agent_type"]["enum"]
    assert result.ok
    assert "agent_type: fact-reader" in result.output
    assert observed_tools == ["read_file"]
    instance = next(iter(manager.agents.values()))
    assert [tool.name for tool in manager._filtered_tools(
        instance.definition,
        ToolRegistry([read_file_tool, write_file_tool]),
    ).list()] == ["read_file"]


def test_background_control_accepts_only_read_only_custom_agents(tmp_path: Path) -> None:
    _write_agent(
        tmp_path,
        "researcher",
        """---
name: researcher
description: Read-only research
permissionMode: plan
---
Research the task without changing files.
""",
    )
    _write_agent(
        tmp_path,
        "implementer",
        """---
name: implementer
description: Can implement changes
tools: Read, Write
---
Implement the requested change.
""",
    )
    manager = SubAgentManager("parent-session", cwd=tmp_path)
    control = create_subagent_control_tool(
        str(tmp_path),
        {"provider": "openai", "model": "test-model"},
        manager=manager,
    )

    parsed = control.validator(
        {"action": "spawn", "agent_type": "researcher", "task": "inspect code"}
    )
    assert parsed["agent_type"] == "researcher"
    assert "researcher" in control.input_schema["properties"]["agent_type"]["enum"]
    assert "implementer" not in control.input_schema["properties"]["agent_type"]["enum"]
    with pytest.raises(ValueError, match="write-capable"):
        control.validator(
            {"action": "spawn", "agent_type": "implementer", "task": "change code"}
        )

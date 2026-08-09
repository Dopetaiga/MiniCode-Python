"""Lightweight sub-agent system for MiniCode Python.

Inspired by Claude Code's AgentTool and coordinator/ system.
Provides specialized agents for different task types:
- Explore: Read-only, fast, for codebase exploration
- Plan: Read-only, thorough, for context gathering
- General-purpose: Full tools, for complex multi-step tasks

Each agent runs in isolation with its own context window,
preventing main conversation context from bloating.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from minicode.context_manager import ContextManager
from minicode.state import AppState, Store
from minicode.tooling import ToolRegistry

if TYPE_CHECKING:
    from minicode.hooks import HookManager
    from minicode.permissions import PermissionManager


READ_ONLY_TOOL_NAMES = {
    "read_file",
    "list_files",
    "grep_files",
    "find_symbols",
    "find_references",
    "get_ast_info",
    "code_review",
    "file_tree",
    "diff_viewer",
    "web_fetch",
    "web_search",
}

SUBAGENT_ERROR_PREFIXES = (
    "Model API error",
    "Model API timeout",
    "Network error",
    "Reached the maximum tool step limit",
)

CLAUDE_TOOL_ALIASES = {
    "read": "read_file",
    "glob": "list_files",
    "grep": "grep_files",
    "bash": "run_command",
    "write": "write_file",
    "edit": "edit_file",
    "webfetch": "web_fetch",
    "websearch": "web_search",
    "notebookedit": "notebook_edit",
}

_AGENT_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


# ---------------------------------------------------------------------------
# Agent types
# ---------------------------------------------------------------------------

class AgentType(str, Enum):
    """Sub-agent types (inspired by Claude Code's built-in agents)."""
    EXPLORE = "explore"           # Read-only, fast (like Haiku)
    PLAN = "plan"                 # Read-only, thorough (like Sonnet in plan mode)
    GENERAL = "general"           # Full tools, complex tasks


@dataclass
class AgentDefinition:
    """Sub-agent definition.
    
    Inspired by Claude Code's agent definitions with custom system prompts,
    tool whitelists, and model selection.
    """
    type: AgentType
    name: str
    description: str
    system_prompt_template: str
    slug: str | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] = field(default_factory=list)
    model: str = "inherit"  # inherit from parent or specific model
    max_turns: int = 10
    is_read_only: bool = False
    source: str = "built_in"
    path: str | None = None

    @property
    def identifier(self) -> str:
        return self.slug or self.type.value
    
    @classmethod
    def explore_agent(cls) -> "AgentDefinition":
        """Create Explore agent - fast, read-only exploration."""
        return cls(
            type=AgentType.EXPLORE,
            name="Explore",
            description="Fast, read-only agent for codebase exploration and search",
            system_prompt_template=(
                "You are an exploration agent. Your job is to quickly search and "
                "understand codebases. You should be fast and focused on finding "
                "relevant files and understanding structure. "
                "You can only use read-only tools."
            ),
            allowed_tools=["read_file", "list_files", "grep_files"],
            is_read_only=True,
            max_turns=5,
        )
    
    @classmethod
    def plan_agent(cls) -> "AgentDefinition":
        """Create Plan agent - thorough context gathering."""
        return cls(
            type=AgentType.PLAN,
            name="Plan",
            description="Thorough agent for gathering context and understanding code",
            system_prompt_template=(
                "You are a planning agent. Your job is to thoroughly understand "
                "the codebase and task before acting. Read multiple files, trace "
                "code paths, and build a complete mental model. "
                "You can only use read-only tools."
            ),
            allowed_tools=["read_file", "list_files", "grep_files"],
            is_read_only=True,
            max_turns=8,
        )
    
    @classmethod
    def general_agent(cls) -> "AgentDefinition":
        """Create General-purpose agent - full capabilities."""
        return cls(
            type=AgentType.GENERAL,
            name="General",
            description="Full-featured agent for complex multi-step tasks",
            system_prompt_template=(
                "You are a general-purpose coding agent. You can read, write, "
                "and modify code. Follow best practices and explain your changes. "
                "Break complex tasks into smaller steps."
            ),
            disallowed_tools=["ask_user", "delegate_task"],
            is_read_only=False,
            max_turns=15,
        )


def _strip_yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _parse_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    """Parse the small YAML subset used by common Claude agent definitions."""
    normalized = markdown.replace("\r\n", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing YAML frontmatter")
    try:
        closing = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration as error:
        raise ValueError("unterminated YAML frontmatter") from error

    values: dict[str, Any] = {}
    active_list: str | None = None
    for raw_line in lines[1:closing]:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("-") and active_list is not None:
            values[active_list].append(_strip_yaml_scalar(stripped[1:]))
            continue
        if ":" not in raw_line:
            raise ValueError(f"invalid frontmatter line: {stripped}")
        key, raw_value = raw_line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        active_list = None
        if not raw_value:
            values[key] = []
            active_list = key
        elif raw_value.startswith("[") and raw_value.endswith("]"):
            values[key] = [
                _strip_yaml_scalar(item)
                for item in raw_value[1:-1].split(",")
                if item.strip()
            ]
        else:
            values[key] = _strip_yaml_scalar(raw_value)
    return values, "\n".join(lines[closing + 1 :]).strip()


def _tool_names(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_names = value.split(",")
    elif isinstance(value, list):
        raw_names = value
    else:
        raise ValueError(f"{field_name} must be a comma-separated string or list")
    result: list[str] = []
    for raw_name in raw_names:
        name = str(raw_name).strip()
        if not name:
            continue
        normalized = CLAUDE_TOOL_ALIASES.get(name.lower(), name)
        if normalized not in result:
            result.append(normalized)
    return result


def load_agent_definition(path: str | Path, source: str) -> AgentDefinition:
    """Load one Claude-compatible Markdown agent definition."""
    file_path = Path(path)
    metadata, prompt = _parse_frontmatter(file_path.read_text(encoding="utf-8"))
    name = str(metadata.get("name", "")).strip()
    description = str(metadata.get("description", "")).strip()
    if not _AGENT_NAME_PATTERN.fullmatch(name):
        raise ValueError("name must contain only lowercase letters, digits, and hyphens")
    if not description:
        raise ValueError("description is required")
    if not prompt:
        raise ValueError("agent prompt body is required")

    tools_present = "tools" in metadata
    allowed_tools = _tool_names(metadata.get("tools"), "tools") if tools_present else None
    disallowed_tools = _tool_names(metadata.get("disallowedTools"), "disallowedTools")
    try:
        max_turns = int(metadata.get("maxTurns", 10))
    except (TypeError, ValueError) as error:
        raise ValueError("maxTurns must be an integer") from error
    if not 1 <= max_turns <= 50:
        raise ValueError("maxTurns must be between 1 and 50")

    permission_mode = str(metadata.get("permissionMode", "default")).strip()
    inferred_read_only = allowed_tools is not None and all(
        tool in READ_ONLY_TOOL_NAMES for tool in allowed_tools
    )
    is_read_only = permission_mode == "plan" or inferred_read_only
    if permission_mode == "plan" and allowed_tools is None:
        allowed_tools = sorted(READ_ONLY_TOOL_NAMES)

    return AgentDefinition(
        type=AgentType.EXPLORE if is_read_only else AgentType.GENERAL,
        slug=name,
        name=name,
        description=description,
        system_prompt_template=prompt,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        model=str(metadata.get("model", "inherit")).strip() or "inherit",
        max_turns=max_turns,
        is_read_only=is_read_only,
        source=source,
        path=str(file_path),
    )


def discover_agent_definitions(
    cwd: str | Path,
    *,
    home: str | Path | None = None,
) -> tuple[dict[str, AgentDefinition], list[str]]:
    """Load user definitions first, then let project definitions override them."""
    roots = [
        (Path(home) if home is not None else Path.home()) / ".claude" / "agents",
        Path(cwd) / ".claude" / "agents",
    ]
    sources = ["user", "project"]
    definitions: dict[str, AgentDefinition] = {}
    errors: list[str] = []
    for root, source in zip(roots, sources):
        if not root.exists():
            continue
        for path in sorted(root.glob("*.md")):
            try:
                definition = load_agent_definition(path, source)
            except (OSError, UnicodeError, ValueError) as error:
                errors.append(f"{path}: {error}")
                continue
            definitions[definition.identifier] = definition
    return definitions, errors


# ---------------------------------------------------------------------------
# Agent instance (runtime)
# ---------------------------------------------------------------------------

@dataclass
class AgentInstance:
    """Running agent instance."""
    id: str
    definition: AgentDefinition
    parent_session_id: str
    task_description: str
    model: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    context_manager: ContextManager | None = None
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    status: str = "running"  # running, completed, failed, cancelled
    result: str | None = None
    error: str | None = None
    turn_count: int = 0


class _CountingModel:
    """Count model invocations without changing the wrapped adapter contract."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls = 0

    def next(self, messages: list[dict[str, Any]]):
        self.calls += 1
        return self.inner.next(messages)


# ---------------------------------------------------------------------------
# Sub-agent manager
# ---------------------------------------------------------------------------

class SubAgentManager:
    """Manages sub-agent lifecycle.
    
    Inspired by Claude Code's coordinator/ system.
    """
    
    def __init__(
        self,
        parent_session_id: str,
        app_state: Store[AppState] | None = None,
        hook_manager: "HookManager | None" = None,
        max_active_agents: int = 4,
        cwd: str | Path | None = None,
        definitions: dict[str, AgentDefinition] | None = None,
    ):
        self.parent_session_id = parent_session_id
        self.app_state = app_state
        self.hook_manager = hook_manager
        self.max_active_agents = max(1, max_active_agents)
        self._lock = threading.RLock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._shutdown = False
        self.agents: dict[str, AgentInstance] = {}
        self.definitions: dict[str, AgentDefinition] = {
            AgentType.EXPLORE.value: AgentDefinition.explore_agent(),
            AgentType.PLAN.value: AgentDefinition.plan_agent(),
            AgentType.GENERAL.value: AgentDefinition.general_agent(),
        }
        self.definition_errors: list[str] = []
        if cwd is not None:
            discovered, self.definition_errors = discover_agent_definitions(cwd)
            self.definitions.update(discovered)
        if definitions:
            self.definitions.update(definitions)
    
    def get_definition(self, agent_type: AgentType | str) -> AgentDefinition:
        """Get agent definition."""
        key = agent_type.value if isinstance(agent_type, AgentType) else str(agent_type).strip().lower()
        try:
            return self.definitions[key]
        except KeyError as error:
            choices = ", ".join(sorted(self.definitions))
            raise ValueError(f"Unknown sub-agent type '{key}'. Available: {choices}") from error

    def available_agent_types(self) -> list[str]:
        return sorted(self.definitions)

    def _fire_hook(self, event_name: str, **data: Any) -> None:
        if self.hook_manager is None:
            return
        from minicode.hooks import HookEvent

        self.hook_manager.fire_sync(getattr(HookEvent, event_name), **data)

    @staticmethod
    def _filtered_tools(
        definition: AgentDefinition,
        tools: ToolRegistry,
    ) -> ToolRegistry:
        allowed = set(definition.allowed_tools or [])
        disallowed = set(definition.disallowed_tools) | {
            "delegate_task",
            "subagent_control",
        }
        selected = []
        for tool in tools.list():
            if tool.name in disallowed:
                continue
            if definition.allowed_tools is not None and tool.name not in allowed:
                continue
            if definition.is_read_only and tool.name not in READ_ONLY_TOOL_NAMES:
                continue
            selected.append(tool)
        return ToolRegistry(
            selected,
            skills=tools.get_skills(),
            mcp_servers=tools.get_mcp_servers(),
        )
    
    def spawn_agent(
        self,
        agent_type: AgentType | str,
        task_description: str,
        model: str | None = None,
    ) -> AgentInstance:
        """Spawn a new sub-agent.
        
        Args:
            agent_type: Type of agent to spawn
            task_description: Task description for the agent
            model: Optional model override
        
        Returns:
            AgentInstance
        """
        normalized_task = task_description.strip()
        if not normalized_task:
            raise ValueError("Sub-agent task description cannot be empty")
        with self._lock:
            if self._shutdown:
                raise RuntimeError("Sub-agent manager is shutting down")
            if len(self.get_active_agents()) >= self.max_active_agents:
                raise RuntimeError(
                    f"Sub-agent limit reached ({self.max_active_agents} active agents)"
                )
            definition = self.get_definition(agent_type)
            agent_id = f"agent-{uuid.uuid4().hex[:8]}"
            context_manager = ContextManager(
                model=model or (definition.model if definition.model != "inherit" else "default"),
            )
            system_message = {
                "role": "system",
                "content": definition.system_prompt_template,
            }
            instance = AgentInstance(
                id=agent_id,
                definition=definition,
                parent_session_id=self.parent_session_id,
                task_description=normalized_task,
                model=model,
                messages=[system_message],
                context_manager=context_manager,
            )
            self.agents[agent_id] = instance
            self._cancel_events[agent_id] = threading.Event()
        self._fire_hook(
            "SUBAGENT_START",
            agent_id=agent_id,
            agent_type=definition.identifier,
            task=normalized_task,
            parent_session_id=self.parent_session_id,
        )
        return instance
    
    def add_message(self, agent_id: str, message: dict[str, Any]) -> bool:
        """Add message to agent conversation."""
        instance = self.agents.get(agent_id)
        if not instance or instance.status != "running":
            return False
        
        instance.messages.append(message)
        
        # Update context
        if instance.context_manager:
            instance.context_manager.add_message(message)
        
        return True

    def execute_agent(
        self,
        agent_id: str,
        *,
        model: Any,
        tools: ToolRegistry,
        cwd: str,
        permissions: "PermissionManager | None" = None,
    ) -> AgentInstance:
        """Run one isolated sub-agent to completion synchronously.

        The sub-agent receives only its specialization prompt and delegated task,
        not the parent's conversation history. Its tools are filtered by the
        definition and nested delegation is always disabled.
        """
        instance = self.agents.get(agent_id)
        if instance is None:
            raise KeyError(f"Unknown sub-agent: {agent_id}")
        if instance.status != "running":
            return instance

        system_prompt = "\n\n".join(
            [
                instance.definition.system_prompt_template,
                f"Current workspace: {cwd}",
                "Work only on the delegated task. You do not have the parent's conversation history.",
                "Do not delegate again and do not ask the user questions. If blocked, report the exact blocker.",
                "Use available tools as needed, then return a concise final result with concrete evidence.",
            ]
        )
        instance.messages[0] = {"role": "system", "content": system_prompt}
        if not any(message.get("role") == "user" for message in instance.messages):
            instance.messages.append({"role": "user", "content": instance.task_description})

        selected_tools = self._filtered_tools(instance.definition, tools)
        counting_model = _CountingModel(model)
        cancel_event = self._cancel_events[agent_id]
        if instance.context_manager is not None:
            configured_model = instance.model
            if configured_model is None:
                runtime = getattr(model, "runtime", {})
                configured_model = runtime.get("model") if isinstance(runtime, dict) else None
            instance.context_manager.update_model(configured_model or "default")

        from minicode.agent_loop import run_agent_turn

        try:
            messages = run_agent_turn(
                model=counting_model,
                tools=selected_tools,
                messages=instance.messages,
                cwd=cwd,
                permissions=permissions,
                max_steps=instance.definition.max_turns,
                context_manager=instance.context_manager,
                should_cancel=cancel_event.is_set,
            )
            instance.messages = messages
            instance.turn_count = counting_model.calls
            if instance.context_manager is not None:
                instance.context_manager.messages = messages
            result = next(
                (
                    message.get("content", "")
                    for message in reversed(messages)
                    if message.get("role") == "assistant"
                ),
                "",
            ).strip()
            if instance.status == "cancelled":
                return instance
            if not result:
                self.fail_agent(agent_id, "Sub-agent returned no final result")
            elif result.startswith(SUBAGENT_ERROR_PREFIXES):
                self.fail_agent(agent_id, result)
            else:
                self.complete_agent(agent_id, result)
        except (KeyboardInterrupt, SystemExit):
            self.cancel_agent(agent_id)
            raise
        except Exception as error:  # noqa: BLE001
            instance.turn_count = counting_model.calls
            self.fail_agent(agent_id, f"{type(error).__name__}: {error}")
        return instance

    def execute_task(
        self,
        agent_type: AgentType | str,
        task_description: str,
        *,
        model: Any,
        tools: ToolRegistry,
        cwd: str,
        permissions: "PermissionManager | None" = None,
        model_name: str | None = None,
    ) -> AgentInstance:
        """Spawn and synchronously execute a delegated task."""
        instance = self.spawn_agent(agent_type, task_description, model=model_name)
        return self.execute_agent(
            instance.id,
            model=model,
            tools=tools,
            cwd=cwd,
            permissions=permissions,
        )

    def start_task(
        self,
        agent_type: AgentType | str,
        task_description: str,
        *,
        resource_factory: Any,
        cwd: str,
        permissions: "PermissionManager | None" = None,
        model_name: str | None = None,
    ) -> AgentInstance:
        """Start a delegated task on a daemon worker thread."""
        instance = self.spawn_agent(agent_type, task_description, model=model_name)

        def worker() -> None:
            child_tools: ToolRegistry | None = None
            try:
                model, child_tools = resource_factory(instance)
                self.execute_agent(
                    instance.id,
                    model=model,
                    tools=child_tools,
                    cwd=cwd,
                    permissions=permissions,
                )
            except Exception as error:  # noqa: BLE001
                self.fail_agent(instance.id, f"{type(error).__name__}: {error}")
            finally:
                if child_tools is not None:
                    child_tools.dispose()

        thread = threading.Thread(
            target=worker,
            name=f"minicode-{instance.id}",
            daemon=True,
        )
        with self._lock:
            self._threads[instance.id] = thread
        thread.start()
        return instance

    def wait_agent(self, agent_id: str, timeout: float | None = None) -> AgentInstance | None:
        """Wait for a background sub-agent, returning its latest state."""
        with self._lock:
            instance = self.agents.get(agent_id)
            thread = self._threads.get(agent_id)
        if instance is None:
            return None
        if thread is not None:
            thread.join(timeout=max(0.0, timeout) if timeout is not None else None)
        return instance

    def shutdown(self, timeout: float = 1.0) -> None:
        """Cancel active workers and wait briefly for cooperative shutdown."""
        with self._lock:
            self._shutdown = True
            active_ids = [agent.id for agent in self.get_active_agents()]
            threads = list(self._threads.values())
        for agent_id in active_ids:
            self.cancel_agent(agent_id)
        deadline = time.monotonic() + max(0.0, timeout)
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(remaining)
    
    def complete_agent(self, agent_id: str, result: str) -> bool:
        """Mark agent as completed with result."""
        with self._lock:
            instance = self.agents.get(agent_id)
            if not instance or instance.status != "running":
                return False
            instance.status = "completed"
            instance.result = result
            instance.completed_at = time.time()
        self._fire_hook(
            "SUBAGENT_STOP",
            agent_id=agent_id,
            agent_type=instance.definition.identifier,
            status=instance.status,
            result=result,
        )
        return True
    
    def fail_agent(self, agent_id: str, error: str) -> bool:
        """Mark agent as failed."""
        with self._lock:
            instance = self.agents.get(agent_id)
            if not instance or instance.status != "running":
                return False
            instance.status = "failed"
            instance.error = error
            instance.completed_at = time.time()
        self._fire_hook(
            "SUBAGENT_STOP",
            agent_id=agent_id,
            agent_type=instance.definition.identifier,
            status=instance.status,
            error=error,
        )
        return True
    
    def cancel_agent(self, agent_id: str) -> bool:
        """Cancel a running agent."""
        with self._lock:
            instance = self.agents.get(agent_id)
            if not instance or instance.status != "running":
                return False
            self._cancel_events[agent_id].set()
            instance.status = "cancelled"
            instance.completed_at = time.time()
        self._fire_hook(
            "SUBAGENT_STOP",
            agent_id=agent_id,
            agent_type=instance.definition.identifier,
            status=instance.status,
        )
        return True
    
    def get_agent(self, agent_id: str) -> AgentInstance | None:
        """Get agent instance by ID."""
        with self._lock:
            return self.agents.get(agent_id)
    
    def get_active_agents(self) -> list[AgentInstance]:
        """Get all running agents."""
        with self._lock:
            return [
                agent for agent in self.agents.values()
                if agent.status == "running"
            ]
    
    def format_agent_status(self) -> str:
        """Format status report for all agents."""
        with self._lock:
            instances = list(self.agents.items())
        if not instances:
            lines = ["No sub-agents spawned."]
            if self.definition_errors:
                lines.append(f"Definition errors: {len(self.definition_errors)}")
                lines.extend(f"- {error}" for error in self.definition_errors)
            return "\n".join(lines)
        
        lines = ["Sub-Agents Status", "=" * 50, ""]
        
        for agent_id, instance in instances:
            status_icon = {
                "running": "◐",
                "completed": "✓",
                "failed": "✗",
                "cancelled": "⊘",
            }.get(instance.status, "?")
            
            duration = time.time() - instance.started_at
            if instance.completed_at:
                duration = instance.completed_at - instance.started_at
            
            lines.extend([
                f"{status_icon} {instance.definition.name} ({agent_id})",
                f"  Task: {instance.task_description[:60]}",
                f"  Status: {instance.status}",
                f"  Turns: {instance.turn_count}/{instance.definition.max_turns}",
                f"  Duration: {duration:.0f}s",
            ])
            
            if instance.result:
                result_preview = instance.result[:100]
                lines.append(f"  Result: {result_preview}...")
            
            if instance.error:
                lines.append(f"  Error: {instance.error}")
            
            lines.append("")
        
        active = len(self.get_active_agents())
        lines.append(f"Active: {active} | Total: {len(instances)}")
        if self.definition_errors:
            lines.append(f"Definition errors: {len(self.definition_errors)}")
            lines.extend(f"- {error}" for error in self.definition_errors)
        
        return "\n".join(lines)
    
    def compile_result_summary(self, agent_id: str) -> str:
        """Compile a summary of agent execution for parent context."""
        instance = self.agents.get(agent_id)
        if not instance:
            return f"Agent {agent_id} not found."
        
        lines = [
            f"[Sub-agent {instance.definition.name} completed]",
            f"  Turns: {instance.turn_count}",
            f"  Status: {instance.status}",
        ]
        
        if instance.result:
            lines.append(f"  Result: {instance.result[:200]}")
        
        if instance.context_manager:
            stats = instance.context_manager.get_stats()
            lines.append(f"  Tokens used: {stats.total_tokens:,}")
        
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------

def should_use_sub_agent(
    task_complexity: str,
    available_context: float,
) -> bool:
    """Decide if a task should be delegated to a sub-agent.
    
    Args:
        task_complexity: "simple", "moderate", "complex"
        available_context: Percentage of context window available
    
    Returns:
        True if should use sub-agent
    """
    # Use sub-agent for complex tasks or when context is limited
    if task_complexity == "complex":
        return True
    if task_complexity == "moderate" and available_context < 50:
        return True
    return False


def choose_agent_type(task_description: str) -> AgentType:
    """Choose appropriate agent type based on task.
    
    Args:
        task_description: User's task description
    
    Returns:
        Recommended AgentType
    """
    desc_lower = task_description.lower()
    
    # Exploration tasks
    exploration_keywords = ["explore", "search", "find", "understand", "explain"]
    if any(kw in desc_lower for kw in exploration_keywords):
        return AgentType.EXPLORE
    
    # Planning/context-gathering tasks
    planning_keywords = ["plan", "analyze", "review", "audit", "survey"]
    if any(kw in desc_lower for kw in planning_keywords):
        return AgentType.PLAN
    
    # Default to general-purpose
    return AgentType.GENERAL

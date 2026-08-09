"""Run MiniCode AgentBench v1.2 with offline oracle checks or a live model.

The default mode is free and deterministic: it validates task schemas and proves
that each hidden verifier rejects the broken fixture and accepts the oracle
solution. API calls happen only when ``--live`` is explicitly supplied.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = Path(__file__).with_name("minicode_agentbench_v1.jsonl")
DEFAULT_OUTPUT = PROJECT_ROOT / "test_results" / "minicode-agentbench-v1-2.json"
BENCHMARK_NAME = "MiniCode AgentBench v1.2"
VERIFY_TIMEOUT_SECONDS = 30
MAX_CAPTURE_CHARS = 8_000
sys.path.insert(0, str(PROJECT_ROOT))


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    cases = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not cases:
        raise ValueError("Benchmark must contain at least one case")

    ids = [case.get("id") for case in cases]
    if any(not isinstance(case_id, str) or not case_id for case_id in ids):
        raise ValueError("Every case must have a non-empty string id")
    if len(ids) != len(set(ids)):
        raise ValueError("Case ids must be unique")

    valid_difficulties = {"easy", "medium", "hard"}
    for case in cases:
        case_id = case["id"]
        if not isinstance(case.get("prompt"), str) or not case["prompt"].strip():
            raise ValueError(f"{case_id}: prompt must be a non-empty string")
        if not isinstance(case.get("category"), str) or not case["category"]:
            raise ValueError(f"{case_id}: category must be a non-empty string")
        if case.get("difficulty") not in valid_difficulties:
            raise ValueError(f"{case_id}: difficulty must be easy, medium, or hard")
        if not isinstance(case.get("files", {}), dict):
            raise ValueError(f"{case_id}: files must be an object")
        if not isinstance(case.get("expect"), dict):
            raise ValueError(f"{case_id}: expect must be an object")
        if case["expect"].get("verify") is True:
            if not case.get("hidden_files") or not case.get("solution_files"):
                raise ValueError(
                    f"{case_id}: verified tasks need hidden_files and solution_files"
                )
        for field in ("files", "hidden_files", "solution_files"):
            for relative_path, content in case.get(field, {}).items():
                candidate = Path(relative_path)
                if candidate.is_absolute() or ".." in candidate.parts:
                    raise ValueError(f"{case_id}: unsafe path in {field}: {relative_path}")
                if not isinstance(content, str):
                    raise ValueError(f"{case_id}: {field} values must be strings")
    return cases


def select_cases(cases: list[dict[str, Any]], case_ids: list[str] | None) -> list[dict[str, Any]]:
    if not case_ids:
        return cases
    requested = set(case_ids)
    selected = [case for case in cases if case["id"] in requested]
    missing = requested - {case["id"] for case in selected}
    if missing:
        raise ValueError(f"Unknown case ids: {', '.join(sorted(missing))}")
    return selected


def _write_files(workspace: Path, files: dict[str, str]) -> None:
    for relative_path, content in files.items():
        target = workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _verifier_environment(workspace: Path) -> dict[str, str]:
    environment = os.environ.copy()
    for name in list(environment):
        upper_name = name.upper()
        if upper_name.endswith(("_API_KEY", "_AUTH_TOKEN")) or "PASSWORD" in upper_name:
            environment.pop(name, None)
    isolated_home = workspace / ".home"
    isolated_temp = workspace / ".tmp"
    isolated_home.mkdir(exist_ok=True)
    isolated_temp.mkdir(exist_ok=True)
    environment.update(
        {
            "HOME": str(isolated_home),
            "USERPROFILE": str(isolated_home),
            "TEMP": str(isolated_temp),
            "TMP": str(isolated_temp),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": str(workspace),
        }
    )
    return environment


def _run_hidden_verifier(workspace: Path) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--basetemp",
            str(workspace / ".pytest-tmp"),
            "hidden_tests",
        ],
        cwd=workspace,
        env=_verifier_environment(workspace),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=VERIFY_TIMEOUT_SECONDS,
    )
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    return {
        "passed": completed.returncode == 0,
        "return_code": completed.returncode,
        "duration_seconds": round(time.perf_counter() - started, 4),
        "output": output[:MAX_CAPTURE_CHARS],
        "output_truncated": len(output) > MAX_CAPTURE_CHARS,
    }


def validate_oracles(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases:
        if case["expect"].get("verify") is not True:
            results.append(
                {
                    "id": case["id"],
                    "status": "schema_only",
                    "meaningful": True,
                    "oracle_passed": None,
                }
            )
            continue

        with tempfile.TemporaryDirectory(prefix=f"agentbench-baseline-{case['id']}-") as raw:
            workspace = Path(raw)
            _write_files(workspace, case.get("files", {}))
            _write_files(workspace, case["hidden_files"])
            baseline = _run_hidden_verifier(workspace)

        with tempfile.TemporaryDirectory(prefix=f"agentbench-oracle-{case['id']}-") as raw:
            workspace = Path(raw)
            _write_files(workspace, case.get("files", {}))
            _write_files(workspace, case["solution_files"])
            _write_files(workspace, case["hidden_files"])
            oracle = _run_hidden_verifier(workspace)

        meaningful = not baseline["passed"]
        results.append(
            {
                "id": case["id"],
                "status": "passed" if meaningful and oracle["passed"] else "failed",
                "meaningful": meaningful,
                "oracle_passed": oracle["passed"],
                "baseline_return_code": baseline["return_code"],
                "oracle_return_code": oracle["return_code"],
                "baseline_output": baseline["output"],
                "oracle_output": oracle["output"],
            }
        )
    return results


def readiness(cases: list[dict[str, Any]]) -> dict[str, Any]:
    state: dict[str, Any] = {
        "case_count": len(cases),
        "categories": dict(Counter(case["category"] for case in cases)),
        "difficulties": dict(Counter(case["difficulty"] for case in cases)),
        "hidden_verifier_cases": sum(case["expect"].get("verify") is True for case in cases),
        "provider": "",
        "model": "",
        "base_url": "",
        "api_key_present": False,
        "thinking": "",
        "provider_detection": "offline local candidate; no model-catalog probe",
        "config_error": None,
        "runtime_compatibility": {
            "subagent_tool": "task",
            "subagent_modes": ["explore", "plan", "general"],
            "background_subagent_control": False,
            "custom_agent_files": False,
            "historical_v1_1_subagent_api": ["delegate_task", "subagent_control"],
        },
    }
    try:
        from minicode.config import load_runtime_config
        from minicode.model_registry import Provider, detect_provider

        runtime = load_runtime_config(PROJECT_ROOT)
        provider = detect_provider(
            runtime.get("model", ""), runtime, probe_openai_models=False
        )
    except Exception as error:  # noqa: BLE001
        state["config_error"] = f"{type(error).__name__}: {error}"
        return state
    if provider is Provider.OPENAI:
        base_url = runtime.get("openaiBaseUrl", "")
        api_key = runtime.get("openaiApiKey", "")
    elif provider is Provider.OPENROUTER:
        base_url = runtime.get("openrouterBaseUrl", "")
        api_key = runtime.get("openrouterApiKey", "")
    elif provider is Provider.CUSTOM:
        base_url = runtime.get("customBaseUrl", "")
        api_key = runtime.get("customApiKey", "")
    else:
        base_url = runtime.get("baseUrl", "")
        api_key = runtime.get("apiKey") or runtime.get("authToken") or ""
    state.update(
        {
            "provider": provider.value,
            "model": runtime.get("model", ""),
            "base_url": base_url,
            "api_key_present": bool(api_key),
            "thinking": runtime.get("thinkingMode") or "",
        }
    )
    return state


class UsageCollector:
    """Thread-safe aggregation for parent and delegated sub-agent API calls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._totals: Counter[str] = Counter()
        self._api_calls = 0

    def add(self, usage: dict[str, Any]) -> None:
        with self._lock:
            self._api_calls += 1
            for name, value in usage.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self._totals[name] += value

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"api_calls": self._api_calls, **dict(self._totals)}


def _benchmark_permission_prompt(request: dict[str, Any]) -> dict[str, str]:
    kind = request.get("kind")
    if kind == "edit":
        return {"decision": "allow_once"}
    # The benchmark deliberately blocks all access and command prompts outside
    # its preselected tool surface. This is workspace isolation, not an OS sandbox.
    return {"decision": "deny_once"}


def _create_benchmark_tools(
    cwd: str,
    runtime: dict[str, Any],
    *,
    include_subagents: bool,
):
    from minicode.tooling import ToolRegistry
    from minicode.tools import create_default_tool_registry

    complete = create_default_tool_registry(
        cwd,
        runtime=runtime,
    )
    allowed = {
        "list_files",
        "grep_files",
        "read_file",
        "write_file",
        "modify_file",
        "edit_file",
        "patch_file",
    }
    if include_subagents:
        allowed.add("task")
    selected = [tool for tool in complete.list() if tool.name in allowed]
    return ToolRegistry(
        selected,
        skills=[],
        mcp_servers=[],
        disposer=complete.dispose,
    )


def _evaluate_expectations(
    case: dict[str, Any],
    workspace: Path,
    final_text: str,
    tool_calls: list[str],
    tool_events: list[dict[str, Any]],
) -> tuple[list[str], dict[str, Any] | None]:
    failures: list[str] = []
    expect = case["expect"]
    for expected in expect.get("text_contains", []):
        if str(expected).lower() not in final_text.lower():
            failures.append(f"final response missing {expected!r}")

    for relative_path, expected_json in expect.get("json_files", {}).items():
        target = workspace / relative_path
        if not target.exists():
            failures.append(f"missing output file {relative_path}")
            continue
        try:
            actual_json = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            failures.append(f"invalid JSON in {relative_path}: {error}")
            continue
        if actual_json != expected_json:
            failures.append(f"unexpected JSON value in {relative_path}")

    counts = Counter(tool_calls)
    for tool_name, minimum in expect.get("tools_min", {}).items():
        if counts[tool_name] < minimum:
            failures.append(
                f"expected parent tool {tool_name} >= {minimum}, got {counts[tool_name]}"
            )
    for group in expect.get("tool_groups_min", []):
        actual = sum(counts[name] for name in group["tools"])
        if actual < group["min"]:
            failures.append(
                f"expected parent tools {group['tools']} total >= {group['min']}, got {actual}"
            )
    for tool_name in expect.get("tools_forbidden", []):
        if counts[tool_name]:
            failures.append(f"forbidden parent tool used: {tool_name}")

    for rule in expect.get("tool_args", []):
        tool_name = str(rule.get("tool", ""))
        expected_args = rule.get("contains", {})
        minimum = int(rule.get("min", 1))
        matches = sum(
            event.get("name") == tool_name
            and isinstance(event.get("input"), dict)
            and all(event["input"].get(key) == value for key, value in expected_args.items())
            for event in tool_events
        )
        if matches < minimum:
            failures.append(
                f"expected parent tool {tool_name} with args {expected_args} "
                f">= {minimum}, got {matches}"
            )

    verifier = None
    if expect.get("verify") is True:
        _write_files(workspace, case["hidden_files"])
        verifier = _run_hidden_verifier(workspace)
        if not verifier["passed"]:
            failures.append("hidden pytest verifier failed")
    return failures, verifier


def run_episode(case: dict[str, Any], run_index: int) -> dict[str, Any]:
    from minicode.agent_loop import run_agent_turn
    from minicode.config import load_runtime_config
    from minicode.model_registry import create_model_adapter
    from minicode.permissions import PermissionManager
    from minicode.prompt import build_system_prompt

    with tempfile.TemporaryDirectory(
        prefix=f"agentbench-live-{case['id']}-{run_index}-"
    ) as raw:
        workspace = Path(raw)
        _write_files(workspace, case.get("files", {}))
        runtime = load_runtime_config(PROJECT_ROOT)
        runtime["mcpServers"] = {}
        usage = UsageCollector()
        runtime["usageSink"] = usage.add
        include_subagents = case["category"] == "subagent"
        tools = _create_benchmark_tools(
            str(workspace),
            runtime,
            include_subagents=include_subagents,
        )
        permissions = PermissionManager(
            str(workspace),
            prompt=_benchmark_permission_prompt,
        )
        tool_calls: list[str] = []
        tool_events: list[dict[str, Any]] = []
        tool_errors: list[str] = []
        started = time.perf_counter()

        def record_tool_start(name: str, input_data: dict[str, Any]) -> None:
            tool_calls.append(name)
            tool_events.append({"name": name, "input": dict(input_data)})

        model_adapter = create_model_adapter(
            model=runtime.get("model", ""),
            tools=tools,
            runtime=runtime,
        )
        if type(model_adapter).__name__ == "OpenAIModelAdapter":
            live_provider = "openai-compatible"
            live_base_url = model_adapter.runtime.get("openaiBaseUrl", "")
        else:
            live_provider = "anthropic-compatible"
            live_base_url = model_adapter.runtime.get("baseUrl", "")
        try:
            messages = run_agent_turn(
                model=model_adapter,
                tools=tools,
                messages=[
                    {
                        "role": "system",
                        "content": build_system_prompt(
                            str(workspace),
                            permissions.get_summary(),
                            {
                                "skills": [],
                                "mcpServers": [],
                                "subagents": include_subagents,
                                "runtime": runtime,
                            },
                        )
                        + "\nBenchmark constraint: use only the provided workspace and tools; do not seek credentials or external files.",
                    },
                    {"role": "user", "content": case["prompt"]},
                ],
                cwd=str(workspace),
                permissions=permissions,
                runtime=runtime,
                max_steps=24,
                on_tool_start=record_tool_start,
                on_tool_result=lambda name, output, is_error: (
                    tool_errors.append(f"{name}: {output[:500]}") if is_error else None
                ),
            )
        finally:
            tools.dispose()
        duration = time.perf_counter() - started

        final_text = next(
            (
                str(message.get("content", ""))
                for message in reversed(messages)
                if message.get("role") == "assistant"
            ),
            "",
        )
        failures, verifier = _evaluate_expectations(
            case, workspace, final_text, tool_calls, tool_events
        )
        return {
            "id": case["id"],
            "category": case["category"],
            "difficulty": case["difficulty"],
            "run": run_index,
            "adapter": type(model_adapter).__name__,
            "provider": live_provider,
            "base_url": live_base_url,
            "passed": not failures,
            "failures": failures,
            "duration_seconds": round(duration, 3),
            "parent_tool_calls": tool_calls,
            "parent_tool_events": tool_events,
            "parent_tool_call_count": len(tool_calls),
            "tool_errors": tool_errors,
            "usage": usage.snapshot(),
            "verifier": verifier,
            "final_text": final_text,
        }


def _episode_worker(
    case: dict[str, Any],
    run_index: int,
    result_queue: Any,
) -> None:
    try:
        result_queue.put({"ok": True, "episode": run_episode(case, run_index)})
    except BaseException as error:  # noqa: BLE001
        result_queue.put(
            {
                "ok": False,
                "error": f"{type(error).__name__}: {error}",
            }
        )


def run_episode_with_timeout(
    case: dict[str, Any],
    run_index: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Run one episode in a killable process so a stuck model cannot block a suite."""

    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_episode_worker,
        args=(case, run_index, result_queue),
        name=f"agentbench-{case['id']}-{run_index}",
    )
    started = time.perf_counter()
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(10)
        result_queue.close()
        return {
            "id": case["id"],
            "category": case["category"],
            "difficulty": case["difficulty"],
            "run": run_index,
            "passed": False,
            "failures": [f"episode timed out after {timeout_seconds:g} seconds"],
            "duration_seconds": round(time.perf_counter() - started, 3),
            "parent_tool_calls": [],
            "parent_tool_events": [],
            "parent_tool_call_count": 0,
            "tool_errors": [],
            "usage": {},
            "verifier": None,
            "final_text": "",
            "timed_out": True,
        }

    try:
        payload = result_queue.get(timeout=5)
    except queue.Empty:
        payload = {
            "ok": False,
            "error": f"episode process exited with code {process.exitcode} without a result",
        }
    finally:
        result_queue.close()

    if payload["ok"]:
        return payload["episode"]
    return {
        "id": case["id"],
        "category": case["category"],
        "difficulty": case["difficulty"],
        "run": run_index,
        "passed": False,
        "failures": [payload["error"]],
        "duration_seconds": round(time.perf_counter() - started, 3),
        "parent_tool_calls": [],
        "parent_tool_events": [],
        "parent_tool_call_count": 0,
        "tool_errors": [],
        "usage": {},
        "verifier": None,
        "final_text": "",
        "process_exit_code": process.exitcode,
    }


def _rate(passed: int, total: int) -> float:
    return round(passed / total, 4) if total else 0.0


def summarize(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    grouped_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    grouped_difficulty: dict[str, list[dict[str, Any]]] = defaultdict(list)
    grouped_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total_usage: Counter[str] = Counter()
    for episode in episodes:
        grouped_category[episode["category"]].append(episode)
        grouped_difficulty[episode["difficulty"]].append(episode)
        grouped_task[episode["id"]].append(episode)
        for name, value in episode["usage"].items():
            if isinstance(value, (int, float)):
                total_usage[name] += value

    def group_rates(groups: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        return {
            name: {
                "passed": sum(item["passed"] for item in items),
                "total": len(items),
                "success_rate": _rate(sum(item["passed"] for item in items), len(items)),
            }
            for name, items in sorted(groups.items())
        }

    passed = sum(episode["passed"] for episode in episodes)
    task_all = sum(all(item["passed"] for item in items) for items in grouped_task.values())
    task_any = sum(any(item["passed"] for item in items) for items in grouped_task.values())
    return {
        "episodes_passed": passed,
        "episodes_total": len(episodes),
        "episode_success_rate": _rate(passed, len(episodes)),
        "tasks_passed_all_runs": task_all,
        "tasks_passed_any_run": task_any,
        "tasks_total": len(grouped_task),
        "by_category": group_rates(grouped_category),
        "by_difficulty": group_rates(grouped_difficulty),
        "average_parent_tool_calls": round(
            sum(item["parent_tool_call_count"] for item in episodes) / len(episodes), 3
        )
        if episodes
        else 0.0,
        "average_latency_seconds": round(
            sum(item["duration_seconds"] for item in episodes) / len(episodes), 3
        )
        if episodes
        else 0.0,
        "usage": dict(total_usage),
    }


def _print_preflight(state: dict[str, Any], oracle_results: list[dict[str, Any]]) -> None:
    oracle_failures = [result for result in oracle_results if result["status"] == "failed"]
    summary = {
        **state,
        "oracle_validation": {
            "passed": len(oracle_results) - len(oracle_failures),
            "total": len(oracle_results),
            "failed_ids": [result["id"] for result in oracle_failures],
        },
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def _build_report(
    *,
    state: dict[str, Any],
    cases: list[dict[str, Any]],
    runs: int,
    episode_timeout_seconds: float,
    oracle_results: list[dict[str, Any]],
    episodes: list[dict[str, Any]],
    status: str,
) -> dict[str, Any]:
    live_routes = sorted(
        {
            (
                str(episode.get("adapter", "")),
                str(episode.get("provider", "")),
                str(episode.get("base_url", "")),
            )
            for episode in episodes
            if episode.get("adapter")
        }
    )
    return {
        "benchmark": BENCHMARK_NAME,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "runner": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "workspace_isolation": "per-episode temporary directory; not a container sandbox",
            "process_isolation": "one spawned process per episode",
            "episode_timeout_seconds": episode_timeout_seconds,
            "checkpoint_after_each_episode": True,
            "mcp_disabled": True,
            "subagents_only_for_subagent_category": True,
            "parent_tool_trace_only": True,
        },
        "model": {
            "preflight_provider": state["provider"],
            "provider_detection": state["provider_detection"],
            "name": state["model"],
            "preflight_base_url": state["base_url"],
            "thinking": state["thinking"],
            "live_routes": [
                {"adapter": adapter, "provider": provider, "base_url": base_url}
                for adapter, provider, base_url in live_routes
            ],
        },
        "runtime_compatibility": state["runtime_compatibility"],
        "runs_per_case": runs,
        "selected_case_ids": [case["id"] for case in cases],
        "oracle_validation": oracle_results,
        "summary": summarize(episodes),
        "episodes": episodes,
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = path.with_suffix(path.suffix + ".tmp")
    checkpoint.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    checkpoint.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=BENCHMARK_NAME)
    parser.add_argument("--live", action="store_true", help="Call the configured model API")
    parser.add_argument("--case", action="append", dest="case_ids", help="Run only this case id")
    parser.add_argument("--runs", type=int, default=1, help="Independent live repetitions per case")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Live result JSON path")
    parser.add_argument(
        "--episode-timeout",
        type=float,
        default=180,
        help="Hard wall-clock timeout for each live episode (default: 180 seconds)",
    )
    parser.add_argument(
        "--skip-oracle-validation",
        action="store_true",
        help="Skip deterministic hidden-test oracle checks",
    )
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.episode_timeout < 10:
        parser.error("--episode-timeout must be at least 10 seconds")

    try:
        cases = select_cases(load_cases(), args.case_ids)
    except ValueError as error:
        parser.error(str(error))

    oracle_results = [] if args.skip_oracle_validation else validate_oracles(cases)
    state = readiness(cases)
    _print_preflight(state, oracle_results)
    oracle_failures = [result for result in oracle_results if result["status"] == "failed"]
    if oracle_failures:
        print("Oracle validation failed; live evaluation was not started.", file=sys.stderr)
        for result in oracle_failures:
            print(
                f"- {result['id']}: meaningful={result['meaningful']} "
                f"oracle_passed={result['oracle_passed']}",
                file=sys.stderr,
            )
        return 2
    if not args.live:
        print("Preflight only: hidden verifiers checked; no API request was made.")
        return 0
    if state["config_error"]:
        raise SystemExit(f"Live configuration error: {state['config_error']}")
    if not all((state["provider"], state["model"], state["base_url"], state["api_key_present"])):
        raise SystemExit("Live provider, model, base URL, or API key is missing")

    episodes: list[dict[str, Any]] = []
    try:
        for run_index in range(1, args.runs + 1):
            for case in cases:
                print(f"Running {case['id']} ({run_index}/{args.runs})...", flush=True)
                episode = run_episode_with_timeout(
                    case,
                    run_index,
                    args.episode_timeout,
                )
                episodes.append(episode)
                checkpoint_report = _build_report(
                    state=state,
                    cases=cases,
                    runs=args.runs,
                    episode_timeout_seconds=args.episode_timeout,
                    oracle_results=oracle_results,
                    episodes=episodes,
                    status="running",
                )
                _write_report(args.output, checkpoint_report)
                print("PASS" if episode["passed"] else "FAIL", flush=True)
    except KeyboardInterrupt:
        interrupted_report = _build_report(
            state=state,
            cases=cases,
            runs=args.runs,
            episode_timeout_seconds=args.episode_timeout,
            oracle_results=oracle_results,
            episodes=episodes,
            status="interrupted",
        )
        _write_report(args.output, interrupted_report)
        print(f"Interrupted; checkpoint saved to {args.output}", file=sys.stderr)
        return 130

    report = _build_report(
        state=state,
        cases=cases,
        runs=args.runs,
        episode_timeout_seconds=args.episode_timeout,
        oracle_results=oracle_results,
        episodes=episodes,
        status="completed",
    )
    _write_report(args.output, report)
    print(json.dumps({"summary": report["summary"], "output": str(args.output)}, indent=2))
    return 0 if report["summary"]["episodes_passed"] == len(episodes) else 1


if __name__ == "__main__":
    raise SystemExit(main())

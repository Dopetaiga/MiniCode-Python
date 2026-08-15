"""Small, explainable MiniCode agent evaluation for OpenAI-compatible models.

Running without ``--live`` only validates fixtures and environment readiness.
No API request is made unless ``--live`` is explicitly supplied.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = Path(__file__).with_name("dsv4_agent_cases.jsonl")
sys.path.insert(0, str(PROJECT_ROOT))


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [case.get("id") for case in cases]
    if not cases or any(not isinstance(case_id, str) or not case_id for case_id in ids):
        raise ValueError("Every case must have a non-empty string id")
    if len(ids) != len(set(ids)):
        raise ValueError("Case ids must be unique")
    for case in cases:
        if not isinstance(case.get("prompt"), str) or not isinstance(case.get("expect"), dict):
            raise ValueError(f"Invalid case schema: {case['id']}")
    return cases


def readiness(cases: list[dict[str, Any]]) -> dict[str, Any]:
    state: dict[str, Any] = {
        "case_count": len(cases),
        "case_ids": [case["id"] for case in cases],
        "provider": "",
        "model": "",
        "base_url": "",
        "api_key_present": False,
        "thinking": "",
        "provider_detection": "offline local candidate; no model-catalog probe",
        "config_error": None,
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


def _write_fixture_files(workspace: Path, files: dict[str, str]) -> None:
    for relative_path, content in files.items():
        target = workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _benchmark_permission_prompt(request: dict[str, Any]) -> dict[str, str]:
    if request.get("kind") == "edit":
        return {"decision": "allow_once"}
    return {"decision": "deny_once"}


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    from minicode.agent_loop import run_agent_turn
    from minicode.config import load_runtime_config
    from minicode.model_registry import create_model_adapter
    from minicode.permissions import PermissionManager
    from minicode.prompt import build_system_prompt
    from minicode.tools import create_default_tool_registry

    with tempfile.TemporaryDirectory(prefix=f"minicode-{case['id']}-") as temp_dir:
        workspace = Path(temp_dir)
        _write_fixture_files(workspace, case.get("files", {}))
        runtime = load_runtime_config(PROJECT_ROOT)
        runtime["mcpServers"] = {}
        tools = create_default_tool_registry(str(workspace), runtime=runtime)
        permissions = PermissionManager(
            str(workspace),
            prompt=_benchmark_permission_prompt,
        )
        tool_calls: list[str] = []
        tool_events: list[dict[str, Any]] = []

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
                                "subagents": tools.find("task") is not None,
                                "runtime": runtime,
                            },
                        ),
                    },
                    {"role": "user", "content": case["prompt"]},
                ],
                cwd=str(workspace),
                permissions=permissions,
                runtime=runtime,
                max_steps=12,
                on_tool_start=record_tool_start,
            )
        finally:
            tools.dispose()

        final_text = next(
            (message.get("content", "") for message in reversed(messages) if message["role"] == "assistant"),
            "",
        )
        failures: list[str] = []
        for expected in case["expect"].get("text_contains", []):
            if expected.lower() not in final_text.lower():
                failures.append(f"final response missing {expected!r}")
        counts = Counter(tool_calls)
        for tool_name, minimum in case["expect"].get("tools_min", {}).items():
            if counts[tool_name] < minimum:
                failures.append(f"expected {tool_name} >= {minimum}, got {counts[tool_name]}")
        for tool_name in case["expect"].get("tools_forbidden", []):
            if counts[tool_name]:
                failures.append(f"forbidden parent tool used: {tool_name}")
        for rule in case["expect"].get("tool_args", []):
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
                    f"expected {tool_name} with args {expected_args} >= {minimum}, got {matches}"
                )
        for relative_path, expected_content in case["expect"].get("files", {}).items():
            target = workspace / relative_path
            if not target.exists():
                failures.append(f"missing output file {relative_path}")
            elif target.read_text(encoding="utf-8") != expected_content:
                failures.append(f"unexpected content in {relative_path}")

        return {
            "id": case["id"],
            "category": case.get("category"),
            "adapter": type(model_adapter).__name__,
            "provider": live_provider,
            "base_url": live_base_url,
            "passed": not failures,
            "failures": failures,
            "tool_calls": tool_calls,
            "tool_events": tool_events,
            "final_text": final_text,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Actually call the configured API")
    parser.add_argument("--case", action="append", dest="case_ids", help="Run only this case id")
    parser.add_argument("--output", type=Path, help="Result JSON path")
    args = parser.parse_args()

    cases = load_cases()
    if args.case_ids:
        selected = set(args.case_ids)
        cases = [case for case in cases if case["id"] in selected]
        missing = selected - {case["id"] for case in cases}
        if missing:
            raise SystemExit(f"Unknown case ids: {', '.join(sorted(missing))}")

    state = readiness(cases)
    print(json.dumps(state, indent=2, ensure_ascii=False))
    if not args.live:
        print("Preflight only: no API request was made. Add --live when quota is available.")
        return 0
    required = {
        "provider": state["provider"],
        "model": state["model"],
        "base_url": state["base_url"],
        "api_key": "present" if state["api_key_present"] else "",
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit(f"Missing live configuration: {', '.join(missing)}")

    results = [run_case(case) for case in cases]
    live_routes = sorted(
        {
            (result["adapter"], result["provider"], result["base_url"])
            for result in results
        }
    )
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "benchmark": "MiniCode DSV4 smoke pack v1.2",
        "preflight": state,
        "live_routes": [
            {"adapter": adapter, "provider": provider, "base_url": base_url}
            for adapter, provider, base_url in live_routes
        ],
        "model": state["model"],
        "thinking": state["thinking"],
        "passed": sum(result["passed"] for result in results),
        "total": len(results),
        "results": results,
    }
    output = args.output or PROJECT_ROOT / "test_results" / "dsv4-agent-eval-v1-2.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "total": report["total"], "output": str(output)}, indent=2))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

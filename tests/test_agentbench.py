from pathlib import Path

from benchmarks.run_agentbench import (
    UsageCollector,
    _create_benchmark_tools,
    load_cases,
    select_cases,
    summarize,
    validate_oracles,
)


def test_agentbench_schema_and_inventory() -> None:
    cases = load_cases()

    assert len(cases) == 15
    assert len({case["id"] for case in cases}) == 15
    assert sum(case["expect"].get("verify") is True for case in cases) == 8
    assert sum(case["category"] == "subagent" for case in cases) == 3


def test_agentbench_hidden_verifiers_reject_baselines_and_accept_oracles() -> None:
    verified = [case for case in load_cases() if case["expect"].get("verify") is True]

    results = validate_oracles(verified)

    assert all(result["meaningful"] for result in results)
    assert all(result["oracle_passed"] for result in results)
    assert all(result["status"] == "passed" for result in results)


def test_agentbench_case_selection_preserves_file_order() -> None:
    cases = load_cases()

    selected = select_cases(cases, ["dual_subagent_synthesis", "evidence_read"])

    assert [case["id"] for case in selected] == [
        "evidence_read",
        "dual_subagent_synthesis",
    ]


def test_agentbench_exposes_latest_task_tool_only_for_subagent_cases(tmp_path: Path) -> None:
    runtime = {"mcpServers": {}}

    regular_tools = _create_benchmark_tools(
        str(tmp_path), runtime, include_subagents=False
    )
    subagent_tools = _create_benchmark_tools(
        str(tmp_path), runtime, include_subagents=True
    )
    try:
        assert "task" not in regular_tools.list_all()
        assert "task" in subagent_tools.list_all()
        assert "delegate_task" not in subagent_tools.list_all()
        assert "subagent_control" not in subagent_tools.list_all()
    finally:
        regular_tools.dispose()
        subagent_tools.dispose()


def test_usage_collector_and_summary_aggregate_numeric_metrics() -> None:
    collector = UsageCollector()
    collector.add({"prompt_tokens": 10, "completion_tokens": 3, "cached": False})
    collector.add({"prompt_tokens": 5, "completion_tokens": 2})
    usage = collector.snapshot()
    episodes = [
        {
            "id": "one",
            "category": "evidence",
            "difficulty": "easy",
            "passed": True,
            "parent_tool_call_count": 2,
            "duration_seconds": 1.5,
            "usage": usage,
        },
        {
            "id": "one",
            "category": "evidence",
            "difficulty": "easy",
            "passed": False,
            "parent_tool_call_count": 4,
            "duration_seconds": 2.5,
            "usage": {"api_calls": 1, "prompt_tokens": 7},
        },
    ]

    result = summarize(episodes)

    assert usage == {"api_calls": 2, "prompt_tokens": 15, "completion_tokens": 5}
    assert result["episode_success_rate"] == 0.5
    assert result["tasks_passed_any_run"] == 1
    assert result["tasks_passed_all_runs"] == 0
    assert result["average_parent_tool_calls"] == 3
    assert result["usage"]["prompt_tokens"] == 22

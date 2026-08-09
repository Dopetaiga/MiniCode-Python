# MiniCode Python DSV4 test pack v1.2

This pack separates protocol failures from model-quality failures. The default
command is offline-only and never sends an API request.

## Preflight

```powershell
.\.venv\Scripts\python.exe benchmarks\run_dsv4_agent_eval.py
```

It validates eight fixed cases: direct response, single-file read, grep search,
multi-tool synthesis, isolated file creation, one `explore` sub-agent, two
delegated `explore` calls, and one `plan` sub-agent route. The original v1.1
pack remains under `benchmarks/legacy/` for historical result verification.

## Live baseline

Configure the DeepSeek OpenAI-compatible endpoint in the current PowerShell
session, keep thinking disabled, and run one inexpensive case first:

```powershell
$env:MINI_CODE_MODEL = "deepseek-v4-pro"
$env:CUSTOM_API_KEY = "<deepseek-key>"
$env:CUSTOM_API_BASE_URL = "https://api.deepseek.com/v1"
$env:MINI_CODE_MAX_OUTPUT_TOKENS = "4096"

.\.venv\Scripts\python.exe benchmarks\run_dsv4_agent_eval.py --live --case read_fact
```

Run the whole pack only after that passes:

```powershell
.\.venv\Scripts\python.exe benchmarks\run_dsv4_agent_eval.py --live
```

Run the lifecycle cases independently when diagnosing delegation:

```powershell
.\.venv\Scripts\python.exe benchmarks\run_dsv4_agent_eval.py --live --case subagent_delegation
.\.venv\Scripts\python.exe benchmarks\run_dsv4_agent_eval.py --live --case dual_subagent_delegation
.\.venv\Scripts\python.exe benchmarks\run_dsv4_agent_eval.py --live --case plan_subagent_delegation
```

Reports are written under `test_results/` and never contain the API key. If the
gateway is configured through the OpenAI channel instead of the custom channel,
use `OPENAI_API_KEY` and `OPENAI_BASE_URL` while keeping `MINI_CODE_MODEL` set.

Initial acceptance target: all eight cases pass. Retain per-case tool traces so
failures can be classified as protocol, tool selection, or answer synthesis.

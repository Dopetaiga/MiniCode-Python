# MiniCode Python DSV4 test pack

This pack separates protocol failures from model-quality failures. The default
command is offline-only and never sends an API request.

## Preflight

```powershell
.\venv\Scripts\python.exe benchmarks\run_dsv4_agent_eval.py
```

It validates eight fixed cases: direct response, single-file read, grep search,
multi-tool synthesis, isolated file creation, synchronous sub-agent delegation,
two-worker background delegation, and project-defined `.claude/agents`
delegation.

## Live baseline

Configure the DeepSeek OpenAI-compatible endpoint in the current PowerShell
session, keep thinking disabled, and run one inexpensive case first:

```powershell
$env:MINI_CODE_PROVIDER = "openai"
$env:OPENAI_API_KEY = "<deepseek-key>"
$env:OPENAI_BASE_URL = "https://api.deepseek.com"
$env:OPENAI_MODEL = "deepseek-v4-pro"
$env:MINI_CODE_THINKING = "disabled"
$env:MINI_CODE_MAX_OUTPUT_TOKENS = "4096"

.\venv\Scripts\python.exe benchmarks\run_dsv4_agent_eval.py --live --case read_fact
```

Run the whole pack only after that passes:

```powershell
.\venv\Scripts\python.exe benchmarks\run_dsv4_agent_eval.py --live
```

Run the lifecycle cases independently when diagnosing delegation:

```powershell
.\venv\Scripts\python.exe benchmarks\run_dsv4_agent_eval.py --live --case subagent_delegation
.\venv\Scripts\python.exe benchmarks\run_dsv4_agent_eval.py --live --case parallel_subagents
.\venv\Scripts\python.exe benchmarks\run_dsv4_agent_eval.py --live --case custom_subagent_delegation
```

Then repeat with `MINI_CODE_THINKING=enabled`. Reports are written under
`test_results/` and never contain the API key.

Initial acceptance target: all eight cases pass. Retain per-case tool traces so
failures can be classified as protocol, tool selection, or answer synthesis.

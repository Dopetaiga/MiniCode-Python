# MiniCode-Python: Reimplementation and Extension Record

## Project statement

MiniCode-Python is an independently recreated and packaged Python coding agent
built after studying the MiniCode source code and its Python implementation. It
is not presented as an official MiniCode release or as a clean-room design.
The upstream source remains credited, and its MIT license is retained.

The purpose of this repository is to make the learning process inspectable:
recreate a working agent first, then extend it through testable runtime
capabilities and reproducible experiments.

## What was recreated

- the model/tool execution loop and multi-turn state flow;
- terminal-oriented local repository interaction;
- tool registration, dispatch, and result replay;
- configuration and OpenAI-compatible provider access;
- the Python package, CLI entry points, and testable runtime boundaries.

These items describe implementation work, not a claim that their product ideas
were invented here. The MiniCode repository is the explicit study source.

## What was added during continued development

| Engineering area | Repository evidence |
| --- | --- |
| Durable sessions and replay | `minicode/session.py`, session CLI tests |
| Working and project memory | `minicode/working_memory.py`, `minicode/memory_pipeline.py` |
| Safe checkpoint and rewind | session/checkpoint implementation and recovery tests |
| Bounded task subagents | task tool/runtime implementation and `tests/test_task_tool.py` |
| Provider readiness and fallback evidence | `minicode/readiness.py`, `minicode/release_readiness.py` |
| Agent evaluation | `benchmarks/LITECODEBENCH.md`, versioned JSONL tasks, raw result JSON |
| Cross-platform regression | `.github/workflows/ci.yml` on Python 3.11 and 3.12 |

## Evaluation discipline

LiteCodeBench keeps prompts, task assets, verifiers, raw results, and the human
report together. A score is only comparable when the dataset and contract are
the same. The 2026-08-15 report therefore records the original 14/15 run and the
revised-contract 15/15 run separately instead of describing them as a direct
model-quality improvement.

## How to discuss this project

A concise and accurate description is:

> I studied MiniCode's Python source, independently recreated and packaged a
> runnable Python agent, then extended it with durable sessions, memory,
> recovery, bounded subagents, provider readiness, and a reproducible agent
> benchmark. I retained upstream attribution and used tests and raw evaluation
> artifacts to distinguish reproduction from my later engineering work.

Avoid claiming that the MiniCode concept, name, or all repository history was
created here. The value of this project is the demonstrated source-reading,
reimplementation, runtime engineering, and evaluation process.

## Attribution

- Study source: [LiuMengxuan04/MiniCode](https://github.com/LiuMengxuan04/MiniCode)
- Current independently maintained repository:
  [Dopetaiga/MiniCode-Python](https://github.com/Dopetaiga/MiniCode-Python)
- License: MIT; original copyright notice preserved in `LICENSE`.

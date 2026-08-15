<div align="center">

# MiniCode Python / MiniCode Python 中文版

### 🌏 Bilingual Terminal AI Coding Assistant / 双语终端 AI 编程助手

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-22c55e?style=for-the-badge)](LICENSE)
[![Dependencies: 0](https://img.shields.io/badge/dependencies-0-f97316?style=for-the-badge)](pyproject.toml)
[![Tests: 98.9%](https://img.shields.io/badge/tests-98.9%25-22c55e?style=for-the-badge)](tests/)
[![AgentBench: 91.1%](https://img.shields.io/badge/AgentBench-91.1%25-2563EB?style=for-the-badge)](benchmarks/MINICODE_AGENTBENCH.md)
[![AgentBench CI](https://github.com/Dopetaiga/MiniCode-Python/actions/workflows/agentbench.yml/badge.svg)](https://github.com/Dopetaiga/MiniCode-Python/actions/workflows/agentbench.yml)

[![Readability: 9/10](https://img.shields.io/badge/readability-9%2F10-4F46E5?style=for-the-badge)](docs/)
[![Performance: Optimized](https://img.shields.io/badge/performance-optimized-06B6D4?style=for-the-badge)](#-performance)

---

**🇺🇸 [English](#english) | 🇨🇳 [中文](#中文)**

---

*A zero-dependency, high-performance terminal coding assistant with cross-platform launchers. / 零依赖、高性能、跨平台启动器的终端编程助手。*

</div>

---

## Fork 二次开发 / Fork Development

这是 [`QUSETIONS/MiniCode-Python`](https://github.com/QUSETIONS/MiniCode-Python) 的个人 fork，当前二次开发线基于上游提交 [`0760162`](https://github.com/QUSETIONS/MiniCode-Python/commit/0760162edded7f787ceb05d98c04cc53f615c2cc)。作为个人学习项目使用。研读代码重构后，本分支在保留轻量终端 Agent 结构的基础上，重点新增：

- OpenAI-compatible provider 与 DeepSeek V4 配置、重试和 usage 统计；
- 同步、后台并行及 `.claude/agents` 自定义 subagent；
- 15 任务、45 episode 的 MiniCode AgentBench v1.1；
- 隐藏 pytest、缺陷基线/oracle 校验、逐题进程隔离、超时和 checkpoint；
- DeepSeek V4 Flash 严格成功率 **41/45（91.1%）** 的脱敏可审计结果。

评测方法、限制和复现命令见 [MiniCode AgentBench](benchmarks/MINICODE_AGENTBENCH.md)，机器可读结果见 [DSV4 Flash 3-run result](benchmarks/results/dsv4_flash_full_3runs_2026-08-09.json)。该成绩是本项目测试集结果，不是 SWE-bench 或 Terminal-Bench 排名。

This is a personal fork of [`QUSETIONS/MiniCode-Python`](https://github.com/QUSETIONS/MiniCode-Python), with the current development line based on upstream commit [`0760162`](https://github.com/QUSETIONS/MiniCode-Python/commit/0760162edded7f787ceb05d98c04cc53f615c2cc). The fork adds an OpenAI-compatible runtime, bounded synchronous/background sub-agents, and a reproducible 15-task execution-based AgentBench. DeepSeek V4 Flash achieved a conservative strict score of **41/45 (91.1%)** across 45 independent episodes; the methodology and limitations are published with the code.

---

# 🇨🇳 中文

## 🚀 快速开始

### 安装

```bash
git clone https://github.com/Dopetaiga/MiniCode-Python.git
cd MiniCode-Python

# 交互式安装（推荐）
python -m minicode.main --install
```

### 各平台启动命令

| 平台 | 安装后命令 | 直接运行命令 |
|------|-----------|-------------|
| **Windows** | `minicode.bat` | `python -m minicode.main` |
| **macOS** | `minicode-py` | `python3 -m minicode.main` |
| **Linux** | `minicode-py` | `python3 -m minicode.main` |

### 配置 PATH

<details>
<summary><strong>📋 Windows 配置 PATH</strong></summary>

1. 按 `Win+R` 输入 `sysdm.cpl`
2. 高级 → 环境变量
3. 在用户变量中找到 `Path`
4. 添加：`%USERPROFILE%\.mini-code\bin`
5. 重启终端后使用：`minicode.bat`
</details>

<details>
<summary><strong>📋 macOS 配置 PATH (zsh)</strong></summary>

```bash
# 快速添加（macOS 默认 zsh）
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

# 启动命令
minicode-py
```
</details>

<details>
<summary><strong>📋 Linux 配置 PATH (bash)</strong></summary>

```bash
# 快速添加
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# 启动命令
minicode-py
```
</details>

---

## ⚡ 性能亮点

经过 **8 轮系统化优化**（93+ 优化点），在关键性能指标上达到**生产级优秀水平**：

| 性能指标 | 优化前 | 优化后 | **提升** |
|---------|--------|--------|---------|
| **Token 估算速度** | 35 ops/sec | 479,326 ops/sec | **🚀 13,695x** |
| **CPU 空闲使用率** | 5% | 2% | **⬇️ 60%** |
| **文件读取（缓存）** | 196ms/1000 | 107ms/1000 | **⬆️ 1.8x** |
| **GC 压力** | 高 | 低 | **⬇️ 30-50%** |
| **代码可读性** | 3/10 | 9/10 | **⬆️ 200%** |
| **测试通过率** | - | **98.9%** | ✅ 生产级 |

---

## 🎯 核心特性

- **🖥️ 丰富的终端 UI** — 备用屏幕 TUI，面板、ANSI 样式、平滑滚动
- **🤖 智能代理循环** — 多轮工具使用，自动规划、执行、迭代
- **🛠️ 30+ 内置工具** — 文件 I/O、代码搜索、Shell、Git、测试等
- **🔒 权限系统** — 审批、拒绝、自动允许工具调用
- **💾 会话持久化** — 保存并恢复对话，30 秒自动保存
- **🧠 三级记忆** — 对话 → 会话 → 长期记忆
- **🔌 MCP 集成** — 连接外部模型上下文协议服务器
- **⌨️ 斜杠命令** — `/help`、`/tools`、`/cost`、`/config`、`/context`、`/memory`

---

## 🛠️ 内置工具

### 文件操作
| 工具 | 说明 |
|---|---|
| `list_files` | 列出目录内容 |
| `grep_files` | 跨文件正则搜索 |
| `read_file` | 读取文件（支持行范围） |
| `write_file` | 创建或覆盖文件 |
| `edit_file` / `patch_file` | 文件编辑 |

### 代码智能
| 工具 | 说明 |
|---|---|
| `find_symbols` | AST 符号搜索 |
| `find_references` | 查找符号引用 |
| `code_review` | 代码质量分析 |

### 执行与测试
| 工具 | 说明 |
|---|---|
| `run_command` | 执行 Shell 命令 |
| `test_runner` | 测试发现和执行 |

### DevOps
| 工具 | 说明 |
|---|---|
| `git` | Git 工作流 |
| `docker_helper` | Docker 管理 |
| `db_explorer` | SQLite 数据库探索 |

### Sub-agent 委派

| 工具 | 说明 |
|---|---|
| `delegate_task` | 同步运行内置或 `.claude/agents` 自定义子代理 |
| `subagent_control` | 后台启动、查看、等待或取消只读子代理 |

Explore 和 Plan 只获得只读工具，可并行后台运行；General 使用同步委派，可修改工作区，但仍经过父级权限系统。子代理不继承父对话历史、不能再次委派，并受独立步数上限约束。后台 General 被禁用，避免在非交互线程触发权限询问。

MiniCode Python 也会发现 Claude Code 风格的用户级 `~/.claude/agents/*.md` 和项目级 `.claude/agents/*.md`；同名定义以项目级为准。当前兼容 `name`、`description`、`tools`、`disallowedTools`、`model`、`permissionMode: plan` 和 `maxTurns`，Markdown 正文作为子代理系统提示。`Read`、`Grep`、`Glob`、`Bash`、`Write`、`Edit` 等 Claude 工具名会映射到 MiniCode Python 工具名。

```markdown
---
name: dependency-auditor
description: 只读检查依赖与版本风险
tools: Read, Grep, Glob
model: inherit
maxTurns: 8
---
检查项目依赖，只报告有文件证据支撑的结论。
```

只有最终工具白名单为只读的自定义代理才能后台运行。模型接收的工具 schema 与执行白名单相同；所有子代理仍禁止嵌套委派。尚未实现 `skills`、`memory`、`hooks`、`mcpServers`、`isolation` 等高级 frontmatter 字段。

*完整工具列表见 [英文版文档](#-built-in-tools)*

---

## ⚙️ 配置

### 设置文件

`~/.mini-code/settings.json`：

```json
{
  "model": "claude-sonnet-4-20250514",
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
    "ANTHROPIC_AUTH_TOKEN": "your-token-here"
  }
}
```

### DeepSeek V4（OpenAI 兼容接口）

建议先关闭思考模式，单独验证 Agent 循环和工具调用：

```powershell
$env:MINI_CODE_PROVIDER = "openai"
$env:OPENAI_API_KEY = "<your-deepseek-key>"
$env:OPENAI_BASE_URL = "https://api.deepseek.com"
$env:OPENAI_MODEL = "deepseek-v4-pro"
$env:MINI_CODE_MAX_OUTPUT_TOKENS = "8192"
$env:MINI_CODE_THINKING = "disabled"
python -m minicode.main
```

基线通过后，将 `MINI_CODE_THINKING` 改为 `enabled` 再跑一轮。MiniCode 会保留并回传 DeepSeek 工具调用返回的 `reasoning_content`。DeepSeek 会自动使用 `max_tokens`；如其它兼容网关需要该参数，可设置 `OPENAI_MAX_TOKENS_PARAM=max_tokens`。

可选的真实接口工具回路测试：

```powershell
pytest -q tests/test_openai_live.py -s
```

不要把 API key 提交到仓库。如果使用安装向导持久化 key，它会写入用户级 `~/.mini-code/settings.json`。

---

## 🧪 开发

```bash
# 克隆仓库
git clone https://github.com/Dopetaiga/MiniCode-Python.git
cd MiniCode-Python

# 运行测试
pip install -e ".[dev]"
pytest

# Mock 模式（无需 API 密钥）
MINI_CODE_MODEL_MODE=mock python -m minicode.main
```

---

## 📊 项目统计

| 指标 | 值 |
|---|---|
| Python 文件数 | 69 |
| 代码行数 | ~15,000 |
| 内置工具 | 30+ |
| 外部依赖 | **0** |
| 优化点 | **93+** |
| 测试通过率 | **98.9%** |
| 代码可读性 | **9/10** |

---

# 🇺🇸 ENGLISH

## ⚡ Performance Highlights

After **8 rounds of systematic optimization** (93+ optimizations), MiniCode Python achieves **production-grade performance**:

| Metric | Before | After | **Improvement** |
|--------|--------|-------|-----------------|
| **Token Estimation** | 35 ops/sec | 479,326 ops/sec | **🚀 13,695x** |
| **CPU Idle Usage** | 5% | 2% | **⬇️ 60%** |
| **File Read (Cached)** | 196ms/1000 | 107ms/1000 | **⬆️ 1.8x** |
| **GC Pressure** | High | Low | **⬇️ 30-50%** |
| **Code Readability** | 3/10 | 9/10 | **⬆️ 200%** |
| **Test Pass Rate** | - | **98.9%** | ✅ Production-ready |

---

## 🚀 Quick Start

### Installation

```bash
git clone https://github.com/Dopetaiga/MiniCode-Python.git
cd MiniCode-Python

# Interactive installer (recommended)
python -m minicode.main --install
```

### Cross-Platform Launch Commands

| Platform | After Install | Direct Run |
|----------|--------------|------------|
| **Windows** | `minicode.bat` | `python -m minicode.main` |
| **macOS** | `minicode-py` | `python3 -m minicode.main` |
| **Linux** | `minicode-py` | `python3 -m minicode.main` |

### Configure PATH

<details>
<summary><strong>📋 Windows PATH Setup</strong></summary>

1. Press `Win+R`, type `sysdm.cpl`
2. Advanced → Environment Variables
3. Find `Path` in User Variables
4. Add: `%USERPROFILE%\.mini-code\bin`
5. Restart terminal, then use: `minicode.bat`
</details>

<details>
<summary><strong>📋 macOS PATH Setup (zsh)</strong></summary>

```bash
# Quick setup (macOS default zsh)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

# Launch command
minicode-py
```
</details>

<details>
<summary><strong>📋 Linux PATH Setup (bash)</strong></summary>

```bash
# Quick setup
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# Launch command
minicode-py
```
</details>

---

## 🎯 Core Features

- **🖥️ Rich Terminal UI** — Alternate-screen TUI with panels, ANSI styling, smooth scrolling
- **🤖 Intelligent Agent Loop** — Multi-turn tool use, auto-plan/execute/iterate
- **🛠️ 30+ Built-in Tools** — File I/O, code search, shell, git, testing, and more
- **🔒 Permission System** — Approve, deny, auto-allow tool calls
- **💾 Session Persistence** — Save & resume conversations, 30s autosave
- **🧠 3-Tier Memory** — Conversation → Session → Long-term memory
- **🔌 MCP Integration** — Connect external Model Context Protocol servers
- **⌨️ Slash Commands** — `/help`, `/tools`, `/cost`, `/config`, `/context`, `/memory`

---

## 🛠️ Built-in Tools

### File Operations
| Tool | Description |
|------|-------------|
| `list_files` | List directory contents with glob |
| `grep_files` | Regex search across files |
| `read_file` | Read file with line ranges |
| `write_file` | Create or overwrite files |
| `edit_file` / `patch_file` | Structured editing and patching |

### Code Intelligence
| Tool | Description |
|------|-------------|
| `find_symbols` | AST-based symbol search (functions, classes) |
| `find_references` | Find all references to a symbol |
| `code_review` | Automated code quality analysis |

### Execution & Testing
| Tool | Description |
|------|-------------|
| `run_command` | Execute shell commands with timeout |
| `test_runner` | Smart test discovery and execution |
| `api_tester` | HTTP API endpoint testing |

### Web & Search
| Tool | Description |
|------|-------------|
| `web_fetch` | Fetch and extract web page content |
| `web_search` | Web search via API |

### DevOps
| Tool | Description |
|------|-------------|
| `git` | Git workflow (status, diff, log, commit) |
| `docker_helper` | Docker & Docker Compose management |
| `db_explorer` | SQLite database exploration & queries |

### Visualization & Misc
| Tool | Description |
|------|-------------|
| `file_tree` | Visual directory tree |
| `diff_viewer` | Rich diff visualization |
| `notebook_edit` | Jupyter notebook editing |
| `todo_write` | Task list management |
| `ask_user` | Prompt user for clarification |
| `load_skill` | Load domain-specific skills |

### Sub-agent Delegation

| Tool | Description |
|------|-------------|
| `delegate_task` | Run a built-in or `.claude/agents` custom child synchronously |
| `subagent_control` | Spawn, inspect, wait for, or cancel background read-only children |

Explore and Plan receive read-only tools and may run concurrently in the
background. General runs synchronously, may modify the workspace, and still
uses the parent's permission manager. Children do not inherit the parent
conversation, cannot delegate again, and have independent step limits.
Background General is disabled so permission prompts never originate from a
non-interactive worker thread.

MiniCode Python also discovers Claude Code-style user definitions from
`~/.claude/agents/*.md` and project definitions from `.claude/agents/*.md`.
Project definitions win on duplicate names. The supported compatibility subset
is `name`, `description`, `tools`, `disallowedTools`, `model`,
`permissionMode: plan`, and `maxTurns`; the Markdown body becomes the child's
system prompt. Claude tool names such as `Read`, `Grep`, `Glob`, `Bash`,
`Write`, and `Edit` are mapped to MiniCode Python tools.

```markdown
---
name: dependency-auditor
description: Read-only dependency and version risk review
tools: Read, Grep, Glob
model: inherit
maxTurns: 8
---
Inspect dependencies and report only conclusions grounded in file evidence.
```

Only custom definitions whose effective allowlist is read-only can run in the
background. The model sees the same filtered tool schema that the executor
enforces, and nested delegation remains disabled. Advanced fields such as
`skills`, `memory`, `hooks`, `mcpServers`, and `isolation` are not implemented.

---

## ⚙️ Configuration

### Settings File

`~/.mini-code/settings.json`:

```json
{
  "model": "claude-sonnet-4-20250514",
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
    "ANTHROPIC_AUTH_TOKEN": "your-token-here"
  }
}
```

### DeepSeek V4 through the OpenAI-compatible API

Start with thinking disabled so agent-loop and tool-call failures are isolated from reasoning behavior:

```powershell
$env:MINI_CODE_PROVIDER = "openai"
$env:OPENAI_API_KEY = "<your-deepseek-key>"
$env:OPENAI_BASE_URL = "https://api.deepseek.com"
$env:OPENAI_MODEL = "deepseek-v4-pro"
$env:MINI_CODE_MAX_OUTPUT_TOKENS = "8192"
$env:MINI_CODE_THINKING = "disabled"
python -m minicode.main
```

After that baseline passes, change `MINI_CODE_THINKING` to `enabled`. MiniCode preserves DeepSeek's `reasoning_content` across tool-call turns. DeepSeek endpoints automatically select `max_tokens`; set `OPENAI_MAX_TOKENS_PARAM=max_tokens` only when another compatible gateway also requires it.

Run the optional live tool-call smoke test with the same environment variables:

```powershell
pytest -q tests/test_openai_live.py -s
```

Do not commit API keys. The interactive installer stores credentials in the user-level `~/.mini-code/settings.json`.

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Anthropic API key | — |
| `ANTHROPIC_AUTH_TOKEN` | Auth token (alternative) | — |
| `ANTHROPIC_BASE_URL` | API base URL | `https://api.anthropic.com` |
| `ANTHROPIC_MODEL` | Model name | — |
| `MINI_CODE_PROVIDER` | `anthropic` or `openai` | inferred, then `anthropic` |
| `OPENAI_API_KEY` | OpenAI-compatible API key | — |
| `OPENAI_BASE_URL` | OpenAI-compatible base URL | `https://api.openai.com` |
| `OPENAI_MODEL` | OpenAI-compatible model name | — |
| `OPENAI_MAX_TOKENS_PARAM` | `max_completion_tokens` or `max_tokens` | provider-aware |
| `MINI_CODE_MAX_OUTPUT_TOKENS` | Maximum generated tokens | provider default |
| `MINI_CODE_THINKING` | DeepSeek thinking mode: `enabled` or `disabled` | provider default |
| `MINI_CODE_REASONING_EFFORT` | Reasoning effort for compatible OpenAI models | provider default |
| `MINI_CODE_MODEL_MODE` | Set to `mock` for testing | — |

---

## 📖 Usage

### Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/tools` | List all tools |
| `/cost` | Show session cost |
| `/config` | Show configuration diagnostics |
| `/context` | Show context window usage |
| `/memory` | Show memory system status |
| `/exit` | Exit MiniCode |

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Enter` | Submit input |
| `Up/Down` | Input history |
| `PageUp/PageDown` | Scroll transcript |
| `Ctrl+C` | Cancel operation |
| `Ctrl+U` | Clear input line |

---

## 🧪 Development

```bash
# Clone
git clone https://github.com/Dopetaiga/MiniCode-Python.git
cd MiniCode-Python

# Run tests
pip install -e ".[dev]"
pytest

# Mock mode (no API key needed)
MINI_CODE_MODEL_MODE=mock python -m minicode.main
```

### Project Stats

| Metric | Value |
|--------|-------|
| Python files | 69 |
| Lines of code | ~15,000 |
| Built-in tools | 30+ |
| External dependencies | **0** |
| Optimizations | **93+** |
| Test pass rate | **98.9%** |
| Code readability | **9/10** |

---

## 🙏 Acknowledgments

- **[@LiuMengxuan04](https://github.com/LiuMengxuan04)** — Creator of [MiniCode](https://github.com/LiuMengxuan04/MiniCode) (TypeScript original)
- **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** — Design inspiration
- **All Contributors** — Everyone who contributed to MiniCode

---

## 📄 License

MIT — see [LICENSE](LICENSE) for details.

---

<div align="center">

**🇨🇳 由 [@QUSETIONS](https://github.com/QUSETIONS) 用 ❤️ 制作** | **🇺🇸 Made with ❤️ by [@QUSETIONS](https://github.com/QUSETIONS)**

*轻量终端 AI 编程助手 / Lightweight Terminal AI Coding Assistant*

[⬆ Back to Top](#minicode-python--minicode-python-中文版)

</div>

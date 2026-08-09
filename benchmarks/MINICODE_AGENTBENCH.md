# MiniCode AgentBench v1.2

MiniCode AgentBench v1.2 是面向 **MiniCode Python 版** 的项目级执行式评测。它测量 Agent 是否真的读取证据、修改文件、通过隐藏测试并正确调度 subagent，而不只判断最终回答是否“看起来合理”。

它适合作为项目实验与简历证据，但不是公共排行榜，也不能把结果直接写成 SWE-bench 或 Terminal-Bench 成绩。

## 为什么自建

- [SWE-bench](https://github.com/SWE-bench/SWE-bench) 使用真实 GitHub issue 和容器化仓库验证，可信度高，但复现实验的资源与工程成本更高。
- [Terminal-Bench](https://github.com/harbor-framework/terminal-bench) 面向终端任务，采用任务环境与测试脚本验证，同样偏重容器基础设施。
- [SWE-Lancer](https://openai.com/index/swe-lancer/) 来自真实自由职业软件工程任务，适合研究模型的软件工程能力，但不针对 MiniCode Python 当前的工具和 subagent 接口。

因此 v1.2 延续一个透明、可复现、能暴露失败原因的本项目基线；后续再接公共 benchmark，而不是用少量自建题替代行业评测。

## 任务构成

共 15 个任务：

| 能力 | 数量 | 核心验证 |
|---|---:|---|
| 证据读取与检索 | 3 | 必须命中指定事实与工具调用 |
| 结构化文件产物 | 1 | JSON 语义等价，不按字符串格式评分 |
| 代码修复 | 2 | 隐藏 pytest |
| 函数实现 | 3 | 隐藏 pytest、输入不变性与边界条件 |
| 安全修复 | 2 | 路径逃逸、递归敏感信息脱敏 |
| 跨文件修复 | 1 | 多文件读取与隐藏 pytest |
| subagent | 3 | `task` 工具、双次委派、`explore`/`plan` 路由参数 |

难度分布为 3 个 easy、6 个 medium、6 个 hard。其中 8 个代码任务使用隐藏测试；运行器会先证明原始缺陷版本无法通过、oracle 版本可以通过，避免出现“测试本来就绿”或“题目无正确解”的伪评测。

## 评分原则

每次 episode 只有在所有适用条件都满足时才通过：

1. 最终回答包含任务要求的关键证据；
2. 结构化文件与期望 JSON 语义一致；
3. 隐藏 pytest 返回成功；
4. 必需的父 Agent 工具调用达到下限；
5. 没有使用任务禁止的父 Agent 工具。

报告同时记录：

- episode 成功率、按类别和难度拆分的成功率；
- 每题多次运行时的“所有轮次通过”和“至少一次通过”；
- 父 Agent 工具轨迹、工具错误、平均动作数和延迟；
- 父 Agent 与 subagent 汇总的 API usage（取决于服务端是否返回 usage 字段）。

当前轨迹记录父 Agent 的工具名与调用参数；subagent 内部 token 会汇总，但其内部工具轨迹尚未展开。因此报告明确标注 `parent_tool_trace_only: true`。

## 版本与上游更新说明

- `benchmarks/minicode_agentbench_v1.jsonl` 是适配最新 `main`（`2141e8d`）的 v1.2：最新版 subagent 入口为同步 `task` 工具，支持 `explore`、`plan`、`general` 三种路由。
- `benchmarks/legacy/minicode_agentbench_v1_1.jsonl` 原样保存旧分支 v1.1，继续作为历史 DSV4 Flash 结果的题集证据。
- v1.1 使用 `delegate_task`/`subagent_control`，包含后台并行与自定义 agent 文件；v1.2 不把这些旧接口伪装成最新版能力，而是改测当前真实公开接口。
- 两个版本的 subagent rubric 不同，因此旧版 91.11% 不能直接当作最新版 v1.2 成绩；最新版必须重新运行后单独报告。

## 环境隔离与限制

每个 episode 都在独立子进程和独立临时目录执行，只写入公开 fixture；Agent 完成后才注入隐藏测试。单题默认 180 秒硬超时，每题完成后原子写入 checkpoint。MCP 被关闭，普通任务只暴露文件工具，只有 subagent 类任务获得委派工具；工作区外访问请求会被拒绝。隐藏测试进程使用隔离的 HOME/TEMP，并移除常见 API key/token 环境变量。

这属于**工作区级隔离**，不是 Docker、虚拟机或恶意代码安全沙箱。当前题目是仓库内受控 fixture；如果未来导入第三方任务，应切换到容器并限制网络、CPU、内存与执行时间。

## 运行方式（PowerShell）

在项目目录中执行离线自检，不会调用 API：

```powershell
.\.venv\Scripts\python.exe benchmarks\run_agentbench.py
```

运行 3 个代表性任务各一次：

```powershell
.\.venv\Scripts\python.exe benchmarks\run_agentbench.py --live `
  --case repair_chunking `
  --case repair_safe_join `
  --case dual_subagent_synthesis
```

正式实验建议每题独立运行 3 次，共 45 个 episode：

```powershell
.\.venv\Scripts\python.exe benchmarks\run_agentbench.py --live --runs 3
```

先用代表子集估算 token、时延和失败类型，再决定是否跑完整 45 次。默认报告写入 `test_results/minicode-agentbench-v1-2.json`；该目录应保持为本地运行产物，避免把冗长模型输出或环境信息直接提交。

## 最新 `main` 的 v1.2 连通性验证

2026-08-09 在最新上游 `2141e8d` 上完成两个低成本 live smoke：

- 模型：`deepseek-v4-flash`；模型目录探测后实际路由为 `OpenAIModelAdapter`、OpenAI-compatible、`https://api.deepseek.com`；
- 结果：2/2 通过；
- `read_fact` 由父 Agent 调用 `read_file` 并精确返回 `Orion-7`；
- `subagent_delegation` 只由父 Agent 调用一次 `task(agent_type="explore")`，父层没有直接读取目标文件，最终返回 `cobalt-29`。

这次真实 subagent 运行暴露并修复了两个问题：OpenAI-compatible 推理模型在工具回合中需要按 tool-call ID 回传 `reasoning_content`；旧 rubric 还可能把“子 Agent 失败后父 Agent 直接读文件”误判为成功。v1.2 现在禁止该父层回退，因此这里的通过结果确实来自 `task` 子 Agent。

脱敏机器报告见 [`results/dsv4_flash_v1_2_runtime_smoke_2026-08-09.json`](results/dsv4_flash_v1_2_runtime_smoke_2026-08-09.json)。它只证明最新版运行时、provider、基础文件工具和一次同步 subagent 链路连通，不是 v1.2 总体成功率。

## DSV4 Flash 历史正式结果（v1.1）

2026-08-09 在基于 MiniCode Python 固定快照 `0760162` 的旧开发分支上，以 DeepSeek V4 Flash 完成 v1.1 的 15 题 × 3 次，共 45 个独立 episode：

- 原始严格得分：41/45，成功率 **91.11%**，Wilson 95% 区间为 79.27%–96.49%。
- 13/15 个任务三轮全过，14/15 个任务至少成功一次。
- 隐藏测试任务：21/24，成功率 **87.50%**。
- 代码修复、证据、安全、跨文件和结构化产物类别均为 100%。
- subagent：8/9；其中一次是 rubric 只接受 `delegate_task`、但模型用功能等价的 `subagent_control` 正确完成，属于评分器假阴性。审计后能力结果为 42/45（93.33%），但简历采用保守原始分。
- 唯一稳定能力失败是 `implement_deep_merge`：0/3。模型三次都使用浅拷贝，未满足返回对象与输入嵌套结构完全解耦的要求。
- 平均每题 4.644 次父 Agent 工具调用、20.790 秒；合计 225 次 API 调用和 701,052 tokens，其中缓存命中 469,120 tokens。

机器可读摘要见 [`results/dsv4_flash_full_3runs_2026-08-09.json`](results/dsv4_flash_full_3runs_2026-08-09.json)。原始逐 episode 报告保存在被 Git 忽略的 `test_results/minicode-agentbench-v11-dsv4-3runs.json`。

rubric 修正后又单独运行 `subagent_evidence` 3 次，结果为 3/3；该复验只证明评分规则修复有效，不回填或替换原始 45 次主实验。

## 早期 DSV4 Flash smoke 快照（v1.1）

2026-08-09 使用已配置的 DeepSeek V4 Flash 对 3 个代表任务各运行 1 次，结果为 3/3：

| 任务 | 类型 | 结果 | 延迟 | 父 Agent 工具调用 |
|---|---|---:|---:|---:|
| `repair_chunking` | 代码修复 | 通过 | 15.095 s | 4 |
| `repair_safe_join` | 安全修复 | 通过 | 58.919 s | 8 |
| `parallel_subagent_synthesis` | 并行 subagent | 通过 | 12.042 s | 4 |

三题合计 25 次 API 调用、94,501 tokens，其中缓存命中 69,632 tokens；平均延迟 28.685 秒。脱敏后的机器可读快照见 [`results/dsv4_flash_smoke_2026-08-09.json`](results/dsv4_flash_smoke_2026-08-09.json)。

这是连通性和代表性能力验证，不是完整 benchmark 成绩：样本只有 3 题、每题只有 1 次，不能据此声称总体成功率为 100%。

## 如何形成可信简历证据

基础版表述：

> 为 MiniCode Python 版设计并实现 15 任务执行式 Agent 评测集，覆盖代码修复、安全边界、跨文件修改与 subagent 调度；引入隐藏 pytest、缺陷基线/oracle 双向校验、工具策略检查及 token/时延追踪，支持逐题临时工作区隔离和多轮复现实验。

v1.1 历史结果版表述：

> 为 MiniCode Python 版构建 15 任务执行式 AgentBench，使用隐藏 pytest、缺陷基线/oracle 双向校验、逐题子进程隔离、180 秒硬超时及 token/工具轨迹追踪；在固定旧版运行时与 DeepSeek V4 Flash 的 45 次独立实验中取得 91.1% 严格成功率，代码修复、安全、证据检索与跨文件任务均为 100%，并定位深层对象别名及工具 rubric 假阴性问题；随后将题集迁移到最新版 `task` subagent 接口并保留版本化历史基线。

不要写“达到行业 SOTA”“SWE-bench X%”或“生产级安全沙箱”，除非后续确实完成相应公共评测或容器安全工程。

## 下一版路线

1. 把 fixture 扩展为固定 commit 的真实小型仓库，并记录许可证与来源。
2. 增加超时、错误恢复、长上下文和多轮状态保持任务。
3. 展开 subagent 内部工具轨迹，统计并行收益、委派开销与重复工作率。
4. 用 Docker/Windows Sandbox 建立强隔离，并固定 Python、依赖和模型参数。
5. 引入第二个模型或无 subagent 消融组，报告置信区间而非单次分数。

# LiteCodeBench v1.0 — DeepSeek V4 Flash 实验报告

## 摘要

LiteCodeBench 是面向 MiniCode-Python 的项目级执行式 Agent 评测。本报告记录 2026-08-15 的两次 15 题单轮实验：第一次使用早期 MiniCode AgentBench v1.2 名称和原始契约，取得 14/15；随后针对唯一失败题澄清“输入不变”与“返回值无可变别名”的区别、增强隐藏测试，并在干净发布 worktree 上取得 15/15。

第二次结果不能被解释为模型能力从 93.33% 提升到 100%。它证明的是：原题存在契约表达不充分，修订后的 prompt 与 verifier 对齐后，模型能够生成满足更强别名隔离要求的实现。

## 实验设置

- 模型：`deepseek-v4-flash`
- 实际通道：`OpenAIModelAdapter`，OpenAI-compatible，`https://api.deepseek.com`
- 任务：15 个；证据 3、结构化产物 1、代码修复 2、函数实现 3、安全 2、跨文件 1、subagent 3
- 隐藏 verifier：8 个任务；每次 live 运行前先证明缺陷 fixture 失败且 oracle 通过
- 隔离：每个 episode 独立进程和临时工作区，单题 180 秒硬超时
- MCP：关闭
- Subagent：只在 subagent 类任务开放；报告仅记录父 Agent 工具轨迹
- 重复次数：每题 1 次，因此结果是单轮观测，不是稳定成功率估计

## 结果对比

| 运行 | 契约与 verifier | 结果 | Wilson 95% 区间 | API calls | Tokens | 平均时延 |
|---|---|---:|---:|---:|---:|---:|
| 原始运行 | “不修改输入”；只对 base 侧别名做反向变异 | 14/15（93.33%） | 70.18%–98.81% | 84 | 398,434 | 31.519 s |
| 改进运行 | 明确“不得保留任一输入的可变别名”；覆盖 base 与 override | 15/15（100%） | 79.61%–100% | 85 | 428,373 | 30.390 s |

改进运行的详细指标：

- Easy 3/3、Medium 6/6、Hard 6/6
- 证据、结构化产物、代码修复、函数实现、安全、跨文件、subagent 全类别通过
- 平均父 Agent 工具调用 5.6 次
- 时延中位数 20.626 秒，P95（nearest-rank）与最大值均为 92.786 秒
- 平均每题 5.667 次 API 调用、28,558.2 tokens
- Prompt cache hit rate 18.06%

## 唯一失败的成因

原始 `implement_deep_merge` 任务要求递归合并、替换非字典值、保留 base 未覆盖键并且“不修改输入”。模型返回的核心实现从 `result = dict(base)` 开始。这个操作只复制最外层字典：

```python
result = dict(base)
```

调用期间函数确实没有执行 `base[...] = ...`，但 `result["server"]["env"]` 仍与 `base["server"]["env"]` 指向同一个嵌套字典。隐藏测试修改返回值后，原始 `base` 同步变化，因此 verifier 失败。

这暴露出两个不同层面的原因：

1. **模型实现缺陷**：把顶层复制误当成递归结构的完全独立复制，并在最终解释中声称输入不会受影响。
2. **评测契约歧义**：“不修改输入”通常只承诺函数执行时不原地写入；原 verifier 实际要求更强的 postcondition——返回对象与两个输入之间不存在可变别名。隐藏标准强于 prompt 的明确程度。

这个失败在历史 v1.1 的三次运行中也出现过 3 次，说明浅拷贝不是偶然格式错误，而是稳定的语义盲点；但不能把全部责任归给模型，因为原始自然语言契约没有清楚命名 alias-freedom。

## 改进内容

### 1. 澄清任务契约

Prompt 从“do not mutate either input”改为：

> do not mutate or retain mutable aliases to either input

这把执行期间的不变性和返回后的引用隔离拆成两个可验证要求。

### 2. 加强隐藏测试

- 保留 base 侧嵌套字典反向变异测试；
- 新增 override 侧嵌套字典和列表反向变异测试；
- 继续要求缺陷 fixture 失败、oracle 通过，防止测试失去区分度。

### 3. 改进统计报告

Runner 新增 Wilson 95% 区间、时延中位数/P95/最大值、平均 API 调用、平均 token 和缓存命中率，避免只展示单一成功率。

### 4. 修复 provider 预检

旧版离线 preflight 在同时存在本地 Anthropic 代理和 DeepSeek OpenAI 配置时可能误报 `127.0.0.1`，而 live 运行实际走 DeepSeek。新版优先读取显式 `settings.provider`，无需联网探测即可正确报告 `openai → https://api.deepseek.com`。

## 改进后的实现行为

增强版运行中，模型主动构造 `_copy_value`/`_copy_dict`，递归复制字典和列表；还创建了一个临时自检文件，覆盖 base、override、MCP 风格嵌套、列表替换和返回值反向变异。最终隐藏 verifier 为 3/3。

这个结果说明：精确指出引用隔离约束后，模型能够处理该语义；它不证明模型在模糊需求下已经稳定掌握深复制。要评估稳定性仍需在冻结 commit 上运行至少 3 轮，并加入等价但不同措辞的盲测题。

## 可信度与限制

- LiteCodeBench 是项目内部 benchmark，不是 SWE-bench、Terminal-Bench 或行业排行榜。
- 15/15 只有 15 个 episode；Wilson 下界仍为 79.61%。
- 两次运行的 prompt/verifier 不同，不应直接计算“提升 6.67 个百分点”。
- 工作区隔离不是 Docker 或虚拟机安全沙箱。
- Subagent 3/3 只证明父层正确调用 `task` 并满足最终 rubric；内部工具轨迹尚未展开。
- 正式简历数字应使用固定 commit 的 3 轮结果，并同时披露任务数、重复次数和置信区间。

## 面试回答建议

> 我为 MiniCode-Python 构建了 LiteCodeBench，一套 15 题执行式 Agent 评测，覆盖证据检索、代码修复、安全、跨文件修改和 subagent 调度。它使用隐藏 pytest、缺陷基线/oracle 双向校验、逐题进程隔离、硬超时以及工具和 token 轨迹，而不是只用 LLM 判断回答是否合理。第一次 DSV4 单轮实验为 14/15，唯一失败是 deep merge 使用浅拷贝，模型声称没有修改输入，但返回值仍与输入共享嵌套引用。进一步分析发现原 prompt 的“不修改输入”和 verifier 要求的“无可变别名”并不完全等价。我没有放宽测试，而是明确 alias-freedom 契约，并增加 base 与 override 两侧反向变异测试；增强版单轮为 15/15。这个案例体现了我不仅会报 benchmark 分数，还会审计题目有效性、定位模型失败与规格歧义，并通过可执行测试闭环改进。

不要把结果表述为“SWE-bench 100%”“行业 SOTA”或“模型能力提升 6.67%”。

## 机器报告

- 原始 14/15：[`litecodebench_v1_dsv4_full_1run_2026-08-15.json`](litecodebench_v1_dsv4_full_1run_2026-08-15.json)，GitHub LF 规范化 SHA-256 `DC07AC62CDFBDFCB5EDE5B5F01217E87EFF5BDA0C99910453F7169AE9E974B06`
- 改进 15/15：[`litecodebench_v1_dsv4_full_improved_1run_2026-08-15.json`](litecodebench_v1_dsv4_full_improved_1run_2026-08-15.json)，GitHub LF 规范化 SHA-256 `9C620B9DFF58E1B11E52321D3740915E89BA4BE67B3C1A20A0062A92857C6B74`

两份报告均已扫描，不包含 API key。

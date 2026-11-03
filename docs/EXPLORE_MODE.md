# GeoPhysAgent Explore 模式

> 状态：Prototype  
> 目标版本：v0.1  
> 最后更新：2026-09-04

## 1. 目的

Explore 模式用于处理尚未沉淀为正式 Geo Tool 的长尾表格分析需求。LLM 可以生成一次性 Python 计算代码，但代码不能直接访问原始文件、网络、环境变量、进程或 GeoPhysAgent 源码。

Explore 结果默认标记为 `exploratory`。程序成功运行只证明代码产生了结果，不证明方法适用或地球物理结论正确。

## 2. 数据接口

生成代码获得几个稳定的只读输入快照：

- `COLS`：列名到列值列表的映射，获取整列时优先使用；
- `ROWS`：行字典列表，适合需要保持多列行对齐的计算；
- `COLUMN_NAMES`：列名列表；
- `DATA`：包含资产引用、结构元数据和底层行数据的数据包；
- `math`、`statistics`：预加载标准库模块。

```text
DATA
├── asset       # 使用 asset://primary，不含真实路径
├── columns     # 列名
├── rows        # JSON 行字典列表；使用 row["列名"]，不是 row[0]
├── row_count
└── profile     # 编码、分隔符、数值列、深度候选等
```

代码必须把 JSON 可序列化结果赋给全局变量 `RESULT`。

```python
values = [value for value in COLS["GR"] if value is not None]
RESULT = {"mean_gr": sum(values) / len(values)}
```

`DATA` 本身不是列映射，因此不能写 `DATA["GR"]`；`profile` 只包含结构元数据，不包含曲线数据。文本中的空值会转换为 Python `None`，数值计算必须先处理空值。常见的错误数据访问会在启动子进程前被契约检查拒绝，并返回可操作的修正提示。

## 3. 当前限制

- 禁止 `import`；
- 禁止 `open`、`eval`、`exec`、`compile` 和反射入口；
- 禁止私有及双下划线属性；
- 禁止 `while`、类定义和异步结构；
- 拒绝静态可识别的超大 `range`、字面量重复和幂运算；
- 不向代码提供文件、网络、进程和环境变量 API；
- 只提供有限 builtins，以及预加载的 `math`、`statistics`；
- 在 `python -I -B` 独立进程中运行；
- 默认超时 10 秒；
- 输入 JSON 上限 32 MiB；
- 结果 JSON 上限 2 MiB；
- stdout/stderr 保存并截断到 64,000 字符；
- 每次尝试使用独立 UUID 工作区。

这套机制是应用层策略隔离，不是操作系统级安全沙箱。Windows 初版尚未提供硬内存配额、容器、低权限账户或虚拟机隔离，因此不能运行来自不受信任攻击者的代码。后续若开放更宽松的 Python、第三方包或文件访问，必须先引入真正的 OS/容器隔离。

## 4. 追踪内容

每个 Explore 工作区保存：

```text
explore/<run-id>/
├── analysis.py
├── data.json
├── result.json
├── run-record.json
├── stdout.txt
├── stderr.txt
└── _runner.py
```

`run-record.json` 记录代码哈希、输入资产哈希、Python 版本、策略版本、资源限制、状态和错误。模型只收到逻辑 Run ID，不接收本机工作区路径。

Agent 级运行摘要保存在输出目录的 `explore-agent-run.json`，分别记录 `execution_status` 与 `task_status`，以及每次代码尝试的状态、代码哈希、提案文件、对应 Run ID 和失败原因。所有模型生成的代码提案（包括在执行前被契约或安全策略拒绝的代码）保存在 `explore-agent-attempts/`。代码执行错误只向模型反馈脱敏后的异常类型与简要原因，本机 traceback 和工作区路径不会发给模型。

Explore 允许在预算内执行多个成功的中间步骤：一次 Python 成功运行只表示产生了一个可用结果，不自动表示用户任务完成。MiniMax 返回的文本化 `<minimax:tool_call>` 只有在名称位于本轮允许列表时才会被适配器转换成结构化 Tool Call；它不能直接成为最终回答。只有同时存在成功执行结果和通过协议检查的最终说明时，`task_status` 才能成为 `completed`。若只有执行结果而没有合格回答，状态为 `incomplete`，不会生成成功式报告。

一个输出目录只属于一次 Explore 调用。若目录中已经存在 `explore-agent-run.json`、`explore-response.md`、`explore-agent-attempts/` 或 `explore/`，新调用会在模型请求前失败并要求选择新目录，避免覆盖追踪文件后留下无法归属的历史 UUID。

## 5. 候选 Tool 提升

`promote` 不会自动注册或启用生成代码。它只把成功 Explore Run 复制为 `pending_review` 候选，并要求完成：

1. 人工代码审查；
2. 输入输出 Schema；
3. 单元测试；
4. 黄金数据测试；
5. 领域 Validator；
6. 依赖审查。

候选目录会保留代码、源 RunRecord、示例结果、审查清单和 Tool Contract 模板。完成上述步骤后，开发者才能手动把它重构为 Built-in Tool 或独立 MCP Tool，并通过正常代码评审完成注册。候选目录本身永远不会被运行时自动扫描或启用。

## 6. 与正式 LLM-first 流程的关系

- 正式用户任务：必须经过 LLM Agent；Agent 优先选择已注册、可验证的正式 Tool；
- Strict Tool：`analyze`、`preprocess`、`segy` 等确定性能力，是 Agent 的本地计算底座，也可由开发、测试和批处理接口直接执行；
- Explore Tool：正式 Tool 不足时，由 LLM 生成受限代码，结果始终标为探索级；
- `promote`：创建未激活候选，不改变运行时能力；候选通过人工审查后才能成为 Agent 可选择的正式 Tool。

当前独立 `agent` 和 `explore` 命令是过渡入口。目标 `run` Runtime 会在同一个 AgentRun 内依据能力、风险和计划策略选择 Strict 或 Explore，而不是要求最终用户预先决定执行模式。

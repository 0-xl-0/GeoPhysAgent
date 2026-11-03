# Harness 生命周期与恢复（Increment 6）

## 1. 一个用户任务，一个权威 TaskRun

Increment 6 统一了确定性 CLI、LAS Agent 和 Explore 的任务状态。顶层关系为：

```text
TaskRun
├── TaskSpec + ExecutionPlan
├── AgentRun（仅 Agent/Explore）
│   ├── Model Turn 事件
│   └── Tool Attempt（实际 Provider 调用）
├── Plan Step
├── Evidence
├── Validation Execution
└── Artifact
```

LAS Agent 不再创建一个成功的 LAS 子任务后，把尚未生成的最终回答误报为任务成功。Explore 的 `explore-agent-run.json` 只保留 `diagnostic_only` 诊断信息；`run-record.json` 是唯一任务状态权威来源。

顶层成功要求同时满足：

- 所有必需 Evidence 已绑定；
- 所有必需 Validator 已执行且没有 `fail`；
- 必需最终 Artifact 存在、哈希一致；
- 最终报告/回答显式包含其依赖的事实 Evidence ID；
- Agent Run（如果存在）已完成。

## 2. 四层 Validator Registry

`ValidationCoordinator` 按固定顺序执行：

1. Data；
2. Semantic；
3. Process；
4. Result。

内置 Validator 包括：

- `core.data.validation_evidence`：聚合 Tool 产生的领域 ValidationReport；
- `core.process.required_evidence`：检查计划要求的 Evidence 是否满足；
- `core.result.artifact_integrity`：检查产物存在性、大小、SHA-256 和必需角色；
- `core.result.final_artifact_evidence`：检查最终文本是否引用全部必需事实 Evidence ID。

验证记录写入 `RunRecord.validations`。计算执行完成但证据或结果验证失败时，任务进入 `incomplete/completed`，不会伪装为成功。

## 3. 检查点与单写者

每次状态、Attempt、Evidence、Validation 或 Artifact 变化都会建立新修订：

```text
<run>/
├── checkpoint.json              # 原子提交指针，权威修订
├── .checkpoints/
│   ├── 00000001/
│   │   ├── run-record.json
│   │   ├── state.json
│   │   └── artifact-manifest.json
│   └── ...
├── run-record.json              # 当前修订的人类可读镜像
├── state.json
├── artifact-manifest.json
├── task.json                    # 不可变
├── plan.json                    # 不可变
├── input-manifest.json          # 可分享，路径为 asset://
├── input-locations.json         # 仅本地恢复使用，不进入模型/事件
└── events.jsonl
```

新修订先完整写入版本目录，再更新根目录镜像，最后原子替换 `checkpoint.json`。如果进程在中途终止，Loader 仍读取上一个已提交修订；`validate` 会报告根镜像不一致、未提交检查点、临时文件或损坏事件尾行。

写任务必须获得 `.writer.lock`。锁记录 PID、主机和随机 Token；同一目录只能有一个写者。同主机进程异常退出后，下一次操作可以回收确认已失效的 PID 锁。

## 4. RecoveryPolicy

自动恢复同时受两个条件约束：

- Tool Definition 必须声明 `replay_policy=safe`；
- 错误类别和最大尝试次数必须符合 `RecoveryPolicy` 与 `PlanStep.max_attempts`。

默认瞬时错误类别为 `ConnectionError`、`InterruptedError`、`OSError` 和 `TimeoutError`。解析错误、Schema 错误和领域输入错误不会自动重试。

恢复时：

- 已完成 Step 的 Evidence 和 Artifact 完整：复用，不重复调用 Tool；
- 已完成输出不完整：只有安全 Tool 且仍有预算时才失效旧 Attempt 并重放；
- 上次进程留下 `running` Attempt：标记为 `InterruptedExecution`，然后按安全策略处理；
- Agent/Explore Runtime 不是安全可重放 Tool：自动恢复进入 `waiting_for_user`，不会重复产生模型调用或代码执行；
- 终态不可恢复，也不能追加 Attempt、Evidence、Validation 或 Artifact。

## 5. CLI

```powershell
# 查看权威状态、检查点和时间线摘要
python -m geophysagent status runs/example

# 输出完整结构化状态与重建时间线
python -m geophysagent status runs/example --json

# 恢复非终态任务；只自动重放 safe Tool
python -m geophysagent resume runs/example

# 空闲任务立即取消；活动任务写入取消请求，在安全边界生效
python -m geophysagent cancel runs/example --reason "用户终止任务"

# 离线验证检查点、不可变文件、输入、事件、状态和 Artifact
python -m geophysagent validate runs/example
python -m geophysagent validate runs/example --json
```

`status` 即使发现完整性问题也会尽力显示可读取的权威状态；`validate` 在发现错误时返回非零退出码。

## 6. 时间线重建

`rebuild_timeline` 以 `events.jsonl` 为主，并使用已提交 `RunRecord`、`state.json/checkpoint.json` 和 `ArtifactManifest` 补齐缺失的 Attempt/Artifact 快照。每一项标记来源，避免把日志文本误认为权威状态。

重建与离线验证检测：

- Task ID 不一致和状态事件断层；
- 无效或半写的 JSONL 行；
- Checkpoint 文件缺失或哈希变化；
- 根镜像与已提交修订不一致；
- Task/Plan/Input Manifest 被修改；
- 输入资产消失或内容变化；
- Artifact 丢失、路径逃逸、符号链接、大小或哈希变化；
- 终态仍存在 running Attempt/AgentRun；
- 同一个确定性 Plan Step 出现多个有效 completed Attempt；
- 成功任务缺少必需 Validator 或包含失败 Validator。

## 7. 取消边界与已知限制

当前取消在模型轮次、Tool 调用前、步骤之间和验证前检查。同步运行中的第三方函数不能被 Python Harness 强制安全终止，因此长耗时外部软件需要在后续 MCP/Job Tool 中实现协议级取消和进程级终止。

`resume` 能自动恢复确定性 safe Tool。Agent/Explore 中断后会进入 `waiting_for_user`，因为重复模型请求或重复执行生成代码不满足安全重放条件。未来可在保存模型会话检查点和幂等协议后增加人工确认式继续。

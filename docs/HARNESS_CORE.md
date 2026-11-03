# GeoPhysAgent Harness Core（Increment 4–6）

## 1. 目的

Increment 4 把 Harness 从若干互不相连的类型和 Tool Registry，提升为真实执行确定性计划的领域内核。内核负责回答五个问题：

1. 输入究竟是什么类型，哪些操作已实现？
2. 当前任务允许调用哪些 Tool，计划是否合法？
3. 每一步是否真的执行、由哪个 Provider 执行、结果是否符合 Schema？
4. 成功结论依赖的证据和产物是否齐全、是否仍完整？
5. 任务状态与计算执行状态分别是什么？

MCP、Built-in、Explore 只是不同 Tool Provider。MCP 不拥有任务状态，也不能绕过 Harness 的计划、Schema、证据和 Artifact 规则。

## 2. 数据类型先于 Parser

`register_asset` 通过 `AssetAdapterRegistry` 生成 `DataAsset`：

```text
path
  -> Adapter detection
  -> DataAsset(format, data_type, adapter_id, capabilities, sha256)
  -> require_capability
  -> format-specific Parser / GeoTool / Explore
```

首批 Adapter：

| Adapter | 数据类型 | 当前能力 |
|---|---|---|
| `well-log-las` | `well_log` | 表格检查/读取、预处理、Explore、LAS QC |
| `delimited-text` | `tabular` | 表格检查/读取、预处理、Explore |
| `seismic-segy` | `seismic` | 资产检查、只读 SEG-Y Header/Trace 检查与基础 QC |
| `binary-with-text-extension` | `unknown_binary` | 仅资产检查 |

因此 SEG-Y 不会再经过表格文本编码、分隔符和列宽启发式。`segy` 工作流通过显式 `seismic.segy.inspect` capability 进入有界的二进制 Parser；Explore 仍没有这项能力，会在模型调用前得到 `UnsupportedAssetCapabilityError`。

## 3. 执行契约

- `TaskSpec`：用户/应用请求、workflow、授权资产和 Tool allowlist；
- `ExecutionPlan`：版本化目标、步骤、依赖和必需证据；
- `PlanStep`：已注册 Tool、参数、依赖、最大尝试次数和 Evidence ID；
- `ExpectedEvidence`：证据类型、来源 Step 和结果 JSON Pointer；
- `StepAttempt`：一次实际 Provider 调用及其状态、时间、结果哈希或错误；
- `EvidenceRecord`：绑定后的值、状态和值哈希；
- `ArtifactRecord`：任务内相对路径、MIME、大小、SHA-256 和来源 Step；
- `RunRecord`：任务状态、执行状态、Attempts、Evidence、Artifacts、warnings 和 errors 的权威快照。

ExecutionPlan 不是给模型直接执行的自然语言。所有 Step 必须通过 `PlanPolicy`，然后只能由 `ToolProviderRegistry` 路由。

## 4. 状态语义

```text
created
  -> inspecting_input
  -> planning
  -> ready
  -> executing
  -> validating
       -> completed
       -> completed_with_warnings
       -> incomplete
       -> failed
```

- `task_status` 表示用户/领域任务是否满足成功条件；
- `execution_status` 表示计算是否实际完成；
- 计算成功但缺少必需 Evidence：`task_status=incomplete`、`execution_status=completed`；
- Tool 或执行失败：`failed/failed`；
- `completed_with_warnings` 必须有实际 warning；
- 任一终态都不能再通过状态机进入执行状态。

## 5. 证据与事实绑定

ExpectedEvidence 使用 JSON Pointer，例如：

```json
{
  "evidence_id": "las-validation",
  "kind": "validation",
  "source_step_id": "las-qc",
  "selector": "/analysis/validation",
  "required": true
}
```

Tool 完成后，Harness 从已通过 Output Schema 的 `GeoToolResult.data` 解析该 Pointer，保存值与 canonical JSON SHA-256。缺少必需 Evidence 时不能进入成功终态。报告层后续必须按 Evidence ID 获取关键事实，而不是重新从自由文本猜测数字。

## 6. Run 与 Artifact 持久化

```text
<output>/
├── task.json
├── plan.json
├── input-manifest.json
├── state.json
├── run-record.json
├── artifact-manifest.json
├── events.jsonl
└── <tool outputs>
```

JSON 快照采用同目录临时文件、flush、fsync 和原子替换。Artifact 只能登记任务目录内的普通文件；清单保存相对路径，拒绝路径逃逸和符号链接，并可使用 `ArtifactStore.verify` 复核大小与 SHA-256。

## 7. 已迁移路径

- `analyze`：完整 LAS QC 计划由 GeoDomainHarness 执行；
- `preprocess`：参数化 Pipeline 由 GeoDomainHarness 执行；
- `segy`：只读 Header/Trace 检查、流式统计和基础 QC 由 GeoDomainHarness 执行；
- LAS Agent：顶层 TaskRun 包含 AgentRun、实际 LAS Tool Attempt、最终回答、Evidence 和 Validation；
- Explore：顶层 TaskRun 包含 AgentRun、生成代码 Tool Attempt、代码/结果 Artifact、最终回答和 Validation。

`explore-agent-run.json` 只作为诊断文件，不再保存另一套权威任务状态。所有入口都以顶层 `run-record.json` 的 `task_status/execution_status` 为准。

## 8. 安全与失败原则

- 输入路径不会进入模型 Tool Result；
- Input Manifest 使用 `asset://<id>`；
- Schema 校验递归覆盖对象和数组；
- 未实现的 Schema 关键字明确报错；
- Provider 身份和 Tool 名称必须与注册表一致；
- 非安全重放 Tool 不能配置自动重试；
- 失败也必须保存 Attempt、结构化 Error 和终态；
- `pi/` 不参与任何 import、构建、测试或运行。

## 9. Increment 5 对 Harness 的验证

`seismic.inspect_segy` 没有创建地震专用执行旁路。它与 LAS 一样通过 Tool Provider Registry 调用，返回符合 Output Schema 的 `GeoToolResult`，由 Harness 绑定 Binary Header、道摘要、振幅统计、Validator 和 Artifact 五组必需证据。缺少任一必需证据仍进入 `incomplete`；Tool/Parser 失败仍进入 `failed/failed`。

第二领域验证表明现有 `DataAsset`、Tool、Plan、Evidence、Run 和 Artifact 契约可以承载二进制流式工作流。唯一需要扩展的是资产 capability 与新的领域结果 Schema，而不是修改 Harness 的状态语义。

## 10. Increment 6 生命周期加固

- `TaskRunSession` 让 Agent/Explore 复用 Harness 状态、Evidence、Artifact 和 Validation 规则；
- AgentRun、Plan Step、Tool Attempt 分层记录；
- 四层 ValidationCoordinator 控制成功终态；
- 版本化 Checkpoint 和单写者锁保证中断后仍有可读提交；
- RecoveryPolicy 只自动重放 `replay_policy=safe` Tool；
- `status/resume/cancel/validate` 提供生命周期操作和离线完整性检查；
- 完整的已完成 Step 在恢复时复用，损坏或缺失结果会被检测；
- 终态 RunRecord 拒绝追加 Attempt、Evidence、Validation 或 Artifact。

详细结构、恢复判断和 CLI 见 [HARNESS_LIFECYCLE.md](HARNESS_LIFECYCLE.md)。

## 11. 当前非目标

当前 Harness 仍不包含 MCP、运行中同步 Python 函数的强制终止、Agent/Explore 模型会话自动重放、OS 级代码沙箱和完整的 Event Sourcing。Increment 5 的 SEG-Y 能力也不包含图件、处理、解释和完整 Rev2 扩展语义。这些不是通过添加空接口来“完成”，而是在后续增量中用真实垂直工作流验收。

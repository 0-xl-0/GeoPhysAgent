# Context Assembly 与 Agent 恢复

Increment 7A.1 把模型上下文从 `general_agent.py` 中不断追加的临时消息列表，提升为由 Harness 管理、受预算约束并可审计恢复的运行视图。Context 不是长期 Memory，也不是新的事实来源。

## 权威边界

- `RunRecord.agent_checkpoint` 保存恢复所需的消息、轮次、已批准计划、完成步骤、Evidence ID 和人工续跑原因；它随 Run Store 的版本化快照原子提交。
- 根目录 `checkpoint.json` 仍是 Run Store 的提交指针；新的运行不再创建独立 `agent-checkpoint.json`。
- 旧运行中的 `agent-checkpoint.json` 只允许读取一次并迁入 `RunRecord`，迁移会产生 `agent_checkpoint_migrated` 事件，不会把旧文件继续作为权威来源。
- 每次模型调用前记录 `ContextSnapshot`，只保存策略版本、区块哈希、Tool/Provider 版本、估算 Token、Evidence/Artifact 引用和压缩边界，不保存完整系统 Prompt、密钥或认证 Header。

## Context 区块

Context 按以下来源投影：

1. 系统安全策略；
2. TaskSpec 中的用户目标；
3. Harness 已审批计划的安全结构；
4. Input Manifest 中的资产格式、类型、哈希和 capability；
5. 当前 capability 与 allowlist 允许的 Tool Definition；
6. RunRecord 与 Artifact Manifest 中的 Evidence/Artifact 引用；
7. Event Log 的事件 ID/类型投影；
8. 最近的模型消息与完整 Tool Call/Tool Result 单元。

用户文本、模型文本、Tool Result、文件内容、Provider 描述、未来 MCP 返回文本和压缩摘要都不能进入系统安全策略，也不能扩大权限或 Tool allowlist。

## 默认预算

| 范围 | 默认限制 |
|---|---:|
| 总上下文估算 | 24,000 tokens |
| 系统区块 | 4,000 tokens |
| Tool Definitions | 5,000 tokens |
| 历史消息 | 14,000 tokens |
| 单次反馈给模型的 Tool Result | 32 KiB |
| 最少保留的最近消息单元 | 4 |
| 自动压缩阈值 | 总预算的 80% |

Token 数是确定性的保守估算，只用于 Context Policy，不代替 Provider 返回的计费用量。

超过单次 Tool Result 限制时，完整脱敏结果写入并登记为 Artifact，Evidence 保存 Artifact 哈希与受限摘要，模型只收到该受限投影。超过历史或总预算时，Harness 生成“已验证引用摘要 + 最近原始尾部”；Tool Call 与对应 Tool Result 始终作为不可拆分单元保留或裁剪。压缩摘要只是导航，不是 Evidence。

可在下一个安全模型边界请求一次诊断压缩：

```powershell
geophysagent run --resume --output runs/run-las --compact-context
```

## 最终回答门禁

通用 Agent 完成计划后必须调用 `submit_final_answer`，不能再用自由文本直接结束任务。模型需要提交：

- 解释性文字；
- 至少一个 Evidence ID 与 JSON Pointer；
- 至少一项能力边界。

每个事实引用必须解析到不可变 Evidence 中的标量值。Harness 生成 `agent-claim-map`，在最终报告中单独展示“已验证事实”和“模型解释”，并由 `core.result.claim_evidence` Validator 复核引用、值和哈希。无法解析、指向对象/数组、重复或与 Evidence 不一致的引用会被拒绝并要求模型重写。

该门禁能阻止没有 Evidence Pointer 的事实进入“已验证事实”区，但不会把模型解释自动升级为专业结论。Increment 8 已加入 LAS 版本化规则、合成黄金回归和通用评测；授权真实黄金样本与专业结论走查仍是发布前人工门禁。

## 中断与恢复

- 模型调用前先提交 AgentCheckpoint 和 ContextSnapshot；未确认的模型调用不会在后台自动重放。
- 进程在模型响应后终止时，显式 `run --resume` 会把遗留的 `running` AgentRun 收敛为 `incomplete`，再创建新的 AgentRun。
- 压缩提交中断时，旧 `checkpoint.json` 仍指向最后一个有效快照；孤儿快照保留为诊断信息，后续提交自动使用新的修订号。
- 模型故障或 Context Budget 失败进入 `waiting_for_user`，调整配置或预算后才能显式续跑。
- 已完成 Tool 通过 Attempt/Evidence 恢复，不因 Context 压缩而自动重放；非幂等模型调用和生成代码仍保持人工续跑边界。

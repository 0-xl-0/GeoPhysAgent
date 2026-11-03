# ADR-013：LLM-first、Local-execution、Harness-governed

- 状态：Accepted
- 日期：2026-09-07
- 适用版本：v0.1 及后续

## 背景

GeoPhysAgent 同时具有可独立测试的确定性 Tool 和最小 LLM Agent。把二者描述成“无网模式”和“LLM 模式”容易造成误解：最终用户可能被迫选择具体命令和 Tool，LLM 则看起来只是可选的回答增强层。这不符合 Agent 产品定位。

本地执行、是否使用 LLM、模型部署位置是三个不同维度。专业计算可以留在本机，同时由云端或本地 LLM 负责理解、规划和解释。

## 决策

1. 正式最终用户任务采用 LLM-first。自然语言任务必须创建 AgentRun，由 LLM 参与意图理解、计划候选、Tool 选择、结果观察和最终解释。
2. LLM 是控制入口，不是专业事实计算器。数值、单位、异常位置和质量结论仍来自受注册、Schema 和 Validator 约束的 Tool。
3. 数据和专业计算本地优先。大型原始数据不直接进入模型上下文，只发送任务所需的受控元数据、摘要、Evidence 和 Tool Result。
4. GeoDomainHarness 对模型计划拥有最终执行权。LLM 不能绕过 capability、allowlist、PlanPolicy、权限、Evidence、Validator、Artifact 和状态机。
5. 当前 MiniMax 云端 Provider 是 v0.1 的真实模型验收实现；ModelProvider 保持可替换，未来可以接入本地 LLM，但任何正式用户任务仍必须经过某个 LLM Provider。
6. `inspect`、`analyze`、`preprocess`、`segy` 等确定性命令继续保留，定位为开发、测试、批处理和诊断接口，不作为最终用户主流程。
7. 模型不可用时不得静默绕过 Agent。任务必须明确进入失败或等待状态，并允许用户重试、切换 Provider 或显式退出。
8. 默认回归测试继续使用 Fake Model/Fake Transport，以保证可重复和无密钥 CI；允许调用真实 MiniMax，并要求候选发布前通过少量显式启用的在线端到端验收。

## 目标调用路径

```text
用户自然语言
  → Application Service
  → Agent Runtime / ModelProvider
  → 候选 ExecutionPlan
  → GeoDomainHarness 审批与执行
  → Built-in / Explore / MCP Tool Provider
  → Validation + Evidence + Artifact
  → Agent 基于已验证事实生成最终回答
```

## 测试约束

- 无网测试验证 Tool 算法、Harness 状态、计划策略、恢复和 Fake Model 轨迹。
- MiniMax 在线测试验证真实协议、Tool Call、上下文回填、多轮停止和最终回答 Evidence 引用。
- 在线测试只能从环境变量读取 API Key，必须显式启用，并应提示可能产生费用。
- 在线断言关注结构化轨迹、状态和安全边界，不要求自然语言逐字一致。
- 真实模型失败不能被误判为专业计算失败，也不能破坏已提交检查点。

## 影响

- 通用 Agent Runtime 从后续增强提升为 P0，并先于 MCP Provider 完成基础闭环。
- MCP 仍只是 Agent 可选择的 Tool Provider，不承担对话、规划或领域状态。
- GUI、CLI 和未来 API 都必须调用同一 Application/Harness，不复制 Agent 或状态机。
- 确定性 Tool 仍必须能独立测试和执行，这是 LLM Agent 可验证、可复现和可诊断的基础。

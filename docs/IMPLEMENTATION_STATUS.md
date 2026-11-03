# GeoPhysAgent 实现状态

> 当前阶段：v0.1 Increment 8 工程实现完成，真实数据/在线重复性/专业走查待验收
> 下一目标：完成 Increment 8 候选发布的三项外部验收门禁
> 最后更新：2026-09-16

目标产品采用 LLM-first、Local-execution、Harness-governed。`run` 已成为通用自然语言入口；`agent` 保留为 LAS 单 Tool 兼容/诊断闭环，直接确定性命令用于开发、测试、批处理和诊断。

## 已完成

- Python 工程骨架和命令行入口；
- 基础 LAS 2.x 段解析；
- 输入文件 SHA-256 追踪；
- `GeoToolResult`、`ValidationReport` 和 `AnalysisResult` 最小领域对象；
- 曲线有效值、缺失率、最小值、最大值和均值；
- 深度方向、重复深度和不规则采样检查；
- JSON 结构化结果和 Markdown 报告；
- 133 项无公网单元/集成测试与 CLI/MCP/loopback API 冒烟测试，另有 3 项默认跳过的付费在线模型门禁；
- 单一 TOML 多命名 ModelProfile、默认 Profile、一轮 CLI 覆盖、环境变量凭据引用、列表和配置诊断；
- Anthropic Messages-compatible 同步/SSE 客户端，以及 OpenAI Chat Completions-compatible 同步客户端；
- OpenCode Go `minimax-m3` 默认 Profile、`kimi-k3` OpenAI-compatible 示例和旧 MiniMax 直连备用 Profile；
- OpenCode Go 会话路由 Header 绑定 Harness Task ID，跨模型轮次和人工续跑保持稳定；
- 受约束的最小 Agent Loop：必须先调用 LAS QC Tool，再依据结果回答；
- LLM Tool Result 隐藏本机绝对输入路径；
- 未授权资产和未知 Tool 不可执行；
- 通用 DataAsset，以及 LAS/TXT/CSV/TSV 表格视图；
- 文本编码、分隔符、表头、数值列和深度候选探测；
- Built-in/Explore Tool Provider Registry；
- 参数化预处理 Pipeline：重命名、选列、排序、去重、丢弃空值、线性插值；
- Explore Python Workspace、策略检查、独立进程、超时和大小限制；
- Explore 稳定 `ROWS/COLS` 数据契约、执行前契约检查和可修复错误反馈；
- 支持预算内多次成功的 Explore 中间步骤，不再把第一次代码成功等同于任务完成；
- MiniMax 文本化 Tool Call 归一化、最终回答协议检查，以及执行/任务状态分离；
- Explore 输出目录单次运行约束，防止追踪文件覆盖和 UUID 工作区堆积；
- 生成代码、输入哈希、运行环境、stdout/stderr 和结果追踪；
- `pending_review` 候选 Tool 提升流程；
- AssetAdapter Registry 和显式 capability 路由；
- LAS、分隔文本、可只读检查的 SEG-Y 和伪文本二进制适配；
- 不支持的 SEG-Y Explore 和未知二进制在 Parser、Tool 和模型调用前得到可操作的拒绝；
- `TaskSpec`、`ExecutionPlan`、`PlanStep`、`ExpectedEvidence`、`StepAttempt`、`RunRecord` 和 `ArtifactManifest`；
- 显式任务状态机，以及彼此独立的 `task_status`/`execution_status`；
- 计划 Tool allowlist、依赖环检查、Provider 身份检查和重放策略约束；
- Tool 输入/输出的递归 JSON Schema 校验；
- 文件型 Run Store、原子 JSON 快照和领域事件；
- Artifact 任务目录约束、相对路径、SHA-256 登记与完整性复核；
- 必需证据门禁：计算完成但证据不足只能进入 `incomplete`；
- `analyze`、`preprocess` 和 Agent 的确定性 LAS QC 子任务通过 GeoDomainHarness 执行；
- SEG-Y 3200-byte 文本头 ASCII/EBCDIC 识别和基础 Binary Header 解析；
- SEG-Y Rev0/Rev1 大端及 Rev2 标准大/小端处理，未知或成对换字节明确拒绝；
- SEG-Y 标准 Trace Header 的安全遍历、固定/可变道长检查、坐标标量/范围及 Inline/Crossline 候选；
- SEG-Y 采样格式 1、2、3、5、6、8 的分块解码与在线振幅统计；
- SEG-Y 死道、常值道、非有限值、采样参数不一致和异常 Trace RMS 候选检查；
- `seismic.inspect_segy`、独立 Validator、五组 ExpectedEvidence 和两个登记 Artifact；
- `segy` CLI 在无模型、无密钥和无 MCP 时通过同一 GeoDomainHarness 完成只读检查；
- 固定合成 SEG-Y 黄金夹具覆盖文本编码、采样格式、字节序、可变道长、截断和 Harness 流程；
- 本地样本 `data/local/model20260730_Synthetic.sgy` 已完成实际文件 CLI 冒烟检查。
- Agent Runtime、Explore 和确定性工作流使用同一个顶层 `RunRecord` 与 Task 状态定义；
- `AgentRunRecord`、Plan Step 和 `StepAttempt` 分层记录，模型轮次不会冒充 Tool Attempt；
- Data/Semantic/Process/Result 四层 `ValidatorRegistry` 和有序 `ValidationCoordinator`；
- 最终报告/回答成为带角色的计划内 Artifact，并验证必需事实 Evidence ID 引用；
- `.checkpoints/<revision>/` 版本化快照与原子 `checkpoint.json` 提交指针；
- 跨平台 `.writer.lock` 单写者约束和同主机失效 PID 锁回收；
- 可配置 `RecoveryPolicy`，只允许 `replay_policy=safe` Tool 自动重放；
- 恢复时验证输入哈希、已完成 Step Evidence 和 Artifact，完整 Step 直接复用；
- `status`、`resume`、`cancel` 和 `validate` CLI；
- 从 Event、RunRecord、State/Checkpoint 和 Artifact Manifest 重建带来源的时间线；
- 离线检测半写镜像、不可变元数据变化、无效事件、输入变化、Artifact 丢失/篡改和重复完成；
- 故障注入覆盖原子替换失败、各主要状态进程终止、验证失败、恢复成功与再次失败。
- Windows CPython 3.11–3.14 支持矩阵、锁定开发依赖、统一 Ruff/测试命令、CI 和可复现 sdist/wheel 构建；
- GPL-3.0-only 开源许可基线、第三方来源/许可证清单、贡献指南、安全策略、CHANGELOG 和已知限制；
- 通用 `geophysagent run`，按 DataAsset capability 只暴露获准的资产、测井、表格、SEG-Y 和 Explore Tool；
- LLM 候选 ExecutionPlan 的 Schema 校验、PlanPolicy 审批、计划 Artifact 和模型安全 Tool 别名；
- 多 Tool、多轮结果回填与明确停止条件；Tool Call 全部通过 TaskRunSession/Registry/Harness，不存在专业实现旁路；
- 动态 Tool Result/Validation Evidence、Evidence index、Agent trace 和最终回答计划内 Artifact；
- AgentRun 的 Provider、Model、协议、轮次、Token、延迟、响应 ID、重试与可用费用字段；
- 模型凭据/网络/限流/协议错误的分类、有限重试、`waiting_for_user` 检查点和显式 `run --resume`；
- 取消边界、模型输出大小限制、路径脱敏，以及不提供 Shell/任意文件/Validator 绕过工具；
- 手动真实模型在线测试入口，支持指定任一 Profile，覆盖 LAS Agent、表格 Explore 和通用多 Tool；未设置开关与对应密钥时绝不联网。
- `ContextBudget`、`ContextBlock`、`ContextSnapshot`、`CompactionRecord` 和 `AgentCheckpoint` 版本化契约；
- `ContextAssembler` 从 TaskSpec、RunRecord、已审批计划、Input/Artifact Manifest 和 Event Log 投影模型上下文；
- 系统策略、用户目标、资产摘要、Tool Definition、Evidence/Artifact 引用和模型历史具有显式来源、信任级别与内容哈希；
- 每次模型调用前持久化 Context Snapshot 摘要、预算、Tool/Provider 版本、Evidence/Artifact ID 和上下文哈希；
- 总体、系统、Tool Definition、历史和单次 Tool Result 预算，以及自动/显式 Context Compaction；
- Tool Call/Tool Result 不可拆分裁剪；压缩摘要只用于导航，不成为 Evidence；
- 超大 Tool Result 自动登记为 Artifact，模型只收到哈希、大小和受限结构摘要；
- Agent 消息、轮次、计划进度和恢复原因进入权威 RunRecord；新运行不再创建独立 `agent-checkpoint.json`；
- 旧 Agent 检查点只读迁移、遗留 running AgentRun 收敛，以及孤儿快照修订跳过恢复；
- `submit_final_answer`、`agent-claim-map` 和 `core.result.claim_evidence`，将已验证标量事实与模型解释分开展示；
- 长轨迹、大结果、预算失败、提示注入、无效 Evidence Pointer、压缩提交中断和模型响应后中断测试。
- 流式 Tool Call 在完整消息结束后定稿参数；保留 thinking/signature，兼容累计/重叠 JSON delta，并把输出上限截断分类为可重试错误；
- 模型每轮只看到当前计划依赖已满足的 Tool，最终阶段只暴露 `submit_final_answer`，同时限制最终提交体积。
- Tool Definition 已包含模型别名、用途、资产 capability、使用指导、风险/副作用、Evidence/Artifact 和 Schema 版本；通用 Agent 不再维护静态 Tool 映射或名称分支；
- Provider Registry 已实现 prepare/health/execute/cancel/dispose、资源作用域、原子激活/回滚、运行快照、独立生命周期事件和 Fake Provider 故障测试；
- MCP `2025-11-25` 本地 stdio Client/Provider、initialize/initialized、能力协商、分页 tools/list、tools/call、取消和传输关闭；
- 版本化 Integration Manifest 对命令、cwd、env、Server/Tool、协议、超时、重启和权限执行白名单；v0.1 强制只读，允许精确 Origin 白名单内的 API GET，并拒绝无限制网络、写入和外部控制；
- MCP Schema、结构化结果、文本摘要和资源引用映射进入普通 GeoToolResult；Server instructions 不进入系统提示，大结果复用 Artifact 投影；
- 最小只读 Server、个人 Python 项目包装和 REST sidecar 边界示例，以及协议污染、超时、取消、崩溃、重启、非法结果、伪路径和密钥脱敏测试。
- 无本地 DataAsset 的 external-only TaskSpec、资产无关 Tool 暴露和外部数据源上下文；
- 本地 Integration Package 的安装、列出、启用、禁用、删除、原子登记和激活前完整性验证；
- 受限只读 JSON API Client：GET、精确 Origin、loopback HTTP/远程 HTTPS、无重定向、响应预算和环境变量凭据；
- `software-a-readonly` 垂直示例与 loopback 模拟 API，贯通自然语言计划、MCP stdio、API、Evidence、Validation 和最终回答。
- LAS 2.0 `WRAP.YES/NO`、SPACE/COMMA/TAB、UTF-8/UTF-16 BOM、厂商扩展保留和确定性损坏拒绝；
- 版本化 LAS 曲线别名、单位别名和 Rule Pack，包含缺失、宽范围、平直、尖峰与突跳候选规则；
- LAS 机器结果、语义分层报告、SVG 测井图和带轴/单位/来源/Evidence 的通用图件元数据；
- 10 个明确标为合成的 LAS 回归样本及清单；
- 领域无关 ReferenceSuite/ReferenceCase、黄金断言和 `evaluate` CLI，统一统计计划、Tool、Evidence、Validator、Artifact 和任务指标；
- 同一评测器覆盖 LAS、SEG-Y 和 external-only Software A loopback API，且通用评测模块不导入 LAS；
- 确定性 LAS 分析身份与 Evidence 哈希跨重复运行稳定。

## 当前限制

- LAS 解析与 QC 已覆盖参考产品所需的 LAS 2.0 子集，但不是完整 LAS 标准或测井解释系统；
- 10 个新样本均为合成数据；授权真实黄金样本、当前 OpenCode Go Profile 重复评测和地球物理专业用户走查仍待完成；
- 已支持确定性 safe Tool 的检查点恢复；同步 Tool 执行期间的取消只能在下一个安全边界生效；
- Increment 7A/7A.1 的三项真实 MiniMax 基线已由用户在本机通过；改用 OpenCode Go 后尚需用新默认 Profile 复跑完整三项；
- Explore 是应用层策略隔离，不是操作系统级安全沙箱；
- TXT/CSV 探测是启发式实现，复杂厂商文件仍需要用户确认或专用 Parser；
- Pipeline 当前不输出 LAS，只生成派生 CSV 和运行清单；
- MCP 当前只支持显式清单或已启用本地包中的 stdio、只读 Tools 和协议 `2025-11-25`；已支持本地目录包管理和受限只读 API，但尚无远程传输、在线安装/签名/依赖解析、Resources/Prompts/Sampling、写入/无限制网络/外控权限或 OS 沙箱；
- 当前报告只做数据质控和统计，不做储层或岩性解释；
- SEG-Y 当前只提供结构检查、聚合统计和基础 QC，不提供剖面/道集图、地震处理、解释、完整 Rev2 扩展字段或数据拖尾支持；
- SEG-Y 的厂商 Trace Header Extension、成对换字节和未列出的采样格式会明确拒绝，不做猜测式解析；
- 通用 Agent 已有模型轮次检查点与人工续跑；旧 `agent`/`explore` 兼容入口仍只进入 `waiting_for_user`，尚未迁移为同等会话续跑；
- 事实—Evidence Validator 能证明选定 JSON Pointer 的值与哈希，Rule Pack 和合成黄金评测已落地；模型解释仍不是独立 Evidence，专业结论需由授权真实样本和人工走查验证；
- 文件型 Run Store 使用快照和事件重建，不提供把历史 Event 逐条重新执行为状态的 Event Sourcing；
- 当前内置 Schema Validator 覆盖本项目使用的递归子集，遇到未实现关键字会拒绝；引入更广公共 Schema 前需采用完整 Draft 2020-12 实现。

## 建议迭代顺序

1. 用授权真实 LAS 建立黄金预期并完成专业用户走查；
2. 使用统一 `run` 对同一参考任务重复执行当前 OpenCode Go 默认 Profile，保存 ReferenceSuite 指标并审查 Tool 选择与 Evidence 引用；
3. 为 Explore 增加内存硬限制或容器/低权限进程隔离；
4. 在权限确认机制完成后，再为长任务与外部 MCP Job Tool 增加运行中取消、外部 Job ID 和幂等键；
5. 在真实需求与黄金数据明确后，再扩展 SEG-Y 图件、处理和更多修订特性。

每一步都应保持 Tool 与 Harness 的无网确定性测试可独立运行；这用于隔离计算和状态错误，不表示最终用户可以绕过 LLM。真实供应商测试允许调用外部 API、消耗订阅额度或产生费用，但必须显式启用、使用环境变量凭据、控制数据披露，并在候选发布前通过。

详细任务与验收门槛见 [TODO.md](../TODO.md)，MCP 实现见 [MCP_STDIO_PROVIDER.md](MCP_STDIO_PROVIDER.md)，Context 与 Agent 恢复见 [CONTEXT_ASSEMBLY.md](CONTEXT_ASSEMBLY.md)，Harness 内核说明见 [HARNESS_CORE.md](HARNESS_CORE.md)，生命周期与恢复见 [HARNESS_LIFECYCLE.md](HARNESS_LIFECYCLE.md)，SEG-Y 支持边界见 [SEGY_SUPPORT.md](SEGY_SUPPORT.md)。

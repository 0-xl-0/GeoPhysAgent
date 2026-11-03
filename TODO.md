# GeoPhysAgent TODO

> 当前基线：v0.1 Increment 6.5、7A、7A.1、7B、7C 已完成；7A.1 包含真实 MiniMax 在线验收（2026-09-08）；多供应商命名模型配置已完成（2026-09-16）
> 原则：Harness 是内核，专业参考产品用于验证内核；LAS 是当前第一个参考实现，不是 GeoPhysAgent 的能力边界；MCP 是 Tool Provider 之一，不等于整个 GeoPhysAgent。
> 最新审计：2026-09-16；共发现 136 项测试，133 项默认测试通过（含本机 loopback 模拟 API），3 项真实模型用例默认跳过；历史 7A.1 MiniMax 三场景已通过，更换为 OpenCode Go 后尚需用新默认 Profile 复跑；当前属于开发者预览版。
> 产品入口决策：最终用户任务采用 LLM-first；LLM 是 Agent 必经控制入口，专业计算以本地 Tool 为主并由 Harness 约束。直接确定性 CLI 保留给开发、测试、批处理和故障诊断。
> 测试决策：默认回归继续使用 Fake Model/无网测试；允许并要求在候选发布前使用环境变量中的 API Key 对候选默认 Model Profile 执行少量真实在线端到端验收。

## P0：多供应商模型配置与 OpenCode Go 迁移（已完成）

- [x] 在一个 `geophysagent.toml` 中保存多个命名 Model Profile，并用 `models.default` 一行切换项目默认模型；
- [x] `run`、`agent`、`explore`、`doctor` 支持 `--profile` 单次覆盖，`run --resume` 可显式改用另一 Profile；
- [x] 增加 `models`/`models --json`，只展示凭据是否存在，不输出凭据值；
- [x] 支持 Anthropic Messages-compatible 与 OpenAI Chat Completions-compatible Adapter，并统一回填内部 Tool Call/Result 契约；
- [x] 默认迁移到 OpenCode Go `minimax-m3`，提供 OpenCode Go `kimi-k3` 示例和旧 MiniMax 直连备用 Profile；
- [x] 按 OpenCode Go 的编码 Agent 路由要求发送独立 User-Agent 和稳定 `x-opencode-session`，并以 Harness Task ID 保证续跑一致；
- [x] 保留旧 `[model]` 格式只读兼容，新增配置校验、协议适配与 CLI 回归；
- [ ] 使用用户的 OpenCode Go Key 对默认 `opencode-go-minimax-m3` Profile 复跑三项真实在线门禁；
- [ ] 实现 OpenAI Responses Adapter 后，才允许配置只提供 `/v1/responses` 的模型。

## P0：Increment 4——修正数据类型与 Harness 核心（已完成）

这一增量的目标不是再增加一种临时解析逻辑，而是修复“非 LAS 一律当文本”和“各命令各自执行、Harness 只有局部对象”两个根因。

### 4.1 数据资产适配与能力路由

- [x] 建立 `AssetAdapterRegistry`，数据必须先完成类型识别，再进入格式解析器；
- [x] 建立 LAS、分隔文本和 SEG-Y recognition-only 适配器；
- [x] `DataAsset` 记录 `data_type`、`adapter_id`、`capabilities` 和识别置信来源；
- [x] 把表格探测、表格读取、预处理、Explore 和 LAS QC 设为显式能力；
- [x] `.sgy/.segy` 识别为 `seismic/segy`，但在 Increment 5 Parser 完成前只允许资产检查；
- [x] 二进制内容即使使用 `.txt` 扩展名，也不能落入 Latin-1 文本兜底；
- [x] 不支持的能力必须在 Parser、Tool 和 LLM 之前失败，并返回格式、适配器、请求能力和现有能力；
- [x] `inspect` 可以展示已识别但尚无领域 Parser 的资产，不伪造表格 Profile。

### 4.2 Harness 公共契约

- [x] 实现 `TaskSpec`；
- [x] 实现 `ExecutionPlan` 和 `PlanStep`；
- [x] 实现 `ExpectedEvidence` 和 `EvidenceRecord`；
- [x] 实现统一 `StepAttempt`；
- [x] 实现 `ArtifactRecord` 和 `ArtifactManifest`；
- [x] 实现统一 `RunRecord`，明确区分 `task_status` 与 `execution_status`；
- [x] 契约均带 `schema_version`，并能稳定序列化为 JSON。

### 4.3 计划策略与 Tool 边界

- [x] 计划的 task、workflow、资产和 Tool allowlist 必须与 TaskSpec 一致；
- [x] Step ID、Evidence ID 必须唯一且使用安全标识符；
- [x] 拒绝未知依赖、自依赖和循环依赖；
- [x] PlanStep 只能调用已注册 Tool；
- [x] Tool Definition 记录只读、幂等和重放策略；
- [x] 非安全重放 Tool 不能通过计划请求自动重试；
- [x] Provider 返回的 Tool 名称和 Provider 身份必须与注册信息一致；
- [x] Tool 参数和结构化结果均经过递归 JSON Schema 校验；
- [x] Schema 中尚未实现的关键字会被明确拒绝，不会被静默忽略。

### 4.4 状态机、Run Store 与 Artifact Store

- [x] 实现显式任务状态机和合法转移表；
- [x] 终态禁止继续执行；
- [x] 实现文件型 Run Store；
- [x] `task.json`、`plan.json`、`input-manifest.json`、`state.json`、`run-record.json` 和 `artifact-manifest.json` 使用临时文件加原子替换；
- [x] 建立 `events.jsonl`，记录任务、资产、状态、Step、Tool 和 Artifact 事件；
- [x] 每次 Tool 调用记录 Attempt ID、Provider、起止时间、结果哈希或结构化错误；
- [x] Artifact 必须位于任务目录内，拒绝路径逃逸和符号链接；
- [x] Artifact 使用相对路径、大小和 SHA-256 登记，并提供离线完整性复核 API；
- [x] 已有 Harness 输出目录默认不复用，避免新任务覆盖权威记录。

### 4.5 证据门禁与真实工作流迁移

- [x] ExpectedEvidence 使用 JSON Pointer 绑定 Tool Result 中的事实、验证结果和产物；
- [x] 记录证据值及其 SHA-256，最终报告层可按 Evidence ID 获取已绑定事实；
- [x] 执行成功但缺少必需证据时，任务终态必须是 `incomplete`，不能是成功；
- [x] Tool 失败时持久化 `failed/failed` 权威状态；
- [x] warning 只有实际 warning 存在时才能进入 `completed_with_warnings`；
- [x] `analyze` 和 `preprocess` CLI 已迁移到 `GeoDomainHarness`；
- [x] LAS LLM Agent 的确定性 QC 子任务已迁移到 `GeoDomainHarness`；
- [x] Explore 在数据类型路由阶段受同一 Adapter 能力门禁，不支持的数据不会调用模型。

### 4.6 Increment 4 验收门槛

- [x] 原有 28 项测试不回归；
- [x] 总计 40 项无网络测试通过；
- [x] `.sgy` 能被识别为地震资产；
- [x] `.sgy` 不能再被表格 Loader 或 Explore 当成文本；
- [x] 不支持的 SEG-Y Explore 测试中 Model Client 调用次数为 0；
- [x] 合法计划产生完整 Task、Plan、State、Run、Event 和 Artifact 记录；
- [x] 非法计划、非法 Schema、缺失证据、Tool 崩溃、Artifact 越界和 Artifact 篡改均有测试；
- [x] 真实 LAS QC 参考工作流通过 Harness 完成并登记两个产物；
- [x] `pi/` 未被修改，也未成为构建、测试或运行时依赖。

## P0：Increment 5——最小只读 SEG-Y 垂直切片（已完成）

- [x] 实现真正的 `SegyAssetAdapter` Parser，而不是仅识别扩展名；
- [x] 读取 3200-byte 文本头并识别 ASCII/EBCDIC；
- [x] 读取 Binary Header：格式码、采样间隔、每道样点数、固定长度标志；
- [x] 安全遍历 Trace Header，给出道数、坐标标量、坐标范围和 inline/crossline 候选；
- [x] 支持首版明确列出的 SEG-Y sample formats，其他格式可操作地拒绝；
- [x] 计算有限、分块的振幅统计，不把完整道集发送给模型；
- [x] 实现死道/常值道/异常振幅的基础确定性检查；
- [x] 增加固定合成 SEG-Y 黄金夹具和头字段、字节序、截断文件测试；
- [x] 新增 `seismic.inspect_segy` GeoTool、Validator、ExpectedEvidence 和 Artifact；
- [x] 用 SEG-Y 作为第二领域验证 Asset/Tool/Harness 抽象；该工作流直接复用现有 Plan、Run、Evidence、Artifact 和状态机，未建立旁路。

### Increment 5 验收

- [x] 合法样本的关键头字段与生成参数一致；
- [x] 截断、未知格式码、错误道长和不一致采样参数不会造成静默错读；
- [x] 大文件使用流式/分块访问，不复制完整数据到 JSON；
- [x] SEG-Y Tool 通过同一 ExecutionPlan、RunRecord、Evidence 和 Artifact 流程；
- [x] 无 LLM 时也能完成确定性检查；
- [x] 48 项无网络测试通过，并使用仓库内实际 1.2 MB SEG-Y 文件完成 CLI 冒烟测试。

## P0：Increment 6——Harness 生命周期加固（已完成）

Increment 4 已把 Harness 从“局部数据对象”提升为可执行内核；以下是完整 v0.1 生命周期仍需补齐的部分，不应再回到各命令自建状态文件。

- [x] 把 Agent Runtime 和 Explore 多轮执行接入统一的顶层 TaskRun；
- [x] 区分顶层用户任务、Agent Run、Plan Step 和 Tool Attempt，避免子步骤成功被误报为用户任务完成；
- [x] 实现 `ValidationCoordinator` 和 Data/Semantic/Process/Result Validator Registry；
- [x] 把 Final Answer/Report 作为计划内产物，并验证其中关键事实的 Evidence ID；
- [x] 实现可配置的 RecoveryPolicy，只有 `replay_policy=safe` 的 Tool 才能自动重放；
- [x] 实现版本化检查点、`status`、`resume` 和 `cancel`；
- [x] 从 `state.json + events.jsonl + manifests` 重建时间线；
- [x] 检测半写、丢失 Artifact、哈希变化和已完成 Step 的重复执行；
- [x] 为 Run Store 增加跨平台单写者锁与同主机失效 PID 锁回收；
- [x] 增加故障注入：写入中断、各主要状态进程终止、验证失败、恢复再次失败；
- [x] 增加独立 `validate <run-directory>` 命令。

### Increment 6 验收

- [x] 各主要状态中断后，最后一个已提交检查点仍可读取；
- [x] 顶层任务只在必需证据、验证和最终产物全部满足后成功；
- [x] 已成功且完整性通过的 Step 不被无条件重复执行；
- [x] 终态后不能新增 Tool Attempt、Evidence、Validation 或 Artifact；
- [x] Agent/Explore/确定性 CLI 使用同一 Task 状态定义，不再维护互不兼容的成功语义；
- [x] 总计 59 项无网络测试通过，包括 CLI 生命周期与故障注入验收。

## 当前差距与改进排序（2026-09-09）

| 优先级 | 当前差距 | 主要影响 | 承接任务 |
|---|---|---|---|
| P0 | 工程基线已冻结；需持续保持 CI、依赖审查和可复现构建 | 防止后续 MCP 和重构破坏可靠回退 | Increment 6.5（持续门禁） |
| P0 | 本地 stdio MCP、Integration Package 和受限只读 API 查询已完成；远程 MCP、写入和外部修改仍冻结 | 可查询经过审查的软件 API，但不能创建任务、导出或修改商业软件工程 | 权限确认机制与后续 MCP 增量 |
| P0 | 参考产品工程闭环和跨领域评测已实现，但真实 LAS 黄金、在线重复性和专业走查仍不足 | 已能证明契约复用和合成场景稳定性，仍不能宣称真实专业产品验收完成 | Increment 8B 外部门禁 |
| P0 | LLM-first 统一 `run` 入口和模型不可用语义已实现；最小本地工作台尚未设计 | 当前仍以 CLI 为开发者预览入口 | v0.1 产品入口落地 |
| P1 | Explore 只有应用层策略隔离 | 不适合执行不受信任代码或开放给多用户 | Explore 安全与产品化 |
| P1 | Harness 核心模块职责开始集中 | 继续加入 MCP 特判会提高耦合和回归风险 | Harness 可维护性 |
| P1 | 已有事件日志，但缺少稳定的运行视图、游标订阅和统一 Runtime Profile | GUI、多客户端和运维接入容易各自解释内部状态 | Runtime 可观测性与配置 |
| 后续版本 | 尚无跨任务、可治理的长期知识复用 | 重复分析较多，但过早实现会混淆 Memory 与 Recovery | v0.2 长期 Memory 候选路线 |
| 后续版本 | 尚无受管父子 AgentRun 和编排策略 | 复杂任务不能安全并行、竞争假设或分角色复核 | v0.3 多智能体候选路线 |

改进原则：Increment 7A.1 已收敛 Context/Checkpoint 和事实—Evidence 绑定并通过真实 MiniMax 门禁；Increment 7B 已证明 MCP 适配现有 Provider/Tool/Harness 契约而不成为旁路；Increment 7C 已补齐外部数据源任务、受限只读 API 和本地 Integration Package。下一步先建立可复用的参考产品验收基础，再以 LAS 完成第一个专业闭环，并用 SEG-Y/外部 API 路径证明通用能力没有被 LAS 特化；领域能力和最终用户体验必须用真实数据、真实 MiniMax、黄金结果和专业走查共同验收。

### 本轮参考结论与借鉴边界

- [x] 确认当前设计属于 Harness 架构，而不是“LLM 调几个函数”的薄封装：Context、计划、Tool、状态、恢复、证据、验证、Artifact 和审计均由模型外 Runtime 管理；
- [x] 文章中的“Context Assembly → Agent Loop → Tool → Compaction → Permission/Sandbox → Event/Persistence → Sub-Agent”用于识别缺口，不作为要求一次性实现全部阶段的指令；
- [x] `pi/` 仅用于阅读其 Session Tree、Context 投影、Compaction 边界、Tool 元数据、Resource Loader 和事件观察方式；不修改 `pi/`，不导入或依赖 Pi 包，不整段复制 Pi 实现；
- [x] GeoPhysAgent 继续独立设计和重写所需机制，并以地球物理数据安全、可验证证据和专业工作流为验收中心；
- [x] 不因文章强调多智能体、长期记忆或常驻 Runtime 就把它们提前塞入 v0.1；先用单 Agent、有限上下文、确定性验证和最小 MCP 证明内核。

### 本轮代码审计发现

- [x] 消除通用 Agent 中静态 Tool 说明映射以及对 `table.preprocess`、`explore.python` 等具体名称的提示词特判，改由 Tool/Provider 自描述；
- [x] 将不断增长的原始消息列表改为受预算约束、可投影、可压缩的工作上下文；
- [x] 把独立 Agent 消息检查点收敛进版本化 Run Store，避免恢复时出现两个“权威状态”；
- [x] 为 Provider 补齐启动、健康检查、取消、关闭和资源释放语义，再接 MCP 子进程；
- [ ] 将现有事件日志提升为稳定的只读运行视图；UI 和外部调用方不直接拼接内部 JSON 文件；
- [x] 建立最终回答的事实—Evidence 映射与验证门禁，避免模型把当前 Tool 未检查的项目表述为已通过；
- [ ] 渐进拆分体量较大的 `harness.py` 与 `general_agent.py`，但不做一次性重写。

### 建议开发顺序与停线门槛

1. [x] 完成 Increment 7A 的三项真实 MiniMax 冒烟并保存基线指标：LAS Agent、表格 Explore、通用多 Tool 均通过，运行目录完整性有效。
2. [x] 完成 Increment 7A.1 的 Context 契约和单一检查点事实源；长轨迹、压缩与恢复测试已通过。
3. [x] 完成 7B.0 的 Tool 自描述与 Provider 生命周期；Fake Provider 覆盖部分启动、健康失败、取消、重复释放、冲突和回滚。
4. [x] 实现 MCP stdio 的启动、发现、调用、取消和关闭，并接入一个仓库内只读示例 Server。
5. [x] 证明 MCP Attempt/Evidence/Artifact/Validation 与内置 Tool 完全同路，没有 MCP 专用 Run 状态或证据旁路。
6. [x] 完成 REST API 包装边界和个人 Python 项目包装两个示例，未复制外部项目源码进领域内核。
7. [x] 完成 Increment 7C：无本地资产任务、受限只读 API Connector、Integration Package 安装和软件 A 垂直示例。
8. [x] 转入 Increment 8A；先建立跨领域可复用的参考产品验收、报告和评测基础。
9. [ ] 完成 Increment 8B；用真实 LAS 样本、黄金预期和专业走查交付第一个可用参考应用。
10. [x] 完成 Increment 8C；复跑 SEG-Y 与 external-only API 场景，证明同一 Harness 能跨领域复用且没有 LAS 特判。
11. [ ] 最后再根据产品走查决定最小本地工作台、写操作审批和 OS 沙箱的进入顺序；v0.1 稳定后按后续路线建设长期 Memory 与多智能体。

## P0：Increment 6.5——工程基线冻结

- [x] 初始化 GeoPhysAgent 自身 Git 仓库，并提交当前测试通过的干净基线；
- [x] 保持 `/pi/`、`data/local/`、`runs/`、本地配置和密钥文件不进入版本控制；
- [x] 明确正式支持的 Python 版本及 Windows 测试矩阵；
- [x] 增加统一格式化、静态检查和无网络单元测试命令；
- [x] 建立基础 CI，至少执行安装、静态检查和完整测试；
- [x] 在引入 MCP SDK 前确定依赖锁定和升级审查方式；
- [x] 在干净 Windows 环境验证安装、CLI 入口和测试；
- [x] 确定许可证、第三方来源清单、贡献指南和安全说明；
- [x] 增加 CHANGELOG、已知限制和可复现发布包方案；
- [x] 在出现明确跨语言需求前保持 Python-only v0.1，不预先增加 TypeScript 控制平面。

### Increment 6.5 验收

- [x] 新环境可以按照 README 完成安装并运行全部测试；
- [x] CI 与本地使用同一组质量命令，当前离线测试无回归；
- [x] Git 状态中不包含 API Key、真实本地数据、运行产物或 `pi/` 内容；
- [x] 新依赖的版本、来源、许可证和更新方法都有记录。

## P0：Increment 7A——通用 LLM-first Agent Runtime（已完成）

目标是让最终用户的自然语言任务必经 LLM Agent，由 Agent 识别资产、构造受约束计划、选择 Tool、观察结果并生成带 Evidence 的回答；本地确定性 Tool 是 Agent 的计算底座，不是与 Agent 竞争的产品模式。

- [x] 新增统一用户入口 `geophysagent run <asset> --request "..." --output <dir>`；
- [x] 把 `asset.inspect`、`well_log.qc`、`table.preprocess`、`seismic.inspect_segy` 和 `explore.python` 暴露为受 capability/allowlist 约束的 Agent Tool；
- [x] 实现从自然语言目标到结构化 ExecutionPlan 的候选生成、Schema 校验和 PlanPolicy 审批；
- [x] 支持预算内多 Tool、多轮“模型决定—Harness 执行—结果回填—继续/停止”循环；
- [x] 所有最终用户运行必须创建顶层 TaskRun 和 AgentRun；Tool 成功不得直接等同于用户任务成功；
- [x] 最终回答必须是计划内 Artifact，并引用支持关键事实的 Evidence ID；
- [x] 模型不可用、凭据缺失、限流或协议失败时进入明确的失败/等待状态，不得静默退化为直接确定性工作流；
- [x] 保留 `inspect/analyze/preprocess/segy/status/resume/cancel/validate` 作为开发、测试、批处理和诊断接口；
- [x] 记录 Provider、Model、协议、模型轮次、Token、延迟和可用费用信息，但不记录 API Key 或认证 Header；
- [x] 增加流式响应、明确停止条件、超时、取消和有限 Runtime 重试；
- [x] 设计通用 Agent 模型轮次检查点与人工确认式续跑，不自动重放非幂等模型调用或生成代码；旧 `agent/explore` 兼容入口的同等续跑留作后续收敛；
- [x] 不提供通用 Shell、任意文件写入或绕过 Geo Tool Registry 的模型工具。

### Increment 7A 测试与验收

- [x] Fake Model/Fake Transport 覆盖资产识别、计划审批、多 Tool 轨迹、失败、停止、取消、重试和 Evidence 最终回答，默认测试无需网络；
- [x] 建立显式在线测试入口；当前支持通用在线开关、Profile 选择和 Profile 自身的环境变量凭据，并兼容旧 MiniMax 开关；
- [x] 真实 MiniMax 已完成 LAS Agent、通用表格 Explore 和一个多 Tool 任务的端到端冒烟测试；三个 Run 均为 `completed/completed` 且完整性验证通过；
- [x] 在线测试记录模型名称、测试任务、TaskRun 和公开用量信息，不保存密钥和完整敏感响应；
- [x] 在线测试允许产生费用和模型输出差异，断言结构化 Tool 轨迹、Evidence、状态和安全边界，不断言固定自然语言措辞；
- [x] 候选发布必须通过无网完整回归和候选默认 Profile 的真实模型冒烟测试；普通本地提交和无密钥 CI 不强制访问网络；
- [x] LLM 不能直接调用专业实现、伪造 Tool Result、绕过 Validator 或在证据不足时完成任务。

## P0：Increment 7A.1——Context Assembly 与恢复事实源收敛（已完成）

目标不是引入长期 Memory，而是让每次模型调用看到“足够、可解释、受预算约束”的工作上下文，并让对话轨迹、任务状态和恢复点归属于同一个权威 Run。该增量完成后再把外部 MCP Tool 大量引入上下文。

### 7A.1.1 Context 契约与装配

- [x] 定义 `ContextAssembler`、`ContextBudget`、`ContextSnapshot` 和 `CompactionRecord` 的最小稳定契约；
- [x] 将工作上下文拆成有序区块：系统安全策略、用户目标、已审批计划、资产摘要、活动 Tool 定义、已验证 Evidence/Artifact 引用、最近模型轮次；
- [x] 每个非系统区块记录来源、可信级别和 Run 内引用，外部文件内容、MCP 返回文本、模型摘要永远不能升级为系统指令；
- [x] 从 `RunRecord`、Plan、事件和 Manifest 投影上下文，禁止让未经登记的内存对象成为唯一事实来源；
- [x] Tool 描述只包含当前资产能力和当前策略允许的 Tool，不把全部 Provider 能力无条件塞给模型；
- [x] 为每次模型调用记录 Context Snapshot 摘要：策略版本、活动 Tool/Provider 版本、消息数、估算 Token、Evidence/Artifact ID 和摘要边界；不持久化密钥、认证 Header 或不必要的完整敏感内容。
- [x] 为最终回答建立结构化事实—Evidence 映射；Validator 必须拒绝或要求重写没有对应 Evidence、超出 Tool 检查范围的事实性结论。

### 7A.1.2 预算、压缩与大结果处理

- [x] 设置系统区块、历史、Tool 定义、单次 Tool Result 和总体上下文预算，并在模型调用前确定性检查；
- [x] 大型 Tool Result 先写入 Artifact/Evidence，再只把受限摘要、哈希和引用放入模型上下文；
- [x] 实现“已验证事实摘要 + 最近原始尾部”的最小 Compaction，保留当前目标、未完成 Step、审批状态、失败原因和 Evidence ID；
- [x] Tool Call 与对应 Tool Result 必须作为不可拆分单元裁剪，不能产生孤立调用或孤立结果；
- [x] Compaction 摘要只是导航信息，不是证据；最终回答中的关键事实仍必须解析到原始 Evidence；
- [x] 支持预算阈值自动压缩和显式诊断触发；压缩失败必须可恢复，不能破坏上一个已提交检查点；
- [x] v0.1 不做跨任务自动记忆写入、向量库用户画像或后台“自我学习”。

### 7A.1.3 检查点与恢复收敛

- [x] 将 Agent messages、当前模型轮次、Compaction 边界和待确认续跑信息纳入版本化 Run Store 检查点；
- [x] 淘汰独立 `agent-checkpoint.json` 作为第二权威事实源；如需兼容旧运行，只允许只读迁移并记录迁移事件；
- [x] 检查点提交继续使用原子替换，并与 Run 状态、事件序号和 Artifact Manifest 建立可校验关联；
- [x] 恢复时重建 Context 投影，不直接信任缓存的完整 Prompt；
- [x] 未确认的模型调用、生成代码或非幂等 Tool 在崩溃后仍不得自动重放。

### Increment 7A.1 验收

- [x] 使用 Fake Model 构造超长多轮轨迹和超大 Tool 输出，能够在预算内继续运行且不丢失必需 Evidence；
- [x] 压缩前后，未完成计划、失败记录、Tool 调用/结果配对和最终 Evidence 引用保持一致；
- [x] 注入到数据文件、MCP 文本或模型摘要中的伪系统指令不会改变权限和 Tool allowlist；
- [x] 在压缩前、压缩提交中和模型调用后的故障注入均可恢复到最后一个有效检查点；
- [x] 相同 Run 状态和策略版本可以生成确定性的 Context 结构与快照哈希；
- [x] 使用故意生成“未执行异常检查但声称无异常”等回答的 Fake Model，验证事实—Evidence 门禁能够阻止顶层任务完成或触发受控重写；
- [x] 现有 LAS、表格 Explore、SEG-Y 和通用多 Tool 测试无回归。
- [x] 使用轮换后的新 API Key 按新 `submit_final_answer` 协议完成三项真实 MiniMax 门禁；原失败 Run 可从权威检查点恢复，通用场景修复后复跑通过。
  - [x] LAS Agent 在线场景通过；
  - [x] 表格 Explore 在线场景通过；
  - [x] 通用多 Tool 场景在修复流式 Tool 参数定稿、`max_tokens` 截断分类和完整 thinking/signature 回传后重新通过。

## P0：Increment 7B——最小 MCP stdio Provider

目标是证明“外部 MCP Tool 仍然是普通 Tool Provider”，而不是在 Harness 内增加第二套执行、状态或证据系统。

### 7B.0 Provider 扩展边界（先于 MCP Client）

- [x] 扩展 Tool Definition 的模型侧元数据：稳定名称、用途、输入资产能力、使用指导、风险/副作用级别、期望 Evidence/Artifact 类型和版本；
- [x] 让通用 Agent 完全从已激活 Tool Definition 生成模型工具列表和提示指导，删除静态 `AGENT_TOOLS` 映射及按 Tool 名称分支；
- [x] 定义最小 Provider 生命周期：`prepare/start`、`health`、`execute`、`cancel`、`stop/dispose`；内置 Provider 提供无操作实现；
- [x] 引入 Provider 资源作用域，统一拥有子进程、后台读取任务、诊断缓冲和受限环境变量，并保证成功、失败、取消后释放；
- [x] Provider 激活原子化：先完成 Manifest、Schema、命名冲突和策略校验，再整体替换可见 Registry；失败时回滚并释放新资源；
- [x] 每个模型轮次/Plan Step 冻结活动 Tool、Provider 版本、权限和工作目录指纹，运行中 Registry 变化不能改变已审批计划；
- [x] 记录 Provider discovering/starting/ready/degraded/stopping/stopped 事件；Provider 状态与顶层 Task 状态分离；
- [x] 使用 Fake Process/Fake Provider 覆盖部分启动、健康检查失败、取消、重复释放、注册冲突和回滚；
- [x] v0.1 不做自动安装未知 Server、代码热重载或无人审批的在线插件市场。

### 7B.1 MCP stdio Client/Provider

- [x] 固定并审查 MCP SDK/协议版本；v0.1 固定 `2025-11-25`，官方 Python SDK v2/MIT 已审查但暂不引入运行时依赖；
- [x] 实现本地 stdio Server 生命周期、initialize、capability negotiation 和 shutdown；
- [x] 实现 `tools/list` 分页与 `tools/call`；
- [x] 实现 MCP Tool 到 GeoToolDefinition、CallToolResult 到 GeoToolResult 的映射；
- [x] 实现版本化 Integration Manifest：Server ID、启动命令、工作目录、允许继承的环境变量、协议版本、Tool allowlist、超时/重启/权限策略；
- [x] 对启动命令、工作目录和环境变量做显式 allowlist；API Key 只在启动边界注入，不进入事件、Prompt、错误详情或 Artifact；
- [x] 实现 Server/Tool allowlist、稳定命名空间和冲突检查；发现新 Tool 不代表自动授权；
- [x] 复用 Harness 的输入/输出 Schema、Attempt、Evidence 和 Artifact 记录；
- [x] 实现超时、取消、崩溃、协议输出/诊断输出分离和受控重启；
- [x] 对 MCP 文本、结构化内容和文件/资源引用分别映射；大结果进入 Artifact Store，模型只接收受限投影；
- [x] MCP instructions、Resources、Prompts 默认不进入模型系统指令；
- [x] 首版只正式支持 Tools；Resources/Prompts/Sampling/远程传输在能力协商中明确禁用或忽略并留下诊断；
- [x] 创建最小只读示例 Server，以及“现有 REST API 包装为 MCP Server”和“个人 Python 项目包装为 MCP Server”两个接入示例；
- [x] MCP 只作为 Provider，不在领域内核中加入 MCP 特判。

### Increment 7B 验收

- [x] 独立测试 Client 能完成启动、协商、分页发现、调用和关闭；
- [x] MCP Tool 的 Schema、Provider 身份、Server/Tool 版本和调用结果进入现有 RunRecord；
- [x] MCP Tool 产生的 Attempt、Evidence、Validation 和 Artifact 与内置 Tool 使用同一契约；
- [x] 未授权 Server/Tool、命名冲突、协议污染、超时、崩溃和非法结果都有确定性测试；
- [x] Server 重启不会在单次调用内隐式重放，也不能绕过 Harness 尝试、重放、取消或终态约束；
- [x] MCP 返回的 Prompt 注入文本、超大内容和伪造本地路径不能提升权限或绕过 Artifact 边界；
- [x] 只读示例默认无需人工批准；任何有写入、导出、网络副作用或外部软件控制能力的 Tool 在 v0.1 中默认禁用，直到权限确认机制完成；
- [x] 示例个人项目不需要修改 GeoDomainHarness 核心即可接入。

## P0：Increment 7C——只读 API、外部数据源任务与 Integration Package（已完成）

目标是让“安装软件 A Connector 后用自然语言查询软件 A”成为真实可运行的垂直路径，同时
继续拒绝写入、任务提交和外部工程修改。

### 7C.1 外部数据源任务

- [x] `run` 新任务不再强制要求本地输入文件；无本地资产时只开放 `input_capabilities=[]` 的资产无关 Tool；
- [x] TaskSpec 使用空 `input_asset_ids` 表达 external-only，Run Store 写入空输入清单并可恢复、验证；
- [x] Context Assembly 明确记录 `external_only`，不伪造 `primary` DataAsset；
- [x] 用户提供的项目名和对象名仍是不可信输入，必须由 Tool 查询结果转为 Evidence；
- [x] 本地资产任务保持原有 capability 路由，没有因外部模式放宽 Built-in Tool 权限。

### 7C.2 受限只读 API Connector

- [x] 增加只读 JSON API Client：只允许 GET、精确 Origin allowlist、有限超时/响应大小和 JSON 响应；
- [x] 非 loopback 明文 HTTP、重定向、URL 凭据、未授权 Origin、非法 JSON 和超大响应全部拒绝；
- [x] MCP Manifest 新增 `network_read` 与 `network_policy.allowed_origins`，未知权限或无限制 `network` 继续拒绝；
- [x] Host 将只读网络策略注入 MCP 子进程；API Key 仍只通过显式环境变量 allowlist 注入；
- [x] Tool Binding/RunRecord 冻结网络 Origin，恢复时策略变化会被拒绝；
- [x] 明确该 Client 是受审查 Connector 的约束库，不是针对恶意 Server 的 OS 网络沙箱。

### 7C.3 Integration Package

- [x] 定义本地目录包 `package.json`：稳定 ID/版本、显示信息、MCP Manifest 和显式文件清单；
- [x] 实现 `integration install/list/enable/disable/remove`；安装默认启用；
- [x] `run` 默认发现并激活所有已启用包，也支持选择指定包或禁用本次自动发现；
- [x] 安装拒绝未声明文件、符号链接、疑似凭据文件、路径逃逸、重复 ID 和超限包；
- [x] 安装采用 staging + 原子替换，登记内容 SHA-256；每次激活前重新验证完整性；
- [x] 不自动安装 Python/Node 依赖，不从网络下载包，不支持签名市场或 MCPB；包本身是受信任代码，需安装前审查；
- [x] 提供 `software-a-readonly` 示例包，将 MCP Tools 映射到本机白名单 HTTP API。

### Increment 7C 验收

- [x] 无本地文件的自然语言任务可以调用已安装软件 A Tool，并完成 Attempt、Evidence、Validation、Artifact 和终态；
- [x] loopback 模拟 API 验证真实 `GeoPhysAgent → MCP stdio → API GET → Evidence` 路径；
- [x] API Token 不进入 Prompt、事件或 RunRecord；
- [x] 安装、禁用、启用、完整性失败和删除都有确定性测试；
- [x] CLI 无位置输入参数即可运行 external-only 任务；
- [x] 写入、导出、任意网络、远程 MCP 和外部软件修改仍不可通过 7C 获得授权。

## P0：Increment 8——完成第一个专业参考产品闭环

目标不是把 GeoPhysAgent 做成 LAS 专用程序，而是用一个范围可控、可重复验收的专业应用，
证明自然语言任务、计划审批、Tool、Validator、Evidence、Artifact、恢复和最终报告能够共同
形成最终用户可使用的闭环。LAS 由于现有基础最成熟、数据规模适中且容易建立确定性黄金
预期，被选为第一个参考实现；以后替换或增加领域时不得修改 Harness 的基本语义。

### Increment 8A——参考产品通用基础

- [x] 定义与领域无关的 Reference Workflow 验收契约：用户任务、输入/外部数据源、期望 capability、允许的 Tool 轨迹、必需 Evidence、Validator、Artifact 和最终报告要点；
- [x] 定义版本化黄金预期格式，分别表达精确值、允许误差、集合/范围、预期 warning、预期失败和不应声称的结论；
- [x] 建立可重复的评测运行器，分别统计计划有效率、Tool 成功率、Evidence 完整率、Validator 通过率和最终任务成功率，不把自然语言相似度作为唯一指标；
- [x] 为 Fake Model 确定性回归和真实模型重复评测使用同一任务集与结果格式；真实模型单次偶然成功不能代替稳定性评测；
- [x] 建立通用 Rule Pack 身份和来源版本记录，让每条规则判断可追溯到规则 ID、版本、参数和输入 Evidence；
- [x] 定义图件 Artifact 的通用元数据：来源数据/结果、绘图 Tool 版本、坐标轴、单位、图例、范围和关联 Evidence；
- [x] 统一最终报告的语义分层：观测事实、确定性规则判断、模型解释、假设、未解决问题和能力边界；
- [x] 报告中的关键数字和确定性判断必须绑定 Evidence ID；模型解释不得伪装为已验证事实；
- [x] 评测失败必须能定位到 Context/计划、Provider/Tool、Validator、Evidence/Artifact、恢复或最终表达层；
- [x] 通用基础只能依赖 Harness/Provider/Validator/Artifact 契约，不能导入 LAS Parser 或按 Tool 名称写领域分支。

### Increment 8A 验收

- [x] 至少一个最小非 LAS 夹具能够复用黄金预期、评测指标、报告分层和图件/Artifact 元数据契约；
- [x] 相同输入、规则版本和 Tool 版本得到一致的确定性结构化结果与 Evidence 哈希；
- [x] 篡改黄金预期、Evidence Pointer、规则版本或图件来源时，评测能够确定性失败；
- [x] 离线测试不调用真实模型，在线评测必须显式开关、记录模型/用量且不记录凭据；
- [x] 文档明确“参考产品是 Harness 的验证应用，不是 GeoPhysAgent 支持领域的白名单”。

### Increment 8B——LAS QC 首个参考应用

- [x] 扩充 `WRAP.YES`、编码、厂商扩展和损坏文件处理；
- [x] 建立曲线别名、单位规范化和 LAS Rule Pack 来源版本；
- [x] 增加范围、尖峰、平线、异常跳变等确定性规则，并明确规则适用条件和不确定性；
- [x] 生成标准测井图并按 8A 契约登记图件 Artifact；
- [ ] 建立约 10 个授权真实样本及版本化黄金预期，覆盖正常、边界、厂商差异和损坏场景；
- [ ] 为真实样本建立用户任务集，记录期望的能力路由、允许的 Tool 轨迹、必需 Evidence、Validator 结果和最终报告要点；
- [ ] 使用 Fake Model 完成稳定回归，并对同一任务重复运行当前候选默认 Profile 若干次，输出 8A 定义的分层指标；（Fake Model 已完成；OpenCode Go 真实重复评测待执行）
- [x] 报告关键数字和确定性 QC 判断 100% 绑定 Evidence ID；
- [x] 报告明确区分数据观测、规则异常、模型解释、需要专业人员确认的假设和未覆盖能力；
- [ ] 对专业结论、图件、单位、异常解释和能力边界做地球物理用户走查；
- [x] LAS 应用仅通过 DataAsset capability、Tool/Provider、Validator、Rule Pack、Evidence 和 Artifact 扩展，不在通用 Agent Loop 中增加 LAS 特判。

### Increment 8B 验收

- [ ] 约 10 个授权真实样本具有可审查黄金预期，原始输入保持只读；
- [x] 图件曲线、深度方向、单位、标签、报告数字和规则判断与 Evidence 一致；
- [x] 正常、warning、损坏和能力不支持场景均具有明确终态，不猜测式解析或静默成功；
- [ ] Fake Model 回归与真实模型评测都能定位失败层级并输出可比较指标；
- [ ] 专业用户能够完成导入、自然语言质控、查看计划/证据/图件和导出报告的完整走查。

### Increment 8C——跨领域复用证明

- [x] 选择现有 SEG-Y 只读检查作为第二个 DataAsset 领域，并选择 `software-a-readonly` 作为 external-only 数据源场景；不把它们扩张成完整地震处理或商业软件控制产品；
- [x] 用 8A 的任务集和评测格式分别描述 SEG-Y 文件任务、无本地文件的外部 API 查询任务；
- [x] 复跑两条路径的候选计划、Tool 调用、Evidence、Validation、Artifact、最终回答和 Run 完整性检查；
- [x] 验证一资产、零资产两种 TaskSpec 都使用同一个 Agent Runtime、Provider Registry、Harness、Run Store 和报告事实门禁；
- [x] 验证新增 LAS Rule Pack、图件和报告能力不会出现在不匹配 capability 的 SEG-Y/API 任务中；
- [x] 代码审计不得出现按 `.las` 扩展名、LAS Tool 名称或 Provider 名称决定通用 Harness 行为的分支；
- [x] 若跨领域复用暴露通用契约缺口，修复应进入 8A/Harness 抽象并增加跨领域回归，而不是给第二领域建立旁路。

### Increment 8C 验收

- [x] SEG-Y 和 external-only API 场景无需修改 Harness 状态机、Evidence、Artifact 或恢复契约即可通过；
- [x] 模型只能看到与当前资产/外部任务匹配的 Tool，无法调用 LAS 专用 Tool；
- [x] 两条非 LAS 路径都能用 8A 指标说明成功或准确定位失败层级；
- [x] `pi/` 仍只作参考，LAS、SEG-Y 和外部软件适配均未复制为彼此独立的 Agent/Harness；
- [x] Increment 8 的完成结论表述为“首个参考产品和跨领域复用通过”，不得表述为“GeoPhysAgent 已覆盖全部地球物理任务”。

### Increment 8 总体验收

- [ ] 8A 通用契约、8B LAS 参考应用和 8C 跨领域复用证明全部通过；
- [ ] 至少一次候选发布评测同时包含真实 LAS 数据、SEG-Y 夹具和本机 loopback 外部 API；
- [ ] 真实模型重复评测、专业用户走查和确定性黄金测试的结果可共同审查；
- [ ] 已证明 LAS 是第一个参考应用而不是架构中心；后续增加测井解释、地震处理、重磁电或其他软件 Integration 时可以沿用相同扩展点。

> 工程实现状态（2026-09-09）：8A 与 8C 已通过离线门禁；8B 的解析、Rule Pack、图件、
> 报告和合成黄金回归已完成。Increment 8 尚不整体标记完成，因为授权真实 LAS 黄金样本、
> 当前 OpenCode Go Profile 的真实重复评测和地球物理专业用户走查仍需要用户提供数据并执行验收。

## P0：v0.1 产品入口落地

- [x] 用 ADR 明确正式用户任务采用 LLM-first，本地 Tool 负责计算，Harness 负责约束和验证；
- [x] 明确直接确定性 CLI 是开发、测试、批处理和诊断接口，不代表最终用户主流程；
- [x] 用统一 `run` 命令证明普通用户无需选择具体 Parser 或 Tool 即可发起任务；
- [x] 明确目标用户、安装门槛、多供应商命名配置、OpenCode Go 默认 Profile、切换流程、演示脚本、费用提示和数据发送边界；
- [ ] 定义未来最小本地工作台：数据导入、自然语言任务、计划确认、进度/取消、报告和 Evidence 查看；
- [ ] 任意 GUI/API 必须调用同一 Application/Harness 服务，不能复制状态机或绕过验证；
- [ ] 本地工作台只消费稳定的 Run View 和命令 API，不直接读写 `state.json`、`events.jsonl` 或内部检查点；
- [ ] 外部软件控制在界面上展示 Server、Tool、目标对象、副作用、权限和可取消性，不能用笼统的“继续”掩盖高风险操作；
- [ ] 在 Increment 7A/7B/7C/8 验收前不扩展大型 UI，避免界面问题掩盖内核和专业正确性问题。

## P1：Explore 安全与产品化

- [ ] 使用低权限账户、Job Object/容器等 OS 级隔离替代单纯 AST 应用策略；
- [ ] 增加内存、CPU、进程树和输出配额；
- [ ] 把生成代码候选提升流程接入正式 Tool 契约测试和人工审批；
- [ ] 增加 Explore 结果充分性 Validator，不只依赖模型文字判断。

### Explore 安全验收

- [ ] 超时、超内存、递归创建进程、越界文件访问和超量输出均被终止并留下可审计记录；
- [ ] Explore 失败、被取消或资源超限时不能被标记为顶层成功；
- [ ] 候选 Tool 在 Schema、单元测试、Validator、依赖和人工审批完成前不能进入正式 Registry。

## P1：权限确认与外部副作用

目标是把“模型建议做什么”“用户是否允许”和“操作系统是否允许”拆成三层。MCP 只是传输和发现协议，不能替代权限策略或沙箱。

- [ ] 定义 `ActionRisk` 与 `ApprovalDecision`：只读、受控写入、外部系统变更、不可逆操作；
- [ ] PlanPolicy 根据 Tool 声明、实际参数和目标对象计算本次调用风险，不能只相信 Provider 自报；
- [ ] 审批绑定 Run、Plan Step、Tool 版本、参数摘要、目标对象和有效期，参数变化后旧审批失效；
- [ ] 将“是否询问用户”的 Approval 与“即使批准也不能越界”的 ExecutionPolicy/OS Sandbox 分离；
- [ ] 默认拒绝未声明副作用、任意路径写入、任意网络访问和不可验证的外部修改；
- [ ] 外部软件 API/MCP Server 尽可能提供 dry-run、读取后校验、幂等键和补偿/回滚信息；
- [ ] 事件和最终报告记录谁批准了什么、实际执行了什么和结果如何，但不记录凭据。

### 权限验收

- [ ] 只读调用无需反复确认，高风险调用无法通过提示词、Tool 别名或参数编码逃过审批；
- [ ] 批准 A 文件/对象不等于批准 B 文件/对象，批准旧参数不等于批准修改后的参数；
- [ ] 用户拒绝、审批超时、取消和外部系统部分成功都有明确终态与恢复说明；
- [ ] Approval 不能扩大 Workspace、文件路径、网络和进程沙箱本身允许的范围。

## P1：Runtime 可观测性与统一配置

- [ ] 定义只读 `RunView`/`RuntimeItem` 投影，稳定表达用户消息、模型消息、计划、Tool 调用/结果、审批、验证、Compaction、Artifact 和终态；
- [ ] 为事件增加单调序号、Schema 版本和断点游标；订阅流程采用“快照 + after-sequence 追赶”，避免 UI 在快照与监听之间漏事件；
- [ ] CLI、未来 GUI 和 API 共用同一查询/命令服务，不各自推断内部状态；
- [ ] 定义版本化 `RuntimeProfile`：模型与协议、最大轮次/Token、活动 Provider/Tool、权限策略、验证策略、Context Budget 和费用边界；
- [ ] LAS、SEG-Y、Explore 以 Profile/Workflow 配置能力和策略，不复制新的 Agent Loop；
- [ ] 运行开始时冻结 Profile 快照；配置变化只影响新 Run 或经过明确迁移的暂停 Run；
- [ ] 增加脱敏日志和基础指标：任务成功率、Evidence 完整率、Tool 错误率、模型轮次、Context 压缩次数、Provider 启动/调用延迟和取消成功率。

### Runtime 可观测性验收

- [ ] 从 Run View 能解释“模型为什么看到这些 Tool、为什么调用失败、为何任务没有成功”；
- [ ] 模拟订阅连接断开后可从游标继续，不漏事件且不会把重复事件当成新副作用；
- [ ] Profile 与 Provider 变更不会改变正在执行的已审批 Step；
- [ ] 日志、视图、测试夹具和错误报告中不出现 API Key、认证 Header 或未经允许的完整数据内容。

## P1：失败证据与单 Agent 编排策略

先提高单 Agent 的求证能力，再考虑多智能体。失败应成为后续决策可引用的结构化知识，而不是只剩一段异常字符串。

- [ ] 定义 `FailureEvidence`/`RejectedHypothesis`：失败类别、触发条件、Tool/Validator、可重试性、相关 Evidence 和建议下一步；
- [ ] 将 Tool 失败、Validator 否决、假设被数据反驳和用户拒绝分别记录，避免模型无意义重复同一路径；
- [ ] 为 Explore 采用有限的“生成 → 执行 → 确定性测试 → 修正”循环，修正次数受预算和重放策略约束；
- [ ] 对解释性结论增加可选 Critic/Review 阶段；Critic 只能指出缺口或请求新证据，不能覆盖确定性 Validator；
- [ ] 对相互独立、只读且幂等的 Plan Step 评估并行执行；共享写入、顺序依赖和外部副作用仍保持串行；
- [ ] 在测井异常解释中试验“候选假设 → 可证伪预测 → Tool/数据检验 → 保留/拒绝”模板，避免过早共识；
- [ ] 先使用同一个模型的结构化多阶段工作流，不把多 Agent 当作质量提升的默认答案。

### 编排策略验收

- [ ] 相同失败不会在无新证据、无参数变化时被无限重试；
- [ ] Review 阶段能定位缺失证据，且不能把未验证推断改写成事实；
- [ ] 并行与串行执行得到一致的 Evidence/Artifact 语义，取消时所有子执行都被回收；
- [ ] 与单轮基线相比，改进以任务成功率、Evidence 完整率、成本和延迟共同衡量。

## P1：Harness 可维护性

- [ ] 明确 Executor、Recovery、TaskRunSession、Policy、Validation 和 Persistence 的模块边界；
- [ ] 仅在职责和测试边界明确后渐进拆分 `harness.py`，不进行一次性重写；
- [ ] 将 `general_agent.py` 拆分为 Context/Planning/Model Loop/Tool Dispatch/Finalization，保持现有外部入口兼容；
- [ ] 让 `TaskRunSession` 通过明确公共接口工作，停止跨模块调用 Harness 私有持久化和状态方法；
- [ ] 所有 Agent、Compaction 和 Provider 检查点/Artifact 写入都经 Run Store/Artifact Store，不各自实现原子 JSON 写入；
- [ ] 为 Provider、Validator 和 Artifact 扩展点形成稳定的最小公共 API；
- [ ] 禁止在 Harness 中按 LAS、SEG-Y、MCP 或具体 Provider 名称分支；
- [ ] 增加导入方向/循环依赖检查，并保持现有生命周期与故障注入测试无回归；
- [ ] 统一质量脚本的解释器选择与依赖自检；缺少 Ruff/测试依赖时给出可操作提示，避免误判为代码失败。

## 后续版本路线（v0.2+，已确定需要实现）

以下能力不进入 v0.1，并不表示放弃。暂定先建设长期 Memory 基础，再建设多智能体编排；最终版本号可以根据 v0.1 交付后的真实评测调整。两者都必须复用现有 Harness、Evidence、Validation、Artifact、Policy 和 Run Store 契约，不能形成第二套运行时。

### v0.2 候选：长期 Memory

目标是让 GeoPhysAgent 能够跨任务复用经过验证的项目知识、用户偏好和已批准工作流，同时保证来源清楚、可以纠错、可以删除。长期 Memory 与当前任务的 Context、Session 历史和崩溃恢复是四种不同概念，必须分开设计。

#### Memory 契约与存储

- [ ] 通过 ADR 明确 Working Context、Session History、Recovery Checkpoint 与 Long-term Memory 的边界和数据流；
- [ ] 定义 `MemoryRecord`：Memory ID、类型、作用域、内容、来源 Run/Evidence、可信度、创建者、时间、版本、敏感级别、过期策略和状态；
- [ ] 首版支持三类 Memory：项目/数据集事实，用户明确偏好，经人工批准的工作流经验；
- [ ] 原始 Chain-of-Thought、完整敏感数据、API Key、未经验证的模型推断和临时异常信息不得写入长期 Memory；
- [ ] 定义 `MemoryStore` 抽象并为本地单用户选择可迁移的参考实现；存储格式和索引版本必须可升级、备份和完整性检查；
- [ ] Memory 使用独立命名空间隔离用户、项目、数据集和组织；默认禁止跨项目检索；
- [ ] 支持查看、导出、更正、失效、删除和可审计的 Tombstone，不允许只能追加而无法遗忘。

#### Memory 写入、检索与治理

- [ ] 使用“候选提取 → 来源/Evidence 校验 → 冲突检测 → Policy/人工审批 → 提交”的写入流程，模型不能直接写入权威 Memory；
- [ ] 每条事实 Memory 尽可能绑定 Evidence ID、Tool/规则版本和数据资产指纹；源 Evidence 失效时标记 Memory 待复核；
- [ ] 检索同时考虑作用域、任务相关性、时效、可信度、敏感性和 Context Budget，不以向量相似度作为唯一准入条件；
- [ ] 将检索结果作为带来源的非系统上下文区块；Memory 中的指令文本不能提升权限、改变系统策略或扩大 Tool allowlist；
- [ ] 对冲突事实并列呈现来源和时间，禁止静默覆盖；由确定性规则或用户确认选择当前有效版本；
- [ ] 将“从失败中学习”限制为候选 Memory；只有重复出现、可复现并通过验证的经验才可晋升；
- [ ] 后台归纳/反思任务默认关闭；启用后只能生成候选，不得自动修改专业规则、Tool 或安全策略；
- [ ] 为 Memory 检索和写入记录事件、耗时、命中、采用/拒绝原因和最终使用的 Memory ID。

#### 长期 Memory 验收

- [ ] Memory 功能完全关闭时，现有 Run Recovery 和单任务 Context 行为不受影响；
- [ ] 跨项目、跨用户和敏感级别越权检索测试全部被拒绝；
- [ ] 过期、冲突、已删除、来源 Evidence 损坏和提示注入 Memory 不会作为可信事实进入最终回答；
- [ ] 用户可以回答“系统记住了什么、为什么记住、来自哪里、在哪些回答中使用”，并能更正或删除；
- [ ] 在固定跨任务评测集上，Memory 相比无 Memory 基线提高任务成功率或减少重复计算，且错误事实率、成本和延迟不超过门槛；
- [ ] Memory 不得成为最终专业结论的唯一证据，关键结论仍需当前有效 Evidence 或明确标记为历史参考。

### v0.3 候选：多智能体编排

目标不是简单增加更多模型调用，而是让适合拆分、并行或相互质疑的地球物理任务获得可度量收益。默认仍使用单 Agent；只有任务策略明确选择且预算允许时才创建子 Agent。

#### 父子运行与隔离

- [ ] 定义父 `AgentRun`、子 `AgentRun`、`AgentTaskBrief`、`AgentResult` 和依赖图契约；每个子 Agent 都有独立上下文、状态、预算和终态；
- [ ] 子 Agent 作为顶层 TaskRun 下的受管运行单元，复用同一事件、Attempt、Evidence、Validation 和 Artifact 系统；
- [ ] 父 Agent 只向子 Agent 传递结构化任务简报、允许的资产/Evidence 引用和最小必要上下文，不共享可变消息列表；
- [ ] 为角色定义版本化 Agent Profile：目标、模型、Tool/Provider allowlist、数据范围、权限、Validator、最大轮次、Token、费用和截止时间；
- [ ] 子 Agent 使用最小权限；不能继承父 Agent 未显式下放的 Tool、文件、网络或外部系统权限；
- [ ] 人工审批不能由另一个 Agent 代替，子 Agent 也不能扩大父任务的授权范围；
- [ ] 定义父取消、子失败、超时、部分完成和孤儿子进程的传播与回收规则。

#### 编排策略

- [ ] 实现顺序型“生成 → 测试/Validator → 修正”策略，用于代码、报告和解释的有限迭代；
- [ ] 实现并行 Worker + 汇总策略，只允许独立、只读或可安全合并的任务并行；
- [ ] 实现候选假设 + Critic/证伪策略，让不同 Agent 提出相互竞争的解释并要求可检验预测；
- [ ] 定义 Coordinator 的确定性合并规则：优先 Evidence 和 Validator，子 Agent 的自然语言结论不能直接晋升为事实；
- [ ] 冲突结果必须保留各自来源、证据和否决理由；不以多数投票替代专业验证；
- [ ] 建立全局与分 Agent Budget Manager，限制并发数、模型轮次、Token、费用、Tool 调用和墙钟时间；
- [ ] 支持异构模型是后续优化项，不作为首个多智能体版本的必需条件；先验证编排策略本身是否有效；
- [ ] 将失败的假设、Critic 意见和未解决冲突写入当前 Run；是否晋升长期 Memory 必须经过 Memory 写入门禁。

#### 多智能体验收

- [ ] 通过事件时间线可以重建父子关系、上下文输入、Tool 调用、Evidence 来源、预算消耗、取消和最终合并过程；
- [ ] 任一子 Agent 不能伪造其他 Agent 的 Evidence、修改其他 Agent 的上下文或绕过 Harness 直接完成顶层任务；
- [ ] 部分子 Agent 失败、超时或被取消时，Coordinator 能给出可解释的部分结果或安全失败，不产生孤儿运行；
- [ ] 并行执行对共享 Artifact、外部软件和非幂等 Tool 不产生竞态或重复副作用；
- [ ] 在固定的复杂任务评测集上，与“同模型、同预算的单 Agent”比较任务成功率、Evidence 完整率、专业错误率、成本和延迟；
- [ ] 只有多智能体在目标任务类别上稳定优于单 Agent 基线时才默认启用，否则保留为显式可选策略。

### Memory 与多智能体联合门禁

- [ ] 子 Agent 默认只能读取任务允许的 Memory 视图，不能直接写长期 Memory；
- [ ] 多个 Agent 提交的 Memory 候选必须去重、检测冲突并保留各自 Evidence/AgentRun 来源；
- [ ] Memory 检索、Agent 间消息和共享 Artifact 全部受同一数据分级、项目隔离和审计策略约束；
- [ ] 对“错误 Memory 被多个 Agent 放大”“Agent 合谋绕过审批”“循环委派耗尽预算”和“提示注入跨 Agent 传播”建立专项安全测试。

## 暂不进入 v0.1

- 完整 Web/桌面产品；是否增加最小本地工作台由“v0.1 产品入口落地”任务决定；
- 多智能体：转入“v0.3 候选”路线，v0.1 只完成其所需的单 Agent 编排、事件、权限和评测基础；
- 长期 Memory：转入“v0.2 候选”路线，v0.1 只完成 Context、Session、Recovery 的边界收敛；
- 大规模多井/三维地震解释；
- 自动执行任意 Shell 或任意外部代码；
- 自动下载/安装未知 MCP Server、在线插件市场和无审批热更新；
- MCP Resources、Prompts、Sampling 与远程 Streamable HTTP 的生产支持。

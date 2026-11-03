# GeoPhysAgent v0.1 开发路线图

> 状态：Draft；Increment 6 已完成统一顶层 TaskRun、验证协调与生命周期恢复  
> 产品范围：[docs/MVP_SPEC.md](docs/MVP_SPEC.md)  
> 架构设计：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)  
> MCP 集成设计：[docs/MCP_INTEGRATION.md](docs/MCP_INTEGRATION.md)  
> Explore 模式设计：[docs/EXPLORE_MODE.md](docs/EXPLORE_MODE.md)  
> 当前实现状态：[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md)  
> 参考代码政策：[docs/REFERENCE_POLICY.md](docs/REFERENCE_POLICY.md)  
> 最后更新：2026-09-07

## 1. 路线图目标

本路线图把 GeoPhysAgent 推进到可安装、可验证、可演示、可由真实用户试用的 v0.1。实际完成情况以实现状态文档为准；当前 Python 垂直原型已经验证 DataAsset、Provider Registry、Pipeline、Explore、最小 LLM Tool Call，以及统一 TaskRun、验证门禁和生命周期恢复。正式产品采用 LLM-first、Local-execution、Harness-governed，下一阶段先冻结工程基线并完成通用 Agent Runtime，再接入 MCP Provider。

路线图遵循以下实施顺序：

```text
产品边界
→ 工程底座
→ 稳定契约
→ Provider-neutral Tool 契约
→ 通用 DataAsset、确定性 Pipeline 与受控 Explore
→ 领域 Harness、Validator 与生命周期闭环
→ 通用 LLM-first Agent Runtime
→ MCP 参考集成
→ 参考产品
→ 评测与发布
```

关键原则：最终用户任务必须经过 LLM Agent，但专业计算仍由可独立测试的确定性 Tool 完成。默认回归用 Fake Model 和无网测试隔离算法、Harness 与模型问题；候选发布前允许并要求调用真实 MiniMax 完成少量在线端到端验收。

## 2. 里程碑总览

| 里程碑 | 结果 | 建议累计人周 |
|---|---|---:|
| M0 方向冻结 | MVP、架构、数据和发布策略明确 | 1–2 |
| M1 工程闭环 | Python、CI、Tool Provider 与测试替身打通 | 4–6 |
| M2 确定性 LAS 管线 | LAS 工具完成质控、统计、绘图，并通过 MCP 参考集成验证 | 7–11 |
| M3 Agent 闭环 | 自研 Runtime、Tool Provider、GeoDomainHarness、Skill 和恢复接通 | 12–16 |
| M4 产品候选版 | CLI、报告、分析包和扩展文档可供内部试用 | 17–21 |
| M5 v0.1 发布 | 核心/MCP 契约、黄金数据、故障测试和干净环境验收通过 | 22–25 |

人周用于表达工作量，不等同于自然周。熟悉 Agent Runtime、Python 打包和测井数据的多人团队可并行缩短日历时间；单人开发应优先保持范围稳定。由于不复用 Pi Runtime，自研 Agent Loop、模型流式协议和恢复边界会增加工作量。

## 3. 工作流与负责人角色

即使由一人开发，也应区分以下责任视角：

- 产品：范围、用户故事、交付体验；
- 地球物理：曲线、单位、质量规则和解释边界；
- 平台：自研 Agent Runtime、Harness、Tool Provider、MCP 集成、状态机和持久化；
- Python：LAS 数值计算、绘图和 Bundled MCP 参考 Server；
- 质量：黄金数据、故障注入和发布验收；
- 安全：数据披露、文件权限、日志脱敏和依赖审查。

任何领域规则进入默认规则集前，至少经过地球物理和质量两个视角审查。

## 4. Phase 0：范围与关键决策

### 目标

把愿景转化为可执行且可验收的 v0.1 边界。

### 工作项

- 评审并冻结 MVP 产品规格；
- 决定正式支持的平台：Windows-only 或 Windows + Linux；
- 决定默认模型、可替代模型和凭据方式；
- 确认 v0.1 的第一个真实 Model Provider；默认采用兼容协议适配器，并以 MiniMax 配置完成验收；
- 决定发送给模型的数据策略；
- 明确“基础分析”与“专业解释”的边界；
- 选择 10 个左右具有代表性的 LAS 样本；
- 核实样例数据授权；
- 决定开源许可证；
- 确认 `pi/` 仅为只读参考，不进入构建和发布依赖；
- 定义第三方代码参考、独立重写和选择性移植的审查流程；
- 确认 Tool Provider 是统一执行边界，MCP Server 是外部集成机制之一；
- 固定 v0.1 只正式支持本地 stdio，Streamable HTTP 延后到 v0.2；
- 创建 ADR 目录并记录首批决策；
- 建立 v0.1 风险登记表。

### 交付物

- 审批后的 `MVP_SPEC.md`；
- 审批后的 `ARCHITECTURE.md`；
- 首批架构 ADR；
- 审批后的 `REFERENCE_POLICY.md`；
- 数据集清单与授权记录；
- 风险登记表。

### 完成标准

- 团队能用一句话描述 v0.1；
- 每个超范围需求都能明确归入非目标；
- 至少一个正常样本和五类异常样本可用于开发；
- Runtime 自研边界、代码来源政策和模型数据策略没有阻断性待定项。
- MCP Host/Server 边界、首版传输和 Tool 信任策略明确。

## 5. Phase 1：工程底座

### 目标

建立可以稳定开发、测试、打包和诊断的 Python-first 工程；出现明确跨语言需求前不增加第二控制平面。

### 工作项

- 初始化 GeoPhysAgent 自己的 Git 仓库；
- 将 `pi/` 明确排除在 GeoPhysAgent workspace、构建、测试和发布之外；
- 在根 `.gitignore` 中忽略 `/pi/`，但保持其自身 Git 仓库独立；
- 建立第三方参考代码清单，记录参考仓库、提交和许可证；
- 建立 Python `pyproject.toml` 和锁定依赖方案；
- 建立 Python 格式化、类型检查和静态检查；
- 建立单元测试框架；
- 建立跨平台脚本；
- 建立 CI：lint、type-check、unit、contract；
- 建立 `.gitignore`、许可证、贡献指南和安全说明；
- 增加 `doctor` 原型，检查 Python、模型配置和依赖；
- 配置依赖安全审查和版本固定策略。

### 交付物

- 可安装的空 CLI；
- 可启动的最小 MCP Server 和可连接的 MCP Client；
- CI 工作流；
- 开发环境说明；
- 第一个端到端冒烟测试。

### 完成标准

- 干净环境可按 README 安装依赖；
- 一个命令执行 Python 格式、类型、静态和单元测试检查；
- CLI 可以通过 stdio 启动 MCP Server、完成版本与能力协商并列出测试 Tool；
- CI 在无模型密钥时也能完整运行非 E2E 测试。

## 6. Phase 2：核心契约

### 目标

在实现业务逻辑前固定系统边界语言。

### 工作项

- 定义 `TaskSpec`；
- 定义 `DataAsset`；
- 定义 `ExecutionPlan` 和 `PlanStep`；
- 定义 `ToolDefinition`、`ToolInvocation` 和 `ToolResult`；
- 定义 `ValidationReport`；
- 定义 `ArtifactManifest`；
- 定义 `RunRecord` 和领域事件；
- 定义错误分类和错误序列化；
- 定义 `McpServerConfig`、`IntegrationManifest` 和 `McpConnectionSnapshot`；
- 定义 MCP Tool 与 GeoTool 的映射契约；
- 定义 Schema 版本策略；
- 生成或共享 TS/Python 类型；
- 建立合法、非法和兼容性样例；
- 建立契约测试。

### 交付物

- `packages/contracts`；
- Python 对应模型；
- JSON Schema；
- 契约测试夹具；
- Schema 版本和迁移说明。

### 完成标准

- 两端对同一批样例做出一致的接受或拒绝结果；
- 未知破坏性版本不会被静默接受；
- 错误和警告能被机器处理，而非只能解析文本；
- 路径、哈希和 Artifact 关系有统一表示。

## 7. Phase 3：MCP Host 与集成层

> 实施顺序说明：按当前增量计划，本节在通用 LLM-first Agent Runtime 的最小闭环之后实施；章节编号仅保留早期主题分组，不再表示实际开发先后。

### 目标

建立 Provider-neutral Tool 执行层，以及可靠、隔离、可取消、可诊断的 MCP 集成能力，并用 Bundled Server 验证真实接入路径。

### 工作项

- 选择、固定并审查官方 MCP Client/Server SDK；
- 实现 MCP Client Manager；
- 实现 Server Registry 和 Integration Manifest 加载；
- 实现 stdio Server 启动、版本与能力协商和关闭；
- 实现 `tools/list` 分页和 `tools/call`；
- 实现 MCP Tool 到 GeoToolDefinition 的映射；
- 实现 CallToolResult 到 GeoToolResult 的映射；
- 实现 Tool 名称空间、allowlist 和冲突检测；
- 实现连接和调用超时；
- 实现取消和强制终止；
- 分离协议输出与诊断输出；
- 实现输入、structuredContent 和 Artifact Schema 校验；
- 实现只读、幂等和重放元数据；
- 实现 Server 崩溃后的受控重启；
- 为 Streamable HTTP 定义传输接口和测试桩；
- 编写协商失败、协议不兼容、非法消息、超时、崩溃和大输出测试。

### 交付物

- `mcp-client-manager` 包；
- `integration-registry` 包；
- 最小测试 MCP Server；
- 示例 `echo` 和 `health` MCP Tool；
- 故障注入测试。

### 完成标准

- 连续调用不会串线；
- MCP Server 崩溃不会拖垮 Host；
- 超时任务能终止；
- 非法结果不能进入上层状态；
- 所有调用都生成结构化记录。
- Server instructions、Resources 和 Prompts 不会自动进入系统指令。

## 8. Phase 4：确定性 LAS 数据管线

### 目标

不使用 LLM，也能通过统一 GeoTool 契约稳定完成标准 LAS 质控、统计和绘图；其中至少一组能力通过 Bundled LAS MCP Server 验证 MCP Provider 路径。

### 工作项

#### 数据资产

- 文件格式和编码探测；
- 输入哈希；
- 只读输入注册；
- 派生数据路径管理。

#### LAS 工具

- `inspect_las`；
- `compute_curve_statistics`；
- `check_depth_axis`；
- `detect_curve_anomalies`；
- `plot_well_logs`。

以上工具注册为统一 GeoTool。v0.1 可由 Bundled LAS MCP Server 提供其中一组或全部实现，但 Harness 只依赖 GeoTool 契约，不依赖 MCP。经 MCP 调用的工具必须返回符合 GeoPhysAgent Schema 的 structuredContent。

#### 规则

- 深度曲线候选；
- 曲线别名；
- 常见单位；
- NULL 值；
- 采样间隔；
- 基础范围和异常规则；
- 规则来源和版本。

#### 产物

- 曲线清单 JSON；
- 统计 JSON；
- QC JSON；
- PNG 图件；
- ArtifactManifest。

### 交付物

- 可通过 MCP Client 调用的确定性 LAS pipeline；
- 可独立启动和测试的 Bundled LAS MCP Server；
- 第一版规则集；
- 黄金数据测试；
- 基准测试脚本。

### 完成标准

- 正常样本产生稳定结果；
- 深度倒序、重复深度、不规则采样、大量 NULL、未知单位和损坏文件有明确结果；
- 原始输入不被修改；
- 相同输入和工具版本得到一致结构化结果；
- 图件方向、标签和曲线对应关系正确。
- Server 可被独立 MCP 测试 Client 完成连接、能力协商、发现和调用。

## 9. Phase 5：Validator 框架

> 实现状态：Increment 6 已完成四层 Registry、固定顺序协调、成功门禁、最终产物 Evidence 引用检查和离线完整性验证；用户确认、建议恢复动作与完整 Validator SDK 仍按后续增量推进。

### 目标

让验证成为可以控制执行的独立系统能力。

### 工作项

- 建立 Validator Registry；
- 建立 Data、Semantic、Process、Result 四类接口；
- 实现验证依赖和执行顺序；
- 实现 `pass/warning/fail` 聚合规则；
- 实现 `continuation.allowed`；
- 实现用户确认触发条件；
- 实现建议恢复动作；
- 实现报告数字来源验证；
- 实现 Artifact 完整性验证；
- 实现独立 `validate <run-directory>` 命令；
- 提供新增 Validator 示例。

### 交付物

- Validator SDK；
- LAS 默认 Validator 集；
- ValidationReport；
- 分析包离线验证命令。

### 完成标准

- Validator 失败能阻止成功终态；
- warning 能进入最终报告；
- 每个检查包含证据和影响；
- 离线验证可以发现被修改或丢失的 Artifact；
- 新 Validator 不需要修改 GeoDomainHarness 核心。

## 10. Phase 6：Run Store、Artifact Store 与状态机

> 实现状态：Increment 6 已完成版本化检查点、原子提交、Artifact 哈希、状态机、终态约束、单写者锁、时间线重建及 `status/resume/cancel`；MCP 外部 Job ID 和协议级运行中取消留待 MCP 增量。

### 目标

建立可恢复、可审计的领域执行系统。

### 工作项

- 实现任务目录布局；
- 实现文件型 Run Store；
- 实现临时写入和原子替换；
- 实现 Artifact 注册和哈希；
- 实现领域事件日志；
- 实现状态机及转移约束；
- 实现步骤尝试和任务尝试上限；
- 实现终态规则；
- 实现恢复点；
- 实现任务查询、取消和恢复；
- 持久化 MCP Server、协议版本、Tool Schema 和外部 Job ID；
- 测试各状态中断后的恢复行为。

### 交付物

- `geo-harness` 基础包；
- `artifact-store`；
- `run-store`；
- `status`、`resume` 和 `cancel` 用例。

### 完成标准

- 任意阶段中断后，状态文件不会半写损坏；
- 完成步骤不被无条件重复执行；
- 达到终态后不能继续写步骤；
- 产物丢失或哈希变化可被发现；
- 运行记录可以重建用户可读的执行时间线。

## 11. Phase 7：自研 Agent Runtime

> 状态（2026-09-07）：代码、离线 Fake Model 验收、工程基线和显式在线入口已完成；真实 MiniMax 三场景因当前执行环境未注入密钥，仍是候选发布前的未完成门禁。

### 目标

独立实现满足 v0.1 需求的通用 LLM-first Agent Loop。最终用户任务必须由模型参与理解、规划、Tool 选择、结果观察和解释，同时不得污染领域核心或绕过 Harness。

### 工作项

- 实现内部 `AgentRuntime` 接口；
- 实现模型客户端 Port 和第一个 Provider Adapter；
- 实现 ModelProfile 加载、环境变量凭据解析和配置诊断；
- 支持 Anthropic-compatible 或 OpenAI-compatible 流式消息与 Tool Call，并用 MiniMax API 完成真实冒烟测试；
- 确保 API Key、认证 Header 和敏感模型响应不进入日志、报告或 RunRecord；
- 实现流式 Assistant 响应；
- 实现 Tool Call 解析、参数 Schema 校验和 Tool Result 回填；
- 实现多轮 Agent Loop 与明确停止条件；
- 实现取消、超时和有限重试；
- 实现 Runtime 事件并映射到领域事件；
- 将 Runtime Tool Call 交给 Geo Tool Registry，由 Tool Provider Registry 路由；
- 实现受控 Geo Tool 注册，不提供通用 Shell 和文件写入工具；
- 建立系统 Prompt 的数据/指令边界；
- 实现最小对话上下文和运行记录；
- 建立 Mock Model 和 Mock Runtime 测试；
- 建立少量真实模型冒烟测试；
- 建立显式在线测试开关；允许使用环境变量 API Key 和产生费用，候选发布前必须执行；
- 参考 Pi 的 Agent Loop、事件和 Session 测试设计，但独立编写实现与测试；
- 验证 Runtime 重试与领域恢复不会重复执行工具效果。
- 验证 Runtime 不会绕过 Geo Tool Registry 直接调用专业计算实现；MCP Tool 必须经 MCP Tool Provider 路由。

### 交付物

- `agent-runtime`；
- 第一个 `model-client`；
- Runtime 契约测试；
- 模型与运行配置文档；
- 第三方参考与代码来源记录。

### 完成标准

- Geo 核心测试不需要真实模型；
- 领域层不直接依赖模型供应商事件和消息结构；
- Agent 只能调用任务允许的工具；
- 专业 Tool Call 必须通过已登记 Tool Provider；MCP Tool 必须来自受信任 Server；
- 模型失败不会破坏已提交的专业结果；
- 替换 Mock Model 与真实模型客户端时核心工作流行为不变；
- 真实 MiniMax 能完成至少一个多 Tool TaskRun，最终回答引用已验证 Evidence；
- 模型不可用时正式用户任务明确失败或等待，不得静默退化为直接确定性工作流；
- 构建和测试过程不读取或编译 `pi/` 中的源码。

## 12. Phase 8：LAS QC Skill 与 Agent 计划

### 目标

让自然语言任务可靠映射到受约束的 LAS 工作流。

### 工作项

- 编写 `well-log-qc` Skill；
- 建立确定性计划模板；
- 实现任务分类；
- 实现 LLM 候选计划解析；
- 实现 PlanPolicy；
- 实现曲线选择和歧义处理；
- 实现假设与限制记录；
- 实现超范围请求处理；
- 实现用户确认状态；
- 建立不同表达方式的 Agent 评测集。

### 交付物

- LAS QC Skill 包；
- 计划模板和策略；
- Agent 场景评测；
- 用户确认交互。

### 完成标准

- 标准任务稳定选择 LAS QC 工作流；
- 候选计划不能引用未注册 Tool；
- 必须的质控和验证步骤不能被模型跳过；
- 超范围请求不会产生无依据结论；
- 单位或曲线存在实质歧义时正确请求确认。

## 13. Phase 9：报告与参考产品

### 目标

把内核能力形成地球物理用户可以直接使用的产品体验。

### 工作项

- 完成 CLI 命令；
- 完成 `mcp list`、`mcp doctor` 和 Server 诊断输出；
- 实现阶段与进度输出；
- 实现 Markdown 报告模板；
- 从已验证结果绑定报告事实；
- 实现图件引用；
- 展示假设、限制和验证状态；
- 实现人类可读错误信息；
- 增加 `--json` 自动化输出；
- 增加 `doctor` 完整诊断；
- 编写快速开始和故障排查；
- 生成一个完整示例分析包；
- 进行地球物理用户走查。

### 交付物

- v0.1 候选 CLI；
- 标准报告；
- 示例分析包；
- 用户文档；
- Tool 和 Validator 扩展教程。
- MCP Server 开发、注册、授权和故障排查教程。

### 完成标准

- 新用户按文档可独立完成示例；
- 报告中的关键数字 100% 可追溯；
- warning 和 limitation 不会被隐藏；
- 失败任务不会生成成功式报告；
- 输出目录可整体复制并离线检查。

## 14. Phase 10：加固与发布

### 目标

达到可交付标准，而不只是开发机器上的演示。

### 工作项

- 扩充黄金数据；
- 执行全部故障注入；
- 执行模型变化回归；
- 执行性能和内存基准；
- 执行路径安全和 Prompt Injection 测试；
- 执行 MCP instructions、Tool 描述、Resources 和 Prompts 注入测试；
- 执行 Server 名称冲突、断线、协议错误和版本变化测试；
- 检查日志和报告脱敏；
- 检查第三方许可证；
- 锁定依赖和生成软件物料清单；
- 在干净 Windows 环境安装；
- 如承诺 Linux，则执行 Linux 验收；
- 发布候选版给专业用户试用；
- 修复阻断问题；
- 编写 CHANGELOG、已知限制和升级说明；
- 打标签并生成发布包。

### 完成标准

- `MVP_SPEC.md` 中的全部发布门槛通过；
- 无未关闭的发布阻断问题；
- 安装、运行、失败诊断和卸载路径清楚；
- 示例数据授权和第三方许可证完整；
- 已知限制公开；
- 发布产物可从零复现。

## 15. 优先级 Backlog

### P0：v0.1 必须完成

- 产品与架构决策；
- Python-first 工程和 CI；
- 核心 Schema；
- Geo Tool Registry 和 Tool Provider Registry；
- MCP Client Manager 和 Server Registry；
- Bundled LAS MCP Server；
- 五个 LAS 核心 Geo Tools，其中至少一组验证 MCP Provider 路径；
- 四层 Validator；
- Run/Artifact Store；
- 领域状态机；
- 自研 Agent Runtime；
- 模型客户端；
- LLM-first 统一 `run` 入口和多 Tool 计划闭环；
- 候选发布前的真实 MiniMax 在线端到端测试；
- LAS QC Skill；
- CLI；
- Markdown 报告；
- 黄金数据和故障测试；
- 安装与扩展文档；
- 个人项目 MCP Server 示例与文档。

### P1：有余力进入 v0.1

- HTML 报告；
- 更多测井绘图模板；
- 批量任务但不做多井解释；
- SQLite Store；
- 本地模型示例；
- 简单任务历史浏览器。

### P2：v0.1 后考虑

- Web/桌面 UI；
- 多井分析；
- SEG-Y 图件、处理与更完整修订支持；
- 地震属性；
- 长期 Memory；
- Multi-Agent；
- 远程 MCP Server；
- 企业多用户权限。

P1 或 P2 不得挤占 P0 的验证、测试和安装工作。

## 16. 测试矩阵

| 层级 | 是否需要模型 | 主要目标 |
|---|---:|---|
| 单元测试 | 否 | 规则、状态机、统计和路径 |
| 契约测试 | 否 | TS/Python 边界和 Schema |
| MCP 契约测试 | 否 | 版本协商、能力、Tools、传输和错误映射 |
| Tool 集成测试 | 否 | Built-in/MCP Provider、LAS 库和 Artifact |
| Harness 集成测试 | 否，使用 Mock | 计划、状态、验证和恢复 |
| 黄金数据测试 | 否 | 科学与数据正确性 |
| Agent 评测 | Fake Model 为主，真实 MiniMax 为辅 | Skill、计划、Tool 轨迹和解释边界 |
| 端到端测试 | 是，候选发布使用真实 MiniMax | LLM-first 用户完整体验 |
| 故障注入 | 大部分否 | Server 崩溃、断线、超时、半写和恢复 |

真实模型测试数量保持有限，绝大多数 Tool 与 Harness 正确性必须能在无网络、无密钥环境中验证；但 LLM 是正式产品必经入口，因此候选发布不能只依赖 Mock，必须通过显式启用的 MiniMax 在线验收。在线测试不得记录 API Key，且应明确提示费用和数据发送边界。

## 17. 风险登记

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| 自研 Runtime 低估复杂度 | Agent Loop 和恢复不稳定 | 严格限制 v0.1 功能、Mock Model、状态与故障测试 |
| 参考代码被无边界复制 | 许可证和维护风险 | 优先重写、选择性移植审查、来源清单和独立测试 |
| MCP Server 被过度信任 | 越权、提示注入和数据泄露 | Host allowlist、最小权限、内容不可信、调用审计 |
| MCP 传输大型数据 | 性能与内存失控 | DataAsset/Artifact 引用，MCP 仅作控制面 |
| 外部软件长任务重复提交 | 重复计算或修改 | 持久化 Job ID、幂等键、提交 Tool 禁止自动重放 |
| LAS 数据差异大 | 真实文件失败 | 黄金数据、多来源样本、明确兼容范围 |
| LLM 生成错误结论 | 用户误用 | 事实绑定、Validator、解释边界、限制披露 |
| TS/Python 打包复杂 | 用户无法安装 | 早期做干净环境安装，不拖到发布阶段 |
| 规则缺少依据 | 误报或漏报 | 规则来源、版本化、专业审查、可配置阈值 |
| 任意 Python 绕过控制 | 安全与复现失效 | v0.1 不开放通用执行 Tool |
| 运行记录泄露数据 | 合规风险 | 最小披露、脱敏、默认本地和内容分级 |
| Runtime 与领域恢复互相冲突 | 重复执行 | 明确运行级重试与领域步骤恢复边界 |
| 过早追求通用框架 | 延迟交付 | 只服务 LAS 闭环，用第二领域验证抽象 |
| UI 扩张 | 挤占内核质量 | v0.1 只交付 CLI |

## 18. 每个工作项的完成定义

一个工作项只有同时满足以下条件才算完成：

- 行为与 MVP 或架构文档一致；
- 公共边界有类型和运行时 Schema；
- 正常、异常和边界测试通过；
- 错误信息可操作；
- 不把密钥或敏感数据写入日志；
- 文档与实现同步；
- 新增依赖已审查许可证和维护风险；
- 产物和状态不会破坏恢复能力；
- 代码评审中没有未解决的 P0/P1 问题。

## 19. v0.1 发布检查表

### 产品

- [ ] 标准用户故事端到端通过
- [ ] 超范围请求行为符合规格
- [ ] 报告内容由专业用户走查
- [ ] 已知限制完整披露

### 数据与科学性

- [ ] 黄金样本全部通过
- [ ] 严重数据问题无漏检
- [ ] 单位歧义不被静默处理
- [ ] 统计计算和 NULL 策略有测试

### 工程

- [ ] TS/Python 检查全部通过
- [ ] MCP 协商、协议、断线、超时和 Server 崩溃测试通过
- [ ] 状态恢复测试通过
- [ ] Artifact 哈希和离线验证通过

### Agent

- [ ] Tool 白名单有效
- [ ] MCP Server 和 Tool allowlist 有效
- [ ] Server instructions、Resources、Prompts 不会自动进入系统指令
- [ ] 必要步骤不能被计划跳过
- [ ] 报告事实追溯率 100%
- [ ] 模型失败不会产生伪成功

### 安全

- [ ] 输入不可修改
- [ ] 路径逃逸测试通过
- [ ] Prompt Injection 测试通过
- [ ] MCP Server 权限和路径隔离测试通过
- [ ] 日志和报告不含密钥
- [ ] 模型数据披露策略已记录

### 分发

- [ ] 干净环境安装通过
- [ ] `doctor` 能发现常见环境问题
- [ ] 快速开始可独立执行
- [ ] 许可证和第三方清单完整
- [ ] 发布包和示例可复现
- [ ] Bundled LAS MCP Server 可被独立 Client 调用
- [ ] 第三方示例 MCP Server 可按文档接入

## 20. v0.1 之后的决策门

完成 v0.1 后，不立即扩张全部领域。先依据真实使用结果回答：

1. 哪些 Harness 抽象被 LAS 工作流实际使用？
2. 哪些抽象只是提前设计且增加复杂度？
3. Validator 是否真正改变了执行结果？
4. 用户最需要的是自动分析、数据质控还是报告生成？
5. 开发者能否不修改核心代码增加 Tool 和 Validator？
6. 最小只读 SEG-Y 已验证公共契约；下一步应优先多 LAS 批处理，还是扩展到二维 SEG-Y 剖面与处理？

只有这些问题得到证据支持后，再确定 v0.2 和长期公共 API。

# GeoPhysAgent v0.1 架构设计

> 状态：Draft  
> 对应产品规格：[MVP_SPEC.md](MVP_SPEC.md)  
> MCP 集成设计：[MCP_INTEGRATION.md](MCP_INTEGRATION.md)  
> Explore 模式设计：[EXPLORE_MODE.md](EXPLORE_MODE.md)  
> LLM-first 决策：[adr/ADR-013-llm-first-local-execution.md](adr/ADR-013-llm-first-local-execution.md)  
> 参考代码政策：[REFERENCE_POLICY.md](REFERENCE_POLICY.md)  
> 最后更新：2026-09-07

## 1. 架构目标

GeoPhysAgent 使用“框架作为内核，产品作为参考实现”的策略：

- 最终用户任务采用 LLM-first：LLM 是理解、规划、Tool 选择和解释的必经控制入口；
- 专业数据与计算采用 Local-execution：尽量在本地 Tool 中处理，LLM 不直接承担事实计算；
- 全过程采用 Harness-governed：模型计划只有通过策略、权限、Schema、Evidence 和 Validator 后才能执行和完成；
- 内核提供面向地球物理任务的计划、工具、验证、策略、产物和审计契约；
- LAS QC 工作流作为第一套开箱即用的参考产品；
- GeoPhysAgent 独立实现最小 Agent Runtime，并围绕地球物理任务持续演进；
- LLM 是 v0.1 Agent 闭环的必需组成，通过可替换 Model Provider 接入；
- Geo Tool Registry 对实现方式保持中立，可承载内置、受控 Explore 和 MCP Tool Provider；
- MCP Server 是跨进程、个人项目和外部软件接入的重要标准机制，但不是 GeoPhysAgent 的产品主体或唯一 Tool 通道；
- Bundled Python LAS MCP Server 提供首个 MCP 参考实现，不负责对话和任务编排；
- 所有专业结论必须经过结构化工具结果和 Validator，而不是只依赖模型文本。

架构优先保证：

1. 专业结果可验证；
2. 全过程可追溯；
3. Runtime 可替换；
4. 工具和规则可扩展；
5. 失败不会伪装为成功；
6. 第一版可以被真实用户运行和检查。

## 2. 关键设计原则

### 2.1 LLM 驱动，Harness 约束

正式用户任务必须经过 Agent Runtime。模型理解意图、提出计划、选择 Tool、观察中间结果、选择允许的恢复动作并撰写解释，但不能绕过工具白名单、Schema、权限、验证和状态机。模型不可用时任务明确失败或等待，不得静默退化为绕过 Agent 的固定流程。

### 2.2 计算事实与自然语言解释分离

数值、单位、异常位置和统计结论来自确定性工具。LLM 只消费结构化事实并组织表达。最终报告不得把模型自行计算的数字作为事实。

### 2.3 验证是执行控制的一部分

Validator 不只是报告生成后的附加检查。它可以允许继续、带警告继续、要求重试、请求用户确认或终止任务。

### 2.4 输入文件不可变

任何清洗、转换和规范化都生成派生资产。原始输入只读，并通过哈希关联后续产物。

### 2.5 所有边界都使用版本化契约

Python 模块、持久化数据、MCP 进程和未来扩展组件之间均使用带 `schema_version` 的结构化对象。边界对象必须经过运行时验证；只有出现明确跨语言需求并通过 ADR 后，才增加新的语言控制平面。

### 2.6 先完成垂直闭环，再用第二个领域验证

v0.1 以 LAS QC 作为参考产品，同时为 TXT/CSV/LAS 表格提供通用 DataAsset、参数化 Pipeline 和受控 Explore 能力。Increment 5 又用最小只读 SEG-Y 检查验证同一 Harness 能否承载第二领域，但没有借此提前扩张到地震处理、成图和解释。Explore 结果必须标为探索级，不能伪装成已验证的正式工作流。

## 3. 系统上下文

```text
用户
  ↓
CLI / Future UI
  ↓
Application Service
  ↓
Agent Runtime ←────────────→ Model Provider（OpenCode Go / MiniMax / future local LLM）
  │ 候选计划 / Tool Call / 最终回答
  ▼
GeoDomainHarness ←──────────→ Run Store / Artifact Store
  ↓
Geo Tool Registry
  ├── Built-in Tool Provider
  ├── Explore Tool Provider
  └── MCP Tool Provider → MCP Client Manager → MCP Servers → External Software
```

## 4. 自研 Runtime 与 Pi 参考代码的边界

`pi/` 是从外部仓库克隆的只读参考代码。它不属于 GeoPhysAgent 的运行时依赖，不参与 GeoPhysAgent 构建、测试、打包或发布。项目不在 `pi/` 内创建 Geo 代码，也不通过 workspace、npm、Git 子模块或源码引用方式连接 Pi。

需要类似能力时遵循以下顺序：

1. 从 GeoPhysAgent 的用户场景和契约出发定义行为；
2. 参考 Pi 的架构、测试思路和边界处理；
3. 优先独立重写最小实现；
4. 确需复制少量代码时，先审查许可证和依赖，记录来源、提交和修改；
5. 复制后的代码归入 GeoPhysAgent 自己的模块、测试和维护体系，不与 `pi/` 形成运行时连接。

禁止批量复制 Pi CLI、TUI、模型目录、会话系统或完整包。任何选择性移植都必须由实际需求驱动，并通过代码来源清单追踪。

详细规则以 [REFERENCE_POLICY.md](REFERENCE_POLICY.md) 为准。CI 必须能够在不存在 `pi/` 的环境中完成全部构建和测试。

### 4.1 Geo Agent Runtime 负责

- 模型供应商接口和流式响应适配；
- Assistant/Tool Agent Loop；
- 对话上下文；
- Tool Calling 协议和参数校验；
- Runtime 事件；
- 取消、超时和有限重试；
- 最小 Session 状态；
- Runtime 级 Hook。

### 4.2 GeoDomainHarness 负责

- 地球物理任务类型和 TaskSpec；
- 数据资产识别；
- 专业执行计划；
- 允许使用的工作流和 Tool；
- Tool Provider 的信任、名称空间和允许列表；
- MCP Server 的连接与权限策略；
- 领域权限和风险策略；
- 输入、过程和结果验证；
- 领域级恢复决策；
- 假设、限制与证据管理；
- 分析产物和最终报告；
- 面向产品的任务状态。

### 4.3 `AgentRuntime`

GeoPhysAgent 通过自己的 Runtime Port 隔离模型供应商和运行机制：

```typescript
interface AgentRuntime {
  start(input: RuntimeTaskInput): Promise<RuntimeRunHandle>;
  resume(runId: string): Promise<RuntimeRunHandle>;
  cancel(runId: string): Promise<void>;
  subscribe(runId: string, listener: RuntimeEventListener): Unsubscribe;
}
```

实现负责：

- 将 Geo Task 转换为受约束的模型上下文和 Tool 配置；
- 让每个正式用户任务创建 AgentRun，并产出候选 ExecutionPlan；
- 执行模型—工具循环；
- 产生统一 `RuntimeEvent`；
- 管理取消、超时和运行级重试；
- 映射模型错误、工具错误和停止原因；
- 记录 Runtime、模型和协议版本。

### 4.4 Model Provider 与凭据

LLM 是 Agent Runtime 的推理能力，不通过 MCP 调用。Runtime 依赖独立 `ModelProvider` Port。
配置层在一个 TOML 中保存多个命名 Profile，每个 Profile 原子绑定 Provider、模型、协议、
Base URL 和 API Key 环境变量引用。当前实现支持 Anthropic Messages-compatible 和 OpenAI
Chat Completions-compatible，OpenCode Go、MiniMax 或其他真正兼容的第三方 API 通过同一 Port
接入。

```typescript
interface ModelProfile {
  profileName: string;
  provider: string;
  model: string;
  protocol: "anthropic-compatible" | "openai-compatible";
  baseUrl?: string;
  apiKeyEnv: string;
  timeoutMs: number;
}
```

`ModelConfiguration` 还保存 `defaultProfile` 与 Profile 映射。配置文件只保存 `apiKeyEnv`
等凭据引用，不能保存明文 Key。v0.1 从进程环境变量读取密钥，后续可增加操作系统凭据存储。
启动时必须检查 Profile、Provider、Model、协议、Base URL 和凭据是否完整；模型不可用时明确
失败，不得静默换到另一个供应商。旧 `[model]` 单 Profile 格式仅作为迁移兼容入口。

正式用户入口不得在模型不可用时静默改走确定性工作流。直接确定性执行只允许通过明确的开发、测试、批处理或诊断入口触发。LLM 是否必经与模型部署位置无关：当前云端 Profile 需要网络，未来本地 Model Provider 也必须经过相同 AgentRuntime Port。

模型选择优先级为：本次命令的 `--profile`、项目配置的 `models.default`；未来可再增加用户级
默认配置。模型 ID 不能脱离协议单独覆盖，因为同一聚合供应商的不同模型可能使用不同端点。
运行记录按 AgentRun 保存实际 Provider、Model、协议和可公开的 Endpoint 标识，但不保存
API Key、认证 Header 或完整敏感响应。任务续跑可显式改用另一 Profile，历史 AgentRun 保持
不变，以便审计混合模型运行。

供应商特有但不改变消息语义的传输要求由 Model Provider Adapter 处理。例如 OpenCode Go 的
`x-opencode-session` 使用 Harness Task ID，保证一个 Task 的新建、模型多轮和人工续跑共享稳定
会话路由，同时拒绝 CR/LF 等 Header 注入字符。该 Header 不进入 TOML，也不成为认证凭据。

Model Provider 与 Tool Provider 相互独立：前者产生模型消息和 Tool Call 请求，后者执行被 Harness 允许的 Geo Tool。MCP 只属于后者的一种实现。

Runtime 不直接管理地球物理任务状态。未来替换模型客户端、Tool Provider 或重构 Agent Loop 时，领域对象、GeoTool、Validator 和分析包格式不应变化。

Runtime 不直接连接具体工具或外部软件。模型请求的 Tool Call 先交给 GeoDomainHarness 和 Geo Tool Registry，再由 Registry 路由到内置 Tool Provider 或 MCP Tool Provider。

## 5. Tool 执行平面与 MCP 集成

### 5.1 Python-first 控制平面

v0.1 保持单一 Python 控制平面，负责：

- CLI 和应用入口；
- GeoDomainHarness；
- 自研 Agent Runtime；
- Tool Registry；
- Built-in Tool Provider；
- MCP Client Manager 和 Server Registry；
- 策略与状态机；
- Schema 校验；
- Run Store 和 Artifact Store；
- 事件、日志和报告组装；
- MCP Server 连接和本地进程生命周期管理。

不为对齐参考项目或预想中的 UI 提前增加 TypeScript Runtime。未来 Web/桌面界面通过 Application Service/API 调用同一 Python Harness；只有性能、分发或生态需求得到实际验证后，才通过新 ADR 引入跨语言组件。

### 5.2 Tool Provider

Tool Provider 是 Geo Tool Registry 与具体执行方式之间的适配边界。v0.1 至少包含：

- `BuiltInToolProvider`：承载与 Host 同一信任域内的第一方轻量工具、控制能力和 Validator；
- `ExplorePythonProvider`：在受限工作区执行 LLM 生成的一次性表格计算，结果固定标记为探索级；
- `McpToolProvider`：把通过 MCP 发现的外部 Tool 映射为 GeoTool；
- 测试 Provider：为 Harness、恢复和故障注入测试提供确定性替身。

Provider 差异不能泄漏到 Skill 和任务状态机。无论 Tool 来自何处，都必须返回统一 GeoToolResult，并接受相同的 Schema、权限、超时、产物和 Validator 约束。

### 5.3 Bundled LAS MCP Server

Bundled LAS MCP Server 使用 Python 实现，负责：

- LAS 解析；
- 曲线数据访问；
- 数值统计；
- 深度轴和采样检查；
- 异常检测；
- 测井图绘制；
- Python 侧结果验证；
- 专业库和算法封装。

MCP Server 不负责：

- 直接调用 LLM；
- 决定整个任务流程；
- 修改 Host 中的权威任务状态；
- 自行访问任务授权范围之外的文件；
- 向用户输出未经上层验证的最终结论。

### 5.4 MCP 通信

v0.1 把 MCP 作为一种外部 Tool Provider 协议，并正式支持本地 `stdio`：Host 启动 Server 子进程，通过标准输入输出交换 MCP JSON-RPC 消息，Server 的诊断日志只写入 `stderr`。

协议要求：

- 按所支持协议版本完成连接、版本与能力协商后才能发现或调用 Tool；
- 每个 Server 使用独立 Client 连接；
- 实现 `tools/list` 分页和 `tools/call`；
- 记录协商协议版本、Server 身份、能力和 Tool Schema；
- `stdout` 只输出合法 MCP 消息，诊断信息输出到 `stderr`；
- 设置连接、调用和关闭超时；
- 支持取消和强制终止本地 Server；
- 非法协议消息或 structuredContent 视为工具失败；
- 大型数组写入任务数据文件，只通过 DataAsset/Artifact 引用传递；
- Tool 调用参数和结果写入 Run Store；
- Server 崩溃不应导致 Host 崩溃。

v0.1 只正式支持本地 stdio。Streamable HTTP 是 v0.2 远程外部软件 Sidecar 的目标传输，届时必须同时实现认证、TLS、Origin 校验、loopback 约束和断线测试。旧式 HTTP+SSE 不作为新集成默认方案。

## 6. 核心组件

### 6.1 CLI

职责：

- 以 `run <asset> --request "..."` 作为最终用户的 LLM-first 主入口；
- 解析命令和配置；
- 创建、恢复、查询和验证任务；
- 展示阶段、警告和产物位置；
- 管理必要的人机确认；
- 提供人类可读和 JSON 两种输出。

`inspect/analyze/preprocess/segy` 等直接命令是开发、测试、批处理和诊断接口；它们不代表最终用户主流程。所有入口都通过 Application Service，CLI 不直接调用专业 Python 函数。

### 6.2 Application Service

用例入口，包括：

- `RunTask`；
- `InspectAsset`；
- `ResumeTask`；
- `CancelTask`；
- `GetTaskStatus`；
- `ValidateRun`。

它负责组装依赖和事务边界，不包含具体测井规则。

### 6.3 GeoDomainHarness

GeoDomainHarness 是领域控制核心。它消费 TaskSpec、自研 Runtime 产生的 RuntimeEvent、ToolResult 和 ValidationReport，并执行显式状态转移。

主要子组件：

- `TaskClassifier`：把请求归类到支持的任务类型；
- `PlanBuilder`：构造受约束计划；
- `PlanPolicy`：检查步骤、工具、权限和前置条件；
- `StepExecutor`：调度 Tool；
- `ValidationCoordinator`：运行相应 Validator；
- `RecoveryPolicy`：根据错误类别选择允许的恢复动作；
- `ReportAssembler`：从已验证事实和模板生成报告；
- `TaskStateMachine`：维护领域状态。

### 6.4 Tool Registry

每个 Tool 注册：

- 唯一名称和版本；
- 描述和适用条件；
- 输入与输出 Schema；
- 所需权限；
- 超时；
- 是否只读；
- 是否幂等；
- 崩溃后是否允许重放；
- 产生的 Artifact 类型；
- 前置和后置 Validator。

专业 Tool 可以来自受信任的 Built-in Tool Provider 或 MCP Tool Provider；Explore Provider 只提供受控的一次性计算，不自动成为正式专业 Tool。Tool Registry 对所有 Provider 执行统一的名称、版本、Schema、权限和 Validator 检查；MCP Tool 还必须满足：Server 已完成能力协商、Tool 存在于 `tools/list`、本地 Integration Manifest 提供领域映射、Tool 位于 allowlist 中且 Schema 校验通过。无本地 DataAsset 的 external-only 任务只能看见 `input_capabilities=[]` 的资产无关 Tool。

Tool 是原子能力，不包含跨步骤工作流决策。

### 6.5 Tool Provider Registry

负责注册 Provider、解析 Tool 归属、检测名称冲突、校验参数、执行统一策略并把 Provider 错误映射为 GeoToolError。GeoDomainHarness 只依赖这一抽象，不直接依赖 MCP SDK 或具体工具实现。当前 Python 原型已经实现 Built-in、Explore 和 MCP stdio Provider；细节见 [MCP_INTEGRATION.md](MCP_INTEGRATION.md)。

### 6.6 MCP Client Manager

负责：

- 加载受信任 Server 配置；
- 为每个 Server 创建隔离连接；
- 连接、版本与能力协商；
- Tool 发现、分页、刷新和名称空间；
- 本地 stdio Server 生命周期；
- 远程 Streamable HTTP Transport Port 和测试桩；v0.1 不承诺生产连接；
- 认证、超时、取消和关闭；
- MCP Tool 与 GeoTool 的双向映射；
- MCP 日志、指标和诊断；
- Server 断线和版本变化处理。

MCP Server 的 instructions、Resources 和 Prompts 不自动加入模型上下文。v0.1 只自动暴露本地清单明确允许的 Tools。

### 6.6.1 Integration Package 与只读 API Connector

Integration Package 是 Host 侧的本地安装和策略单元：它包含显式文件清单、MCP
Integration Manifest 和 Server 代码。安装、启停和完整性校验不改变 MCP 的调用协议；
激活后的 Tool 仍必须通过 Provider Registry 和 GeoDomainHarness。

受限只读 API Connector 位于经过审查的 MCP Server 内，只允许调用 Manifest 精确列出的
HTTP Origin，并将 JSON 结果映射为结构化 MCP Tool Result。它不是 GeoPhysAgent 核心对
厂商 API 的硬编码，也不是面向恶意 Server 的 OS 网络沙箱。详见
[INTEGRATION_PACKAGES.md](INTEGRATION_PACKAGES.md)。

### 6.7 Skill Registry

Skill 描述“什么时候以及按照什么专业流程使用一组 Tool”。LAS QC Skill 包括：

- 触发条件；
- 输入要求；
- 支持的目标；
- 标准步骤；
- 曲线别名和单位规则来源；
- 参数选择原则；
- 必须运行的 Validator；
- 常见错误；
- 报告最低要求；
- 需要人工确认的场景。

规则表放在结构化 YAML/JSON 中，不把所有知识隐藏在 SKILL.md 的自然语言中。

### 6.8 Validator Registry

Validator 分四层：

1. `DataValidator`：格式、深度、采样、空值和范围；
2. `SemanticValidator`：曲线别名、单位和语义歧义；
3. `ProcessValidator`：步骤完整性、方法适用性和参数规则；
4. `ResultValidator`：统计、图件、报告引用和产物一致性。

Validator 必须是独立组件，不调用主结果生成逻辑。一个验证报告包含：

```typescript
interface ValidationReport {
  schemaVersion: string;
  status: "pass" | "warning" | "fail";
  checks: ValidationCheck[];
  assumptions: Assumption[];
  limitations: Limitation[];
  recommendedActions: RecoveryAction[];
  continuation: {
    allowed: boolean;
    requiresUserApproval: boolean;
  };
}
```

### 6.9 Run Store

保存任务的权威状态：

- TaskSpec；
- 当前状态；
- 计划及版本；
- 步骤和尝试记录；
- Agent Run 与对话上下文关联；
- Tool Provider、工具版本和 Tool Schema 快照；MCP 调用额外保存 Server、连接和协议版本；
- Tool 调用；
- Validator 结果；
- 假设和限制；
- 最终状态。

v0.1 可使用文件型 Store；写入必须采用临时文件加原子替换，避免进程中断造成半写状态。后续可替换为 SQLite。

### 6.10 Artifact Store

管理输入快照、中间数据、图件和报告。它负责：

- 生成任务作用域内的安全路径；
- 记录内容哈希和 MIME 类型；
- 防止路径穿越；
- 维护 ArtifactManifest；
- 区分原始、派生和最终产物；
- 提供报告引用所需的稳定相对路径。

## 7. 领域状态机

```text
CREATED
   │
   ▼
INSPECTING_INPUT ───────────────→ FAILED
   │
   ▼
PLANNING ───────────────────────→ FAILED
   │
   ▼
READY ─────→ WAITING_FOR_USER
   │                 │
   ▼                 └────→ READY / CANCELLED
EXECUTING
   │
   ▼
VALIDATING
   ├──── pass ───────→ COMPLETED
   ├──── warning ────→ COMPLETED_WITH_WARNINGS
   ├──── recover ────→ RECOVERING ───→ EXECUTING
   ├──── approval ───→ WAITING_FOR_USER
   └──── fail ───────→ FAILED
```

通用约束：

- 状态只能通过状态机接口改变；
- 每次转移产生持久化事件；
- 每个状态具有允许的命令集合；
- 恢复时从最后一个已提交状态继续；
- 达到终态后禁止追加执行步骤；
- 报告生成失败不得把任务标记为完整成功；
- `completed_with_warnings` 必须至少关联一个未消除的 warning。

## 8. 计划模型

LLM 可以生成候选计划，但候选必须解析为 `ExecutionPlan` 并通过 PlanPolicy：

```typescript
interface ExecutionPlan {
  schemaVersion: string;
  taskId: string;
  workflow: string;
  workflowVersion: string;
  objectives: string[];
  steps: PlanStep[];
  assumptions: Assumption[];
  expectedArtifacts: ExpectedArtifact[];
}
```

`PlanStep` 只能引用 Tool Registry 中存在的工具，必须声明输入、输出、依赖、验证规则和失败策略。计划不能通过自然语言要求执行未注册命令。

v0.1 的 LAS QC 工作流以确定性模板为骨架，LLM 只允许：

- 根据任务选择可选步骤；
- 选择曲线子集；
- 补充需要披露的假设；
- 在策略提供的候选动作中选择恢复方式。

## 9. 工具调用生命周期

```text
PlanStep Ready
→ 解析 DataAsset
→ 权限与路径检查
→ 输入 Schema 校验
→ 记录 ToolInvocation
→ 解析 MCP Server 与 Tool
→ MCP tools/call
→ MCP 响应与 structuredContent Schema 校验
→ 映射 GeoToolResult
→ 注册 Artifact
→ 执行后置 Validator
→ 提交 StepResult
```

任何一步失败都必须生成结构化错误，不允许只依靠日志文本判断。

### 9.1 幂等与重放

每个 Tool 声明：

- `read_only`：是否不产生持久化效果；
- `idempotent`：相同输入重复调用是否等价；
- `replay_policy`：`safe`、`manual` 或 `never`；
- `idempotency_key`：对可重放写操作标识同一逻辑调用。

读取和统计通常可以安全重放；报告和图件写入必须写入调用专属临时路径，再由 Artifact Store 原子登记。

## 10. 错误与恢复模型

### 10.1 错误分类

| 错误 | 典型原因 | 默认动作 |
|---|---|---|
| `InvalidInput` | 文件损坏、缺少深度列 | 停止并报告 |
| `UnsupportedData` | 不支持的 LAS 结构 | 停止或请求替代输入 |
| `AmbiguousSemantics` | 曲线或单位歧义 | 请求用户确认 |
| `ToolTimeout` | MCP Tool 调用超时 | 按策略有限重试 |
| `McpConnectionFailure` | Server 启动、初始化或连接失败 | 重连或停止 |
| `McpProtocolFailure` | 协议消息、能力或版本异常 | 停止该 Server |
| `ToolCrash` | MCP Server 异常退出 | 重启 Server，安全时重放 |
| `InvalidToolResult` | 返回不符合 Schema | 记录实现错误并停止 |
| `ValidationFailure` | 结果不符合约束 | 候选修复或停止 |
| `PolicyViolation` | 越权路径或禁用工具 | 立即停止相关步骤 |
| `RuntimeFailure` | 模型请求或 Agent Loop 失败 | 采用 Runtime 策略恢复 |
| `UserActionRequired` | 有实质影响的选择 | 进入等待状态 |

### 10.2 恢复策略

恢复动作必须来自白名单：

- 使用同一参数重试；
- 使用规则指定的替代参数；
- 跳过可选步骤；
- 重启或重连 MCP Server；
- 请求用户选择曲线或单位；
- 终止任务并保留诊断包。

v0.1 禁止模型生成任意修复脚本后直接执行。

## 11. 分析包

每次运行产生：

```text
runs/<task-id>/
├── task.json
├── state.json
├── plan.json
├── input-manifest.json
├── artifact-manifest.json
├── validation.json
├── assumptions.json
├── events.jsonl
├── tool-calls.jsonl
├── inputs/
├── data/
├── figures/
├── logs/
└── report.md
```

约束：

- `inputs/` 中保存受控输入引用或只读快照；
- `data/` 只保存派生数据；
- 所有产物先写临时文件，再原子登记；
- `artifact-manifest.json` 是产物清单权威来源；
- `report.md` 只能引用清单中已登记的产物；
- 路径在分析包内使用相对路径，避免移动目录后失效；
- 日志与事件的敏感字段在写入前脱敏。

## 12. 安全与信任边界

### 12.1 不可信输入

以下内容均视为数据而不是指令：

- LAS 头字段；
- 曲线描述；
- 文件名；
- 外部生成的报告；
- Tool 返回的自由文本。

这些内容进入 Prompt 时必须放在明确的数据边界中，并限制长度。系统指令和 Skill 不得从输入文件动态覆盖。

### 12.2 文件权限

- 只允许读取显式输入和任务工作目录；
- 禁止 `..` 路径穿越和符号链接逃逸；
- 默认不覆盖已有输出目录；
- 原始输入只读；
- 删除和覆盖操作不属于 v0.1 Agent Tool 集。

### 12.3 模型数据策略

配置至少支持：

- `metadata_only`：只发送元信息和汇总；
- `summaries`：发送统计、检查和有限样本摘要；
- `explicit`：仅在用户明确允许时发送额外内容。

完整曲线数组默认不发送给远程模型。每次运行记录实际采用的策略，但不记录密钥。

### 12.4 MCP 信任策略

- MCP Server、Tool 描述、instructions、Resources 和 Prompts 均视为不可信输入；
- Tool 必须经过 Server allowlist 与 Integration Manifest 双重允许；
- 本地 Server 只获得最小环境变量和任务作用域目录；
- 只读 API Connector 只获得其清单声明的凭据环境变量和精确 Origin；
- 远程 Server 使用独立认证配置，不共享 Host 通用凭据（规划项）；
- 修改和破坏性 Tool 必须由 Host 侧策略要求人工确认；
- MCP 协议成功不能替代领域 Validator；
- 所有连接、能力变化和 Tool 调用均进入审计记录。

## 13. 可观测性

### 13.1 领域事件

至少记录：

- `task_created`；
- `asset_registered`；
- `plan_created`；
- `plan_approved`；
- `step_started`；
- `tool_started`；
- `tool_completed`；
- `validation_completed`；
- `recovery_started`；
- `user_action_required`；
- `artifact_registered`；
- `task_completed`；
- `task_failed`。

领域事件与 Runtime 原始事件分层保存，避免上层消费者依赖模型客户端和 Agent Loop 的内部事件格式。

### 13.2 指标

- 各阶段耗时；
- Tool 成功、失败和重试次数；
- Validator 的 pass/warning/fail 数量；
- 任务终态比例；
- MCP Server 连接、重连、协议错误和能力变化次数；
- 模型调用次数、Token 和成本；
- 产物数量及大小。

遥测默认不包含曲线内容、Prompt 正文、密钥和绝对路径。

## 14. 建议仓库结构

```text
GeoPhysAgent/
├── src/
│   └── geophysagent/
│       ├── application/
│       ├── agent_runtime/
│       ├── harness/
│       ├── model_providers/
│       ├── tool_providers/
│       ├── validators/
│       └── mcp/
├── integrations/
│   ├── bundled-las/
│   └── examples/
│       └── personal-project-mcp/
├── skills/
│   └── well-log-qc/
├── rules/
│   └── well-log/
├── examples/
│   └── workflows/
├── data/
│   └── local/
├── evals/
├── tests/
│   ├── fixtures/
│   ├── contract/
│   ├── integration/
│   └── e2e/
├── docs/
│   └── adr/
├── runs/
├── pyproject.toml
├── README.md
├── ROADMAP.md
└── LICENSE
```

当前原型仍采用 `src/geophysagent/*.py` 的扁平模块布局；上述子包只在职责边界和测试稳定后渐进拆分。`data/local/`、`runs/` 和本地模型配置应加入版本控制忽略规则，只保留经过脱敏和授权的夹具与示例分析包。

## 15. 部署与打包

v0.1 采用本地 CLI 模式：

- Python 运行 Agent Runtime、GeoDomainHarness 和控制平面；
- 本地 stdio MCP Server 作为独立子进程运行，但可以与 Host 使用同一受控 Python 环境；
- 安装程序检查 Python、模型凭据、MCP 配置和依赖；
- 提供 `doctor` 命令输出环境诊断；
- 所有依赖版本锁定；
- 发布前在干净环境执行安装和端到端冒烟测试。

在验证分发需求之前，不承诺单文件可执行程序。第一版优先保证可诊断、可升级和依赖透明。

## 16. 测试架构

### 16.1 单元测试

覆盖状态机、规则、统计、路径安全、别名、单位和报告引用。

### 16.2 契约测试

Python 内部边界、持久化对象和 MCP 进程使用同一组 JSON Schema 样例，验证请求、响应、错误和版本不兼容行为。未来新增其他语言实现时必须复用这些契约夹具。

### 16.3 集成测试

使用模拟 Runtime 测 Geo Harness；MCP Client Manager 使用内存或测试传输测试协议行为；Bundled Server 使用独立 MCP Client 测试。核心测试不依赖真实模型。

默认测试套件必须在无网络、无密钥环境运行，以确定性验证 Tool 与 Harness。另设显式在线套件，允许使用环境变量中的 MiniMax API Key，并在候选发布前验证真实模型协议和端到端 Agent 行为。在线测试会产生费用，不进入无密钥默认 CI。

### 16.4 黄金数据测试

对固定 LAS 样本验证问题检出、数值结果和图件元数据。浮点数使用明确容差，图件以结构和视觉快照结合验证。

### 16.5 Agent 评测

使用固定任务集合评估：

- Skill 选择；
- 计划合法性；
- Tool 轨迹；
- 假设披露；
- 超范围请求处理；
- 最终报告事实一致性。

Agent 评测同时包含 Fake Model 的可重复轨迹测试和少量真实 MiniMax 冒烟测试。真实模型断言结构化计划、Tool 轨迹、Evidence、停止原因和安全边界，不断言固定自然语言措辞。

### 16.6 故障注入

覆盖 MCP Server 崩溃、连接或协商失败、超时、非法协议消息、产物半写、模型失败、验证失败和恢复过程再次失败。

## 17. 架构决策记录

正式开发时应建立 ADR，至少记录：

- ADR-001：独立实现最小 Geo Agent Runtime；
- ADR-002：通过 Runtime Port 隔离模型客户端和领域层；
- ADR-003：Python-first v0.1；只有明确需求和独立 ADR 才引入跨语言控制平面；
- ADR-004：Tool Provider 是统一执行边界，MCP 是外部集成 Provider 之一；
- ADR-005：文件型 Run Store 及未来 SQLite 迁移路径；
- ADR-006：LAS QC 作为首个垂直工作流；
- ADR-007：报告事实只能来自已验证结构化结果；
- ADR-008：默认本地数据处理和最小模型披露策略；
- ADR-009：`pi/` 只读参考政策与选择性代码移植审查流程；
- ADR-010：v0.1 本地 MCP 使用 stdio，远程集成使用 Streamable HTTP；
- ADR-011：大型地球物理数据使用 DataAsset/Artifact 引用，不进入 MCP 消息正文；
- ADR-012：长任务 v0.1 使用显式 Job Tools，MCP Tasks 等扩展须经能力协商和兼容性决策。
- [ADR-013：LLM-first、Local-execution、Harness-governed](adr/ADR-013-llm-first-local-execution.md)。

## 18. 后续演进边界

### v0.2 候选

- Streamable HTTP 与远程 MCP Server；
- 外部软件 MCP Sidecar 示例；
- 远程认证和 Server 权限配置；
- 多 LAS 批量质控；
- SQLite Run Store；
- 更丰富的曲线模板；
- HTML 报告；
- 本地任务浏览界面。

### v0.3 候选

- 单条二维地震剖面；
- 更完整的 SEG-Y 扩展字段、图件和处理 Tool；
- 更丰富的地震数据 Validator。

Increment 5 已用只读 SEG-Y 检查完成第一次第二领域验证；只有更多真实工作流继续证明接口稳定后，才承诺 Tool/Skill/Validator 的长期公共扩展 API。

## 19. Increment 4–7B 实现映射

截至 2026-09-07，Python 原型已把下列架构从设计落为可执行代码：

| 设计组件 | 当前实现 |
|---|---|
| DataAsset 类型路由 | `asset_adapters.py` 的 `AssetAdapterRegistry` 和 capability 门禁 |
| 核心契约 | `contracts.py` 的 TaskSpec、ExecutionPlan、ExpectedEvidence、Attempt、RunRecord、ArtifactManifest、ContextSnapshot、CompactionRecord 和 AgentCheckpoint |
| GeoDomainHarness | `harness.py` 的 PlanPolicy、TaskStateMachine 和顺序/依赖执行器 |
| Tool 边界 | `providers.py` 的 Provider Registry、递归输入/输出 Schema、身份和重放策略检查 |
| Run Store | `run_store.py` 的原子 JSON 快照、事件追加，以及与事件/Artifact 修订关联的 AgentCheckpoint |
| Artifact Store | 任务目录约束、相对路径、大小/SHA-256 登记和完整性验证 |
| 证据门禁 | JSON Pointer 绑定、值哈希、必需证据缺失进入 `incomplete` |
| 参考工作流 | LAS `analyze`、表格 `preprocess`、Agent 的确定性 LAS QC 子任务 |
| 第二领域工作流 | `segy.py` 流式 Parser、`seismic.inspect_segy` Tool、Validator 和 `segy` CLI，共用相同 Harness 契约 |
| 顶层 Agent 生命周期 | `TaskRunSession`、`AgentRunRecord` 和动态 Tool Attempt；LAS Agent/Explore 不再维护独立任务状态 |
| Validation Coordinator | Data/Semantic/Process/Result 四层 Registry，以及 Evidence、Artifact、最终事实引用门禁 |
| Checkpoint/恢复 | 版本化快照、原子提交指针、输入复核、safe Tool 重放与完整 Step 复用 |
| 生命周期 CLI | `status`、`resume`、`cancel`、`validate` 和多来源时间线重建 |
| 并发与故障 | 单写者锁、失效 PID 回收、终态不可变及写入/进程/验证/二次恢复故障测试 |
| 工程基线 | Windows Python 3.11–3.14、依赖锁、Ruff/测试统一命令、CI、可复现构建和发行文档 |
| 通用 LLM-first Runtime | `general_agent.py` 的候选计划审批、多 Tool 循环、capability/allowlist、Evidence index 和最终 Artifact |
| 模型协议与记录 | Anthropic-compatible 同步/SSE、完整 thinking/signature 回传、流结束后 Tool 参数定稿、输出上限分类、有限重试，以及 AgentRun Token/延迟/响应 ID 字段 |
| Context Assembly | `context.py` 从权威 Run 投影有来源、信任级别和预算的工作上下文，记录 ContextSnapshot 并按 Tool Call/Result 原子单元压缩 |
| 大结果边界 | 超预算 Tool Result 登记为 Artifact，模型只接收受限摘要、哈希和引用 |
| 最终事实门禁 | `submit_final_answer`、`agent-claim-map` 与 `core.result.claim_evidence` 将已验证标量事实绑定到 Evidence JSON Pointer |
| Agent 人工续跑 | RunRecord 内版本化 AgentCheckpoint、模型不可用时 `waiting_for_user` 和显式 `run --resume`；旧 `agent-checkpoint.json` 仅只读迁移 |
| 在线验收 | 默认跳过、必须同时设置开关和环境变量密钥的三场景 MiniMax 测试入口 |
| Provider 扩展边界 | `providers.py` 的自描述 Tool、生命周期、资源作用域、原子激活、冻结绑定和 Provider 事件 |
| MCP stdio Provider | `mcp.py` 的版本化 Manifest、严格 stdio Client、Tool/Result 映射、权限与结果边界；通过普通 Registry/Harness 执行 |

当前 SEG-Y 只覆盖 Header/Trace 结构、聚合振幅统计和基础 QC，不覆盖图件、处理和解释；MCP 只覆盖显式授权的本地只读 stdio Tools，不支持远程、写入或外部控制。同步内置 Tool 运行中强制终止、旧 `agent/explore` 兼容入口的同等会话续跑和完整 Event Sourcing 仍按后续增量实现。Increment 8 已完成 LAS 参考产品的工程闭环和跨领域契约评测，授权真实 LAS 黄金、真实模型重复评测和专业走查仍是发布门禁。详见 [REFERENCE_PRODUCT.md](REFERENCE_PRODUCT.md)、[MCP_STDIO_PROVIDER.md](MCP_STDIO_PROVIDER.md)、[CONTEXT_ASSEMBLY.md](CONTEXT_ASSEMBLY.md)、[HARNESS_CORE.md](HARNESS_CORE.md)、[HARNESS_LIFECYCLE.md](HARNESS_LIFECYCLE.md)、[SEGY_SUPPORT.md](SEGY_SUPPORT.md) 和 [TODO.md](../TODO.md)。

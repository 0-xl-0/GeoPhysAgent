# GeoPhysAgent MCP 集成设计

> 状态：Increment 7B stdio Provider 与 Increment 7C 本地包/只读 API 已实现；远程、写入及 Resources/Prompts 为后续设计
> 目标版本：v0.1
> 最后更新：2026-09-08

## 1. 设计结论

MCP Server 是 GeoPhysAgent 接入跨进程能力、个人项目和外部软件的一种标准边界，但不是 GeoPhysAgent 的产品主体，也不是所有 Geo Tool 的唯一实现方式。

GeoPhysAgent 的主体仍是 Agent Runtime、GeoDomainHarness、专业工作流、Tool/Skill 契约、Validator、Task/Run 状态、Artifact 和可审计报告。MCP 位于 Tool Provider 层，与 Built-in Tool Provider 并列。

正式用户任务采用 LLM-first：Agent Runtime 先理解目标并提出受约束计划，Harness 再把获准的 Tool Call 路由到 Built-in、Explore 或 MCP Provider。用户不需要判断某项能力是否来自 MCP，LLM 也不能直接构造 Server 命令、连接参数或绕过 Integration Manifest。直接 MCP 管理命令只用于开发、配置和诊断。

MCP 集成子系统是 v0.1 必须交付和验收的能力；但使用 MCP 不是每个工作流、每个 Tool 或每次运行的前置条件。是否走 MCP 由 Tool 的部署、隔离和集成需求决定。

当使用 MCP 集成时，GeoPhysAgent 在 MCP 架构中的角色是 **Host**：

- 自研 Agent Runtime 和 GeoDomainHarness 位于 Host 内；
- MCP Client Manager 为每一个 MCP Server 创建独立 Client 连接；
- 本地 LAS 参考工作流选择一组专业 Tool 通过随项目发布的 MCP Server 提供，用于验证 MCP 路径；
- 外部地震、测井、GIS、反演软件和个人项目可以通过各自 MCP Server 接入；
- GeoPhysAgent 不直接依赖外部软件的 API、SDK 或源码；
- MCP Server 内部可以调用软件 API、SDK、CLI、插件接口或项目函数；
- 所有 MCP Tool 必须映射为内部 GeoTool 并接受权限、状态和 Validator 控制。

MCP 是集成协议和可替换的 Tool Provider，不是 GeoPhysAgent 的领域模型。内部 Task、DataAsset、GeoToolResult、ValidationReport 和 ArtifactManifest 仍由 GeoPhysAgent 定义。

## 2. 总体结构

```text
用户
 │
 ▼
GeoPhysAgent Host
├── Agent Runtime
├── GeoDomainHarness
├── Policy / Validator
├── Run Store / Artifact Store
└── Geo Tool Registry
     ├── Built-in Tool Provider
     └── MCP Tool Provider
          └── MCP Client Manager
               ├── Client A ──stdio────────────→ Bundled LAS MCP Server
               ├── Client B ──stdio────────────→ Personal Project MCP Server
               ├── Client C ──Streamable HTTP──→ Remote Seismic MCP Server（规划）
               └── Client D ──stdio────────────→ Commercial Software Sidecar
                                                        │
                                                        └── Vendor API / SDK / Plugin
```

一个 MCP Client 连接一个 MCP Server。Server 之间不共享会话、凭据、根目录和权限状态。

## 3. v0.1 MCP 范围

### 3.1 Increment 7B 已实现

- 本地 Integration Manifest 与每 Server 独立 Client；
- Tool Provider Registry 的原子激活和生命周期；
- MCP 连接、版本与能力协商及版本记录；
- `tools/list` 与分页；
- `tools/call`；
- Tool 列表变化通知只作为诊断，不在运行中热更新；
- 本地 `stdio` 传输；
- MCP Tool 到 GeoToolDefinition 的映射；
- MCP CallToolResult 到 GeoToolResult 的映射；
- Tool 名称空间、白名单和冲突检测；
- Server 启动、健康检查、超时、取消和关闭；
- Server 日志捕获及脱敏；
- 一个随项目发布的最小只读示例 Server；
- 一个独立个人项目包装示例；
- MCP 契约、故障和安全测试。

完整 LAS MCP Server 不属于 7B 的最小协议证明；应在 Increment 8 的专业 LAS 契约稳定后
再决定迁移哪些 LAS Tool，避免为了演示 MCP 重复现有 Built-in 实现。

### 3.2 已冻结但尚未实现的扩展边界

- Resources：未来只能由 Host 按显式配置读取；7B 不实现 `resources/list`/`resources/read`；
- Prompts：未来只能由用户或受信任工作流显式选择；7B 不实现 `prompts/list`/`prompts/get`；
- Streamable HTTP：保留为未来传输适配方向；7B 没有实现配置、连接或测试桩。

以上能力既不对 Server 宣告，也不会在运行时静默降级为“部分支持”。

### 3.3 Increment 7C 已实现

- 无本地 DataAsset 的 external-only 任务，且只暴露资产无关 Tool；
- 本地目录 Integration Package 的 install/list/enable/disable/remove；
- 显式文件清单、原子登记、SHA-256 和激活前完整性复核；
- MCP Manifest 的 `network_read` 与精确 `network_policy.allowed_origins`；
- Host 保留网络策略环境变量，Package 的 env allowlist 不能覆盖；
- GET-only、无重定向、有限响应的 JSON API Client；
- 软件 A 安装包垂直示例和 loopback API 集成测试。

### 3.4 v0.1 不实现

- 将 GeoPhysAgent 本身作为通用 MCP Server 对外发布；
- 自动信任任意本地或远程 MCP Server；
- 自动启用 Server 暴露的全部 Tool；
- 通过 MCP 消息传输完整地震体或大型曲线数组；
- Sampling、Elicitation 等非必要 Client 能力；
- 旧式 HTTP+SSE 作为新集成的默认传输；
- Server 之间直接通信；
- MCP Tool 绕过 GeoDomainHarness 直接修改任务状态。

## 4. 内部抽象

### 4.1 MCP Server 配置

```typescript
interface McpServerConfig {
  schemaVersion: string;
  id: string;
  displayName: string;
  enabled: boolean;
  trust: "bundled" | "approved-local" | "approved-remote";
  transport: McpTransportConfig;
  toolAllowlist: string[];
  resourceAllowlist: string[];
  promptAllowlist: string[];
  permissions: IntegrationPermissions;
  timeouts: {
    connectSeconds: number;
    callSeconds: number;
    shutdownSeconds: number;
  };
}
```

本地配置：

```typescript
interface StdioTransportConfig {
  type: "stdio";
  command: string;
  args: string[];
  cwd?: string;
  envAllowlist: string[];
}
```

远程配置：

```typescript
interface StreamableHttpTransportConfig {
  type: "streamable-http";
  url: string;
  authProfile?: string;
}
```

配置中不得直接保存明文密钥。`command`、`args` 和 `cwd` 来自管理员配置，不允许由 LLM 构造。

### 4.2 MCP Client Manager

```typescript
interface McpClientManager {
  connect(serverId: string): Promise<McpServerSession>;
  disconnect(serverId: string): Promise<void>;
  refreshCapabilities(serverId: string): Promise<void>;
  listConnections(): ReadonlyArray<McpConnectionSnapshot>;
  shutdown(): Promise<void>;
}
```

负责：

- 每个 Server 独立连接；
- 按支持的协议版本完成连接、版本与能力协商；
- 记录协议版本、Server 标识和能力；
- Tool 分页发现；
- 连接状态；
- 超时和取消；
- 本地进程生命周期；
- 远程认证注入；
- 断线、关闭和资源清理；
- 连接级日志和指标。

### 4.3 MCP Tool Provider

```typescript
interface McpToolProvider extends GeoToolProvider {
  readonly serverId: string;
  refreshTools(): Promise<GeoToolDefinition[]>;
}
```

它把 MCP Tool 映射为内部工具：

```text
MCP Tool
name + title + description + inputSchema + outputSchema
             │
             ▼
GeoToolDefinition
provider + namespacedName + permissions + validators + executionPolicy
```

MCP Server 只能声明协议层 Schema，不能自行决定 GeoPhysAgent 的权限、风险级别和 Validator。缺失的领域元数据来自 Integration Manifest。

## 5. Tool 名称与注册

内部 Tool 使用稳定名称：

```text
<domain>.<capability>

well.inspect-las
well.compute-curve-statistics
seismic.compute-rms
seismic.export-volume
```

外部 MCP Tool 名称通过 Integration Manifest 映射：

```yaml
tools:
  - remote_name: inspect_las
    geo_name: well.inspect-las
    risk: read
    validators:
      before: [well.validate-las-asset]
      after: [well.validate-las-metadata]
```

注册规则：

- MCP Tool 不自动启用；
- 必须同时存在于 Server `tools/list` 和本地 allowlist；
- 内部名称冲突时拒绝加载，不能按发现顺序覆盖；
- Server 更新 Tool Schema 后重新执行契约检查；
- 运行中的 Task 固定 Tool、Server 和 Schema 版本快照；
- Server 断线后不得无提示切换到另一个实现。

## 6. 调用生命周期

```text
PlanStep Ready
→ 解析 GeoToolDefinition
→ 确定 MCP Server
→ 校验任务状态和风险策略
→ 校验 DataAsset 权限
→ 执行前置 Validator
→ 校验 Tool 参数
→ 记录 ToolInvocation
→ MCP tools/call
→ 校验 MCP 响应
→ 映射 GeoToolResult
→ 登记 Artifact
→ 执行后置 Validator
→ 提交 StepResult
```

MCP 协议成功不等于专业任务成功。即使 `tools/call` 没有协议错误，GeoToolResult 仍可能进入 `warning` 或 `failed`。

## 7. MCP 结果映射

MCP Tool 结果中：

- `content` 用作模型可读摘要；
- `structuredContent` 用作程序可读结果；
- `isError` 表示 Tool 报告的执行错误。

GeoPhysAgent 要求地球物理 Tool 提供符合版本化 Schema 的 `structuredContent`。只有自由文本而无结构化内容的 Tool，默认只能用于诊断或展示，不能产生可进入最终报告的科学事实。

映射结果必须附加：

- Server ID 和版本；
- 协商的 MCP 协议版本；
- Tool 名称和 Schema 摘要；
- 调用 ID；
- 开始和结束时间；
- 输入资产引用；
- 输出 Artifact 引用；
- 原始 `isError`；
- GeoPhysAgent Validator 结果。

## 8. 大型数据与 Artifact

MCP 是控制面，不作为大型地球物理数据传输通道。

Tool 参数传递 `DataAssetReference`：

```json
{
  "asset_id": "seismic-001",
  "media_type": "application/x-segy",
  "location": {
    "type": "task-local",
    "relative_path": "inputs/volume.segy"
  },
  "sha256": "..."
}
```

Server 只获得被授权资产的可访问位置。输出写入分配的临时目录，并返回 `ArtifactReference`：

```json
{
  "artifact_id": "rms-001",
  "artifact_type": "seismic-attribute-volume",
  "relative_path": "staging/rms-volume.segy",
  "sha256": "..."
}
```

Artifact Store 验证路径、存在性、大小、哈希和类型后，才把它移动或登记到正式产物目录。

远程 Server 使用受控上传、对象存储或数据服务引用。临时凭据必须限时、限资源、限操作，不能把 Host 的通用云凭据传给 Server。

## 9. Bundled LAS MCP Server（Increment 8 后候选）

Increment 7B 不复制现有 LAS Tool。待 Increment 8 稳定专业契约后，可评估随项目发布一个
Python MCP Server：

```text
python/geophys_las_mcp/
├── server.py
├── tools/
│   ├── inspect_las.py
│   ├── curve_statistics.py
│   ├── depth_checks.py
│   ├── anomaly_detection.py
│   └── plot_well_logs.py
├── schemas/
├── tests/
└── pyproject.toml
```

通过 `stdio` 启动。首版 Tools：

- `inspect_las`；
- `compute_curve_statistics`；
- `check_depth_axis`；
- `detect_curve_anomalies`；
- `plot_well_logs`。

Validator 可以位于 Host 内，也可以复用 Server 内确定性计算，但最终继续/停止决策只能由 GeoDomainHarness 作出。

Bundled Server 是参考集成实现，应证明：

- Server 可以独立测试；
- 不依赖 Agent Runtime；
- 只通过 MCP 与 Host 通信；
- 在其他符合规范的 MCP Host 中也能列出和调用 Tools；
- Host 在完全替换 Server 实现后仍能通过内部 GeoTool 契约工作。

## 10. 外部软件 MCP Sidecar

提供 API 或 SDK 的外部软件通过专属 MCP Sidecar 接入：

```text
GeoPhysAgent MCP Client
          │
          ▼
Vendor MCP Sidecar
          │
          ├── REST API
          ├── Python/.NET/Java SDK
          ├── 软件插件接口
          └── 受控 CLI
          │
          ▼
外部地震/测井软件
```

Sidecar 负责：

- 厂商认证和连接；
- 项目、工区和数据对象映射；
- 调用厂商 API；
- 异步 Job 查询与取消；
- 导入和导出 Artifact；
- 软件、插件、模型和算法版本记录；
- 将厂商错误转换为结构化 Tool 错误。

GeoPhysAgent 不在核心中引入厂商 SDK。Sidecar 可以与商业软件部署在同一台机器，并通过本地 stdio 或受认证的 Streamable HTTP 连接。

当前已实现的是本地 stdio Sidecar 调用经过审查的只读 HTTP API。Streamable HTTP MCP、
异步 Job、导入导出和厂商工程修改仍属于后续能力。软件 A/B 的适配代码分别放在独立
Integration Package 中，核心只提供通用安装、MCP、只读网络和 Harness 契约。

## 11. 个人项目接入

个人项目增加独立 MCP Server 包装层：

```text
my-geophysics-project/
├── src/
├── mcp_server/
│   ├── server.py
│   └── tools.py
├── schemas/
├── tests/
└── integration.yaml
```

只公开稳定、可验证的操作，不公开：

- 任意函数调用；
- 任意 Python 执行；
- 任意 Shell；
- 无路径约束的文件读取或写入；
- 未声明副作用的工程修改操作。

## 12. 长任务模型

耗时较长的地震处理不得无限阻塞一次 `tools/call`。建议 Server 暴露明确的 Job Tools：

```text
submit_attribute_job
get_job_status
cancel_job
collect_job_result
```

首次调用返回：

```json
{
  "job_id": "vendor-job-001",
  "state": "submitted",
  "submitted_at": "..."
}
```

GeoDomainHarness 持久化外部 Job ID。Host 重启后调用状态查询，不重复提交计算。提交类 Tool 默认 `replay_policy: never` 或要求幂等键。

v0.1 使用上述显式 Job Tools，便于对不同厂商 API 建立统一、可测试的恢复语义。如果采用 MCP Tasks 等协议扩展，必须先通过 ADR 确认目标规范版本、能力协商、兼容性和降级行为；Host 不能假设所有 Server 都支持该扩展。

## 13. 权限与风险

Tool 风险等级：

| 等级 | 示例 | 默认策略 |
|---|---|---|
| `read` | 列出工程、读取元信息 | 可由用户在任务级授权 |
| `compute` | 创建属性计算 Job | 按任务授权 |
| `export` | 导出数据文件 | 限定输出目录 |
| `modify` | 修改解释对象 | 要求显式授权 |
| `destructive` | 删除、覆盖工程数据 | 必须人工确认，不自动重放 |

权限由 Host 决定，不能相信 Server 自报。每次调用检查：

- Task 是否允许该 Tool；
- Server 是否受信任；
- 输入资产是否授权；
- 输出位置是否在分配目录；
- 网络和凭据是否允许；
- 是否需要人工确认；
- 是否超过并发、时间和重试上限。

用户提交任务只构成对该任务清单中 `read`/`compute` Tool 的作用域授权，不构成对任意 Server 或未来 Tool 的永久授权。`modify` 和 `destructive` 调用必须展示目标与影响，并在调用前取得明确确认。

## 14. MCP 安全策略

- Server 名称、说明、Tool 描述、Resources 和 Prompts 都视为不可信外部输入；
- 不把 Server instructions 自动提升为系统指令；
- 不自动把 Resources 加入模型上下文；
- 本地 stdio Server 使用最小环境变量和任务作用域工作目录；
- 捕获 stderr 时执行脱敏；
- 远程 Server 必须使用认证和 TLS；
- 本地 HTTP Server 只绑定 loopback；
- Streamable HTTP 实现必须校验 Origin；
- 远程地址执行 allowlist 和重定向策略；
- Server 获取的凭据按 Server、任务和用途隔离；
- MCP Tool 仍需 Host 侧 Schema 与领域 Validator；
- 所有调用写入审计日志。

## 15. Integration Manifest

每个 Server 使用本地清单补充 MCP 没有表达的领域信息：

下面是未来远程/写入场景的概念草案，不是 v0.1 可加载的 JSON Manifest：

```yaml
schema_version: "1.0"
id: vendor-seismic
version: "0.1.0"

server:
  transport: streamable-http
  url: https://localhost:8443/mcp

tools:
  - remote_name: compute_rms
    geo_name: seismic.compute-rms
    risk: compute
    supported_assets: [seismic-volume]
    produced_artifacts: [seismic-attribute-volume]
    replay_policy: never
    validators:
      before: [seismic.validate-volume]
      after: [seismic.validate-rms]

permissions:
  read_assets: [seismic-volume]
  write_artifacts: [seismic-attribute-volume]
  network: false
```

清单是 Host 策略配置，不从 Server 动态覆盖。

## 16. 测试

### 16.1 Server 契约测试

- 连接、版本与能力协商；
- Tool 分页发现；
- 输入 Schema；
- structuredContent Schema；
- `isError` 映射；
- 超时和取消；
- 非法 stdout；
- Server 崩溃；
- Tool 列表变化；
- Server 版本变化。

### 16.2 Host 集成测试

- 一个 Server 对应一个隔离 Client；
- Tool 白名单；
- 名称冲突；
- 断线行为；
- 风险审批；
- 路径越界；
- Artifact 登记；
- Validator 阻断；
- 长任务恢复；
- 不可信 instructions/resources/prompts 不进入系统指令。

### 16.3 兼容性测试

- Bundled LAS MCP Server 可被官方 Inspector 或独立测试 Client 调用；
- GeoPhysAgent Host 可连接一个最小第三方示例 Server；
- 协议版本协商结果被记录；
- SDK 升级前运行完整 MCP 契约测试。

## 17. 当前仓库结构与未来布局

当前 Python 原型已经使用：

```text
src/geophysagent/
├── mcp.py
├── integrations.py
└── readonly_api.py

examples/
├── mcp/
└── integrations/software-a/
```

下面的多包布局是未来规模化后的候选，而不是当前文件结构：

```text
packages/
├── tool-provider-registry/
├── mcp-client-manager/
├── integration-registry/
├── geo-tool-registry/
└── integration-contracts/

python/
└── geophys_las_mcp/

integrations/
├── bundled-las/
└── examples/
    └── personal-project-mcp/
```

MCP SDK 是允许使用的第三方协议实现，但必须固定版本、审查许可证并通过契约测试。该政策不改变 `pi/` 仅供只读参考且禁止依赖的决定。

## 18. 后续方向

完成当前 MCP Host、包管理和只读 API 垂直路径后，再考虑：

- 正式支持 Streamable HTTP；
- 远程 OAuth 和凭据代理；
- Server 安装与连接管理 UI；
- MCP Resource 浏览；
- 经审查的 MCP Prompt；
- GeoPhysAgent 自身作为 MCP Server，对外暴露任务创建、状态和产物查询能力。

## 19. 规范基线

Increment 7B 明确固定 MCP `2025-11-25` 会话式 stdio 子集；官方当前更新的
`2026-07-28` 无握手模型不在 v0.1 范围。升级必须通过 ADR 和兼容性测试。当前参考：

- MCP Specification：https://modelcontextprotocol.io/specification/latest
- MCP Architecture：https://modelcontextprotocol.io/specification/latest/architecture
- MCP TypeScript SDK：https://ts.sdk.modelcontextprotocol.io/v2/
- MCP Python SDK：https://py.sdk.modelcontextprotocol.io/

具体实现、安全边界和验收见 [MCP_STDIO_PROVIDER.md](MCP_STDIO_PROVIDER.md) 与
[INTEGRATION_PACKAGES.md](INTEGRATION_PACKAGES.md)、[ADR-014](adr/ADR-014-mcp-stdio-baseline.md)
和 [ADR-015](adr/ADR-015-integration-packages-readonly-api.md)。

规范和 SDK 升级必须通过 ADR、依赖审查和完整 MCP 契约测试，不能仅因存在新版本自动升级。

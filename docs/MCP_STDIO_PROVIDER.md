# MCP stdio Provider 实现说明

> 状态：Implemented（Increment 7B；7C 增加包管理和只读 API 策略）
> 协议基线：MCP `2025-11-25`
> 最后更新：2026-09-08

## 已实现路径

`McpIntegrationManifest → StdioMcpClient → McpToolProvider → ToolProviderRegistry →
TaskRunSession/GeoDomainHarness` 是唯一执行路径。MCP 没有自己的 Task 状态、Attempt、
Evidence、Validation 或 Artifact 类型。外部 Tool 被映射为普通 `ToolDefinition` 后，继续
使用 Harness 的 Schema 校验、计划白名单、尝试次数、证据门禁、Artifact Store 和终态。

Provider 激活依次经历 discovering、starting、ready；启动、健康检查、Tool 分页发现、
本地清单策略和命名冲突全部通过后才原子发布。失败进入 degraded/stopped 并释放已获得的
资源。任务开始时冻结 Tool、Provider、Server、协议、权限和 cwd 指纹；运行中注册表变化
不能改变已审批计划。

## Integration Manifest

清单必须是本地 JSON，固定 `schema_version=1.0` 与
`protocol_version=2025-11-25`，并显式声明：

- Server ID/版本；
- stdio command、args、cwd，以及 command/cwd/env allowlist；
- 连接、调用和关闭超时，以及最多一次的受控重启预算；
- Tool allowlist 与 remote/Geo/model 名称映射；
- Host 拥有的用途、资产能力、风险、只读/幂等/重放和 Evidence 元数据；
- 权限。未知权限及非布尔配置会被拒绝；v0.1 允许同时带精确
  `network_policy.allowed_origins` 的 `network_read`，继续拒绝写入、导出、无限制网络、
  修改、破坏和外部软件控制权限。

`{python}` 和 `{manifest_dir}` 是两个受控的可移植占位符。LLM 不能生成或修改 Server
命令、cwd、环境变量或 Tool allowlist。环境变量值只在子进程启动边界读取；Run、事件、
模型上下文、错误和 Artifact 只记录名称或不可逆指纹。

## 协议与内容边界

- stdout 只接受 UTF-8、逐行分隔的 JSON-RPC 2.0；任何普通日志都是协议污染；
- stderr 是诊断通道，进行脱敏、截断并只输出计数/哈希诊断；
- `instructions` 只记录“存在且已忽略”，不会进入系统提示；
- Resources/Prompts/Sampling/Elicitation/Tasks 不声明为 Client 能力，Server 请求被拒绝；
- `structuredContent` 是科学事实的程序化来源，并按 Server outputSchema 再校验；
- Text 只作为不可信摘要，超限后截断并附长度/哈希；
- file/resource URI 只生成脱敏引用，只有确实位于任务输出目录的现有文件才会标记为
  可授权任务产物；Server 声称的任意本地路径不会自动登记为 Artifact；
- 超大 GeoToolResult 复用现有 Artifact 投影机制，模型只看到有限摘要和哈希引用。

需要调用软件 HTTP API 的经过审查的 Server，可以复用 `ReadOnlyApiClient`。Host 把只读
模式和 Manifest 中的 Origin 注入保留环境变量；Server 不能用自身 env allowlist 覆盖它们。
该 Client 限制为 GET、精确 Origin、无重定向和有限 JSON 响应，但它不是 OS 网络沙箱，
不能约束故意绕开该 Client 的本地 Server。

## 取消、崩溃与重启

调用超时或任务取消会发送 `notifications/cancelled` 并停止等待。超时、取消、崩溃或协议
错误都会隔离当前连接，防止迟到响应被误认为下一次调用的结果。关闭顺序是关闭 stdin、
等待 Server、超时后 terminate、再次超时后 kill。一次 `tools/call` 失败后不会在 Provider
内部隐式重放；只有新的 Harness Attempt 才能消耗清单允许的重启预算，因此不能绕过
PlanStep 最大尝试次数、重放策略或终态。

## 示例与测试

- `examples/mcp/readonly_server.py`：可直接激活的只读、分页 Server；
- `examples/mcp/personal_project_server.py`：把个人项目稳定函数包装为 MCP Tool；
- `examples/mcp/rest_api_wrapper.py`：未来 REST sidecar 的代码边界示例，v0.1 不可激活网络；
- `examples/integrations/software-a/`：可安装的 MCP stdio → 只读 HTTP API 完整示例；
- `tests/test_mcp.py`：独立 Client、分页、调用、关闭、allowlist、协议污染、版本/能力、超时、
  取消、崩溃、重启预算、非法结果、Schema、伪路径、Prompt 注入、密钥脱敏、大结果与完整
  Harness RunRecord；
- `tests/test_providers.py`：部分启动、健康失败、冲突、回滚、取消、冻结和幂等释放。

## 使用

```powershell
python -m geophysagent run tests/fixtures/basic.las `
  --request "先识别资产，再让外部只读集成复核资产描述" `
  --mcp-manifest examples/mcp/readonly.integration.json `
  --output runs/mcp-readonly
```

续跑时必须再次提供同一份、内容未改变的 Manifest；Run Store 会拒绝 Provider/Tool/Server/
协议/权限/cwd/网络 Origin 快照不一致的恢复。也可以通过本地 Integration Package 安装、
启用和自动发现 Manifest，详见 [Integration Package 与只读 API](INTEGRATION_PACKAGES.md)。
v0.1 不提供依赖自动安装、热重载、在线市场、包签名或远程 MCP 传输。

# ADR-014：MCP stdio 协议与 SDK 基线

- 状态：Accepted
- 日期：2026-09-08
- 适用版本：GeoPhysAgent v0.1 Increment 7B

## 决策

v0.1 的本地 MCP Provider 固定实现 MCP `2025-11-25` 的会话式 stdio 子集：
`initialize`、`notifications/initialized`、分页 `tools/list`、`tools/call`、
`notifications/cancelled` 和按传输关闭的 shutdown。只协商 Tools；Resources、Prompts、
Sampling、Elicitation、Tasks 和远程传输不启用。

官方 Python SDK 已完成来源、版本线和许可证审查。审查时官方稳定线为 v2，采用 MIT
许可证，并同时支持 2026-07-28 与更早协议修订。GeoPhysAgent 本增量不安装 SDK，使用
Python 标准库实现上述窄协议 Port，以保持已冻结的零运行时依赖和同步 Harness 接口。
这不是重新定义 MCP；线格式和生命周期由官方规范及契约测试约束。

## 原因

官方最新 `2026-07-28` 修订版已经改为无 `initialize` 会话的 stateless 模型，而本增量的
明确验收范围要求初始化、能力协商和 stdio 子进程生命周期。固定 `2025-11-25` 能够避免
在一个最小实现中混用两套生命周期，也保留与官方 SDK/Server 的明确兼容目标。

## 升级规则

升级到 MCP `2026-07-28` 或引入官方 SDK 必须单独更新本 ADR、依赖锁与第三方通知，并
通过初始化/发现兼容、取消、非法 stdout、崩溃、超时、结果 Schema、权限和恢复测试。
不得自动跟随最新版，也不得在运行中静默切换协议修订。

## 参考

- MCP 2025-11-25 Lifecycle：https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
- MCP stdio transport：https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- MCP Tools：https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- MCP Python SDK：https://github.com/modelcontextprotocol/python-sdk

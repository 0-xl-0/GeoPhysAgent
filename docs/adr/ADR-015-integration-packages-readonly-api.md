# ADR-015：本地 Integration Package 与受限只读 API

- 状态：Accepted
- 日期：2026-09-08
- 适用版本：GeoPhysAgent v0.1 Increment 7C

## 背景

Increment 7B 已证明本地 MCP stdio Tool 能进入统一 Provider Registry 和 Harness，但用户仍
需要手工传入 Manifest，且新任务必须带本地 DataAsset。外部软件通常以 API/SDK 暴露项目
和数据对象，因此还缺少可安装单元、无本地资产任务和最小网络读取策略。

## 决策

1. GeoPhysAgent 定义轻量本地 Integration Package。包由 `package.json`、一个 MCP
   Integration Manifest 和显式文件清单组成；它是 Host 的安装/策略层，不是新的 Tool
   协议。
2. 已安装包仍通过 MCP `2025-11-25` stdio Provider 执行，不新增绕过 Registry/Harness 的
   API Connector 路径。
3. `run` 允许没有本地 DataAsset 的 external-only 任务。此时只暴露明确声明为空资产能力
   集的 Tool，TaskSpec 使用空输入资产 ID。
4. 增加 `network_read` 权限，必须同时声明精确 Origin allowlist。无限制 `network`、写入、
   导出、外控、修改和破坏性权限继续拒绝。
5. 提供只执行 GET 的 JSON Client 供经过审查的 Connector 使用；Host 通过保留环境变量把
   网络模式和 Origin 注入 stdio 子进程。
6. 安装只复制本地显式文件、原子登记并冻结 SHA-256。不下载依赖、不执行安装脚本、不把
   凭据写入包。
7. v0.1 不采用 MCPB。以后若引入 MCPB、签名或市场，需要单独 ADR、供应链验证和兼容迁移。

## 安全解释

Integration Package 和本地 MCP Server 都是会以当前用户身份执行的受信任代码。
`ReadOnlyApiClient` 能约束遵循该库的 Connector，但没有能力阻止恶意 Server 调用其他网络
库或访问用户可访问的文件。因此当前权限模型是“清单约束 + 源码审查 + Harness 审计”，
不是恶意代码隔离。UI 安装确认、签名和 OS 沙箱属于后续工作。

## 结果

- 软件 A/B 可以各自发布独立适配包，GeoPhysAgent 核心无需加入厂商 API 分支；
- 用户安装启用后可直接用自然语言查询外部项目和数据元信息；
- 外部结果仍必须成为 GeoToolResult/Evidence 并通过验证，协议成功不等于专业结论成立；
- 包内容、Tool/Provider 身份和网络 Origin 可在 RunRecord 中审计并参与恢复一致性检查；
- v0.1 的能力明确止于受限读取，不暗示能够操控或修改外部软件。


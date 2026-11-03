# Integration Package 与只读 API Connector

> 状态：Implemented（Increment 7C）  
> 最后更新：2026-09-08

## 用户能够得到什么

当外部软件没有现成 MCP Server 时，可以为该软件编写一个小型、独立的适配包。安装并
启用后，GeoPhysAgent 可以在没有本地输入文件的情况下，根据自然语言选择其中的只读
Tool，例如“查看软件 A 中 XX 项目下的地震数据的信息”。完整调用链是：

```text
用户自然语言
  → LLM 候选计划
  → GeoDomainHarness 审批
  → 已安装 Integration Package 的 MCP Tool
  → 本地 stdio MCP Server
  → 经过审查的只读 API Connector
  → 软件 API GET
  → GeoToolResult / Evidence / Validation / 最终回答
```

MCP 负责标准化 Tool 的发现和调用；Integration Package 负责“如何安装并声明这个软件的
适配器”；GeoDomainHarness 仍负责计划、权限、尝试、证据、验证、产物和终态。三者职责
不同，MCP 并没有取代 GeoPhysAgent 的 Harness 或专业工作流。

## 包结构

当前支持本地目录包：

```text
software-a/
├── package.json
├── software-a.integration.json
└── server.py
```

`package.json` 固定 `schema_version=1.0`，包含稳定的包 ID、版本、显示信息、MCP Manifest
路径和完整文件清单。文件必须全部显式声明；安装会拒绝额外文件、缺失文件、符号链接、
路径逃逸、疑似凭据文件、重复 ID 和超过限制的包。

Integration Manifest 继续声明：

- stdio 启动命令、工作目录和环境变量 allowlist；
- MCP Server/Tool 版本和 Tool 名称映射；
- Tool 输入输出 Schema、只读/幂等/重放策略和 Evidence 契约；
- `network_read` 权限与精确 `network_policy.allowed_origins`。

API Key 不进入包、Manifest 或命令行。Server 启动时只会收到 Manifest 显式允许的环境
变量，以及 Host 注入且不能被包覆盖的只读网络策略。

## 安装与管理

安装仓库中的示例包到默认用户数据目录：

```powershell
python -m geophysagent integration install examples/integrations/software-a
python -m geophysagent integration list
```

安装默认启用。也可以禁用、重新启用或删除：

```powershell
python -m geophysagent integration disable software-a-readonly
python -m geophysagent integration enable software-a-readonly
python -m geophysagent integration remove software-a-readonly
```

测试或便携部署可使用 `--root <目录>`，运行时对应使用 `--integration-root <目录>`。
安装使用 staging 目录和原子登记，并保存包内容 SHA-256；每次激活前重新检查包清单、
Manifest 和摘要。安装后手工修改任何已登记文件会导致激活失败，而不是静默运行新代码。

## 运行外部数据源任务

示例软件服务的地址和凭据由环境变量提供：

```powershell
$env:SOFTWARE_A_API_BASE = "http://127.0.0.1:8765"
$env:SOFTWARE_A_API_TOKEN = "你的软件 A API Token"
python -m geophysagent run `
  --request "查看软件A中XX项目下的地震数据信息" `
  --output runs/software-a
```

`run` 默认激活所有已启用 Integration。可以用重复的 `--integration <包ID>` 只选择特定
包，或用 `--no-installed-integrations` 对本次任务关闭自动发现。仍可用
`--mcp-manifest` 临时接入一份经过审查、未安装的 Manifest。

无本地文件时，TaskSpec 使用空的 `input_asset_ids`，Context 明确标记
`external_only`。此时只向模型开放 `input_capabilities=[]` 的资产无关 Tool；本地文件 Tool
不会因此获得额外权限。用户写出的项目名、工区名和对象名仍是不可信参数，只有 API Tool
返回并通过 Schema/Validator 的内容才能成为 Evidence。

恢复任务时应保持同一个 Integration 已安装、启用且内容未改变。RunRecord 冻结了
Provider、Server、Tool、Schema、协议、权限、工作目录和网络 Origin；不一致会拒绝恢复。

## 只读网络边界

`ReadOnlyApiClient` 仅支持：

- HTTP `GET`；
- Manifest 中精确列出的 Origin；
- 远程 HTTPS，或仅 loopback 的明文 HTTP；
- 禁止重定向、URL 内凭据和 fragment；
- 有限超时、有限响应大小、UTF-8 JSON/`+json` 响应；
- 从单独环境变量注入认证 Header，错误信息不包含凭据值。

“只读”描述的是 GeoPhysAgent 授权的协议操作和 Connector 契约。GET 是否真正无副作用仍取决
于软件 API 的设计，因此接入者必须选择厂商明确标记为读取/查询的端点。

这个 Client 是供经过审查的 Connector 复用的约束库，不是 OS 网络沙箱。MCP Server 是
会以当前用户权限执行的本地代码；恶意或自行使用其他网络库的 Server 可以忽略该帮助类。
因此必须在安装前审查包来源、源码、启动命令、环境变量 allowlist、Origin 和 Tool Schema。

## 当前不做什么

- 不从网络下载包，不提供在线市场或签名验证；
- 不自动安装 Python、Node 或厂商 SDK 依赖；
- 不支持 ZIP/MCPB 包，当前只接受本地目录；
- 不支持远程 MCP Transport、OAuth、Resources 或 Prompts；
- 不授权 POST/PUT/PATCH/DELETE、导出、外部 Job 提交或软件工程修改；
- 不提供面向不受信任 Server 的进程、文件系统或网络沙箱；
- 不在运行期间热更新或替换已冻结的 Integration。

软件 B、C 的接入方式相同：各自拥有独立的包 ID、MCP Server、Manifest、凭据环境变量、
Origin allowlist、Tool Schema 和测试，不向 GeoPhysAgent 核心加入厂商分支。


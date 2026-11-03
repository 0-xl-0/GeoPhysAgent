# GeoPhysAgent

地球物理工作流 Agent · Python · Windows · v0.1 开发预览 · GPL-3.0-only

GeoPhysAgent 是一个由 LLM 驱动、以本地和外部专业 Tool 执行任务、由 Harness 对计划、证据、验证和产物进行约束的地球物理 Agent。正式产品采用 **LLM-first、Local-execution、Harness-governed**：LLM 是最终用户任务的必经控制入口，确定性 Tool 是可独立测试和批处理的计算底座。当前开发者预览版已实现通用 LLM-first `run`、LAS/TXT/CSV/TSV/SEG-Y 能力路由和统一 TaskRun。

## 快速开始（离线，无需 API Key）

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m geophysagent analyze tests/fixtures/basic.las --output runs/quickstart
.\.venv\Scripts\python.exe -m geophysagent validate runs/quickstart
```

命令会生成质量分析、报告和 SVG 图件。再次运行时请换一个新的输出目录。
统一自然语言 `run` 入口需要另行配置模型和环境变量凭据，见下文。
Python 3.11–3.14 均在项目支持范围内，可按已安装版本替换命令中的版本号。

## 安装

正式支持 64 位 Windows CPython 3.11–3.14。运行时没有第三方 Python 依赖：

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --disable-pip-version-check -r requirements-dev.lock
python -m pip install --no-deps --no-build-isolation -e .
geophysagent --help
```

`.venv` 只对当前 PowerShell 会话生效。每次打开新终端后，先在项目根目录执行：

```powershell
.\.venv\Scripts\Activate.ps1
```

如果 `python -m geophysagent` 报告 `No module named geophysagent`，说明当前 `python` 不是
安装了本项目的虚拟环境；可重新激活，或直接使用
`.\.venv\Scripts\python.exe -m geophysagent ...`。

开发依赖、CI 和构建策略见 [工程基线](docs/ENGINEERING_BASELINE.md)，当前安全和专业边界见 [已知限制](docs/KNOWN_LIMITATIONS.md)。

## 当前能力

- 解析 LAS 2.0 必需段、`WRAP.YES/NO`、常用分隔符、UTF-8/UTF-16 BOM 和厂商扩展节；
- 识别深度曲线、NULL 值、曲线元数据；
- 计算曲线有效值、缺失率、最小值、最大值和均值；
- 检查深度方向、重复深度和不规则采样；
- 生成 `result.json`、语义分层 `report.md`、`well-log.svg` 和可追溯图件元数据；
- 使用版本化 LAS Rule Pack 执行缺失、范围、平直段、局部尖峰和突跳候选检查；
- 使用统一 `GeoToolResult` 和 Validator 结果驱动工作流状态；
- 在一个 TOML 中管理多个命名 ModelProfile，支持 Anthropic Messages 与 OpenAI Chat
  Completions 兼容供应商、默认切换和单次运行覆盖；
- 运行受约束的最小 Agent Loop，要求模型先调用确定性 LAS Tool 再回答；
- 将 LAS、TXT、CSV、TSV 注册为统一 DataAsset 并探测表格结构；
- 通过 Tool Provider Registry 路由 Built-in 和 Explore Tool；
- 执行可追踪的参数化预处理 Pipeline；
- 让 LLM 在受限 Explore Workspace 中生成一次性表格分析代码；
- 将成功 Explore Run 复制为未激活的候选 Tool；
- 通过 AssetAdapter Registry 在解析前区分测井、表格、地震和未知二进制资产；
- 通过 TaskSpec、ExecutionPlan、状态机、Run Store、Evidence 和 ArtifactManifest 执行确定性工作流；
- 对 Tool 输入/输出做递归 Schema 校验，并以必需证据控制任务成功终态；
- 只读解析 SEG-Y 文本头、Binary Header 和标准 Trace Header，流式计算振幅统计与基础道级 QC；
- 通过 `seismic.inspect_segy` 把第二个领域工作流接入同一 Harness，并允许该 Tool 独立测试；后续由通用 Agent 统一规划调用。
- 让确定性 CLI、LAS Agent 和 Explore 共用顶层 TaskRun、状态机、Evidence、Validation 和 Artifact 语义；
- 使用版本化检查点、单写者锁和 RecoveryPolicy 安全恢复中断的确定性 Tool；
- 提供 `status`、`resume`、`cancel` 和离线 `validate` 生命周期命令。
- 提供统一 `run`：LLM 先提交结构化候选计划，Harness 审批后才开放与资产 capability 匹配的 `asset.inspect`、`well_log.qc`、`table.preprocess`、`seismic.inspect_segy`、`explore.python`；
- 支持多 Tool/多轮结果反馈、SSE 流式协议、有限模型重试、模型故障等待、显式 `run --resume`、Token/延迟记录和 Evidence-bound 最终回答；
- 通过 `ContextAssembler` 从 RunRecord、Plan、Manifest 和 Event 投影受预算约束的模型上下文，并记录不含完整 Prompt 的 Context Snapshot；
- 将 Agent 消息、计划进度和续跑信息收敛到版本化 Run Store；超长历史按完整 Tool Call/Result 单元压缩，超大 Tool Result 转为 Artifact；
- 要求模型使用 `submit_final_answer` 提交标量 Evidence Pointer，分别展示已验证事实、模型解释和能力边界；
- 通过版本化 Integration Manifest 激活本地 MCP `2025-11-25` stdio Server；外部 Tool
  与 Built-in Tool 共用 Provider Registry、Plan、Attempt、Evidence、Validation、Artifact
  和终态，v0.1 只允许显式白名单中的只读 Tool；
- 安装、启用、禁用和删除本地 Integration Package，并在每次激活前检查显式文件清单和
  SHA-256；
- 在没有本地文件的 external-only 任务中，调用资产无关的外部软件查询 Tool；
- 通过精确 Origin allowlist 的只读 JSON API Connector 查询软件项目和数据元信息；
- 默认离线测试使用 Fake Model；真实供应商验收必须同时设置付费测试开关、Profile 和对应的
  环境变量 API Key。
- 使用领域无关 ReferenceSuite 对 LAS、SEG-Y 和 external-only Harness 运行输出统一分层指标。

## 本地确定性开发与验收接口

以下直接命令用于开发、无网回归、批处理和故障诊断，不代表最终用户需要手动选择具体 Tool。正式用户主流程将由统一 LLM Agent `run` 入口承载。

无需安装第三方依赖：

```powershell
$env:PYTHONPATH = "src"
python -m geophysagent analyze tests/fixtures/basic.las --output runs/basic
```

该命令登记机器结果、语义分层报告、SVG 测井图和图件元数据。LAS 参考产品、规则来源、
合成样本与专业验收边界见 [参考产品与跨领域评测](docs/REFERENCE_PRODUCT.md)。

识别资产类型、已实现能力，并在可用时探测表格结构：

```powershell
python -m geophysagent inspect tests/fixtures/well_log.txt
```

只读检查 SEG-Y，并输出完整 Harness 记录：

```powershell
python -m geophysagent segy data/local/model20260730_Synthetic.sgy `
  --output runs/segy-inspection
```

当前支持 ASCII/EBCDIC 文本头、基础 Binary/Trace Header、固定或标准字段可表达的可变道长，以及采样格式 1、2、3、5、6、8。处理按块流式完成，结果不包含完整道集；详细边界见 [docs/SEGY_SUPPORT.md](docs/SEGY_SUPPORT.md)。SEG-Y 仍不会被当作 TXT 表格，也不会在 Explore 中触发模型调用。

执行参数化预处理：

```powershell
python -m geophysagent preprocess tests/fixtures/well_log.csv `
  --pipeline examples/preprocess-well-log.json `
  --output runs/preprocessed
```

## 配置与切换模型

复制示例配置。当前默认配置档是 OpenCode Go 的 `minimax-m3`；配置文件中不要填写 API Key：

```powershell
Copy-Item geophysagent.example.toml geophysagent.toml
$env:OPENCODE_API_KEY = "你的 OpenCode Go API Key"
python -m geophysagent models
python -m geophysagent doctor
```

`geophysagent.toml` 可同时保存多家供应商和多个模型。修改 `[models]` 下的 `default` 即可
永久切换默认模型；也可用 `--profile` 只切换一次运行：

```powershell
python -m geophysagent doctor --profile opencode-go-kimi-k3
python -m geophysagent run tests/fixtures/basic.las `
  --request "先识别数据，再检查质量并说明主要问题" `
  --profile opencode-go-kimi-k3 `
  --output runs/run-kimi
```

当前支持 Anthropic Messages-compatible 与 OpenAI Chat Completions-compatible 两类协议。
OpenCode Go 不同模型的协议可能不同，不能只替换模型名称；请把模型、协议和 Base URL 放在
同一个命名配置档中。完整配置、迁移及供应商接入流程见
[多供应商模型配置](docs/MODEL_CONFIGURATION.md)。

OpenCode Go 要求编码 Agent 提供自己的 User-Agent 和每段对话稳定的
`x-opencode-session`。GeoPhysAgent 会自动用 Harness Task ID 绑定会话 Header，并在续跑时复用，
无需用户手工配置。

运行统一 LLM-first 产品入口。用户只描述目标，不需要选择 Parser 或具体 Tool：

```powershell
geophysagent run tests/fixtures/basic.las `
  --request "先识别数据，再检查质量并说明主要问题" `
  --output runs/run-las
```

接入经过审查的本地 MCP stdio Server 时，显式传入 Integration Manifest。LLM 仍只描述
目标，不接触启动命令、工作目录或环境变量：

```powershell
geophysagent run tests/fixtures/basic.las `
  --request "先识别资产，再让外部只读集成复核资产描述" `
  --mcp-manifest examples/mcp/readonly.integration.json `
  --output runs/mcp-readonly
```

续跑 MCP 任务时必须再次提供同一 Manifest。当前只正式支持本地 stdio Tools；写入、导出、
无限制网络、外部软件控制、Resources、Prompts、Sampling 和远程传输默认禁用；经过审查
且在 Manifest 中精确声明 Origin 的 Connector 可以执行只读 JSON API GET。实现与清单说明见
[MCP stdio Provider](docs/MCP_STDIO_PROVIDER.md)，可运行 Server 和个人项目包装示例见
[examples/mcp](examples/mcp/README.md)。

把某个软件的 MCP Server、Integration Manifest 和适配代码包装成可安装的本地
Integration Package 后，可以直接运行无本地文件的外部数据源任务：

```powershell
python -m geophysagent integration install examples/integrations/software-a
$env:SOFTWARE_A_API_BASE = "http://127.0.0.1:8765"
$env:SOFTWARE_A_API_TOKEN = "你的软件 A API Token"
python -m geophysagent run `
  --request "查看软件A中XX项目下的地震数据信息" `
  --output runs/software-a
```

该示例假设软件 A 的只读 API 已在本机相应端口运行。真实软件需要自己的 API 路径、Schema、
Validator 和契约测试。包管理、凭据和安全边界见
[Integration Package 与只读 API Connector](docs/INTEGRATION_PACKAGES.md)。

对 SEG-Y 使用同一入口：

```powershell
geophysagent run data/local/model20260730_Synthetic.sgy `
  --request "识别这个地震文件并说明当前能够进行哪些可靠检查" `
  --output runs/run-segy
```

`run` 会产生顶层 TaskRun、候选计划、AgentRun、Context Snapshots、Tool Attempts、Evidence、验证记录和 `agent-response.md`。最终回答中的已验证事实由 Evidence ID 与 JSON Pointer 绑定；自由文本被明确标为模型解释。模型、凭据或 Context Budget 暂时不可用时，任务保持 `waiting_for_user`，不会降级为直接确定性分析；修复后显式续跑：

```powershell
geophysagent run --resume --output runs/run-las
```

需要诊断长对话时，可以请求在下一个安全边界执行一次上下文压缩：

```powershell
geophysagent run --resume --output runs/run-las --compact-context
```

新的通用 Agent 运行只使用 Run Store 的版本化检查点，不再创建独立 `agent-checkpoint.json`；旧运行会在显式续跑时只读迁移。详细契约见 [Context Assembly 与恢复](docs/CONTEXT_ASSEMBLY.md)。

保留的 LAS 单 Tool Agent 兼容/诊断入口：

```powershell
python -m geophysagent agent tests/fixtures/basic.las `
  --request "检查这个 LAS 文件的数据质量并说明主要问题" `
  --output runs/agent-basic
```

处理固定工具尚未覆盖的探索任务：

```powershell
python -m geophysagent explore tests/fixtures/well_log.txt `
  --request "计算各曲线与深度的一阶差分统计，并指出值得进一步检查的区段" `
  --output runs/explore-basic
```

Explore 输出会显示具体 Workspace。确认代码和结果后，可创建一个未激活候选：

Explore 代码使用稳定的 `COLS`（列映射）、`ROWS`（行字典列表）和 `COLUMN_NAMES` 接口，并允许在预算内执行多个中间分析步骤。一次代码成功不等于任务完成；只有成功结果和合格最终说明同时存在时 CLI 才显示 `completed (exploratory)`。如果多次尝试仍失败，先查看输出目录中的 `explore-agent-run.json` 和 `explore-agent-attempts/`；其中保存了每次提案、脱敏错误原因，以及分离的执行状态和任务状态。

每次 `explore` 调用必须使用新的 `--output` 目录。Harness 会拒绝复用已有 Explore 记录的目录，防止新的尝试覆盖旧追踪文件并留下无法归属的 UUID 工作区。

```powershell
python -m geophysagent promote "runs/explore-basic/explore/<run-id>" `
  --name depth-difference-stats `
  --destination candidate-tools
```

候选 Tool 不会自动启用。需要人工审查、Schema、测试、Validator 和依赖审查后才能正式注册。Explore 的限制和安全边界见 [docs/EXPLORE_MODE.md](docs/EXPLORE_MODE.md)。

如果 Explore 代码需要纠错，输出目录中的 `explore-agent-run.json` 会记录各次尝试的状态和安全错误摘要；每次生成的代码及完整本地 stderr 保存在对应的 `explore/<run-id>/` 工作区。

`models` 和 `doctor` 只检查本地配置与密钥是否存在，不会发起 API 请求。`run`、`agent`、
`explore` 会调用选中的模型，可能消耗订阅额度或产生费用。不要把 API Key 写入配置、命令行、
日志或提交到 Git。

运行与 CI 相同的格式、静态检查和离线测试：

```powershell
.\scripts\quality.ps1
```

默认测试使用 Fake Model、确定性 Tool 和本地 stdio 测试 Server，不访问公网。真实模型套件
默认跳过；候选发布前可显式选择任一已配置 Profile，运行 LAS Agent、表格 Explore 和通用
多 Tool 验收：

```powershell
$env:GEOPHYSAGENT_RUN_ONLINE_TESTS = "1"
$env:GEOPHYSAGENT_ONLINE_PROFILE = "opencode-go-minimax-m3"
$env:OPENCODE_API_KEY = "你的 OpenCode Go API Key"
python -m unittest tests.test_online_minimax -v
```

旧的 `GEOPHYSAGENT_RUN_MINIMAX_TESTS=1` 开关仍兼容。Increment 7A/7A.1 的三项 MiniMax
在线基线已经通过；更换供应商或模型后，应重新执行同一套门禁。Increment 7B/7C 的 MCP、
包管理、外部任务和只读 API 验收使用本地 Server/Fake Model，不产生模型费用。

对已完成的候选运行执行跨领域 ReferenceSuite 评测：

```powershell
python -m geophysagent analyze tests/fixtures/las_reference/normal.las `
  --output runs/reference/las-normal
python -m geophysagent evaluate `
  --suite examples/reference/las-suite.json `
  --runs-root runs/reference `
  --output runs/reference/las-evaluation.json
```

准备好 LAS、SEG-Y 和 external-only 三个运行目录后，执行候选发布套件：

```powershell
python -m geophysagent evaluate `
  --suite examples/reference/reference-suite.json `
  --runs-root runs/reference `
  --output runs/reference/evaluation.json
```

评测器只读验证权威 Run Store，不会执行 Tool 或调用模型；Fake Model 与真实模型使用相同
任务集和结果格式。运行目录准备方式与指标说明见
[参考产品与跨领域评测](docs/REFERENCE_PRODUCT.md)。

在线运行会发送受控上下文并产生费用，只记录 Provider、Model、TaskRun、Tool/Evidence IDs 和公开用量字段，不保存密钥、认证 Header 或完整原始模型响应。普通提交和无密钥 CI 不访问外部 API。

检查、恢复、取消和离线验证已有任务：

```powershell
python -m geophysagent status runs/basic
python -m geophysagent resume runs/basic
python -m geophysagent cancel runs/basic --reason "不再需要此任务"
python -m geophysagent validate runs/basic
```

只有 `replay_policy=safe` 且仍有尝试预算的 Tool 才会自动重放；完整的已完成步骤会复用。Agent/Explore Runtime 不会被自动重放，而会进入 `waiting_for_user`。详细语义见 [docs/HARNESS_LIFECYCLE.md](docs/HARNESS_LIFECYCLE.md)。

## 当前边界

这还不是完整的最终用户产品：当前已有通用 LLM-first `run`、结构化候选计划审批、多 Tool
循环、统一顶层 TaskRun、Provider Registry、Pipeline、Explore 闭环、受预算 Context/人工
续跑、最小只读 SEG-Y 垂直切片、MCP stdio Provider、Integration Package 和受限只读 API；
但尚未实现图形工作台、远程 MCP、外部软件写操作、地震图件/处理或完整 LAS 专业参考
产品。MCP Server、API Connector 与 Explore 子进程仍不是面向不受信任代码的 OS 安全边界。

所有正式入口的输出目录现在包含 `task.json`、`plan.json`、`checkpoint.json`、版本化 `.checkpoints/`、`state.json`、`run-record.json`、`artifact-manifest.json` 与 `events.jsonl`。`input-locations.json` 只供本机恢复，不会发送给模型。已有 Harness 任务的输出目录不会被覆盖；新任务请使用新的 `--output`。

`pi/` 仅供阅读参考，不参与本项目的构建、测试和运行。

当前进度和下一步见 [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md)。
完整 TODO 见 [TODO.md](TODO.md)，Harness Core 契约与状态语义见 [docs/HARNESS_CORE.md](docs/HARNESS_CORE.md)。
LLM-first 产品入口决策见 [ADR-013](docs/adr/ADR-013-llm-first-local-execution.md)。

## 开源与贡献

Copyright (c) 2026 GeoPhysAgent contributors.

本项目采用 **GNU GPL v3.0 only**（SPDX: `GPL-3.0-only`），完整条款见
[LICENSE](LICENSE)。第三方材料保留其自身许可证，见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

欢迎提交 Issue 和 Pull Request；开发环境和贡献约定见
[CONTRIBUTING.md](CONTRIBUTING.md)，安全问题见 [SECURITY.md](SECURITY.md)。
GitHub 上传步骤和本次整理范围见 [发布指南](docs/GITHUB_PUBLISH.md)。

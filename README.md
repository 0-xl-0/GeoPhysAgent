# 🌍 GeoPhysAgent

**让地球物理分析有工具支撑、有证据可查、有过程可追溯。**

**简体中文** | [English](README.en.md)

🐍 Python 3.11–3.14 · 🪟 Windows 64 位 · 🧪 v0.1 开发预览 · 📜 GPL-3.0-only

GeoPhysAgent 是一个面向地球物理工作流的 LLM Agent。你用自然语言描述目标，模型提出计划并调用专业工具；Harness（运行控制层）约束执行、验证结果，并记录证据与产物。

项目采用 **LLM-first · Local-execution · Harness-governed**：模型负责规划与解释，本地或经过授权的外部工具负责计算，运行控制层负责约束和追踪。适合学习 Agent 工程、研究可验证分析流程，以及开发自己的地球物理工具集成。

> 当前版本为开发者预览：已实现测井质控、表格处理、基础 SEG-Y 检查与只读外部集成。专业解释结论仍需人工复核，尚不是完整的生产级地球物理平台。

**快速导航：** [快速开始](#quickstart) · [自然语言任务](#agent) · [使用示例](#examples) · [文档](#docs) · [参与贡献](#contributing)

## ✨ 能做什么

| 能力 | 当前支持 |
|---|---|
| 📈 测井 LAS | LAS 2.0 子集解析、曲线统计、缺失/范围/平直段/尖峰/突跳候选检查、SVG 测井图 |
| 📋 表格数据 | TXT / CSV / TSV 结构探测、参数化预处理与受限 Python 探索 |
| 🌊 地震 SEG-Y | 只读检查文本头、二进制头、道头，流式振幅统计和基础道级质控 |
| 🤖 模型驱动 | 命名模型配置、多工具循环、上下文预算、有限重试与人工续跑 |
| 🔌 外部工具 | 本地 MCP stdio、Integration Package、精确 Origin 白名单下的只读 JSON API |
| 🔎 可追溯运行 | 结构化计划、工具调用记录、Evidence、Validator、产物清单、检查点与完整性验证 |

运行时仅使用 Python 标准库，**没有第三方 Python 运行时依赖**。开发、格式检查和构建工具单独安装。

<a id="quickstart"></a>
## 🚀 快速开始：先跑一次离线分析

支持环境：**64 位 Windows，CPython 3.11–3.14，PowerShell**。以下命令以 Python 3.11 为例，可按本机版本调整。

```powershell
git clone https://github.com/0-xl-0/GeoPhysAgent.git
cd GeoPhysAgent
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .

python -m geophysagent analyze tests/fixtures/basic.las --output runs/quickstart
python -m geophysagent validate runs/quickstart
```

这条路径**无需模型 API Key，也不会调用模型**。安装时 pip 可能需要联网获取构建工具。分析完成后，在 `runs/quickstart/` 查看：

| 文件 | 用途 |
|---|---|
| `result.json` | 机器可读的分析结果 |
| `report.md` | 分层展示事实、规则判断与能力边界的报告 |
| `well-log.svg` | 测井曲线图 |
| `well-log.figure.json` | 图件来源与追溯元数据 |
| `run-record.json` | 任务、工具、证据和验证记录 |

示例 LAS 故意包含缺失值，出现 `completed_with_warnings` 属于预期质控结果。`validate` 检查运行记录与产物完整性，不代表专业解释已经通过认证。

> 💡 每个新任务请使用新的 `--output` 目录。已有任务不会被新任务覆盖。新开终端后需重新激活 `.venv`；也可直接使用 `.\.venv\Scripts\python.exe`。

<a id="agent"></a>
## 🤖 用自然语言运行任务

先复制模型配置，并按其中 `api_key_env` 指定的环境变量提供凭据：

```powershell
Copy-Item geophysagent.example.toml geophysagent.toml
$env:OPENCODE_API_KEY = "YOUR_API_KEY"
python -m geophysagent models
python -m geophysagent doctor

python -m geophysagent run tests/fixtures/basic.las `
  --request "先识别数据，再检查质量并说明主要问题" `
  --output runs/agent-las
```

- `models` 和 `doctor` 仅做本地检查，不调用模型；`run` 会向供应商发送任务上下文，可能产生费用。
- 示例 TOML 提供多个命名 Profile；修改 `[models].default` 切换默认项，或用 `--profile PROFILE_NAME` 单次指定。
- 支持 Anthropic Messages-compatible 与 OpenAI Chat Completions-compatible 接口。模型名、协议、Base URL 和凭据变量需要匹配；示例配置不保证供应商始终提供相应模型。
- 密钥仅通过环境变量提供，不写入 TOML、不提交到 Git。`geophysagent.toml` 已被忽略。

模型先提交候选计划，通过 Harness 约束后才能调用工具。最终回答保存在 `agent-response.md`，已验证事实绑定 Evidence 引用，模型解释单独标识。模型不可用时不会静默改走确定性分析。

📖 完整配置与切换方法：[模型配置](docs/MODEL_CONFIGURATION.md)。

<a id="examples"></a>
## 🛠️ 常用示例

激活虚拟环境后，在仓库根目录运行。

**识别文件与探测表格：**

```powershell
python -m geophysagent inspect tests/fixtures/well_log.txt
```

**执行可追踪的表格预处理：**

```powershell
python -m geophysagent preprocess tests/fixtures/well_log.csv `
  --pipeline examples/preprocess-well-log.json `
  --output runs/preprocessed
```

**只读检查 SEG-Y：** 先将自己有权使用的文件放到 `data/local/example.sgy`；仓库不附带真实 SEG-Y 数据。

```powershell
python -m geophysagent segy data/local/example.sgy --output runs/segy-check
```

**探索固定工具尚未覆盖的表格问题（需要模型）：**

```powershell
python -m geophysagent explore tests/fixtures/well_log.txt `
  --request "计算各曲线与深度的一阶差分统计，并说明结果局限" `
  --output runs/explore-basic
```

Explore 结果属于探索性证据。代码与结果需人工审查；候选工具不会自动成为正式工具。更多示例见 [进阶用法](docs/USAGE.md) 与 [Explore 说明](docs/EXPLORE_MODE.md)。

## 🔌 接入外部工具

通过显式声明的 Integration Manifest 激活经过审查的本地 MCP stdio Server：

```powershell
python -m geophysagent run tests/fixtures/basic.las `
  --request "识别资产，并使用外部只读工具复核资产描述" `
  --mcp-manifest examples/mcp/readonly.integration.json `
  --output runs/mcp-readonly
```

当前支持只读工具、本地集成包，以及受白名单约束的 JSON API GET。集成包安装后可用于不带本地文件的外部数据查询；真实软件需要独立适配和授权。

📖 [MCP 示例](examples/mcp/README.md) · [本地集成包示例](examples/integrations/README.md) · [集成与 API 边界](docs/INTEGRATION_PACKAGES.md)

## 🔄 检查与恢复任务

```powershell
python -m geophysagent status runs/agent-las
python -m geophysagent validate runs/agent-las
```

模型故障或配置问题修复后，显式继续等待中的通用 Agent 任务：

```powershell
python -m geophysagent run --resume --output runs/agent-las
```

确定性任务使用 `resume RUN_DIRECTORY`；取消任务使用 `cancel RUN_DIRECTORY --reason "原因"`。只有满足安全重放条件的工具才会自动重放。MCP 任务续跑时需再次提供同一 Manifest。

📖 [任务生命周期](docs/HARNESS_LIFECYCLE.md) · [上下文与恢复](docs/CONTEXT_ASSEMBLY.md)

## 🧪 开发与验证

在已激活的虚拟环境中安装锁定开发工具，并运行与 CI 相同的检查：

```powershell
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps --no-build-isolation -e .
.\scripts\quality.ps1
```

默认测试使用 Fake Model 和本地测试 Server，不访问公网；付费模型测试需显式开启。CI 覆盖 Windows Python 3.11–3.14。构建源码包与 wheel：

```powershell
.\scripts\build-release.ps1
```

评测已记录的参考任务，不重新执行工具或调用模型：

```powershell
python -m geophysagent analyze tests/fixtures/las_reference/normal.las `
  --output runs/reference/las-normal
python -m geophysagent evaluate --suite examples/reference/las-suite.json `
  --runs-root runs/reference --output runs/reference/las-evaluation.json
```

📖 [工程基线](docs/ENGINEERING_BASELINE.md) · [参考产品与评测](docs/REFERENCE_PRODUCT.md) · [GitHub Actions](https://github.com/0-xl-0/GeoPhysAgent/actions)

## ⚠️ 当前边界

- LAS 参考样本为合成数据，规则提示不能直接等同于地质或储层解释结论；真实数据与专业评审仍需补充。
- SEG-Y 目前提供基础只读检查，不包含地震处理、成像或解释工作台。
- Explore、MCP Server 和 API Connector **不是操作系统级安全沙箱**；仅运行已审查、受信任的代码与集成。
- 尚无图形界面、远程 MCP、多 Agent 运行时、长期记忆或外部软件写操作。
- Linux/macOS 尚未作为正式发布验收平台；目前以 Windows 为支持基线。

📖 [已知限制](docs/KNOWN_LIMITATIONS.md) · [安全策略](SECURITY.md)

<a id="docs"></a>
## 📚 文档导航

两份 README 内容对应；详细设计文档目前以简体中文为主，部分工程文档为英文。

| 想了解什么 | 文档 |
|---|---|
| 详细命令、Explore、外部集成与在线验收 | [进阶用法](docs/USAGE.md) |
| 当前实现和后续计划 | [实现状态](docs/IMPLEMENTATION_STATUS.md) · [路线图](ROADMAP.md) · [TODO](TODO.md) |
| 总体架构与 Harness | [架构](docs/ARCHITECTURE.md) · [Harness Core](docs/HARNESS_CORE.md) |
| 模型与上下文 | [模型配置](docs/MODEL_CONFIGURATION.md) · [Context Assembly](docs/CONTEXT_ASSEMBLY.md) |
| 地震数据支持范围 | [SEG-Y 支持](docs/SEGY_SUPPORT.md) |
| 外部工具接入契约 | [MCP Provider](docs/MCP_STDIO_PROVIDER.md) · [Integration Packages](docs/INTEGRATION_PACKAGES.md) |

仓库主要目录：`src/` 源码、`tests/` 测试与合成夹具、`examples/` 示例、`docs/` 设计与使用文档、`scripts/` 质量检查与构建。`runs/` 和 `data/local/` 是本机私有目录，不纳入版本控制。

<a id="contributing"></a>
## 🤝 参与贡献

欢迎通过 [Issues](https://github.com/0-xl-0/GeoPhysAgent/issues) 提交可复现的问题和改进建议，或发起 Pull Request。新工具应提供输入输出契约、测试、验证规则与来源说明。请勿提交密钥、未授权数据或运行记录。

贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全漏洞请按 [SECURITY.md](SECURITY.md) 私下报告。

## 📜 许可证

Copyright (c) 2026 GeoPhysAgent contributors.

本项目采用 **GNU GPL v3.0 only**（`GPL-3.0-only`），完整条款见 [LICENSE](LICENSE)。第三方材料保留各自许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

# 上传 GitHub

本文件夹是从当前工作区整理的独立源码快照，包含尚未提交的最新实现，不携带旧 Git 历史。
版本为 `0.1.0.dev0` 开发预览版，许可证为 `GPL-3.0-only`（仅第 3 版）。
版权声明：Copyright (c) 2026 GeoPhysAgent contributors.

## 上传方法

1. 在 GitHub 创建空仓库，例如 `GeoPhysAgent`，选择 Public。不要另外生成 README、许可证或 .gitignore。
2. 在本文件夹打开 PowerShell，用 Git 上传（推荐，能完整保留 `.github` 和 `.gitignore`）：

```powershell
git init
git add .
git commit -m "Initial open-source developer preview"
git branch -M main
git remote add origin https://github.com/YOUR_ACCOUNT/GeoPhysAgent.git
git push -u origin main
```

把 `YOUR_ACCOUNT` 替换为自己的 GitHub 用户名，按 Git 提示完成身份验证。
上传的是本文件夹内的内容，README.md、LICENSE 和 pyproject.toml 应直接位于仓库根目录。
也可使用 GitHub Desktop：添加本文件夹为本地仓库，提交后 Publish repository，取消私有选项。

网页 Upload files 也可上传源码，但需要确认隐藏文件 `.gitignore` 和 `.github/workflows/` 没有遗漏。
不要直接把 ZIP 作为仓库内的唯一文件；ZIP 是用于传输和备份的源码包。

## 本次整理

- 保留完整 src、tests、合成测试夹具、examples、设计文档、ADR、规划文档和 CI。
- GPL-3.0-only 许可证、包元数据、贡献指南和工程基线保持一致。
- 修正手动 MiniMax 工作流的 Profile，确保使用对应的 MINIMAX_CN_API_KEY。
- 补充无需模型密钥的离线快速开始；sdist 补充示例 TOML、TODO 和 ROADMAP。
- 不包含 .git 历史、pi 外部参考仓库、虚拟环境、缓存、旧 dist、runs、data/local、真实配置和凭据。
- 文档中的 data/local 示例路径需要用户自行提供数据，真实数据不在开源快照中。
- 原始开发目录保持原有实现和许可证，本次许可证变更只应用于此发布副本。

## 上传后

普通 CI 在 Windows Python 3.11–3.14 上运行离线检查与构建，无需模型密钥。
手动在线测试是独立的收费验收工作流；只有要主动执行时，才在 GitHub 配置
`paid-online-tests` Environment 和 `MINIMAX_CN_API_KEY` Secret。
本次整理不执行付费模型调用，也不宣称所有供应商在线验收通过。

请使用 README 中的配置示例，在本机新建被忽略的 geophysagent.toml；不要上传本机密钥或真实业务数据。
详细验证结果见 [发布检查记录](RELEASE_CHECK.md)。

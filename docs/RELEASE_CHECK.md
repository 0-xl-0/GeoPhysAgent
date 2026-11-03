# 发布检查记录

整理日期：2026-11-02（Asia/Shanghai）。版本：0.1.0.dev0。

此快照包含工作区当前源码，包括原仓库尚未提交的实现。

| 检查 | 结果 |
|---|---|
| 发布副本 Ruff 格式检查 | 64 个文件通过 |
| 发布副本 Ruff 静态检查 | 通过 |
| unittest discover 完整测试 | 136 项：133 通过，3 项付费在线测试跳过；无失败 |
| sdist / wheel 构建 | 通过 |
| 包许可证元数据 | GPL-3.0-only；wheel 携带完整 LICENSE |
| sdist 示例配置 | 包含 geophysagent.example.toml |
| 新虚拟环境安装 wheel | 无运行时第三方依赖，离线安装通过 |
| 安装后 LAS 分析 | 产出 result.json、report.md、SVG 和图件元数据 |
| 安装后 validate | Run integrity: valid |
| 私有内容排除 | 未包含旧 Git 历史、本地配置、真实数据、运行记录、pi、虚拟环境和缓存 |
| 常见凭据与个人路径扫描 | 无疑似真实凭据及个人绝对路径；测试中的假密钥是预期夹具 |

许可证全文下载自 https://www.gnu.org/licenses/gpl-3.0.txt 。
本机验收使用 Windows CPython 3.14；其余 Python 版本交由上传后的 CI 矩阵验证。
模型端点与模型可用性未在本次整理中在线验收。
发布文件内容与 SHA-256 清单位于整理目录旁的 `SOURCE_MANIFEST.sha256`，
ZIP 的 SHA-256 位于 `GeoPhysAgent-github.sha256`。

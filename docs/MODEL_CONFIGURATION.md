# 多供应商模型配置

GeoPhysAgent 的正式用户任务必须经过 LLM，但不绑定某一家供应商。项目根目录的
`geophysagent.toml` 保存多个**命名模型配置档**，其中只引用环境变量名，不保存 API Key。

## 配置结构

```toml
[models]
default = "opencode-go-minimax-m3"

[models.profiles.opencode-go-minimax-m3]
provider = "opencode-go"
model = "minimax-m3"
protocol = "anthropic-compatible"
base_url = "https://opencode.ai/zen/go"
api_key_env = "OPENCODE_API_KEY"
timeout_seconds = 120
max_tokens = 4000
```

`models.default` 是所有 LLM 命令的默认配置档。每个 `[models.profiles.<名称>]` 把下列内容
作为一个不可拆分的组合保存：

- `provider`：运行记录中显示的供应商标识；
- `model`：供应商要求的准确模型 ID；
- `protocol`：`anthropic-compatible` 或 `openai-compatible`；
- `base_url`：HTTPS API 根地址，可以带或不带末尾 `/v1`；
- `api_key_env`：保存凭据的环境变量名，不是凭据本身；
- `timeout_seconds`、`max_tokens`：该模型的调用预算。

Profile 名称如果包含点号，必须使用 TOML 引号，例如：

```toml
[models]
default = "opencode-go-deepseek-v4.1-flash"

[models.profiles."opencode-go-deepseek-v4.1-flash"]
```

不加引号时，TOML 会把点号解释为多层表。`api_key_env` 必须填写类似
`OPENCODE_API_KEY` 的环境变量名称，不能填写以 `sk-` 开头的实际 Key。

项目仍能读取旧版单一 `[model]` 配置，但 `models` 命令会提示迁移。旧格式不能提供便捷的
运行级切换，因此只作为兼容入口保留。

## OpenCode Go

OpenCode Go 官方当前允许其他 Agent 直接调用订阅模型。不同模型并不一定使用同一协议：
例如 `minimax-m3` 使用 Anthropic Messages，`kimi-k3` 和 `deepseek-v4.1-flash` 使用 OpenAI Chat Completions，
而部分模型使用 OpenAI Responses。GeoPhysAgent v0.1 当前支持前两种；选择只提供 Responses
端点的模型时，不能把它伪装成 `openai-compatible`，需要等 Responses Adapter 实现后再接入。

从 OpenCode Console 取得 Go API Key 后，在当前 PowerShell 会话中设置：

```powershell
$env:OPENCODE_API_KEY = "你的 OpenCode Go API Key"
python -m geophysagent models
python -m geophysagent doctor
```

GeoPhysAgent 不读取 OpenCode 客户端自己的凭据文件，也不会把 Key 写回 TOML。这样可以避免
两个应用暗中共享凭据，并确保运行目录、日志和 Git 中没有明文密钥。

OpenCode Go 还要求每个对话携带稳定的 `x-opencode-session`。GeoPhysAgent 自动使用 Harness
Task ID 绑定这个 Header：同一任务的所有模型轮次和 `run --resume` 使用同一个值，不同任务
使用不同值。该值不是凭据，用户无需在 TOML 中填写自定义 Header。

## 切换模型

永久切换项目默认模型时，只改一行：

```toml
[models]
default = "opencode-go-kimi-k3"
```

只切换当前一次任务时，不用编辑文件：

```powershell
python -m geophysagent run tests/fixtures/basic.las `
  --request "识别文件并检查数据质量" `
  --profile opencode-go-kimi-k3 `
  --output runs/run-kimi
```

`run`、`agent`、`explore` 和 `doctor` 均支持 `--profile`。列出可选项与凭据是否已配置：

```powershell
python -m geophysagent models
python -m geophysagent models --json
python -m geophysagent doctor --profile opencode-go-kimi-k3
```

模型暂时不可用而任务进入 `waiting_for_user` 后，也可以用另一配置档续跑：

```powershell
python -m geophysagent run --resume `
  --profile opencode-go-kimi-k3 `
  --output runs/run-las
```

每次 AgentRun 都记录实际的 provider、model 和 protocol，因此中途切换不会抹掉原调用事实；
但同一次任务混用模型会影响可复现性，正式评测应固定配置档。

## 添加其他供应商

1. 从供应商官方文档确认模型 ID、协议、HTTPS Base URL 和鉴权 Key；
2. 新增一个 `[models.profiles.<名称>]`，不要覆盖现有配置档；
3. 为它使用独立环境变量，例如 `$env:VENDOR_A_API_KEY = "..."`；
4. 先运行 `models` 与 `doctor --profile <名称>` 做无网络检查；
5. 用 `run --profile <名称>` 做一个会产生费用的真实小任务；
6. 审查 TaskRun、Tool 轨迹、Evidence 与最终回答后，再把它设为默认项。

Anthropic Messages 兼容服务使用 `x-api-key`，OpenAI Chat Completions 兼容服务使用 Bearer
鉴权。如果某供应商需要额外签名、OAuth、云厂商身份链、私有 Header 或不同响应格式，应实现
专门的 Model Provider Adapter，不能仅靠修改配置假装兼容。

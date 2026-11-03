# Increment 8：参考产品与跨领域评测

GeoPhysAgent 的第一个参考产品是 LAS 2.0 数据质控闭环。它的用途是验证 Harness，而不是把
LAS 变成系统支持领域的白名单或架构中心。SEG-Y 只读检查和无本地文件的 Software A API
查询继续使用相同的 TaskSpec、Provider Registry、Harness、Run Store、Evidence、Validator
和 Artifact 契约。

## 参考产品闭环

LAS 工作流现在输出四个计划内 Artifact：

- `result.json`：解析、统计、Rule Pack 观测、Validator 和图件来源；
- `report.md`：观测事实、规则判断、模型解释、假设、未解决问题和能力边界；
- `well-log.svg`：确定性多道曲线图；
- `well-log.figure.json`：轴、单位、范围、曲线、绘图 Tool 版本和 Evidence 映射。

格式解析遵循 CWLS LAS 2.0 的关键约束：Version 段优先且唯一、`VERS`/`WRAP` 必填、
Well/Curve/ASCII 必需、索引与 `STRT`/`STOP`/`STEP` 的一致性。实现支持 `WRAP.YES`、
SPACE/COMMA/TAB 数据分隔、UTF-8/UTF-16 BOM、保守的单字节回退，以及未知厂商扩展节保留。
标准来源是 [CWLS LAS 2.0 Update January 2017](https://www.cwls.org/wp-content/uploads/2017/02/Las2_Update_Jan2017.pdf)。

`geophysagent.las-qc@1.0.0` Rule Pack 保存曲线别名、单位别名、规则 ID、版本、参数、来源、
适用条件和有限异常样例。范围、缺失、平直段、局部尖峰和一阶差分突跳只产生候选 QC 观测；
它们不自动证明地质异常，也不决定数据能否用于解释。

## 通用黄金预期与评测

`examples/reference/reference-suite.json` 是版本化、与领域无关的套件格式。每个 case 可以声明：

- 资产或 external-only 输入；
- 期望 workflow/capability；
- Tool allowlist 和必需 Tool；
- 必需 Evidence、Validator 和 Artifact role；
- 可接受终态；
- `exact`、`approx`、`range`、`contains`、`subset` 断言；
- 最终报告不得出现的越界结论。

评测器只读取已经完成的 Harness 目录，不执行 Tool、不调用模型，也不修改运行。它先验证
Run Store、输入和 Artifact 哈希，再分别输出计划有效性、Tool 成功率、Evidence 完整率、
Validator 非失败率、Artifact 完整率、最终任务状态，以及真实模型运行时已有的 Token/延迟。
因此 Fake Model 和真实模型可以使用同一任务集和 JSON 结果格式。

```powershell
python -m geophysagent analyze tests/fixtures/las_reference/normal.las `
  --output runs/reference/las-normal
python -m geophysagent evaluate `
  --suite examples/reference/las-suite.json `
  --runs-root runs/reference `
  --output runs/reference/las-evaluation.json
```

三领域候选发布套件使用：

```powershell
python -m geophysagent evaluate `
  --suite examples/reference/reference-suite.json `
  --runs-root runs/reference `
  --output runs/reference/evaluation.json
```

评测不会自行生成 `las-normal`、`segy-synthetic` 或 `software-a-external-only` 目录。候选发布
应先运行相应任务，再运行评测；缺失目录会准确报告为 Harness/Run Store 层失败。在线任务仍需
显式设置 MiniMax 测试开关和环境变量凭据，凭据不进入套件或评测结果。

## 样本与验收边界

`tests/fixtures/las_reference/manifest.json` 登记了 10 个合成样本，覆盖正常、WRAP、分隔符、
Unicode/厂商别名、下降索引、NULL、平直、尖峰/突跳、范围和损坏行。它们用于确定性回归，
明确不是现场真实数据。授权真实 LAS 黄金样本、真实 MiniMax 重复评测和地球物理专业用户走查
仍是候选发布的人工作业，不能由合成测试冒充。

MCP 在跨领域证明中仍只是 Provider 的一种实现。MCP Host 负责权限、生命周期和上下文边界，
Server 暴露 Tool；Tool annotations 不能被盲目信任，最终权限仍由 GeoPhysAgent Manifest 与
Harness 门禁决定。协议依据是 [Model Context Protocol 2025-11-25 specification](https://modelcontextprotocol.io/specification/2025-11-25)。

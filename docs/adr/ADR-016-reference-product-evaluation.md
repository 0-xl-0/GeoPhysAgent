# ADR-016：以 LAS 为首个参考应用，以跨领域契约评测 Harness

- 状态：Accepted
- 日期：2026-09-09

## 决策

首个专业参考应用选择 LAS 2.0 QC，因为已有解析、统计、Validator 和 Agent 路径，能以有限
范围建立可重复结果。LAS 能力只通过 DataAsset capability、Tool Definition、Rule Pack、
Evidence 和 Artifact 扩展；通用 Agent Loop、Harness 状态机和评测器不得导入 LAS 模块或按
LAS 扩展名、Tool 名和 Provider 名分支。

新增通用 ReferenceSuite/ReferenceCase 契约，评测已记录的 Harness 运行。评测分层检查 Run
完整性、计划、路由、Tool、Evidence、Validator、Artifact、领域断言和最终表达，并保存可比较
的结构化指标。LAS、SEG-Y 和 external-only API 使用同一评测实现。

Rule Pack 和图件元数据属于通用版本化契约；LAS 只提供第一组具体规则和绘图实现。报告把观测
事实、确定性规则、模型解释、假设、未解决问题和能力边界分开，关键数字必须引用 Evidence。

## 后果

确定性结构和 Evidence 哈希可以回归，错误能够定位到具体契约层。LAS 的规则告警不能伪装成
岩性、流体或储层解释。合成数据只能完成工程验收；授权真实数据、真实模型重复性和专业用户
走查仍是发布前门禁。

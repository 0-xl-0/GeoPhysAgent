# SEG-Y 最小只读垂直切片

## 1. 目标与入口

Increment 5 提供一个可独立测试、无 LLM、无 MCP 也能执行的确定性 SEG-Y Tool 工作流。它只读取输入文件，输出结构化结果和 Markdown 报告，并通过与 LAS 相同的 `ExecutionPlan`、`RunRecord`、`Evidence`、`ArtifactManifest` 和状态机完成审计。独立 `segy` CLI 用于开发、批处理和诊断；目标正式用户流程由 LLM-first Agent 根据资产 capability 规划调用该 Tool。

```powershell
$env:PYTHONPATH = "src"
python -m geophysagent segy <input.sgy> --output <new-output-directory>
```

输出目录包括：

- `segy-result.json`：头字段、道遍历摘要、几何候选、振幅统计和验证结果；
- `segy-report.md`：面向用户的只读检查报告；
- `task.json`、`plan.json`、`checkpoint.json`、版本化 `.checkpoints/`、`state.json`、`run-record.json`、`events.jsonl`：Harness 任务与生命周期记录；
- `artifact-manifest.json`：两个结果文件的大小和 SHA-256。

## 2. 首版支持范围

实现依据 SEG 发布的 [SEG-Y rev1 标准](https://seg.org/wp-content/uploads/2025/11/seg_y_rev1.pdf) 和 [SEG-Y rev2.1 标准](https://seg.org/wp-content/uploads/2025/11/seg_y_rev2_1-oct2023.pdf) 中的基础文件头、标准 Trace Header、字节序和采样格式定义。

| 能力 | 当前支持 |
|---|---|
| 文本头 | 3200-byte ASCII 或 EBCDIC（IBM cp500）自动识别 |
| Binary Header | Job/Line/Reel、采样间隔、每道样点数、格式码、排序码、测量系统、修订号、固定长度标志、扩展文本头数 |
| 修订与字节序 | Rev0/Rev1 大端；Rev2 标准大端或小端字节序常量 |
| 扩展文本头 | 固定数量；`-1` 时有界扫描 `((SEG: EndText))` |
| Trace Header | 标准 240-byte Header；固定数量的 Rev2 Trace Header Extension 可安全跳过 |
| 道长 | 固定长度；标准 Trace Header 能表达的可变长度 |
| 采样格式 | 1 IBM float32、2 int32、3 int16、5 IEEE float32、6 IEEE float64、8 int8 |
| 几何摘要 | 坐标标量、坐标单位码、Source/Group/CDP 坐标范围、Inline/Crossline 候选 |
| 振幅摘要 | 有限/非有限计数、最小/最大、均值、总体标准差、RMS、最大绝对值 |
| 基础 QC | 头字段采样参数一致性、死道、常值道、非有限值、Trace RMS 异常候选 |

未列出的采样格式、成对换字节、可变数量 Trace Header Extension，以及必须从扩展 Trace Header 才能取得道长的文件，会返回可操作的 `SegyUnsupportedError`，不会猜测解析。

## 3. 大文件与数据边界

- `AssetAdapterRegistry` 只读取前 8192 bytes 进行路由，不会为识别而加载整个文件；
- Parser 顺序读取 Trace Header，样点按策略限制的块大小解码；
- 全局统计使用在线算法，不保存完整样点数组；
- 异常阈值只保存有上限的 Trace RMS reservoir，并在需要时进行第二次流式扫描；
- JSON 只保存聚合结果和数量受限的道号示例，不包含完整道集或 `sample_values`；
- 原始文件不被复制或修改，输入 SHA-256 进入任务清单和分析结果。

这意味着内存占用主要取决于 `sample_chunk_bytes`、固定大小统计状态和有界 reservoir，而不是 SEG-Y 文件总大小。

## 4. 验证与错误语义

`seismic.inspect_segy` 产生五组必需证据：

1. `segy-binary-header`；
2. `segy-trace-summary`；
3. `segy-amplitude-statistics`；
4. `segy-validation`；
5. `segy-artifacts`。

文件截断、无法安全定位下一道、非法固定长度标志、零样点、零采样间隔、超出策略限制或未知格式码会使 Tool 和任务失败。采样数/间隔不一致、死道、常值道、非有限值和异常振幅候选属于显式 warning，任务进入 `completed_with_warnings`，而不是悄悄标为正常。

异常振幅当前采用有界样本上的 Trace RMS 中位数与 MAD 阈值，只是待复核候选，不是地质或采集结论。

## 5. 明确非目标

Increment 5 不提供：

- 道集、二维剖面或三维体的可视化；
- 数据重写、格式转换、增益、滤波、反褶积、速度分析、偏移或反演；
- Rev2 数据拖尾的完整解释；
- Trace Header Extension 中厂商/模板字段的语义映射；
- 自动推断测线方向、坐标参考系或地质含义；
- 把完整地震数据发送给 LLM；
- 在 Explore 模式中开放任意 SEG-Y 样点代码执行。

这些能力应在新的受限 Tool、Validator 和黄金数据具备后逐项加入，不能绕过 Harness 直接塞进 CLI。

## 6. 可重复验收

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m geophysagent segy data/local/model20260730_Synthetic.sgy `
  --output runs/segy-smoke
```

自动测试覆盖 ASCII/EBCDIC、IBM/IEEE/整数采样、Rev2 小端、分块读取、固定/可变道长、采样参数不一致、未知格式、成对换字节、截断、CLI 以及 Harness 证据与产物完整性。

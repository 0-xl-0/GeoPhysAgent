from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from geophysagent.domain import AnalysisResult
from geophysagent.las import parse_las
from geophysagent.las_plot import write_las_figure
from geophysagent.las_quality import evaluate_las_quality
from geophysagent.tools import compute_curve_statistics, inspect_las
from geophysagent.validation import validate_las


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def analyze_las(source_path: str | Path) -> AnalysisResult:
    dataset = parse_las(source_path)
    source_sha256 = _sha256(dataset.source_path)
    inspection = inspect_las(dataset)
    statistics = compute_curve_statistics(dataset)
    quality = evaluate_las_quality(dataset)
    tool_results = (inspection, statistics, quality)
    validation = validate_las(dataset, quality)
    status = (
        "failed"
        if validation.status == "fail"
        else "completed_with_warnings"
        if validation.status == "warning"
        else "completed"
    )
    return AnalysisResult(
        run_id="las-analysis-"
        + hashlib.sha256(
            f"well-log-qc@0.2.0:{source_sha256}:{quality.tool_version}".encode("utf-8")
        ).hexdigest()[:24],
        status=status,
        source_path=str(dataset.source_path),
        source_sha256=source_sha256,
        tool_results=tool_results,
        validation=validation,
    )


def write_analysis(result: AnalysisResult, output_dir: str | Path) -> tuple[Path, Path]:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "result.json"
    report_path = output / "report.md"
    dataset = parse_las(result.source_path)
    figure_path, figure_metadata_path, figure_metadata = write_las_figure(dataset, output)
    payload = result.to_dict()
    payload["figure"] = figure_metadata.to_dict()
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    report_path.write_text(
        _render_markdown(
            result,
            figure_name=figure_path.name,
            figure_metadata_name=figure_metadata_path.name,
        ),
        encoding="utf-8",
        newline="\n",
    )
    return json_path, report_path


def _render_markdown(result: AnalysisResult, *, figure_name: str, figure_metadata_name: str) -> str:
    inspection = result.tool_results[0].data
    statistics = result.tool_results[1].data["curves"]
    quality = result.tool_results[2].data
    rule_pack = quality["rule_pack"]
    lines = [
        "# LAS 质控与基础分析报告",
        "",
        "> 本报告由确定性 Harness 工作流生成；规则观测不是地质解释结论。",
        "",
        "## 观测事实",
        "",
        "| 事实 | 值 | Evidence |",
        "|---|---:|---|",
        f"| 运行状态 | `{result.status}` | `las-analysis#/status` |",
        f"| 输入 SHA-256 | `{result.source_sha256}` | `las-analysis#/source_sha256` |",
        f"| 数据行数 | {inspection['row_count']} | `las-analysis#/tool_results/0/data/row_count` |",
        f"| 曲线数量 | {inspection['curve_count']} | `las-analysis#/tool_results/0/data/curve_count` |",
        f"| LAS 版本 | {_cell(inspection.get('format', {}).get('version'))} | `las-analysis#/tool_results/0/data/format/version` |",
        f"| WRAP | {_cell(inspection.get('format', {}).get('wrap'))} | `las-analysis#/tool_results/0/data/format/wrap` |",
        "",
        "## 曲线统计",
        "",
        "| 曲线 | 族 | 单位 | 有效值 | 缺失率 | 最小值 | 最大值 | 均值 | Evidence |",
        "|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for index, curve in enumerate(statistics):
        values = [curve["minimum"], curve["maximum"], curve["mean"]]
        formatted = ["—" if value is None else f"{value:.6g}" for value in values]
        lines.append(
            f"| {_cell(curve['mnemonic'])} | {_cell(curve.get('family'))} | "
            f"{_cell(curve.get('canonical_unit') or curve.get('unit'))} | "
            f"{curve['valid_count']} | {curve['missing_ratio']:.2%} | "
            f"{formatted[0]} | {formatted[1]} | {formatted[2]} | "
            f"`las-analysis#/tool_results/1/data/curves/{index}` |"
        )

    lines.extend(
        [
            "",
            "## 确定性规则判断",
            "",
            f"- Rule Pack：`{rule_pack['pack_id']}@{rule_pack['version']}`",
            f"- 规则来源：{rule_pack['source']}",
            "- 规则阈值、适用范围和版本均记录在机器可读结果中。",
            "",
            "| 状态 | 曲线 | 规则 | 观测数 | 说明 | Evidence |",
            "|---|---|---|---:|---|---|",
        ]
    )
    rule_rows = 0
    for curve_index, curve in enumerate(quality["curves"]):
        for rule_index, rule in enumerate(curve["rule_results"]):
            if rule["status"] != "warning":
                continue
            rule_rows += 1
            lines.append(
                f"| WARNING | {_cell(curve['mnemonic'])} | `{rule['rule_id']}@{rule['rule_version']}` | "
                f"{rule.get('count', 0)} | {_cell(rule['message'])} | "
                f"`las-analysis#/tool_results/2/data/curves/{curve_index}/rule_results/{rule_index}` |"
            )
    if not rule_rows:
        lines.append(
            "| PASS | — | — | 0 | 未发现达到告警阈值的规则观测 | "
            "`las-analysis#/tool_results/2/data/summary` |"
        )

    lines.extend(["", "## Validator 结果", ""])
    for index, check in enumerate(result.validation.checks):
        lines.append(
            f"- **{check.status.upper()}** `{check.code}`：{check.message} "
            f"（`las-validation#/checks/{index}`）"
        )
    lines.extend(
        [
            "",
            "## 图件",
            "",
            f"![确定性 LAS 曲线图]({figure_name})",
            "",
            f"图件轴、单位、曲线、生成工具版本和 Evidence 映射见 `{figure_metadata_name}`。",
            "",
            "## 模型解释",
            "",
            "本确定性参考工作流未调用 LLM。若由 Agent 追加解释，必须把解释与上方事实和规则判断分开，并引用 Evidence。",
            "",
            "## 假设",
            "",
            "- 第一条曲线被作为索引曲线；非标准索引 mnemonic 会由 Validator 明示。",
            "- 物理范围仅在曲线别名和单位均可识别时应用，阈值为保守 QC 规则而非储层评价标准。",
            "- 尖峰、突跳和平直段是候选异常，需要结合采集环境、井眼条件和业务目标复核。",
            "",
            "## 未解决问题",
            "",
            "- 本工作流不推断岩性、流体、储层品质或成因。",
            "- 未识别的厂商 mnemonic、单位或扩展节只保留并披露，不自动赋予专业含义。",
            "",
            "## 能力边界",
            "",
            "本报告的解释边界是：只包含可追溯的格式检查、统计、规则观测和 Validator 结果。任何专业结论都需要人工复核，且不得把告警数量直接解释为数据不可用或地质异常。",
            "",
        ]
    )
    return "\n".join(lines)


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")

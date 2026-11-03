from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from geophysagent.domain import RunStatus, ValidationReport
from geophysagent.segy import SegyInspection, SegyPolicy, inspect_segy
from geophysagent.validation import validate_segy


@dataclass(frozen=True)
class SegyAnalysisResult:
    run_id: str
    status: RunStatus
    source_path: str
    source_sha256: str
    inspection: SegyInspection
    validation: ValidationReport
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_segy(
    source_path: str | Path,
    *,
    policy: SegyPolicy | None = None,
) -> SegyAnalysisResult:
    source = Path(source_path).resolve()
    inspection = inspect_segy(source, policy=policy)
    validation = validate_segy(inspection)
    status: RunStatus = (
        "failed"
        if validation.status == "fail"
        else "completed_with_warnings"
        if validation.status == "warning"
        else "completed"
    )
    return SegyAnalysisResult(
        run_id=str(uuid.uuid4()),
        status=status,
        source_path=str(source),
        source_sha256=_sha256(source),
        inspection=inspection,
        validation=validation,
    )


def write_segy_analysis(
    result: SegyAnalysisResult,
    output_dir: str | Path,
) -> tuple[Path, Path]:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "segy-result.json"
    report_path = output / "segy-report.md"
    result_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(_render_report(result), encoding="utf-8")
    return result_path, report_path


def _render_report(result: SegyAnalysisResult) -> str:
    inspection = result.inspection
    binary = inspection.binary_header
    traces = inspection.trace_summary
    amplitude = inspection.amplitude
    geometry = inspection.geometry
    lines = [
        "# SEG-Y 只读检查与基础质控报告",
        "",
        "> 本报告由确定性 Harness 工作流生成；统计候选不是地质解释结论。",
        "",
        "## 观测事实",
        "",
        f"- 运行状态：`{result.status}`",
        f"- 输入 SHA-256：`{result.source_sha256}`（`segy-result-artifact#/source_sha256`）",
        f"- 文本头编码：`{inspection.textual_header_encoding}`（`segy-result-artifact#/inspection/textual_header_encoding`）",
        f"- SEG-Y 修订：`{binary.revision_major}.{binary.revision_minor}`",
        f"- 字节序：`{binary.byte_order}`",
        f"- 采样格式：`{binary.sample_format_code}` — {binary.sample_format_name}",
        f"- 采样间隔：{binary.sample_interval:g}",
        f"- Binary Header 每道样点：{binary.samples_per_trace}",
        f"- 安全遍历道数：{traces['trace_count']}（`segy-trace-summary#/trace_count`）",
        f"- 总样点数：{traces['total_declared_samples']}",
        "",
        "## 振幅统计",
        "",
        f"- 有限样点：{amplitude['finite_sample_count']}",
        f"- 非有限样点：{amplitude['nonfinite_sample_count']}",
        f"- 最小值：{_number(amplitude['minimum'])}",
        f"- 最大值：{_number(amplitude['maximum'])}",
        f"- 均值：{_number(amplitude['mean'])}",
        f"- 标准差：{_number(amplitude['standard_deviation'])}",
        f"- RMS：{_number(amplitude['rms'])}",
        "",
        "## 几何字段候选",
        "",
        f"- 坐标单位：{geometry['coordinate_units']}",
        f"- Inline：{_range(geometry['inline_candidate'])}",
        f"- Crossline：{_range(geometry['crossline_candidate'])}",
        f"- CDP X：{_range(geometry['cdp_x'])}",
        f"- CDP Y：{_range(geometry['cdp_y'])}",
        "",
        "## 确定性规则判断",
        "",
    ]
    for check in result.validation.checks:
        lines.append(f"- **{check.status.upper()}** `{check.code}`：{check.message}")
    lines.extend(
        [
            "",
            "## Evidence",
            "",
            "- `segy-binary-header`",
            "- `segy-trace-summary`",
            "- `segy-amplitude-statistics`",
            "- `segy-validation`",
            "",
            "## 文本头预览",
            "",
            "```text",
            *inspection.textual_header_lines[:8],
            "```",
            "",
            "## 模型解释",
            "",
            "本确定性工作流未调用 LLM；后续模型解释必须引用上方 Evidence，且不能成为独立事实来源。",
            "",
            "## 假设",
            "",
            "- 标准 Trace Header 字段按所识别修订和字节序读取；统计仅覆盖支持的采样格式。",
            "- 异常振幅是基于 Trace RMS 的候选，需要结合采集与处理历史复核。",
            "",
            "## 未解决问题",
            "",
            "- 本工作流不恢复厂商私有 Trace Header，也不执行速度分析、偏移、反演或地质解释。",
            "",
            "## 能力边界",
            "",
            "本报告只读取标准文件头、标准 Trace Header 和受支持的采样格式，"
            "执行结构检查、有限统计与基础道级候选检测；不修改输入，不进行速度分析、"
            "偏移、反演或地质解释。异常振幅是基于 Trace RMS 的统计候选，需要专业复核。",
            "",
        ]
    )
    return "\n".join(lines)


def _number(value: Any) -> str:
    return "—" if value is None else f"{float(value):.8g}"


def _range(value: dict[str, Any]) -> str:
    if value["minimum"] is None:
        return "—"
    return f"{value['minimum']} … {value['maximum']} (n={value['count']})"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

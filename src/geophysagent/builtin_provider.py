from __future__ import annotations

from geophysagent.domain import GeoToolResult
from geophysagent.asset_adapters import (
    ASSET_INSPECT,
    TABLE_PREPROCESS,
    TABULAR_INSPECT,
    WELL_LOG_QC,
    SEGY_INSPECT,
    get_default_asset_adapter_registry,
)
from geophysagent.pipeline import execute_pipeline, load_pipeline_spec, write_pipeline_result
from geophysagent.providers import (
    BuiltInToolProvider,
    ToolContext,
    ToolDefinition,
    ToolExecution,
    ToolProviderRegistry,
    ToolRegistryError,
)
from geophysagent.tabular import inspect_tabular_asset, load_tabular_asset
from geophysagent.workflow import analyze_las, write_analysis
from geophysagent.seismic_workflow import analyze_segy, write_segy_analysis


def create_builtin_registry() -> ToolProviderRegistry:
    provider = BuiltInToolProvider()
    provider.register(
        ToolDefinition(
            name="asset.inspect",
            version="0.1.0",
            description="返回 Harness 已授权数据资产的类型、格式、哈希与可用能力，不读取任意路径。",
            input_schema=_asset_schema(),
            output_schema=_result_object_schema("asset"),
            model_name="inspect_asset",
            purpose="识别输入资产并确定后续可用能力。",
            usage_guidance="只传入 Harness 已注册的 asset_id。",
            input_capabilities=(ASSET_INSPECT,),
            side_effects=(),
            expected_evidence_types=("tool_result", "fact"),
        ),
        _inspect_asset,
    )
    provider.register(
        ToolDefinition(
            name="seismic.inspect_segy",
            version="0.1.0",
            description="只读解析 SEG-Y 文件头与 Trace，并执行基础结构、振幅和道级质控。",
            input_schema=_asset_schema(),
            output_schema=_result_object_schema("analysis", "artifacts"),
            risk="compute",
            model_name="inspect_segy",
            purpose="读取 SEG-Y 结构、振幅统计和基础质量证据。",
            usage_guidance="仅处理已授权 SEG-Y 资产，不修改源文件。",
            input_capabilities=(SEGY_INSPECT,),
            expected_evidence_types=("tool_result", "validation", "artifact"),
            expected_artifact_roles=("data", "final_report"),
        ),
        _inspect_segy,
    )
    provider.register(
        ToolDefinition(
            name="asset.inspect_table",
            version="0.1.0",
            description="探测 TXT、CSV、TSV 或 LAS 表格资产的结构。",
            input_schema=_asset_schema(),
            output_schema=_result_object_schema("asset", "profile"),
            model_name="inspect_table",
            purpose="识别表格列、行和解析特征。",
            usage_guidance="适用于 Harness 标记为可探测表格的资产。",
            input_capabilities=(TABULAR_INSPECT,),
            expected_evidence_types=("tool_result", "fact"),
        ),
        _inspect_table,
    )
    provider.register(
        ToolDefinition(
            name="las.run_qc",
            version="0.2.0",
            description="执行 LAS 解析、统计、版本化规则质控、验证和确定性图件生成。",
            input_schema=_asset_schema(),
            output_schema=_result_object_schema("analysis", "artifacts"),
            risk="compute",
            input_capabilities=(WELL_LOG_QC,),
            expected_evidence_types=("tool_result", "validation", "artifact"),
            expected_artifact_roles=(
                "data",
                "final_report",
                "figure",
                "figure_metadata",
            ),
        ),
        _run_las_qc,
    )
    provider.register(
        ToolDefinition(
            name="well_log.qc",
            version="0.2.0",
            description="执行测井资产的确定性参考产品工作流；v0.2 当前实现为 LAS。",
            input_schema=_asset_schema(),
            output_schema=_result_object_schema("analysis", "artifacts"),
            risk="compute",
            model_name="run_well_log_qc",
            purpose="对测井资产执行确定性质量检查。",
            usage_guidance="v0.2 仅对 LAS 实现完整参考闭环；其他格式先检查能力。",
            input_capabilities=(WELL_LOG_QC,),
            expected_evidence_types=("tool_result", "validation", "artifact"),
            expected_artifact_roles=(
                "data",
                "final_report",
                "figure",
                "figure_metadata",
            ),
        ),
        _run_well_log_qc,
    )
    provider.register(
        ToolDefinition(
            name="table.run_pipeline",
            version="0.1.0",
            description="执行经过 Schema 校验的参数化表格预处理 Pipeline。",
            input_schema={
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string"},
                    "pipeline": {"type": "object"},
                },
                "required": ["asset_id", "pipeline"],
                "additionalProperties": False,
            },
            output_schema=_result_object_schema(
                "row_count", "columns", "pipeline_sha256", "artifacts"
            ),
            risk="compute",
            input_capabilities=(TABLE_PREPROCESS,),
            expected_evidence_types=("tool_result", "artifact"),
            expected_artifact_roles=("data", "final_manifest"),
        ),
        _run_pipeline,
    )
    provider.register(
        ToolDefinition(
            name="table.preprocess",
            version="0.1.0",
            description="执行经过 Schema 校验的参数化表格预处理 Pipeline。",
            input_schema={
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string"},
                    "pipeline": {"type": "object"},
                },
                "required": ["asset_id", "pipeline"],
                "additionalProperties": False,
            },
            output_schema=_result_object_schema(
                "row_count", "columns", "pipeline_sha256", "artifacts"
            ),
            risk="compute",
            model_name="run_table_preprocess",
            purpose="执行声明式、可审计的表格预处理步骤。",
            usage_guidance=(
                "pipeline 必须含 schema_version=1.0 和非空 steps；支持 rename_columns、"
                "select_columns、sort_rows、deduplicate、drop_null_rows、interpolate_linear。"
            ),
            input_capabilities=(TABLE_PREPROCESS,),
            expected_evidence_types=("tool_result", "artifact"),
            expected_artifact_roles=("data", "final_manifest"),
        ),
        _preprocess_table,
    )
    registry = ToolProviderRegistry()
    registry.register_provider(provider)
    return registry


def _asset_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {"asset_id": {"type": "string"}},
        "required": ["asset_id"],
        "additionalProperties": False,
    }


def _result_object_schema(*required: str) -> dict[str, object]:
    return {
        "type": "object",
        "required": list(required),
        "properties": {name: {} for name in required},
        "additionalProperties": True,
    }


def _asset(arguments: dict[str, object], context: ToolContext):
    asset_id = str(arguments["asset_id"])
    asset = context.assets.get(asset_id)
    if asset is None:
        raise ToolRegistryError(f"Asset is not authorized: {asset_id}")
    return asset


def _inspect_asset(arguments: dict[str, object], context: ToolContext) -> ToolExecution:
    asset = _asset(arguments, context)
    get_default_asset_adapter_registry().require_capability(asset, ASSET_INSPECT)
    result = GeoToolResult(
        tool_name="asset.inspect",
        tool_version="0.1.0",
        status="success",
        data={"asset": asset.to_dict(disclose_path=False)},
    )
    return ToolExecution(result, asset)


def _inspect_table(arguments: dict[str, object], context: ToolContext) -> ToolExecution:
    asset = _asset(arguments, context)
    get_default_asset_adapter_registry().require_capability(asset, TABULAR_INSPECT)
    profile = inspect_tabular_asset(asset)
    result = GeoToolResult(
        tool_name="asset.inspect_table",
        tool_version="0.1.0",
        status="success",
        data={"asset": asset.to_dict(disclose_path=False), "profile": profile.to_dict()},
    )
    return ToolExecution(result, profile)


def _run_las_qc(arguments: dict[str, object], context: ToolContext) -> ToolExecution:
    return _run_las_qc_named("las.run_qc", arguments, context)


def _run_well_log_qc(arguments: dict[str, object], context: ToolContext) -> ToolExecution:
    return _run_las_qc_named("well_log.qc", arguments, context)


def _run_las_qc_named(
    tool_name: str, arguments: dict[str, object], context: ToolContext
) -> ToolExecution:
    asset = _asset(arguments, context)
    get_default_asset_adapter_registry().require_capability(asset, WELL_LOG_QC)
    analysis = analyze_las(asset.source_path)
    result_path, report_path = write_analysis(analysis, context.output_dir)
    figure_path = context.output_dir / "well-log.svg"
    figure_metadata_path = context.output_dir / "well-log.figure.json"
    result = GeoToolResult(
        tool_name=tool_name,
        tool_version="0.2.0",
        status="failed"
        if analysis.status == "failed"
        else "warning"
        if analysis.status.endswith("warnings")
        else "success",
        data={
            "analysis": _safe_analysis(analysis.to_dict()),
            "artifacts": [
                str(result_path),
                str(report_path),
                str(figure_path),
                str(figure_metadata_path),
            ],
        },
    )
    return ToolExecution(result, analysis)


def _run_pipeline(arguments: dict[str, object], context: ToolContext) -> ToolExecution:
    return _run_pipeline_named("table.run_pipeline", arguments, context)


def _preprocess_table(arguments: dict[str, object], context: ToolContext) -> ToolExecution:
    return _run_pipeline_named("table.preprocess", arguments, context)


def _run_pipeline_named(
    tool_name: str, arguments: dict[str, object], context: ToolContext
) -> ToolExecution:
    asset = _asset(arguments, context)
    get_default_asset_adapter_registry().require_capability(asset, TABLE_PREPROCESS)
    pipeline = arguments["pipeline"]
    assert isinstance(pipeline, dict)
    dataset = load_tabular_asset(asset, null_values=[-999.25])
    processed = execute_pipeline(dataset, load_pipeline_spec(pipeline))
    data_path, manifest_path = write_pipeline_result(processed, context.output_dir)
    result = GeoToolResult(
        tool_name=tool_name,
        tool_version="0.1.0",
        status="success",
        data={
            "row_count": len(processed.dataset.rows),
            "columns": list(processed.dataset.columns),
            "pipeline_sha256": processed.pipeline_sha256,
            "artifacts": [str(data_path), str(manifest_path)],
        },
    )
    return ToolExecution(result, processed)


def _inspect_segy(arguments: dict[str, object], context: ToolContext) -> ToolExecution:
    asset = _asset(arguments, context)
    get_default_asset_adapter_registry().require_capability(asset, SEGY_INSPECT)
    analysis = analyze_segy(asset.source_path)
    result_path, report_path = write_segy_analysis(analysis, context.output_dir)
    safe = analysis.to_dict()
    safe["source_path"] = f"asset://{asset.asset_id}"
    result = GeoToolResult(
        tool_name="seismic.inspect_segy",
        tool_version="0.1.0",
        status=(
            "failed"
            if analysis.status == "failed"
            else "warning"
            if analysis.status == "completed_with_warnings"
            else "success"
        ),
        data={
            "analysis": safe,
            "artifacts": [str(result_path), str(report_path)],
        },
    )
    return ToolExecution(result, analysis)


def _safe_analysis(payload: dict[str, object]) -> dict[str, object]:
    result = dict(payload)
    result["source_path"] = "asset://primary"
    return result

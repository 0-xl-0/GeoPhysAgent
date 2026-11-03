from __future__ import annotations

import uuid
from pathlib import Path
from geophysagent.assets import DataAsset
from geophysagent.builtin_provider import create_builtin_registry
from geophysagent.contracts import ExecutionPlan, ExpectedEvidence, PlanStep
from geophysagent.domain import AnalysisResult
from geophysagent.harness import GeoDomainHarness, HarnessRunResult, create_task_spec
from geophysagent.pipeline import PipelineResult, PipelineSpec
from geophysagent.providers import ToolProviderRegistry
from geophysagent.seismic_workflow import SegyAnalysisResult


def execute_las_qc_task(
    *,
    asset: DataAsset,
    request: str,
    output_dir: str | Path,
    registry: ToolProviderRegistry | None = None,
) -> tuple[AnalysisResult, HarnessRunResult]:
    selected_registry = registry or create_builtin_registry()
    task = create_task_spec(
        request=request,
        workflow="well-log-qc",
        input_asset_ids=(asset.asset_id,),
        allowed_tools=("las.run_qc",),
        required_final_artifact_roles=("final_report",),
    )
    evidence = (
        ExpectedEvidence(
            "las-analysis",
            "tool_result",
            "las-qc",
            "/analysis",
            "Deterministic LAS analysis result",
        ),
        ExpectedEvidence(
            "las-validation",
            "validation",
            "las-qc",
            "/analysis/validation",
            "LAS validation report",
        ),
        ExpectedEvidence(
            "las-result-artifact",
            "artifact",
            "las-qc",
            "/artifacts/0",
            "Machine-readable LAS result",
            artifact_role="data",
        ),
        ExpectedEvidence(
            "las-final-report",
            "artifact",
            "las-qc",
            "/artifacts/1",
            "Final Markdown report",
            artifact_role="final_report",
        ),
        ExpectedEvidence(
            "las-figure",
            "artifact",
            "las-qc",
            "/artifacts/2",
            "Deterministic LAS track figure",
            artifact_role="figure",
        ),
        ExpectedEvidence(
            "las-figure-metadata",
            "artifact",
            "las-qc",
            "/artifacts/3",
            "Machine-readable LAS figure provenance",
            artifact_role="figure_metadata",
        ),
    )
    plan = ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        task_id=task.task_id,
        workflow=task.workflow,
        workflow_version="0.2.0",
        objectives=("Run deterministic LAS QC", "Validate and register output evidence"),
        steps=(
            PlanStep(
                step_id="las-qc",
                tool_name="las.run_qc",
                arguments={"asset_id": asset.asset_id},
                expected_evidence_ids=tuple(item.evidence_id for item in evidence),
                max_attempts=2,
            ),
        ),
        expected_evidence=evidence,
    )
    harness_result = GeoDomainHarness(selected_registry).execute(
        task=task,
        plan=plan,
        assets={asset.asset_id: asset},
        output_dir=output_dir,
    )
    execution = harness_result.executions["las-qc"]
    if not isinstance(execution.value, AnalysisResult):
        raise TypeError("las.run_qc returned an invalid internal AnalysisResult")
    return execution.value, harness_result


def execute_table_pipeline_task(
    *,
    asset: DataAsset,
    pipeline: PipelineSpec,
    request: str,
    output_dir: str | Path,
    registry: ToolProviderRegistry | None = None,
) -> tuple[PipelineResult, HarnessRunResult]:
    selected_registry = registry or create_builtin_registry()
    task = create_task_spec(
        request=request,
        workflow="table-preprocessing",
        input_asset_ids=(asset.asset_id,),
        allowed_tools=("table.run_pipeline",),
        required_final_artifact_roles=("final_manifest",),
    )
    evidence = (
        ExpectedEvidence(
            "pipeline-row-count",
            "fact",
            "preprocess",
            "/row_count",
            "Number of rows in the derived table",
        ),
        ExpectedEvidence(
            "pipeline-definition-hash",
            "fact",
            "preprocess",
            "/pipeline_sha256",
            "Canonical preprocessing definition hash",
        ),
        ExpectedEvidence(
            "pipeline-data-artifact",
            "artifact",
            "preprocess",
            "/artifacts/0",
            "Derived CSV",
            artifact_role="data",
        ),
        ExpectedEvidence(
            "pipeline-final-manifest",
            "artifact",
            "preprocess",
            "/artifacts/1",
            "Final preprocessing result record",
            artifact_role="final_manifest",
        ),
    )
    plan = ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        task_id=task.task_id,
        workflow=task.workflow,
        workflow_version="0.1.0",
        objectives=("Execute an approved parameterized preprocessing pipeline",),
        steps=(
            PlanStep(
                step_id="preprocess",
                tool_name="table.run_pipeline",
                arguments={"asset_id": asset.asset_id, "pipeline": pipeline.to_dict()},
                expected_evidence_ids=tuple(item.evidence_id for item in evidence),
                max_attempts=2,
            ),
        ),
        expected_evidence=evidence,
    )
    harness_result = GeoDomainHarness(selected_registry).execute(
        task=task,
        plan=plan,
        assets={asset.asset_id: asset},
        output_dir=output_dir,
    )
    execution = harness_result.executions["preprocess"]
    if not isinstance(execution.value, PipelineResult):
        raise TypeError("table.run_pipeline returned an invalid internal PipelineResult")
    return execution.value, harness_result


def execute_segy_inspection_task(
    *,
    asset: DataAsset,
    request: str,
    output_dir: str | Path,
    registry: ToolProviderRegistry | None = None,
) -> tuple[SegyAnalysisResult, HarnessRunResult]:
    selected_registry = registry or create_builtin_registry()
    task = create_task_spec(
        request=request,
        workflow="segy-readonly-inspection",
        input_asset_ids=(asset.asset_id,),
        allowed_tools=("seismic.inspect_segy",),
        required_final_artifact_roles=("final_report",),
    )
    evidence = (
        ExpectedEvidence(
            "segy-binary-header",
            "fact",
            "inspect-segy",
            "/analysis/inspection/binary_header",
            "Decoded SEG-Y Binary Header",
        ),
        ExpectedEvidence(
            "segy-trace-summary",
            "fact",
            "inspect-segy",
            "/analysis/inspection/trace_summary",
            "Safely traversed Trace summary",
        ),
        ExpectedEvidence(
            "segy-amplitude-statistics",
            "fact",
            "inspect-segy",
            "/analysis/inspection/amplitude",
            "Bounded streaming amplitude statistics",
        ),
        ExpectedEvidence(
            "segy-validation",
            "validation",
            "inspect-segy",
            "/analysis/validation",
            "SEG-Y structural and basic QC validation",
        ),
        ExpectedEvidence(
            "segy-result-artifact",
            "artifact",
            "inspect-segy",
            "/artifacts/0",
            "SEG-Y JSON result",
            artifact_role="data",
        ),
        ExpectedEvidence(
            "segy-final-report",
            "artifact",
            "inspect-segy",
            "/artifacts/1",
            "Final SEG-Y Markdown report",
            artifact_role="final_report",
        ),
    )
    plan = ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        task_id=task.task_id,
        workflow=task.workflow,
        workflow_version="0.1.0",
        objectives=(
            "Read SEG-Y headers and traces without modifying the source",
            "Produce bounded statistics, validation evidence and auditable artifacts",
        ),
        steps=(
            PlanStep(
                step_id="inspect-segy",
                tool_name="seismic.inspect_segy",
                arguments={"asset_id": asset.asset_id},
                expected_evidence_ids=tuple(item.evidence_id for item in evidence),
                max_attempts=2,
            ),
        ),
        expected_evidence=evidence,
    )
    harness_result = GeoDomainHarness(selected_registry).execute(
        task=task,
        plan=plan,
        assets={asset.asset_id: asset},
        output_dir=output_dir,
    )
    execution = harness_result.executions["inspect-segy"]
    if not isinstance(execution.value, SegyAnalysisResult):
        raise TypeError("seismic.inspect_segy returned an invalid internal result")
    return execution.value, harness_result

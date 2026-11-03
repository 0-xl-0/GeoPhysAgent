from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from geophysagent.assets import register_asset
from geophysagent.asset_adapters import WELL_LOG_QC, get_default_asset_adapter_registry
from geophysagent.builtin_provider import create_builtin_registry
from geophysagent.contracts import ExecutionPlan, ExpectedEvidence, PlanStep
from geophysagent.domain import AnalysisResult
from geophysagent.model import ModelClient, bind_model_session
from geophysagent.providers import ToolProviderRegistry
from geophysagent.harness import (
    HarnessRunResult,
    TaskRunSession,
    create_task_spec,
)


class AgentRunError(RuntimeError):
    """Raised when the constrained agent loop cannot safely complete."""


SYSTEM_PROMPT = """你是 GeoPhysAgent 的测井分析助手。
你必须先调用 run_las_qc 获取确定性分析结果，再依据 Tool Result 回答用户。
所有数字必须来自 Tool Result；不得自行编造曲线、单位、统计量或地质结论。
当前工作流只支持 LAS 数据质控和基础统计，不支持储层、岩性或流体定论。
最终回答使用中文，区分事实、警告和能力边界。"""

LAS_QC_TOOL = {
    "name": "run_las_qc",
    "description": "对 Harness 已授权的主 LAS 数据资产执行确定性质控和基础统计。",
    "input_schema": {
        "type": "object",
        "properties": {
            "asset_id": {
                "type": "string",
                "enum": ["primary"],
                "description": "固定使用已授权的主输入资产。",
            }
        },
        "required": ["asset_id"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class AgentRunResult:
    answer: str
    analysis: AnalysisResult
    provider: str
    model: str
    turns: int
    harness: HarnessRunResult


def run_las_agent(
    *,
    client: ModelClient,
    user_request: str,
    source_path: str | Path,
    output_dir: str | Path,
    max_turns: int = 4,
    registry: ToolProviderRegistry | None = None,
) -> AgentRunResult:
    authorized_source = Path(source_path).resolve()
    output = Path(output_dir).resolve()
    asset = register_asset(authorized_source, asset_id="primary")
    get_default_asset_adapter_registry().require_capability(asset, WELL_LOG_QC)
    tool_registry = registry or create_builtin_registry()
    task, plan = _agent_contracts(user_request)
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"用户任务：{user_request}\n"
                "主输入已经由 Harness 注册为 asset_id=primary。"
                "不要请求或猜测本地文件路径。"
            ),
        }
    ]
    analysis: AnalysisResult | None = None
    session = TaskRunSession(
        registry=tool_registry,
        task=task,
        plan=plan,
        assets={"primary": asset},
        output_dir=output,
    )
    bind_model_session(client, task.task_id)
    agent_run_id = ""
    try:
        agent_run_id = session.start_agent_run(
            step_id="agent-runtime",
            runtime="constrained-las-agent",
            provider=client.provider,
            model=client.model,
            protocol=str(getattr(client, "protocol", "unknown")),
        )
        for turn in range(1, max_turns + 1):
            response = client.complete(
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=[LAS_QC_TOOL],
            )
            usage = response.usage or {}
            session.record_agent_turn(
                agent_run_id,
                turn,
                response_id=response.response_id,
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
                latency_ms=response.latency_ms,
            )
            messages.append({"role": "assistant", "content": list(response.content)})
            tool_uses = [block for block in response.content if block.get("type") == "tool_use"]
            text = "\n".join(
                str(block.get("text", "")).strip()
                for block in response.content
                if block.get("type") == "text" and str(block.get("text", "")).strip()
            )

            if tool_uses:
                tool_results: list[dict[str, Any]] = []
                for call in tool_uses:
                    call_id = str(call.get("id", ""))
                    name = call.get("name")
                    arguments = call.get("input")
                    if not call_id:
                        raise AgentRunError("Model returned a tool call without an id")
                    if name != "run_las_qc":
                        tool_results.append(_tool_error(call_id, f"Tool is not allowed: {name}"))
                        continue
                    if not isinstance(arguments, dict) or arguments.get("asset_id") != "primary":
                        tool_results.append(_tool_error(call_id, "asset_id must be 'primary'"))
                        continue
                    if analysis is None:
                        execution, attempt_id = session.invoke_agent_tool(
                            agent_run_id=agent_run_id,
                            step_id="agent-runtime",
                            tool_name="las.run_qc",
                            arguments={"asset_id": "primary"},
                        )
                        if not isinstance(execution.value, AnalysisResult):
                            raise AgentRunError("LAS QC Tool returned an invalid internal result")
                        analysis = execution.value
                        session.bind_tool_evidence(
                            step_id="agent-runtime",
                            execution=execution,
                            attempt_id=attempt_id,
                            evidence_ids=(
                                "las-analysis",
                                "las-validation",
                                "las-result-artifact",
                                "las-supporting-report",
                                "las-figure",
                                "las-figure-metadata",
                            ),
                        )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": json.dumps(
                                _model_safe_analysis(analysis),
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        }
                    )
                messages.append({"role": "user", "content": tool_results})
                continue

            if analysis is not None and text:
                answer_path = _write_agent_answer_content(
                    output,
                    answer=text,
                    provider=client.provider,
                    model=response.model,
                    turns=turn,
                    analysis=analysis,
                    task_id=task.task_id,
                )
                artifact = session.register_planned_artifact(
                    step_id="final-answer",
                    evidence_id="agent-final-answer",
                    path=answer_path,
                    artifact_id="agent-final-answer",
                )
                session.complete_agent_run(
                    agent_run_id,
                    status="completed",
                    turns=turn,
                    final_artifact_id=artifact.artifact_id,
                )
                harness_result = session.finalize()
                if harness_result.record.task_status not in {
                    "completed",
                    "completed_with_warnings",
                }:
                    raise AgentRunError(
                        f"Top-level Agent task did not pass validation: "
                        f"{harness_result.record.task_status}"
                    )
                return AgentRunResult(
                    text,
                    analysis,
                    client.provider,
                    response.model,
                    turn,
                    harness_result,
                )

            messages.append(
                {
                    "role": "user",
                    "content": "必须先调用 run_las_qc；没有 Tool Result 不能给出最终分析。",
                }
            )

        error = AgentRunError(f"Agent did not complete safely within {max_turns} turns")
        if analysis is None:
            session.complete_agent_run(agent_run_id, status="failed", turns=max_turns, error=error)
            session.fail(error)
        else:
            session.complete_agent_run(
                agent_run_id, status="incomplete", turns=max_turns, error=error
            )
            session.finalize()
        raise error
    except Exception as exc:
        if agent_run_id and session.record.task_status not in {
            "completed",
            "completed_with_warnings",
            "incomplete",
            "failed",
            "cancelled",
        }:
            session.complete_agent_run(
                agent_run_id,
                status="failed",
                turns=next(
                    (
                        item.turns
                        for item in session.record.agent_runs
                        if item.agent_run_id == agent_run_id
                    ),
                    0,
                ),
                error=exc,
            )
            session.fail(exc)
        raise
    finally:
        session.close()


def write_agent_answer(result: AgentRunResult, output_dir: str | Path) -> Path:
    path = Path(output_dir).resolve() / "agent-response.md"
    if not path.is_file():
        raise AgentRunError("Final Agent answer is not registered in the top-level TaskRun")
    return path


def _write_agent_answer_content(
    output_dir: Path,
    *,
    answer: str,
    provider: str,
    model: str,
    turns: int,
    analysis: AnalysisResult,
    task_id: str,
) -> Path:
    path = output_dir / "agent-response.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# GeoPhysAgent 回答\n\n"
        f"- Provider：`{provider}`\n"
        f"- Model：`{model}`\n"
        f"- Agent turns：{turns}\n"
        f"- Analysis run：`{analysis.run_id}`\n"
        f"- Harness task：`{task_id}`\n\n"
        "## Evidence\n\n"
        "- `las-analysis`\n"
        "- `las-validation`\n\n"
        f"{answer.strip()}\n",
        encoding="utf-8",
    )
    return path


def _agent_contracts(user_request: str) -> tuple[Any, ExecutionPlan]:
    task = create_task_spec(
        request=user_request,
        workflow="well-log-agent",
        input_asset_ids=("primary",),
        allowed_tools=("las.run_qc",),
        required_final_artifact_roles=("final_answer",),
    )
    evidence = (
        ExpectedEvidence("las-analysis", "tool_result", "agent-runtime", "/analysis"),
        ExpectedEvidence("las-validation", "validation", "agent-runtime", "/analysis/validation"),
        ExpectedEvidence(
            "las-result-artifact",
            "artifact",
            "agent-runtime",
            "/artifacts/0",
            artifact_role="data",
        ),
        ExpectedEvidence(
            "las-supporting-report",
            "artifact",
            "agent-runtime",
            "/artifacts/1",
            artifact_role="supporting_report",
        ),
        ExpectedEvidence(
            "las-figure",
            "artifact",
            "agent-runtime",
            "/artifacts/2",
            artifact_role="figure",
        ),
        ExpectedEvidence(
            "las-figure-metadata",
            "artifact",
            "agent-runtime",
            "/artifacts/3",
            artifact_role="figure_metadata",
        ),
        ExpectedEvidence(
            "agent-final-answer",
            "artifact",
            "final-answer",
            "",
            artifact_role="final_answer",
        ),
    )
    plan = ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        task_id=task.task_id,
        workflow=task.workflow,
        workflow_version="0.2.0",
        objectives=("Obtain deterministic LAS evidence", "Produce an evidence-bound final answer"),
        steps=(
            PlanStep(
                "agent-runtime",
                "agent.runtime",
                {},
                expected_evidence_ids=(
                    "las-analysis",
                    "las-validation",
                    "las-result-artifact",
                    "las-supporting-report",
                    "las-figure",
                    "las-figure-metadata",
                ),
                step_kind="agent",
            ),
            PlanStep(
                "final-answer",
                "artifact.final_answer",
                {},
                depends_on=("agent-runtime",),
                expected_evidence_ids=("agent-final-answer",),
                step_kind="artifact",
            ),
        ),
        expected_evidence=evidence,
    )
    return task, plan


def _tool_error(call_id: str, message: str) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": call_id,
        "is_error": True,
        "content": message,
    }


def _model_safe_analysis(analysis: AnalysisResult) -> dict[str, Any]:
    payload = analysis.to_dict()
    payload["source_path"] = "asset://primary"
    return payload

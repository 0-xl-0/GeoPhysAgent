from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from geophysagent.assets import register_asset
from geophysagent.asset_adapters import EXPLORE_PYTHON, get_default_asset_adapter_registry
from geophysagent.builtin_provider import create_builtin_registry
from geophysagent.contracts import ExecutionPlan, ExpectedEvidence, PlanStep
from geophysagent.explore import (
    ExploreExecutionError,
    ExplorePolicyError,
    ExplorePythonProvider,
    ExploreRun,
)
from geophysagent.model import ModelClient, bind_model_session
from geophysagent.providers import ToolProviderRegistry, ToolRegistryError
from geophysagent.tabular import inspect_tabular_asset
from geophysagent.harness import HarnessRunResult, TaskRunSession, create_task_spec


class ExploreAgentError(RuntimeError):
    """The Explore agent could not complete within its controlled loop."""


EXPLORE_TOOL = {
    "name": "run_python_analysis",
    "description": (
        "生成并运行一次受控 Python 表格分析。运行时预定义 ROWS（行字典列表）、"
        "COLS（列名到列值列表的映射）、COLUMN_NAMES、DATA、math 和 statistics。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "asset_id": {"type": "string", "enum": ["primary"]},
            "purpose": {"type": "string"},
            "code": {
                "type": "string",
                "description": (
                    "Python 源码。先从 COLUMN_NAMES 选择真实列名，再用 COLS[column_name] "
                    "取得整列，或遍历 ROWS 并用 row.get(column_name) 取值；不得把 DATA "
                    "当作列映射。必须赋值 RESULT。"
                ),
            },
        },
        "required": ["asset_id", "purpose", "code"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class ExploreAgentResult:
    answer: str
    run: ExploreRun
    provider: str
    model: str
    turns: int
    harness: HarnessRunResult


def run_explore_agent(
    *,
    client: ModelClient,
    user_request: str,
    source_path: str | Path,
    output_dir: str | Path,
    max_turns: int = 6,
    max_code_attempts: int = 4,
    registry: ToolProviderRegistry | None = None,
) -> ExploreAgentResult:
    if max_turns < 1 or max_code_attempts < 1:
        raise ValueError("Explore turn and code-attempt limits must be positive")
    resolved_output = Path(output_dir).resolve()
    _require_fresh_output(resolved_output)
    asset = register_asset(source_path, asset_id="primary")
    get_default_asset_adapter_registry().require_capability(asset, EXPLORE_PYTHON)
    profile = inspect_tabular_asset(asset)
    tool_registry = registry or create_builtin_registry()
    if registry is None:
        tool_registry.register_provider(ExplorePythonProvider())
    task, plan = _explore_contracts(user_request)
    system = _system_prompt(profile.to_dict())
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"用户任务：{user_request}\n"
                "输入数据已注册为 asset_id=primary。请只通过 ROWS/COLS/DATA 接口进行探索性计算。"
            ),
        }
    ]
    completed_run: ExploreRun | None = None
    completed_execution = None
    completed_attempt_id: str | None = None
    attempts: list[dict[str, Any]] = []
    last_issue = "model did not call the analysis tool"
    session = TaskRunSession(
        registry=tool_registry,
        task=task,
        plan=plan,
        assets={"primary": asset},
        output_dir=resolved_output,
    )
    bind_model_session(client, task.task_id)
    agent_run_id = ""
    try:
        agent_run_id = session.start_agent_run(
            step_id="explore-runtime",
            runtime="controlled-python-explore",
            provider=client.provider,
            model=client.model,
            protocol=str(getattr(client, "protocol", "unknown")),
        )
        for turn in range(1, max_turns + 1):
            available_tools = [EXPLORE_TOOL] if len(attempts) < max_code_attempts else []
            response = client.complete(system=system, messages=messages, tools=available_tools)
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
            calls = [block for block in response.content if block.get("type") == "tool_use"]
            text = "\n".join(
                str(block.get("text", "")).strip()
                for block in response.content
                if block.get("type") == "text" and str(block.get("text", "")).strip()
            )
            if calls:
                results: list[dict[str, Any]] = []
                for call in calls:
                    call_id = str(call.get("id", ""))
                    arguments = call.get("input")
                    if not call_id:
                        raise ExploreAgentError("Model returned a tool call without an id")
                    if len(attempts) >= max_code_attempts:
                        last_issue = f"code-attempt limit ({max_code_attempts}) reached"
                        results.append(_tool_error(call_id, last_issue))
                        continue
                    if call.get("name") != "run_python_analysis" or not isinstance(arguments, dict):
                        last_issue = "Only run_python_analysis is allowed"
                        attempts.append({"turn": turn, "status": "rejected", "error": last_issue})
                        results.append(_tool_error(call_id, last_issue))
                        continue
                    attempt_metadata = _save_code_proposal(
                        resolved_output,
                        attempt_number=len(attempts) + 1,
                        turn=turn,
                        code=arguments.get("code"),
                    )
                    if attempt_metadata.get("proposal_file"):
                        session.register_planned_artifact(
                            step_id="explore-runtime",
                            evidence_id="explore-code-proposals",
                            path=resolved_output / str(attempt_metadata["proposal_file"]),
                            artifact_id=f"explore-code-proposal-{len(attempts) + 1:02d}",
                        )
                    try:
                        execution, attempt_id = session.invoke_agent_tool(
                            agent_run_id=agent_run_id,
                            step_id="explore-runtime",
                            tool_name="explore.run_python",
                            arguments=arguments,
                        )
                        if not isinstance(execution.value, ExploreRun):
                            raise ExploreAgentError(
                                "Explore provider returned an invalid internal result"
                            )
                        completed_run = execution.value
                        completed_execution = execution
                        completed_attempt_id = attempt_id
                        attempts.append(
                            {
                                **attempt_metadata,
                                "turn": turn,
                                "status": "completed",
                                "run_id": completed_run.run_id,
                            }
                        )
                        for index, artifact_path in enumerate(
                            execution.result.data["artifacts"], start=1
                        ):
                            session.register_planned_artifact(
                                step_id="explore-runtime",
                                evidence_id="explore-run-artifacts",
                                path=artifact_path,
                                artifact_id=(f"explore-run-{completed_run.run_id}-{index:02d}"),
                            )
                        model_result = dict(execution.result.data)
                        model_result.pop("artifacts", None)
                        model_result["workspace"] = f"run://{completed_run.run_id}"
                        model_result = _limit_model_result(model_result)
                        results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": call_id,
                                "content": json.dumps(
                                    model_result, ensure_ascii=False, separators=(",", ":")
                                ),
                            }
                        )
                    except ExploreExecutionError as exc:
                        last_issue = exc.failure
                        attempts.append(
                            {
                                **attempt_metadata,
                                "turn": turn,
                                "status": "failed",
                                "error": last_issue,
                                "workspace": str(exc.workspace),
                            }
                        )
                        results.append(_tool_error(call_id, _retry_message(last_issue)))
                    except (ExplorePolicyError, ToolRegistryError) as exc:
                        last_issue = str(exc)
                        attempts.append(
                            {
                                **attempt_metadata,
                                "turn": turn,
                                "status": "rejected",
                                "error": last_issue,
                            }
                        )
                        results.append(_tool_error(call_id, _retry_message(last_issue)))
                messages.append({"role": "user", "content": results})
                if completed_run is None and len(attempts) >= max_code_attempts:
                    break
                continue
            if completed_run is not None and text:
                final_issue = _final_answer_issue(text)
                if final_issue is None:
                    assert completed_execution is not None and completed_attempt_id is not None
                    session.bind_tool_evidence(
                        step_id="explore-runtime",
                        execution=completed_execution,
                        attempt_id=completed_attempt_id,
                        evidence_ids=("explore-result", "explore-code-hash"),
                    )
                    trace_path = _write_agent_trace(
                        resolved_output,
                        status="completed",
                        turns=turn,
                        attempts=attempts,
                        final_run_id=completed_run.run_id,
                    )
                    session.register_planned_artifact(
                        step_id="final-answer",
                        evidence_id="explore-agent-trace",
                        path=trace_path,
                        artifact_id="explore-agent-trace",
                    )
                    answer_path = _write_explore_answer_content(
                        resolved_output,
                        answer=text,
                        run=completed_run,
                        provider=client.provider,
                        model=response.model,
                        task_id=task.task_id,
                    )
                    final_artifact = session.register_planned_artifact(
                        step_id="final-answer",
                        evidence_id="explore-final-answer",
                        path=answer_path,
                        artifact_id="explore-final-answer",
                    )
                    session.complete_agent_run(
                        agent_run_id,
                        status="completed",
                        turns=turn,
                        final_artifact_id=final_artifact.artifact_id,
                    )
                    harness_result = session.finalize()
                    if harness_result.record.task_status not in {
                        "completed",
                        "completed_with_warnings",
                    }:
                        raise ExploreAgentError(
                            f"Top-level Explore task did not pass validation: {harness_result.record.task_status}"
                        )
                    return ExploreAgentResult(
                        text,
                        completed_run,
                        client.provider,
                        response.model,
                        turn,
                        harness_result,
                    )
                last_issue = final_issue
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"上一条内容不能作为最终回答：{final_issue}。"
                            "如果仍需计算，请通过结构化 run_python_analysis 调用继续；"
                            "否则请直接用中文说明方法、结果和限制，不要输出任何工具协议标签。"
                        ),
                    }
                )
                continue
            last_issue = (
                "model returned text before running analysis"
                if completed_run is None
                else "model returned no final explanatory text after producing an analysis result"
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "必须先通过 run_python_analysis 获得足以回答用户任务的结果。"
                        "中间探索成功并不代表任务完成；如果还缺信息，请继续结构化调用工具。"
                    ),
                }
            )

        terminal_status = "incomplete" if completed_run is not None else "failed"
        trace_path = _write_agent_trace(
            resolved_output,
            status=terminal_status,
            turns=turn,
            attempts=attempts,
            final_run_id=completed_run.run_id if completed_run else None,
        )
        session.register_planned_artifact(
            step_id="final-answer",
            evidence_id="explore-agent-trace",
            path=trace_path,
            artifact_id="explore-agent-trace",
        )
        if completed_run is not None:
            assert completed_execution is not None and completed_attempt_id is not None
            session.bind_tool_evidence(
                step_id="explore-runtime",
                execution=completed_execution,
                attempt_id=completed_attempt_id,
                evidence_ids=("explore-result", "explore-code-hash"),
            )
            error = ExploreAgentError(
                "Explore produced executable results but did not produce a valid final answer; "
                f"last issue: {last_issue}; final run preserved as {completed_run.run_id}; "
                f"diagnostic: {trace_path}"
            )
            session.complete_agent_run(agent_run_id, status="incomplete", turns=turn, error=error)
            session.finalize()
            raise error
        error = ExploreAgentError(
            f"Explore analysis did not succeed after {len(attempts)} code attempts "
            f"across {turn} model turns; last issue: {last_issue}; diagnostic: {trace_path}"
        )
        session.complete_agent_run(agent_run_id, status="failed", turns=turn, error=error)
        session.fail(error)
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


def write_explore_answer(result: ExploreAgentResult, output_dir: str | Path) -> Path:
    path = Path(output_dir).resolve() / "explore-response.md"
    if not path.is_file():
        raise ExploreAgentError("Final Explore answer is not registered in the top-level TaskRun")
    return path


def _write_explore_answer_content(
    output_dir: Path,
    *,
    answer: str,
    run: ExploreRun,
    provider: str,
    model: str,
    task_id: str,
) -> Path:
    path = output_dir / "explore-response.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# GeoPhysAgent Explore 回答\n\n"
        "> 此结果来自 LLM 生成代码，当前验证等级为 `exploratory`，不能自动等同于正式专业结论。\n\n"
        f"- Provider：`{provider}`\n"
        f"- Model：`{model}`\n"
        f"- Explore run：`{run.run_id}`\n"
        f"- Code SHA-256：`{run.code_sha256}`\n"
        f"- Harness task：`{task_id}`\n\n"
        "## Evidence\n\n"
        "- `explore-result`\n"
        "- `explore-code-hash`\n\n"
        f"{answer.strip()}\n",
        encoding="utf-8",
    )
    return path


def _explore_contracts(user_request: str):
    task = create_task_spec(
        request=user_request,
        workflow="explore-agent",
        input_asset_ids=("primary",),
        allowed_tools=("explore.run_python",),
        required_final_artifact_roles=("final_answer",),
    )
    evidence = (
        ExpectedEvidence("explore-result", "fact", "explore-runtime", "/result"),
        ExpectedEvidence("explore-code-hash", "fact", "explore-runtime", "/code_sha256"),
        ExpectedEvidence(
            "explore-code-proposals",
            "artifact",
            "explore-runtime",
            "",
            required=False,
            artifact_role="generated_code",
        ),
        ExpectedEvidence(
            "explore-run-artifacts",
            "artifact",
            "explore-runtime",
            "",
            artifact_role="exploratory_result",
        ),
        ExpectedEvidence(
            "explore-agent-trace",
            "artifact",
            "final-answer",
            "",
            artifact_role="trace",
        ),
        ExpectedEvidence(
            "explore-final-answer",
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
        workflow_version="0.1.0",
        objectives=("Run controlled exploratory code", "Produce an evidence-bound final answer"),
        steps=(
            PlanStep(
                "explore-runtime",
                "agent.explore_runtime",
                {},
                expected_evidence_ids=(
                    "explore-result",
                    "explore-code-hash",
                    "explore-code-proposals",
                    "explore-run-artifacts",
                ),
                step_kind="agent",
            ),
            PlanStep(
                "final-answer",
                "artifact.final_answer",
                {},
                depends_on=("explore-runtime",),
                expected_evidence_ids=(
                    "explore-agent-trace",
                    "explore-final-answer",
                ),
                step_kind="artifact",
            ),
        ),
        expected_evidence=evidence,
    )
    return task, plan


def _system_prompt(profile: dict[str, Any]) -> str:
    return f"""你是 GeoPhysAgent Explore 分析助手。输入表格结构如下：
{json.dumps(profile, ensure_ascii=False)}

只有固定工具不足以回答用户时，调用 run_python_analysis。
你可以在预算内多次调用工具：前面的调用可用于检查结构或中间结果，但最终一次计算必须产生足以回答用户原始任务的证据。一次 Python 成功运行不自动等于任务完成。
代码运行环境提供以下稳定接口：
- COLS：列映射，COLS[column_name] 返回该列的值列表。这是获取整列的首选方式。
- ROWS：行字典列表；使用 row[column_name] 或 row.get(column_name) 取单元格。
- COLUMN_NAMES：列名列表。
- DATA：数据包元信息与 ROWS 的底层封装。
- math、statistics：预加载的标准库模块。

DATA 是字典，精确结构如下：
- DATA["columns"] 是列名列表；本次列名见上方 profile.columns。
- DATA["rows"] 是行字典列表；每行必须用 row[列名] 或 row.get(列名) 取值。
- DATA["row_count"] 是原始行数，DATA["profile"] 只保存结构元数据。
正确范式：column_name = COLUMN_NAMES[0]; values = [value for value in COLS[column_name] if value is not None]。
禁止把 DATA 当成列映射使用（如 DATA["某个曲线名"]），禁止用 row[0]，禁止写 DATA.profile。
表格空值在代码中是 None；计算差分等数值操作前必须跳过或明确处理 None。
禁止 import、文件读写、网络、进程、动态执行和私有/dunder 属性。
不要使用 print；需要观察的中间信息也必须写入 RESULT，然后在下一轮继续分析。
代码必须把 JSON 可序列化结果赋给全局变量 RESULT。不要输出图件或修改数据。
Tool Result 是探索性证据。最终中文回答必须说明方法、结果和限制，不得把探索结果冒充已验证地质结论。工具调用必须使用 API 提供的结构化 Tool Call，绝不能把 `<minimax:tool_call>`、`<invoke>` 或 `<parameter>` 标签作为普通文字输出。"""


def _tool_error(call_id: str, message: str) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": call_id,
        "is_error": True,
        "content": message[:1000],
    }


def _retry_message(error: str) -> str:
    return (
        f"{error}\n"
        "Data contract reminder: choose an actual name from COLUMN_NAMES, then use "
        "COLS[column_name] for a complete column, or iterate ROWS (the same list as "
        'DATA["rows"]) and use row.get(column_name). DATA is not a column mapping. '
        "Handle None before numeric operations."
    )


def _limit_model_result(payload: dict[str, Any], max_chars: int = 64_000) -> dict[str, Any]:
    result = payload.get("result")
    rendered = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(rendered) <= max_chars:
        return payload
    limited = dict(payload)
    limited["result"] = {
        "truncated_for_model": True,
        "original_characters": len(rendered),
        "preview": rendered[:max_chars],
    }
    return limited


def _final_answer_issue(text: str) -> str | None:
    normalized = text.strip()
    if len(normalized) < 20:
        return "final answer is too short to contain method, result and limitations"
    protocol_markers = (
        "<minimax:tool_call",
        "</minimax:tool_call",
        "<invoke name=",
        "<parameter name=",
        '"type":"tool_use"',
        '"type": "tool_use"',
    )
    lowered = normalized.lower()
    if any(marker in lowered for marker in protocol_markers):
        return "response contains an unexecuted textual tool call"
    return None


def _save_code_proposal(
    output_dir: Path,
    *,
    attempt_number: int,
    turn: int,
    code: Any,
) -> dict[str, Any]:
    if not isinstance(code, str):
        return {}
    proposal_dir = output_dir / "explore-agent-attempts"
    proposal_dir.mkdir(parents=True, exist_ok=True)
    relative = Path("explore-agent-attempts") / f"attempt-{attempt_number:02d}-turn-{turn:02d}.py"
    (output_dir / relative).write_text(code, encoding="utf-8")
    return {
        "code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
        "proposal_file": relative.as_posix(),
    }


def _require_fresh_output(output_dir: Path) -> None:
    run_markers = (
        output_dir / "task.json",
        output_dir / "checkpoint.json",
        output_dir / "explore-agent-run.json",
        output_dir / "explore-response.md",
        output_dir / "explore-agent-attempts",
        output_dir / "explore",
    )
    if any(marker.exists() for marker in run_markers):
        raise ExploreAgentError(
            f"Explore output already contains a previous run: {output_dir}. "
            "Use a new --output directory so run records and workspaces cannot be mixed."
        )


def _write_agent_trace(
    output_dir: Path,
    *,
    status: str,
    turns: int,
    attempts: list[dict[str, Any]],
    final_run_id: str | None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "explore-agent-run.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "authority": "diagnostic_only",
                "agent_loop_status": status,
                "turns": turns,
                "attempts": attempts,
                "final_run_id": final_run_id,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path

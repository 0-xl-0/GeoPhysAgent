from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import geophysagent.run_store as run_store_module
from geophysagent.general_agent import (
    GeneralAgentError,
    resume_general_agent,
    run_general_agent,
    write_general_agent_answer,
)
from geophysagent.harness import TaskRunSession
from geophysagent.contracts import ContextBudget
from geophysagent.domain import GeoToolResult
from geophysagent.model import ModelClientError, ModelResponse
from geophysagent.providers import ToolDefinition, ToolExecution, ToolProviderRegistry
from geophysagent.run_inspection import validate_run_directory
from geophysagent.run_store import FileRunStore
from segy_factory import write_synthetic_segy


FIXTURES = Path(__file__).parent / "fixtures"


class FakeModel:
    provider = "fake"
    model = "fake-model"
    protocol = "fake-transport"

    def __init__(self, responses: list[ModelResponse | Exception]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.bound_sessions: list[str] = []

    def bind_session(self, session_id: str) -> None:
        self.bound_sessions.append(session_id)

    def complete(self, **request: Any) -> ModelResponse:
        self.calls.append(copy.deepcopy(request))
        selected = self.responses.pop(0)
        if isinstance(selected, Exception):
            raise selected
        return selected


def tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> ModelResponse:
    return ModelResponse(
        content=(
            {
                "type": "tool_use",
                "id": call_id,
                "name": name,
                "input": arguments,
            },
        ),
        stop_reason="tool_use",
        model="fake-model",
        response_id=f"response-{call_id}",
        usage={"input_tokens": 10, "output_tokens": 5},
        latency_ms=7,
    )


def text_response(text: str) -> ModelResponse:
    return ModelResponse(
        content=({"type": "text", "text": text},),
        stop_reason="end_turn",
        model="fake-model",
        response_id="response-final",
        usage={"input_tokens": 12, "output_tokens": 8},
        latency_ms=9,
    )


def final_response(
    text: str,
    evidence_id: str,
    selector: str = "/status",
) -> ModelResponse:
    return tool_call(
        "final",
        "submit_final_answer",
        {
            "interpretation": text,
            "claims": [{"evidence_id": evidence_id, "selector": selector}],
            "limitations": ["只覆盖本次已执行 Tool 明确返回的检查范围。"],
        },
    )


class GeneralAgentTests(unittest.TestCase):
    def test_table_preprocess_tool_runs_from_approved_plan(self) -> None:
        plan = {
            "objective": "选择测井表格中的深度与 GR 列",
            "steps": [
                {
                    "step_id": "preprocess",
                    "tool": "run_table_preprocess",
                    "purpose": "生成受控派生表格",
                }
            ],
        }
        client = FakeModel(
            [
                tool_call("plan", "submit_execution_plan", plan),
                tool_call(
                    "preprocess",
                    "run_table_preprocess",
                    {
                        "asset_id": "primary",
                        "pipeline": {
                            "schema_version": "1.0",
                            "steps": [
                                {
                                    "operation": "select_columns",
                                    "columns": ["MD", "GR"],
                                }
                            ],
                        },
                    },
                ),
                final_response("已生成只包含 MD 和 GR 的派生表格。", "preprocess-result"),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            result = run_general_agent(
                client=client,
                user_request="只保留 MD 和 GR",
                source_path=FIXTURES / "well_log.csv",
                output_dir=directory,
            )
            self.assertEqual(
                [item.tool_name for item in result.harness.record.attempts],
                ["table.preprocess"],
            )
            self.assertTrue((Path(directory) / "preprocessed.csv").is_file())
            self.assertTrue(validate_run_directory(directory).valid)

    def test_segy_capability_routes_only_to_readonly_seismic_tool(self) -> None:
        plan = {
            "objective": "检查 SEG-Y 结构和基础质量",
            "steps": [
                {
                    "step_id": "segy",
                    "tool": "inspect_segy",
                    "purpose": "执行只读 SEG-Y 检查",
                }
            ],
        }
        client = FakeModel(
            [
                tool_call("plan", "submit_execution_plan", plan),
                tool_call("segy", "inspect_segy", {"asset_id": "primary"}),
                final_response("已完成只读 SEG-Y 结构与基础质量检查。", "segy-result"),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = write_synthetic_segy(
                root / "synthetic.sgy",
                traces=[[1.0, 2.0, 3.0], [2.0, 3.0, 4.0]],
                binary_samples=3,
            )
            output = root / "run"
            result = run_general_agent(
                client=client,
                user_request="检查这个地震文件",
                source_path=source,
                output_dir=output,
            )
            self.assertEqual(
                [item.tool_name for item in result.harness.record.attempts],
                ["seismic.inspect_segy"],
            )
            allowed = client.calls[0]["tools"][0]["input_schema"]["properties"]["steps"]["items"][
                "properties"
            ]["tool"]["enum"]
            self.assertIn("inspect_segy", allowed)
            self.assertNotIn("inspect_table", allowed)
            self.assertNotIn("run_well_log_qc", allowed)
            self.assertTrue(validate_run_directory(output).valid)

    def test_retryable_model_failure_is_bounded_and_recorded(self) -> None:
        plan = {
            "objective": "检查资产",
            "steps": [
                {
                    "step_id": "identify",
                    "tool": "inspect_asset",
                    "purpose": "检查资产",
                }
            ],
        }
        client = FakeModel(
            [
                ModelClientError("busy", category="rate_limited", retryable=True),
                tool_call("plan", "submit_execution_plan", plan),
                tool_call("identify", "inspect_asset", {"asset_id": "primary"}),
                final_response("已识别资产。", "identify-result"),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            result = run_general_agent(
                client=client,
                user_request="识别资产",
                source_path=FIXTURES / "basic.las",
                output_dir=directory,
                max_model_retries=1,
            )
            self.assertEqual(result.harness.record.agent_runs[0].model_retry_count, 1)
            events = (Path(directory) / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("model_retry_scheduled", events)

    def test_truncated_final_tool_call_retries_without_replaying_completed_tool(self) -> None:
        plan = {
            "objective": "检查资产",
            "steps": [
                {
                    "step_id": "identify",
                    "tool": "inspect_asset",
                    "purpose": "检查资产",
                }
            ],
        }
        client = FakeModel(
            [
                tool_call("plan", "submit_execution_plan", plan),
                tool_call("identify", "inspect_asset", {"asset_id": "primary"}),
                ModelClientError(
                    "truncated arguments",
                    category="output_limit",
                    retryable=True,
                ),
                final_response(
                    "当前已确认资产格式。",
                    "identify-result",
                    "/data/asset/format",
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            result = run_general_agent(
                client=client,
                user_request="识别资产",
                source_path=FIXTURES / "basic.las",
                output_dir=directory,
                max_model_retries=1,
            )

            self.assertEqual(
                [item.tool_name for item in result.harness.record.attempts],
                ["asset.inspect"],
            )
            self.assertEqual(result.harness.record.agent_runs[0].model_retry_count, 1)
            self.assertEqual(
                [item["name"] for item in client.calls[2]["tools"]],
                ["submit_final_answer"],
            )
            self.assertEqual(client.calls[2]["tools"], client.calls[3]["tools"])
            self.assertTrue(validate_run_directory(directory).valid)

    def test_premature_text_cannot_stop_before_planned_tool(self) -> None:
        plan = {
            "objective": "检查资产",
            "steps": [
                {
                    "step_id": "identify",
                    "tool": "inspect_asset",
                    "purpose": "检查资产",
                }
            ],
        }
        client = FakeModel(
            [
                tool_call("plan", "submit_execution_plan", plan),
                text_response("我猜这是 LAS。"),
                tool_call("identify", "inspect_asset", {"asset_id": "primary"}),
                final_response("已根据 Tool Result 识别资产。", "identify-result"),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            result = run_general_agent(
                client=client,
                user_request="识别资产",
                source_path=FIXTURES / "basic.las",
                output_dir=directory,
            )
            self.assertEqual(len(client.calls), 4)
            self.assertEqual(len(result.harness.record.attempts), 1)

    def test_cancel_request_stops_before_any_tool_attempt(self) -> None:
        plan = {
            "objective": "检查资产",
            "steps": [
                {
                    "step_id": "identify",
                    "tool": "inspect_asset",
                    "purpose": "检查资产",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)

            class CancellingModel(FakeModel):
                def complete(self, **request: Any) -> ModelResponse:
                    response = super().complete(**request)
                    FileRunStore(output).request_cancel("test cancellation")
                    return response

            client = CancellingModel([tool_call("plan", "submit_execution_plan", plan)])
            with self.assertRaises(GeneralAgentError):
                run_general_agent(
                    client=client,
                    user_request="识别资产",
                    source_path=FIXTURES / "basic.las",
                    output_dir=output,
                )
            record = FileRunStore(output).load()
            assert record is not None
            self.assertEqual(record.task_status, "cancelled")
            self.assertEqual(record.attempts, [])

    def test_las_multi_tool_plan_is_approved_executed_and_evidence_bound(self) -> None:
        plan = {
            "objective": "检查 LAS 类型并执行质量控制",
            "steps": [
                {
                    "step_id": "identify",
                    "tool": "inspect_asset",
                    "purpose": "确认资产类型和能力",
                },
                {
                    "step_id": "qc",
                    "tool": "run_well_log_qc",
                    "purpose": "执行确定性质量控制",
                    "depends_on": ["identify"],
                },
            ],
        }
        client = FakeModel(
            [
                tool_call("plan", "submit_execution_plan", plan),
                tool_call("inspect", "inspect_asset", {"asset_id": "primary"}),
                tool_call("qc", "run_well_log_qc", {"asset_id": "primary"}),
                final_response("已完成资产识别和质量控制；结论以返回的证据为准。", "qc-result"),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            result = run_general_agent(
                client=client,
                user_request="检查这个 LAS 文件的数据质量",
                source_path=FIXTURES / "basic.las",
                output_dir=directory,
            )
            answer = write_general_agent_answer(result, directory).read_text(encoding="utf-8")
            record = result.harness.record
            self.assertEqual(record.task_status, "completed_with_warnings")
            self.assertEqual(record.task_spec.workflow, "general-llm-agent")
            self.assertEqual(
                [item.tool_name for item in record.attempts],
                ["asset.inspect", "well_log.qc"],
            )
            self.assertEqual(record.agent_runs[0].protocol, "fake-transport")
            self.assertEqual(record.agent_runs[0].input_tokens, 40)
            self.assertEqual(record.agent_runs[0].output_tokens, 20)
            self.assertEqual(record.agent_runs[0].model_latency_ms, 28)
            self.assertIn("identify-result", answer)
            self.assertIn("qc-result", answer)
            self.assertIn("qc-validation", answer)
            self.assertIn("agent-claim-map", answer)
            self.assertIn("模型解释（不是独立 Evidence）", answer)
            self.assertEqual(len(record.context_snapshots), 4)
            self.assertTrue(all(item.context_sha256 for item in record.context_snapshots))
            self.assertIsNotNone(record.agent_checkpoint)
            self.assertFalse((Path(directory) / "agent-checkpoint.json").exists())
            self.assertTrue((Path(directory) / "candidate-plan.json").is_file())
            self.assertTrue((Path(directory) / "agent-trace.json").is_file())
            self.assertTrue(validate_run_directory(directory).valid)

        first_schema = client.calls[0]["tools"][0]["input_schema"]
        allowed = first_schema["properties"]["steps"]["items"]["properties"]["tool"]["enum"]
        self.assertIn("run_well_log_qc", allowed)
        self.assertNotIn("inspect_segy", allowed)
        self.assertEqual([item["name"] for item in client.calls[1]["tools"]], ["inspect_asset"])
        self.assertEqual(
            [item["name"] for item in client.calls[2]["tools"]],
            ["run_well_log_qc"],
        )
        self.assertEqual(
            [item["name"] for item in client.calls[3]["tools"]],
            ["submit_final_answer"],
        )
        for request in client.calls:
            serialized = json.dumps(request, ensure_ascii=False)
            self.assertNotIn(str((FIXTURES / "basic.las").resolve()), serialized)

    def test_table_plan_can_inspect_then_run_controlled_python(self) -> None:
        plan = {
            "objective": "读取结构后计算 GR 均值",
            "steps": [
                {
                    "step_id": "profile",
                    "tool": "inspect_table",
                    "purpose": "获得列结构",
                },
                {
                    "step_id": "compute",
                    "tool": "run_python_analysis",
                    "purpose": "计算聚合统计",
                    "depends_on": ["profile"],
                },
            ],
        }
        client = FakeModel(
            [
                tool_call("plan", "submit_execution_plan", plan),
                tool_call("profile", "inspect_table", {"asset_id": "primary"}),
                tool_call(
                    "compute",
                    "run_python_analysis",
                    {
                        "asset_id": "primary",
                        "purpose": "计算有效 GR 平均值",
                        "code": (
                            "values = [value for value in COLS['GR'] if value is not None]\n"
                            "RESULT = {'mean_gr': sum(values) / len(values)}"
                        ),
                    },
                ),
                final_response(
                    "探索性计算得到有效 GR 平均值 47.5。",
                    "compute-result",
                    "/data/result/mean_gr",
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            result = run_general_agent(
                client=client,
                user_request="计算 GR 平均值",
                source_path=FIXTURES / "well_log.csv",
                output_dir=directory,
            )
            self.assertEqual(
                [item.tool_name for item in result.harness.record.attempts],
                ["asset.inspect_table", "explore.python"],
            )
            model_feedback = client.calls[3]["messages"][-1]["content"][0]["content"]
            self.assertIn("47.5", model_feedback)
            self.assertNotIn(str(Path(directory).resolve()), model_feedback)
            self.assertIn("workspace://explore", model_feedback)
            self.assertTrue(validate_run_directory(directory).valid)

    def test_invalid_plan_cannot_bypass_capability_or_registry(self) -> None:
        invalid = {
            "objective": "错误地调用地震工具",
            "steps": [
                {
                    "step_id": "escape",
                    "tool": "inspect_segy",
                    "purpose": "不允许",
                }
            ],
        }
        valid = {
            "objective": "只检查资产",
            "steps": [
                {
                    "step_id": "identify",
                    "tool": "inspect_asset",
                    "purpose": "检查授权资产",
                }
            ],
        }
        client = FakeModel(
            [
                tool_call("bad-plan", "submit_execution_plan", invalid),
                tool_call("good-plan", "submit_execution_plan", valid),
                tool_call("identify", "inspect_asset", {"asset_id": "primary"}),
                final_response("资产已识别。", "identify-result"),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            result = run_general_agent(
                client=client,
                user_request="识别文件",
                source_path=FIXTURES / "basic.las",
                output_dir=directory,
            )
            rejection = client.calls[1]["messages"][-1]["content"][0]
            self.assertTrue(rejection["is_error"])
            self.assertIn("Plan rejected", rejection["content"])
            self.assertEqual(
                [item.tool_name for item in result.harness.record.attempts],
                ["asset.inspect"],
            )

    def test_final_claim_requires_a_resolvable_scalar_evidence_pointer(self) -> None:
        plan = {
            "objective": "检查资产",
            "steps": [
                {
                    "step_id": "identify",
                    "tool": "inspect_asset",
                    "purpose": "检查资产",
                }
            ],
        }
        invalid_final = tool_call(
            "invalid-final",
            "submit_final_answer",
            {
                "interpretation": "没有发现任何异常。",
                "claims": [
                    {
                        "evidence_id": "identify-result",
                        "selector": "/data/nonexistent_anomaly_check",
                    }
                ],
                "limitations": ["仅执行资产识别。"],
            },
        )
        client = FakeModel(
            [
                tool_call("plan", "submit_execution_plan", plan),
                tool_call("identify", "inspect_asset", {"asset_id": "primary"}),
                invalid_final,
                final_response(
                    "当前只确认了资产格式，不对异常情况作结论。",
                    "identify-result",
                    "/data/asset/format",
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            result = run_general_agent(
                client=client,
                user_request="识别资产",
                source_path=FIXTURES / "basic.las",
                output_dir=directory,
            )
            self.assertEqual(len(client.calls), 4)
            rejection = client.calls[3]["messages"][-1]["content"][0]
            self.assertTrue(rejection["is_error"])
            self.assertIn("cannot be resolved", rejection["content"])
            claim_map = next(
                item
                for item in result.harness.record.evidence
                if item.evidence_id == "agent-claim-map"
            )
            self.assertEqual(claim_map.value["claims"][0]["value"], "las")
            self.assertTrue(validate_run_directory(directory).valid)

    def test_large_tool_result_becomes_artifact_and_bounded_model_projection(self) -> None:
        class HugeProvider:
            provider_id = "huge"

            def definitions(self):
                return (
                    ToolDefinition(
                        name="asset.inspect",
                        version="9.9.9",
                        description="Return a deliberately large authorized result",
                        model_name="inspect_asset",
                        input_capabilities=("asset.inspect",),
                        input_schema={
                            "type": "object",
                            "properties": {"asset_id": {"const": "primary"}},
                            "required": ["asset_id"],
                            "additionalProperties": False,
                        },
                    ),
                )

            def execute(self, name, arguments, context):
                return ToolExecution(
                    GeoToolResult(
                        tool_name=name,
                        tool_version="9.9.9",
                        status="success",
                        data={"count": 5000, "blob": "x" * 5000},
                        provider=self.provider_id,
                    )
                )

        registry = ToolProviderRegistry()
        registry.register_provider(HugeProvider())
        plan = {
            "objective": "检查大型结果投影",
            "steps": [
                {
                    "step_id": "identify",
                    "tool": "inspect_asset",
                    "purpose": "生成大型结果",
                }
            ],
        }
        client = FakeModel(
            [
                tool_call("plan", "submit_execution_plan", plan),
                tool_call("identify", "inspect_asset", {"asset_id": "primary"}),
                final_response(
                    "完整结果已由 Harness 保存，模型只收到受限投影。",
                    "identify-result",
                    "/data/original_json_bytes",
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            result = run_general_agent(
                client=client,
                user_request="检查大型结果",
                source_path=FIXTURES / "basic.las",
                output_dir=directory,
                registry=registry,
                context_budget=ContextBudget(max_tool_result_bytes=256),
            )
            artifacts = {item.artifact_id: item for item in result.harness.record.artifacts}
            self.assertIn("identify-result-payload", artifacts)
            final_request = json.dumps(client.calls[2], ensure_ascii=False)
            self.assertIn("original_json_bytes", final_request)
            self.assertNotIn("x" * 100, final_request)
            self.assertTrue(validate_run_directory(directory).valid)

    def test_long_agent_history_is_compacted_within_every_recorded_budget(self) -> None:
        plan = {
            "objective": "检查资产",
            "steps": [
                {
                    "step_id": "identify",
                    "tool": "inspect_asset",
                    "purpose": "检查资产",
                }
            ],
        }
        client = FakeModel(
            [
                tool_call("plan", "submit_execution_plan", plan),
                *[
                    text_response(f"尚未调用工具的冗长说明 {index}：" + "解释" * 250)
                    for index in range(5)
                ],
                tool_call("identify", "inspect_asset", {"asset_id": "primary"}),
                final_response("已识别资产。", "identify-result", "/data/asset/format"),
            ]
        )
        budget = ContextBudget(
            max_total_tokens=3200,
            max_system_tokens=2000,
            max_tool_definition_tokens=1000,
            max_history_tokens=1100,
            max_tool_result_bytes=1024,
            recent_message_units=2,
            compaction_trigger_ratio=0.55,
        )
        with tempfile.TemporaryDirectory() as directory:
            result = run_general_agent(
                client=client,
                user_request="识别资产",
                source_path=FIXTURES / "basic.las",
                output_dir=directory,
                context_budget=budget,
            )
            record = result.harness.record
            self.assertTrue(record.compactions)
            self.assertTrue(
                all(
                    item.estimated_tokens <= item.budget.max_total_tokens
                    for item in record.context_snapshots
                )
            )
            self.assertIsNotNone(record.agent_checkpoint)
            assert record.agent_checkpoint is not None
            self.assertIn("CONTEXT_COMPACTION", json.dumps(record.agent_checkpoint.messages))
            self.assertTrue(validate_run_directory(directory).valid)

    def test_context_budget_failure_waits_before_model_and_can_resume(self) -> None:
        client = FakeModel([])
        tiny_budget = ContextBudget(
            max_total_tokens=100,
            max_system_tokens=1,
            max_tool_definition_tokens=10,
            max_history_tokens=10,
            max_tool_result_bytes=10,
            recent_message_units=1,
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(GeneralAgentError, "Context assembly failed"):
                run_general_agent(
                    client=client,
                    user_request="识别资产",
                    source_path=FIXTURES / "basic.las",
                    output_dir=directory,
                    context_budget=tiny_budget,
                )
            self.assertEqual(client.calls, [])
            waiting = FileRunStore(directory).load()
            assert waiting is not None and waiting.agent_checkpoint is not None
            self.assertEqual(waiting.task_status, "waiting_for_user")
            self.assertEqual(
                waiting.agent_checkpoint.model_error["category"],
                "context_budget",
            )
            plan = {
                "objective": "检查资产",
                "steps": [
                    {
                        "step_id": "identify",
                        "tool": "inspect_asset",
                        "purpose": "检查资产",
                    }
                ],
            }
            result = resume_general_agent(
                client=FakeModel(
                    [
                        tool_call("plan", "submit_execution_plan", plan),
                        tool_call("identify", "inspect_asset", {"asset_id": "primary"}),
                        final_response("已识别。", "identify-result", "/data/asset/format"),
                    ]
                ),
                output_dir=directory,
            )
            self.assertIn(
                result.harness.record.task_status,
                {"completed", "completed_with_warnings"},
            )
            self.assertTrue(validate_run_directory(directory).valid)

    def test_interrupted_compaction_commit_recovers_last_authoritative_checkpoint(self) -> None:
        class Terminated(BaseException):
            pass

        plan = {
            "objective": "检查资产",
            "steps": [
                {
                    "step_id": "identify",
                    "tool": "inspect_asset",
                    "purpose": "检查资产",
                }
            ],
        }
        client = FakeModel(
            [
                tool_call("plan", "submit_execution_plan", plan),
                text_response("先产生一轮需要压缩的解释。"),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            original_atomic_json = run_store_module._atomic_json

            def terminate_before_commit_pointer(path, payload):
                events = root / "events.jsonl"
                if (
                    path == root / "checkpoint.json"
                    and events.is_file()
                    and "context_compacted" in events.read_text(encoding="utf-8")
                ):
                    raise Terminated()
                return original_atomic_json(path, payload)

            with (
                mock.patch.object(
                    run_store_module,
                    "_atomic_json",
                    new=terminate_before_commit_pointer,
                ),
                self.assertRaises(Terminated),
            ):
                run_general_agent(
                    client=client,
                    user_request="识别资产",
                    source_path=FIXTURES / "basic.las",
                    output_dir=directory,
                    force_context_compaction=True,
                    context_budget=ContextBudget(recent_message_units=2),
                )
            committed = FileRunStore(root).load()
            assert committed is not None and committed.agent_checkpoint is not None
            self.assertEqual(committed.compactions, [])
            self.assertIsNotNone(committed.agent_checkpoint.approved_plan)
            result = resume_general_agent(
                client=FakeModel(
                    [
                        tool_call("identify", "inspect_asset", {"asset_id": "primary"}),
                        final_response("已识别。", "identify-result", "/data/asset/format"),
                    ]
                ),
                output_dir=root,
            )
            self.assertEqual(result.harness.record.task_status, "completed")
            self.assertTrue(validate_run_directory(directory).valid)

    def test_interruption_after_model_response_requires_manual_resume(self) -> None:
        class Terminated(BaseException):
            pass

        plan = {
            "objective": "检查资产",
            "steps": [
                {
                    "step_id": "identify",
                    "tool": "inspect_asset",
                    "purpose": "检查资产",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(
                    TaskRunSession,
                    "record_agent_turn",
                    side_effect=Terminated(),
                ),
                self.assertRaises(Terminated),
            ):
                run_general_agent(
                    client=FakeModel([tool_call("plan", "submit_execution_plan", plan)]),
                    user_request="识别资产",
                    source_path=FIXTURES / "basic.las",
                    output_dir=directory,
                )
            interrupted = FileRunStore(directory).load()
            assert interrupted is not None and interrupted.agent_checkpoint is not None
            self.assertEqual(interrupted.task_status, "executing")
            self.assertEqual(interrupted.agent_checkpoint.turn, 0)
            result = resume_general_agent(
                client=FakeModel(
                    [
                        tool_call("plan-again", "submit_execution_plan", plan),
                        tool_call("identify", "inspect_asset", {"asset_id": "primary"}),
                        final_response("已识别。", "identify-result", "/data/asset/format"),
                    ]
                ),
                output_dir=directory,
            )
            self.assertEqual(
                [item.status for item in result.harness.record.agent_runs],
                ["incomplete", "completed"],
            )
            self.assertTrue(validate_run_directory(directory).valid)

    def test_model_failure_waits_without_deterministic_fallback_and_can_resume(self) -> None:
        failure = ModelClientError("credential rejected", category="credentials", retryable=False)
        with tempfile.TemporaryDirectory() as directory:
            initial_client = FakeModel([failure])
            with self.assertRaises(GeneralAgentError):
                run_general_agent(
                    client=initial_client,
                    user_request="检查文件",
                    source_path=FIXTURES / "basic.las",
                    output_dir=directory,
                )
            waiting = FileRunStore(directory).load()
            assert waiting is not None
            self.assertEqual(waiting.task_status, "waiting_for_user")
            self.assertEqual(waiting.attempts, [])
            self.assertIsNotNone(waiting.agent_checkpoint)
            self.assertFalse((Path(directory) / "agent-checkpoint.json").exists())
            self.assertEqual(initial_client.bound_sessions, [waiting.task_id])

            plan = {
                "objective": "检查资产",
                "steps": [
                    {
                        "step_id": "identify",
                        "tool": "inspect_asset",
                        "purpose": "检查资产",
                    }
                ],
            }
            resumed_client = FakeModel(
                [
                    tool_call("plan", "submit_execution_plan", plan),
                    tool_call("identify", "inspect_asset", {"asset_id": "primary"}),
                    final_response("已根据证据识别资产。", "identify-result"),
                ]
            )
            result = resume_general_agent(
                client=resumed_client,
                output_dir=directory,
            )
            self.assertEqual(resumed_client.bound_sessions, [waiting.task_id])
            self.assertIn(
                result.harness.record.task_status,
                {"completed", "completed_with_warnings"},
            )
            self.assertEqual(
                [item.status for item in result.harness.record.agent_runs],
                ["incomplete", "completed"],
            )
            self.assertEqual(
                [item.tool_name for item in result.harness.record.attempts],
                ["asset.inspect"],
            )
            self.assertTrue(validate_run_directory(directory).valid)

    def test_legacy_agent_checkpoint_is_read_once_and_migrated_into_run_store(self) -> None:
        failure = ModelClientError("offline", category="network", retryable=False)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(GeneralAgentError):
                run_general_agent(
                    client=FakeModel([failure]),
                    user_request="识别文件",
                    source_path=FIXTURES / "basic.las",
                    output_dir=root,
                )
            store = FileRunStore(root)
            record = store.load()
            assert record is not None and record.agent_checkpoint is not None
            checkpoint = record.agent_checkpoint
            legacy = {
                "schema_version": "1.0",
                "task_id": checkpoint.task_id,
                "turn": checkpoint.turn,
                "messages": list(checkpoint.messages),
                "approved_plan": checkpoint.approved_plan,
                "approved_plan_id": checkpoint.approved_plan_id,
                "completed_steps": list(checkpoint.completed_steps),
                "evidence_ids": list(checkpoint.evidence_ids),
                "model_error": checkpoint.model_error,
                "resume_policy": checkpoint.resume_policy,
            }
            (root / "agent-checkpoint.json").write_text(
                json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
            )
            record.agent_checkpoint = None
            store.save(record)
            plan = {
                "objective": "检查资产",
                "steps": [
                    {
                        "step_id": "identify",
                        "tool": "inspect_asset",
                        "purpose": "检查资产",
                    }
                ],
            }
            result = resume_general_agent(
                client=FakeModel(
                    [
                        tool_call("plan", "submit_execution_plan", plan),
                        tool_call("identify", "inspect_asset", {"asset_id": "primary"}),
                        final_response("已识别。", "identify-result", "/data/asset/format"),
                    ]
                ),
                output_dir=root,
            )
            self.assertIsNotNone(result.harness.record.agent_checkpoint)
            self.assertTrue((root / "agent-checkpoint.json").is_file())
            events = (root / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("agent_checkpoint_migrated", events)
            self.assertTrue(validate_run_directory(root).valid)


if __name__ == "__main__":
    unittest.main()

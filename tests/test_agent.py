from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from typing import Any

from geophysagent.agent import AgentRunError, run_las_agent, write_agent_answer
from geophysagent.model import ModelResponse
from geophysagent.run_inspection import validate_run_directory
from geophysagent.reference_eval import ReferenceCase, evaluate_reference_run


FIXTURE = Path(__file__).parent / "fixtures" / "basic.las"


class FakeModelClient:
    provider = "fake"
    model = "fake-model"

    def __init__(self, responses: list[ModelResponse]):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def complete(self, **request: Any) -> ModelResponse:
        self.calls.append(copy.deepcopy(request))
        return self.responses.pop(0)


class AgentTests(unittest.TestCase):
    def test_agent_must_use_authorized_tool_before_answering(self) -> None:
        client = FakeModelClient(
            [
                ModelResponse(
                    content=(
                        {
                            "type": "tool_use",
                            "id": "call-1",
                            "name": "run_las_qc",
                            "input": {"asset_id": "primary"},
                        },
                    ),
                    stop_reason="tool_use",
                    model="fake-model",
                ),
                ModelResponse(
                    content=(
                        {
                            "type": "text",
                            "text": "数据包含 3 条曲线；GR 缺失率为 25%。未进行储层解释。",
                        },
                    ),
                    stop_reason="end_turn",
                    model="fake-model",
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            result = run_las_agent(
                client=client,
                user_request="检查这个文件",
                source_path=FIXTURE,
                output_dir=directory,
            )
            answer_path = write_agent_answer(result, directory)
            self.assertTrue((Path(directory) / "result.json").is_file())
            self.assertTrue((Path(directory) / "run-record.json").is_file())
            self.assertTrue((Path(directory) / "artifact-manifest.json").is_file())
            self.assertIn("GR 缺失率", answer_path.read_text(encoding="utf-8"))
            self.assertEqual(result.harness.record.task_spec.workflow, "well-log-agent")
            self.assertEqual(len(result.harness.record.agent_runs), 1)
            self.assertEqual(result.harness.record.agent_runs[0].status, "completed")
            self.assertEqual(
                [item.tool_name for item in result.harness.record.attempts],
                ["las.run_qc"],
            )
            self.assertIn("final_answer", {item.role for item in result.harness.record.artifacts})
            self.assertEqual(len(result.harness.record.validations), 4)
            self.assertTrue(validate_run_directory(directory).valid)
            self.assertEqual(result.analysis.status, "completed_with_warnings")
            reference = evaluate_reference_run(
                ReferenceCase(
                    case_id="fake-las-agent",
                    run_directory=".",
                    expected_workflow="well-log-agent",
                    required_capabilities=("well_log.qc",),
                    allowed_tools=("las.run_qc",),
                    required_tools=("las.run_qc",),
                    required_evidence_ids=(
                        "las-analysis",
                        "las-validation",
                        "las-result-artifact",
                        "las-supporting-report",
                        "las-figure",
                        "las-figure-metadata",
                        "agent-final-answer",
                    ),
                    required_artifact_roles=(
                        "data",
                        "supporting_report",
                        "figure",
                        "figure_metadata",
                        "final_answer",
                    ),
                    required_report_sections=(
                        "观测事实",
                        "确定性规则判断",
                        "模型解释",
                        "假设",
                        "未解决问题",
                        "能力边界",
                    ),
                ),
                directory,
            )
            self.assertTrue(reference.passed, [item.message for item in reference.checks])
        self.assertEqual(result.turns, 2)
        tool_result = client.calls[1]["messages"][-1]["content"][0]
        self.assertEqual(tool_result["tool_use_id"], "call-1")
        self.assertIn('"source_path":"asset://primary"', tool_result["content"])
        self.assertNotIn(str(FIXTURE.resolve()), tool_result["content"])

    def test_agent_rejects_unregistered_asset(self) -> None:
        responses = [
            ModelResponse(
                content=(
                    {
                        "type": "tool_use",
                        "id": f"call-{index}",
                        "name": "run_las_qc",
                        "input": {"asset_id": "C:/secret.las"},
                    },
                ),
                stop_reason="tool_use",
                model="fake-model",
            )
            for index in range(4)
        ]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(AgentRunError):
                run_las_agent(
                    client=FakeModelClient(responses),
                    user_request="读取其他文件",
                    source_path=FIXTURE,
                    output_dir=directory,
                )
            self.assertFalse((Path(directory) / "result.json").exists())


if __name__ == "__main__":
    unittest.main()

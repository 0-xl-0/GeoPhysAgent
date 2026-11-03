from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from typing import Any

from geophysagent.explore_agent import ExploreAgentError, run_explore_agent, write_explore_answer
from geophysagent.model import ModelResponse
from geophysagent.run_inspection import validate_run_directory


FIXTURE = Path(__file__).parent / "fixtures" / "well_log.csv"


class FakeExploreModel:
    provider = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses = [
            ModelResponse(
                content=(
                    {
                        "type": "tool_use",
                        "id": "explore-1",
                        "name": "run_python_analysis",
                        "input": {
                            "asset_id": "primary",
                            "purpose": "计算有效 GR 均值",
                            "code": "values = [r['GR'] for r in DATA['rows'] if r['GR'] is not None]\nRESULT = {'mean_gr': sum(values) / len(values)}",
                        },
                    },
                ),
                stop_reason="tool_use",
                model="fake-model",
            ),
            ModelResponse(
                content=(
                    {
                        "type": "text",
                        "text": "探索结果显示有效 GR 均值为 47.5。该结果尚未提升为正式工具。",
                    },
                ),
                stop_reason="end_turn",
                model="fake-model",
            ),
        ]

    def complete(self, **request: Any) -> ModelResponse:
        self.calls.append(copy.deepcopy(request))
        return self.responses.pop(0)


class ExploreAgentTests(unittest.TestCase):
    def test_llm_generated_code_uses_controlled_provider(self) -> None:
        client = FakeExploreModel()
        with tempfile.TemporaryDirectory() as directory:
            result = run_explore_agent(
                client=client,
                user_request="计算 GR 的平均值",
                source_path=FIXTURE,
                output_dir=directory,
            )
            answer = write_explore_answer(result, directory)
            self.assertIn("47.5", answer.read_text(encoding="utf-8"))
            self.assertTrue((result.run.workspace / "analysis.py").is_file())
            self.assertTrue((Path(directory) / "explore-agent-run.json").is_file())
            self.assertEqual(result.run.result["mean_gr"], 47.5)
            self.assertEqual(result.harness.record.task_spec.workflow, "explore-agent")
            self.assertEqual(len(result.harness.record.agent_runs), 1)
            self.assertEqual(result.harness.record.agent_runs[0].status, "completed")
            self.assertEqual(
                [item.tool_name for item in result.harness.record.attempts],
                ["explore.run_python"],
            )
            self.assertIn("final_answer", {item.role for item in result.harness.record.artifacts})
            self.assertTrue(validate_run_directory(directory).valid)
        self.assertIn('DATA["rows"]', client.calls[0]["system"])
        tool_result = client.calls[1]["messages"][-1]["content"][0]["content"]
        self.assertTrue(client.calls[1]["tools"])
        self.assertNotIn(str(FIXTURE.resolve()), tool_result)
        self.assertNotIn(str(result.run.workspace), tool_result)

    def test_failed_workspace_path_is_not_sent_to_model(self) -> None:
        client = FakeExploreModel()
        client.responses = [
            ModelResponse(
                content=(
                    {
                        "type": "tool_use",
                        "id": "bad-run",
                        "name": "run_python_analysis",
                        "input": {
                            "asset_id": "primary",
                            "purpose": "产生不可序列化结果",
                            "code": "RESULT = set([1])",
                        },
                    },
                ),
                stop_reason="tool_use",
                model="fake-model",
            ),
            *client.responses,
        ]
        with tempfile.TemporaryDirectory() as directory:
            result = run_explore_agent(
                client=client,
                user_request="计算 GR 的平均值",
                source_path=FIXTURE,
                output_dir=directory,
            )
            failed_tool_result = client.calls[1]["messages"][-1]["content"][0]["content"]
            self.assertNotIn(str(Path(directory).resolve()), failed_tool_result)
            self.assertIn("TypeError", failed_tool_result)
            self.assertIn('DATA["rows"]', failed_tool_result)
            self.assertEqual(result.run.result["mean_gr"], 47.5)

    def test_wrong_data_shape_is_rejected_with_actionable_retry(self) -> None:
        client = FakeExploreModel()
        client.responses = [
            ModelResponse(
                content=(
                    {
                        "type": "tool_use",
                        "id": "wrong-contract",
                        "name": "run_python_analysis",
                        "input": {
                            "asset_id": "primary",
                            "purpose": "读取深度列",
                            "code": "RESULT = {'depth': DATA['DEPTH']}",
                        },
                    },
                ),
                stop_reason="tool_use",
                model="fake-model",
            ),
            ModelResponse(
                content=(
                    {
                        "type": "tool_use",
                        "id": "correct-contract",
                        "name": "run_python_analysis",
                        "input": {
                            "asset_id": "primary",
                            "purpose": "读取深度列",
                            "code": "RESULT = {'depth': COLS['MD']}",
                        },
                    },
                ),
                stop_reason="tool_use",
                model="fake-model",
            ),
            ModelResponse(
                content=(
                    {
                        "type": "text",
                        "text": "方法是通过 COLS 读取深度列，共获得三个深度值；结果仅用于探索。",
                    },
                ),
                stop_reason="end_turn",
                model="fake-model",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            result = run_explore_agent(
                client=client,
                user_request="读取深度列",
                source_path=FIXTURE,
                output_dir=directory,
            )
            retry = client.calls[1]["messages"][-1]["content"][0]["content"]
            self.assertIn("COLS", retry)
            self.assertEqual(sorted(result.run.result["depth"]), [1000.0, 1000.5, 1001.0])
            proposals = list((Path(directory) / "explore-agent-attempts").glob("*.py"))
            self.assertEqual(len(proposals), 2)

    def test_success_without_final_answer_is_incomplete_not_completed(self) -> None:
        client = FakeExploreModel()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExploreAgentError):
                run_explore_agent(
                    client=client,
                    user_request="计算 GR 的平均值",
                    source_path=FIXTURE,
                    output_dir=directory,
                    max_turns=1,
                )
            trace = (Path(directory) / "explore-agent-run.json").read_text(encoding="utf-8")
            self.assertIn('"authority": "diagnostic_only"', trace)
            task_record = (Path(directory) / "run-record.json").read_text(encoding="utf-8")
            self.assertIn('"execution_status": "completed"', task_record)
            self.assertIn('"task_status": "incomplete"', task_record)

    def test_multiple_successful_analysis_steps_are_allowed(self) -> None:
        client = FakeExploreModel()
        client.responses = [
            ModelResponse(
                content=(
                    {
                        "type": "tool_use",
                        "id": "inspect-step",
                        "name": "run_python_analysis",
                        "input": {
                            "asset_id": "primary",
                            "purpose": "检查列范围",
                            "code": "RESULT = {'columns': COLUMN_NAMES}",
                        },
                    },
                ),
                stop_reason="tool_use",
                model="fake-model",
            ),
            ModelResponse(
                content=(
                    {
                        "type": "tool_use",
                        "id": "compute-step",
                        "name": "run_python_analysis",
                        "input": {
                            "asset_id": "primary",
                            "purpose": "计算 GR 均值",
                            "code": "values = [v for v in COLS['GR'] if v is not None]\nRESULT = {'mean_gr': sum(values) / len(values)}",
                        },
                    },
                ),
                stop_reason="tool_use",
                model="fake-model",
            ),
            ModelResponse(
                content=(
                    {
                        "type": "text",
                        "text": "方法为过滤空值后计算均值，GR 均值为 47.5；结果为探索级。",
                    },
                ),
                stop_reason="end_turn",
                model="fake-model",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            result = run_explore_agent(
                client=client,
                user_request="计算 GR 均值",
                source_path=FIXTURE,
                output_dir=directory,
            )
            trace = (Path(directory) / "explore-agent-run.json").read_text(encoding="utf-8")
            self.assertEqual(result.run.result["mean_gr"], 47.5)
            self.assertEqual(trace.count('"status": "completed"'), 2)

    def test_textual_tool_markup_is_not_accepted_as_final_answer(self) -> None:
        client = FakeExploreModel()
        markup = (
            '<minimax:tool_call><invoke name="run_python_analysis">'
            '<parameter name="asset_id">primary</parameter>'
            '<parameter name="purpose">继续计算</parameter>'
            '<parameter name="code">RESULT = {"ok": True}</parameter>'
            "</invoke></minimax:tool_call>"
        )
        client.responses = [
            client.responses[0],
            ModelResponse(
                content=({"type": "text", "text": markup},),
                stop_reason="end_turn",
                model="fake-model",
            ),
            ModelResponse(
                content=(
                    {
                        "type": "text",
                        "text": "方法为过滤空值后计算均值，GR 均值为 47.5；该结论仅为探索级。",
                    },
                ),
                stop_reason="end_turn",
                model="fake-model",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            result = run_explore_agent(
                client=client,
                user_request="计算 GR 均值",
                source_path=FIXTURE,
                output_dir=directory,
            )
            self.assertEqual(result.turns, 3)
            self.assertNotIn("tool_call", result.answer)

    def test_existing_explore_output_is_not_reused(self) -> None:
        client = FakeExploreModel()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "explore-agent-run.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ExploreAgentError) as raised:
                run_explore_agent(
                    client=client,
                    user_request="计算 GR 均值",
                    source_path=FIXTURE,
                    output_dir=output,
                )
            self.assertIn("new --output", str(raised.exception))
            self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path

from geophysagent.agent import run_las_agent
from geophysagent.config import load_model_profile
from geophysagent.explore_agent import run_explore_agent
from geophysagent.general_agent import run_general_agent
from geophysagent.model import create_model_client
from geophysagent.run_inspection import validate_run_directory
from geophysagent.reference_eval import (
    ReferenceCase,
    ReferenceSuite,
    evaluate_reference_suite,
    write_reference_evaluation,
)


RUN_ONLINE = (
    os.environ.get("GEOPHYSAGENT_RUN_ONLINE_TESTS") == "1"
    or os.environ.get("GEOPHYSAGENT_RUN_MINIMAX_TESTS") == "1"
)
FIXTURES = Path(__file__).parent / "fixtures"


@unittest.skipUnless(
    RUN_ONLINE,
    "set GEOPHYSAGENT_RUN_ONLINE_TESTS=1 and the selected profile credential to run paid tests",
)
class MiniMaxOnlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        config = Path(os.environ.get("GEOPHYSAGENT_ONLINE_CONFIG", "geophysagent.toml"))
        profile_name = os.environ.get("GEOPHYSAGENT_ONLINE_PROFILE")
        cls.profile = load_model_profile(config, profile_name)
        cls.client = create_model_client(cls.profile)
        cls.root = Path(
            os.environ.get("GEOPHYSAGENT_ONLINE_OUTPUT", "runs/online-minimax")
        ).resolve()
        cls.root.mkdir(parents=True, exist_ok=True)

    def test_real_minimax_las_agent(self) -> None:
        output = self._output("las-agent")
        result = run_las_agent(
            client=self.client,
            user_request="检查这个 LAS 文件的数据质量并说明主要问题。必须使用提供的质量控制工具。",
            source_path=FIXTURES / "basic.las",
            output_dir=output,
            max_turns=6,
        )
        self.assertIn(
            result.harness.record.task_status,
            {"completed", "completed_with_warnings"},
        )
        self.assertEqual(
            [item.tool_name for item in result.harness.record.attempts],
            ["las.run_qc"],
        )
        self.assertTrue(validate_run_directory(output).valid)
        reference = evaluate_reference_suite(
            ReferenceSuite(
                suite_id="minimax-las-reference",
                version="1.0.0",
                cases=(
                    ReferenceCase(
                        case_id="las-agent",
                        run_directory=output.name,
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
                ),
            ),
            output.parent,
        )
        self.assertTrue(reference.passed)
        write_reference_evaluation(reference, output / "reference-evaluation.json")
        self._record("las-agent", result.harness.record)

    def test_real_minimax_table_explore(self) -> None:
        output = self._output("table-explore")
        result = run_explore_agent(
            client=self.client,
            user_request="计算 GR 列非空值的平均值，并清楚标注这是探索性结果。",
            source_path=FIXTURES / "well_log.csv",
            output_dir=output,
            max_turns=8,
        )
        self.assertIn(
            result.harness.record.task_status,
            {"completed", "completed_with_warnings"},
        )
        self.assertTrue(
            any(item.tool_name == "explore.run_python" for item in result.harness.record.attempts)
        )
        self.assertTrue(validate_run_directory(output).valid)
        self._record("table-explore", result.harness.record)

    def test_real_minimax_general_multi_tool(self) -> None:
        output = self._output("general-multi-tool")
        result = run_general_agent(
            client=self.client,
            user_request=(
                "先识别这个 LAS 资产，再执行测井质量控制，然后基于两个 Tool Result "
                "给出带 Evidence ID 的中文结论；候选计划必须包含两个依赖步骤。"
            ),
            source_path=FIXTURES / "basic.las",
            output_dir=output,
            max_turns=10,
        )
        tools = [item.tool_name for item in result.harness.record.attempts]
        self.assertIn("asset.inspect", tools)
        self.assertIn("well_log.qc", tools)
        self.assertGreaterEqual(len(tools), 2)
        self.assertTrue(result.evidence_ids)
        self.assertTrue(result.harness.record.context_snapshots)
        self.assertTrue(
            any(item.evidence_id == "agent-claim-map" for item in result.harness.record.evidence)
        )
        self.assertTrue(validate_run_directory(output).valid)
        self._record("general-multi-tool", result.harness.record)

    def _output(self, label: str) -> Path:
        return self.root / f"{label}-{uuid.uuid4().hex[:10]}"

    def _record(self, scenario: str, record) -> None:
        usage = {
            "input_tokens": sum(item.input_tokens for item in record.agent_runs),
            "output_tokens": sum(item.output_tokens for item in record.agent_runs),
            "latency_ms": sum(item.model_latency_ms for item in record.agent_runs),
            "estimated_cost_usd": next(
                (
                    item.estimated_cost_usd
                    for item in reversed(record.agent_runs)
                    if item.estimated_cost_usd is not None
                ),
                None,
            ),
        }
        entry = {
            "at": datetime.now(UTC).isoformat(),
            "scenario": scenario,
            "provider": self.profile.provider,
            "model": self.profile.model,
            "protocol": self.profile.protocol,
            "task_id": record.task_id,
            "task_status": record.task_status,
            "tool_names": [item.tool_name for item in record.attempts],
            "evidence_ids": [item.evidence_id for item in record.evidence],
            "usage": usage,
            "context_snapshot_count": len(record.context_snapshots),
            "compaction_count": len(record.compactions),
            "privacy": "no API key, auth header, or full model response stored",
        }
        with (self.root / "online-summary.jsonl").open(
            "a", encoding="utf-8", newline="\n"
        ) as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import inspect
import tempfile
import unittest
from pathlib import Path

from geophysagent.application import execute_las_qc_task, execute_segy_inspection_task
from geophysagent.assets import register_asset
from geophysagent.cli import main
from geophysagent.reference_eval import (
    ReferenceAssertion,
    ReferenceCase,
    ReferenceSuite,
    evaluate_reference_run,
    evaluate_reference_suite,
    load_reference_suite,
)
import geophysagent.reference_eval as reference_eval_module
from tests.segy_factory import write_synthetic_segy


LAS_FIXTURE = Path(__file__).parent / "fixtures" / "las_reference" / "normal.las"
VALIDATORS = (
    "core.data.validation_evidence",
    "core.process.required_evidence",
    "core.result.artifact_integrity",
    "core.result.final_artifact_evidence",
)


class ReferenceEvaluationTests(unittest.TestCase):
    def test_evaluator_has_no_las_parser_or_tool_name_branch(self) -> None:
        source = inspect.getsource(reference_eval_module)
        self.assertNotIn("geophysagent.las", source)
        self.assertNotIn('tool_name == "las.', source)

    def test_las_case_evaluates_all_contract_layers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "las"
            execute_las_qc_task(
                asset=register_asset(LAS_FIXTURE),
                request="运行 LAS 参考质控",
                output_dir=output,
            )
            case = _las_case("las")
            evaluation = evaluate_reference_run(case, output)
        self.assertTrue(evaluation.passed, [item.message for item in evaluation.checks])
        self.assertEqual(evaluation.metrics["evidence_completeness"], 1.0)
        self.assertEqual(evaluation.metrics["artifact_completeness"], 1.0)

    def test_deterministic_las_evidence_hash_is_stable_between_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = execute_las_qc_task(
                asset=register_asset(LAS_FIXTURE),
                request="运行 LAS 参考质控",
                output_dir=root / "first",
            )[1]
            second = execute_las_qc_task(
                asset=register_asset(LAS_FIXTURE),
                request="运行 LAS 参考质控",
                output_dir=root / "second",
            )[1]
            first_hash = next(
                item.value_sha256
                for item in first.record.evidence
                if item.evidence_id == "las-analysis"
            )
            second_hash = next(
                item.value_sha256
                for item in second.record.evidence
                if item.evidence_id == "las-analysis"
            )
        self.assertEqual(first_hash, second_hash)

    def test_cross_domain_suite_uses_same_generic_evaluator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            execute_las_qc_task(
                asset=register_asset(LAS_FIXTURE),
                request="运行 LAS 参考质控",
                output_dir=root / "las",
            )
            source = write_synthetic_segy(
                root / "source.sgy",
                traces=[[1.0, 2.0, 3.0], [2.0, 3.0, 4.0]],
                binary_samples=3,
            )
            execute_segy_inspection_task(
                asset=register_asset(source),
                request="检查 SEG-Y",
                output_dir=root / "segy",
            )
            suite = ReferenceSuite(
                suite_id="cross-domain",
                version="1.0.0",
                cases=(
                    _las_case("las"),
                    ReferenceCase(
                        case_id="segy",
                        run_directory="segy",
                        expected_workflow="segy-readonly-inspection",
                        required_capabilities=("seismic.segy.inspect",),
                        allowed_tools=("seismic.inspect_segy",),
                        required_tools=("seismic.inspect_segy",),
                        required_evidence_ids=(
                            "segy-binary-header",
                            "segy-trace-summary",
                            "segy-amplitude-statistics",
                            "segy-validation",
                            "segy-result-artifact",
                            "segy-final-report",
                        ),
                        required_validator_ids=VALIDATORS,
                        required_artifact_roles=("data", "final_report"),
                        required_report_sections=(
                            "观测事实",
                            "确定性规则判断",
                            "模型解释",
                            "假设",
                            "未解决问题",
                            "能力边界",
                        ),
                        assertions=(
                            ReferenceAssertion(
                                "trace-count",
                                "evidence:segy-trace-summary",
                                "/trace_count",
                                "exact",
                                2,
                            ),
                        ),
                    ),
                ),
            )
            evaluation = evaluate_reference_suite(suite, root)
        self.assertTrue(evaluation.passed)
        self.assertEqual(evaluation.passed_case_count, 2)
        self.assertEqual(evaluation.metrics["plan_valid_rate"], 1.0)
        self.assertEqual(evaluation.metrics["task_success_rate"], 1.0)

    def test_wrong_domain_assertion_fails_without_changing_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "las"
            execute_las_qc_task(
                asset=register_asset(LAS_FIXTURE),
                request="运行 LAS 参考质控",
                output_dir=output,
            )
            case = ReferenceCase(
                case_id="wrong",
                run_directory="las",
                expected_workflow="well-log-qc",
                assertions=(
                    ReferenceAssertion(
                        "wrong-row-count",
                        "evidence:las-analysis",
                        "/tool_results/0/data/row_count",
                        "exact",
                        999,
                    ),
                ),
            )
            evaluation = evaluate_reference_run(case, output)
        self.assertFalse(evaluation.passed)
        self.assertFalse(
            next(item for item in evaluation.checks if item.code == "wrong-row-count").passed
        )

    def test_bad_pointer_rule_version_and_figure_source_fail_by_layer(self) -> None:
        assertions = (
            ReferenceAssertion(
                "bad-pointer",
                "evidence:las-analysis",
                "/not-present",
                "exact",
                1,
            ),
            ReferenceAssertion(
                "bad-rule-version",
                "evidence:las-analysis",
                "/tool_results/2/data/rule_pack/version",
                "exact",
                "99.0.0",
            ),
            ReferenceAssertion(
                "bad-figure-source",
                "artifact:figure_metadata",
                "/source_result_ids",
                "contains",
                "wrong.source",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "las"
            execute_las_qc_task(
                asset=register_asset(LAS_FIXTURE),
                request="运行 LAS 参考质控",
                output_dir=output,
            )
            evaluation = evaluate_reference_run(
                ReferenceCase(
                    case_id="tampered-expectations",
                    run_directory="las",
                    expected_workflow="well-log-qc",
                    assertions=assertions,
                ),
                output,
            )
        observed = {item.code: item.passed for item in evaluation.checks}
        self.assertFalse(evaluation.passed)
        self.assertFalse(observed["bad-pointer"])
        self.assertFalse(observed["bad-rule-version"])
        self.assertFalse(observed["bad-figure-source"])

    def test_json_suite_and_cli_write_machine_readable_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            execute_las_qc_task(
                asset=register_asset(LAS_FIXTURE),
                request="运行 LAS 参考质控",
                output_dir=root / "las",
            )
            suite_path = root / "suite.json"
            suite_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "suite_id": "cli-eval",
                        "version": "1.0.0",
                        "cases": [
                            {
                                "case_id": "las",
                                "run_directory": "las",
                                "expected_workflow": "well-log-qc",
                                "required_tools": ["las.run_qc"],
                                "required_artifact_roles": [
                                    "data",
                                    "final_report",
                                    "figure",
                                    "figure_metadata",
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            loaded = load_reference_suite(suite_path)
            self.assertEqual(loaded.suite_id, "cli-eval")
            evaluation_path = root / "evaluation.json"
            status = main(
                [
                    "evaluate",
                    "--suite",
                    str(suite_path),
                    "--runs-root",
                    str(root),
                    "--output",
                    str(evaluation_path),
                ]
            )
            payload = json.loads(evaluation_path.read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertTrue(payload["passed"])


def _las_case(run_directory: str) -> ReferenceCase:
    return ReferenceCase(
        case_id="las",
        run_directory=run_directory,
        expected_workflow="well-log-qc",
        required_capabilities=("well_log.qc",),
        allowed_tools=("las.run_qc",),
        required_tools=("las.run_qc",),
        required_evidence_ids=(
            "las-analysis",
            "las-validation",
            "las-result-artifact",
            "las-final-report",
            "las-figure",
            "las-figure-metadata",
        ),
        required_validator_ids=VALIDATORS,
        required_artifact_roles=("data", "final_report", "figure", "figure_metadata"),
        required_report_sections=(
            "观测事实",
            "确定性规则判断",
            "模型解释",
            "假设",
            "未解决问题",
            "能力边界",
        ),
        assertions=(
            ReferenceAssertion(
                "row-count",
                "evidence:las-analysis",
                "/tool_results/0/data/row_count",
                "exact",
                8,
            ),
            ReferenceAssertion(
                "rule-pack-version",
                "evidence:las-analysis",
                "/tool_results/2/data/rule_pack/version",
                "exact",
                "1.0.0",
            ),
            ReferenceAssertion(
                "figure-sources",
                "artifact:figure_metadata",
                "/source_result_ids",
                "contains",
                "las.quality-rules",
            ),
        ),
        forbidden_report_phrases=("确定为油层", "保证可用于解释"),
    )


if __name__ == "__main__":
    unittest.main()

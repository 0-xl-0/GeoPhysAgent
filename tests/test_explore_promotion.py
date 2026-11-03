from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geophysagent.assets import register_asset
from geophysagent.explore import (
    ExploreExecutionError,
    ExplorePolicyError,
    run_explore_code,
    validate_explore_data_access,
    validate_explore_code,
)
from geophysagent.promotion import promote_explore_run
from geophysagent.tabular import load_tabular_asset


FIXTURE = Path(__file__).parent / "fixtures" / "well_log.csv"


class ExploreAndPromotionTests(unittest.TestCase):
    def test_controlled_code_runs_and_is_tracked(self) -> None:
        asset = register_asset(FIXTURE)
        dataset = load_tabular_asset(asset)
        code = "values = [row['GR'] for row in DATA['rows'] if row['GR'] is not None]\nRESULT = {'mean_gr': sum(values) / len(values)}"
        with tempfile.TemporaryDirectory() as directory:
            run = run_explore_code(
                data=dataset.to_model_data(),
                code=code,
                purpose="Compute mean GR",
                output_dir=directory,
                input_sha256=asset.sha256,
            )
            self.assertEqual(run.result["mean_gr"], 47.5)
            self.assertTrue((run.workspace / "run-record.json").is_file())
            candidate = promote_explore_run(
                run.workspace, name="mean-gr", destination=Path(directory) / "candidates"
            )
            self.assertTrue((candidate / "candidate-tool.json").is_file())
            self.assertIn(
                '"active": false', (candidate / "candidate-tool.json").read_text(encoding="utf-8")
            )
            self.assertTrue((candidate / "review-checklist.json").is_file())
            self.assertTrue((candidate / "tool-contract.example.json").is_file())
            self.assertTrue((candidate / "evidence" / "source-run-record.json").is_file())
            self.assertTrue((candidate / "evidence" / "example-result.json").is_file())

    def test_file_and_import_access_are_rejected(self) -> None:
        for code in (
            "import os\nRESULT = {}",
            "RESULT = open('secret.txt').read()",
            "RESULT = (1).__class__",
            "while True:\n    pass\nRESULT = {}",
            "RESULT = [0] * 1000001",
            "RESULT = list(range(1000001))",
            "RESULT = 10 ** 1001",
            "print('inspect')\nRESULT = {}",
        ):
            with self.subTest(code=code):
                with self.assertRaises(ExplorePolicyError):
                    validate_explore_code(code)

    def test_execution_error_exposes_safe_cause_and_preserves_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExploreExecutionError) as raised:
                run_explore_code(
                    data={
                        "columns": ["DEPTH"],
                        "rows": [{"DEPTH": 1000.0}],
                        "row_count": 1,
                        "profile": {},
                    },
                    code="RESULT = DATA['MISSING']",
                    purpose="Exercise diagnostics",
                    output_dir=directory,
                    input_sha256="fixture-hash",
                )
            self.assertIn("KeyError", raised.exception.failure)
            self.assertNotIn(str(Path(directory).resolve()), raised.exception.failure)
            self.assertTrue((raised.exception.workspace / "run-record.json").is_file())

    def test_column_helpers_and_contract_validation(self) -> None:
        with self.assertRaises(ExplorePolicyError) as raised:
            validate_explore_data_access("RESULT = DATA['DEPTH']", ("DEPTH", "GR"))
        self.assertIn('COLS["DEPTH"]', str(raised.exception))
        with tempfile.TemporaryDirectory() as directory:
            run = run_explore_code(
                data={
                    "columns": ["DEPTH", "GR"],
                    "rows": [{"DEPTH": 1000.0, "GR": 42.0}],
                    "row_count": 1,
                    "profile": {},
                },
                code="RESULT = {'depth': COLS['DEPTH'], 'rows': len(ROWS)}",
                purpose="Exercise stable data helpers",
                output_dir=directory,
                input_sha256="fixture-hash",
            )
            self.assertEqual(run.result, {"depth": [1000.0], "rows": 1})


if __name__ == "__main__":
    unittest.main()

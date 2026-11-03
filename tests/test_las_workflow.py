from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from geophysagent.las import LasParseError, parse_las
from geophysagent.workflow import analyze_las, write_analysis


FIXTURE = Path(__file__).parent / "fixtures" / "basic.las"


class LasWorkflowTests(unittest.TestCase):
    def test_parse_basic_las(self) -> None:
        dataset = parse_las(FIXTURE)
        self.assertEqual([curve.mnemonic for curve in dataset.curves], ["DEPT", "GR", "RHOB"])
        self.assertEqual(dataset.null_value, -999.25)
        self.assertEqual(dataset.well["WELL"], "Demo-01")
        self.assertIsNone(dataset.rows[2][1])

    def test_analysis_is_traceable_and_valid(self) -> None:
        result = analyze_las(FIXTURE)
        self.assertEqual(result.status, "completed_with_warnings")
        self.assertEqual(result.validation.status, "warning")
        self.assertEqual(len(result.source_sha256), 64)
        self.assertEqual(len(result.tool_results), 3)
        stats = result.tool_results[1].data["curves"]
        gamma_ray = next(item for item in stats if item["mnemonic"] == "GR")
        self.assertEqual(gamma_ray["missing_count"], 1)
        self.assertAlmostEqual(gamma_ray["mean"], 145 / 3)

    def test_outputs_are_machine_and_human_readable(self) -> None:
        result = analyze_las(FIXTURE)
        with tempfile.TemporaryDirectory() as directory:
            json_path, report_path = write_analysis(result, directory)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            report = report_path.read_text(encoding="utf-8")
            figure = Path(directory) / "well-log.svg"
            figure_metadata = json.loads(
                (Path(directory) / "well-log.figure.json").read_text(encoding="utf-8")
            )
            self.assertTrue(figure.is_file())
        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(figure_metadata["figure_type"], "well-log-tracks")
        self.assertIn("LAS 质控与基础分析报告", report)
        self.assertIn("观测事实", report)
        self.assertIn("确定性规则判断", report)
        self.assertIn("模型解释", report)
        self.assertIn("能力边界", report)

    def test_missing_curve_section_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.las"
            path.write_text("~Version\nVERS. 2.0\n~Ascii\n1 2\n", encoding="utf-8")
            with self.assertRaises(LasParseError):
                parse_las(path)


if __name__ == "__main__":
    unittest.main()

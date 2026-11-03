from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from geophysagent.application import execute_segy_inspection_task
from geophysagent.assets import register_asset
from geophysagent.segy import (
    SegyPolicy,
    SegyTruncatedError,
    SegyUnsupportedError,
    inspect_segy,
)
from geophysagent.seismic_workflow import analyze_segy

from segy_factory import write_synthetic_segy


class SegyTests(unittest.TestCase):
    def test_ascii_ieee_golden_file_headers_geometry_statistics_and_qc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_synthetic_segy(
                Path(directory) / "golden.sgy",
                traces=[
                    [0.0, 0.0, 0.0, 0.0],
                    [1.0, 2.0, 3.0, 4.0],
                    [1000.0, -1000.0, 1000.0, -1000.0],
                ],
            )
            inspection = inspect_segy(path)
            binary = inspection.binary_header
            self.assertEqual(inspection.textual_header_encoding, "ascii")
            self.assertEqual(binary.job_id, 1234)
            self.assertEqual(binary.sample_interval, 2000.0)
            self.assertEqual(binary.samples_per_trace, 4)
            self.assertEqual(binary.sample_format_code, 5)
            self.assertEqual(inspection.trace_summary["trace_count"], 3)
            self.assertEqual(inspection.trace_summary["total_declared_samples"], 12)
            self.assertEqual(inspection.geometry["inline_candidate"]["minimum"], 101)
            self.assertEqual(inspection.geometry["inline_candidate"]["maximum"], 103)
            self.assertAlmostEqual(inspection.geometry["source_x"]["minimum"], 1001.0)
            self.assertEqual(inspection.amplitude["finite_sample_count"], 12)
            self.assertEqual(inspection.amplitude["minimum"], -1000.0)
            self.assertEqual(inspection.amplitude["maximum"], 1000.0)
            self.assertEqual(inspection.qc_observations["dead_trace_count"], 1)
            self.assertEqual(inspection.qc_observations["constant_trace_count"], 1)
            self.assertEqual(inspection.qc_observations["abnormal_amplitude_trace_count"], 1)
            chunked = inspect_segy(path, policy=SegyPolicy(sample_chunk_bytes=5))
            self.assertEqual(chunked.amplitude, inspection.amplitude)

    def test_ebcdic_and_ibm_floating_point_are_decoded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_synthetic_segy(
                Path(directory) / "ibm.sgy",
                traces=[[1.0, -2.0, 0.5]],
                format_code=1,
                textual_encoding="cp500",
                binary_samples=3,
            )
            inspection = inspect_segy(path)
            self.assertEqual(inspection.textual_header_encoding, "cp500")
            self.assertEqual(inspection.binary_header.sample_format_code, 1)
            self.assertAlmostEqual(inspection.amplitude["minimum"], -2.0, places=6)
            self.assertAlmostEqual(inspection.amplitude["maximum"], 1.0, places=6)

    def test_revision_2_little_endian_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_synthetic_segy(
                Path(directory) / "little.segy",
                traces=[[1, 2, 3], [4, 5, 6]],
                format_code=3,
                byte_order="little",
                revision_major=2,
                binary_samples=3,
            )
            inspection = inspect_segy(path)
            self.assertEqual(inspection.binary_header.byte_order, "little")
            self.assertEqual(inspection.binary_header.revision_major, 2)
            self.assertEqual(inspection.trace_summary["trace_count"], 2)
            self.assertEqual(inspection.amplitude["maximum"], 6.0)

    def test_all_declared_sample_formats_are_decoded(self) -> None:
        cases = {
            2: [-20, 0, 30],
            3: [-20, 0, 30],
            6: [-20.5, 0.0, 30.25],
            8: [-20, 0, 30],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for format_code, values in cases.items():
                with self.subTest(format_code=format_code):
                    path = write_synthetic_segy(
                        root / f"format-{format_code}.sgy",
                        traces=[values],
                        format_code=format_code,
                        binary_samples=3,
                    )
                    inspection = inspect_segy(path)
                    self.assertEqual(inspection.binary_header.sample_format_code, format_code)
                    self.assertAlmostEqual(
                        inspection.amplitude["minimum"], float(values[0]), places=6
                    )
                    self.assertAlmostEqual(
                        inspection.amplitude["maximum"], float(values[-1]), places=6
                    )

    def test_variable_length_traces_are_traversed_and_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_synthetic_segy(
                Path(directory) / "variable.sgy",
                traces=[[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0]],
                fixed_length=0,
                binary_samples=4,
                trace_sample_intervals=[2000, 4000],
            )
            result = analyze_segy(path)
            observed = result.inspection.trace_summary["samples_per_trace"]
            self.assertEqual(observed["minimum"], 3)
            self.assertEqual(observed["maximum"], 4)
            self.assertEqual(
                result.inspection.trace_summary["binary_header_sample_count_mismatch_count"],
                1,
            )
            self.assertEqual(result.status, "completed_with_warnings")
            self.assertEqual(
                result.inspection.trace_summary["binary_header_sample_interval_mismatch_count"],
                1,
            )

    def test_unknown_format_and_truncated_trace_fail_actionably(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unsupported = write_synthetic_segy(
                root / "unsupported.sgy",
                traces=[[1, 2, 3]],
                format_code=4,
                binary_samples=3,
            )
            with self.assertRaises(SegyUnsupportedError) as raised:
                inspect_segy(unsupported)
            self.assertIn("supported codes are 1, 2, 3, 5, 6, 8", str(raised.exception))

            truncated = write_synthetic_segy(
                root / "truncated.sgy",
                traces=[[1.0, 2.0, 3.0, 4.0]],
                truncate_bytes=2,
            )
            with self.assertRaises(SegyTruncatedError) as raised:
                inspect_segy(truncated)
            self.assertIn("declares 4 samples", str(raised.exception))

            pairwise = write_synthetic_segy(
                root / "pairwise.sgy",
                traces=[[1.0, 2.0, 3.0]],
                revision_major=2,
                binary_samples=3,
            )
            payload = bytearray(pairwise.read_bytes())
            payload[3296:3300] = b"\x02\x01\x04\x03"
            pairwise.write_bytes(payload)
            with self.assertRaises(SegyUnsupportedError) as raised:
                inspect_segy(pairwise)
            self.assertIn("Pair-wise byte-swapped", str(raised.exception))

    def test_harness_tool_evidence_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = write_synthetic_segy(
                root / "source.sgy",
                traces=[[1.0, 2.0, 3.0], [2.0, 3.0, 4.0]],
                binary_samples=3,
            )
            output = root / "run"
            source_hash_before = register_asset(source).sha256
            analysis, harness = execute_segy_inspection_task(
                asset=register_asset(source),
                request="检查 SEG-Y",
                output_dir=output,
            )
            self.assertIn(harness.record.task_status, {"completed", "completed_with_warnings"})
            self.assertEqual(
                {item.evidence_id for item in harness.record.evidence},
                {
                    "segy-binary-header",
                    "segy-trace-summary",
                    "segy-amplitude-statistics",
                    "segy-validation",
                    "segy-result-artifact",
                    "segy-final-report",
                },
            )
            self.assertTrue(all(item.status == "satisfied" for item in harness.record.evidence))
            self.assertEqual(len(harness.record.artifacts), 2)
            payload = json.loads((output / "segy-result.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["inspection"]["trace_summary"]["trace_count"], 2)
            self.assertEqual(register_asset(source).sha256, source_hash_before)
            self.assertNotIn("sample_values", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()

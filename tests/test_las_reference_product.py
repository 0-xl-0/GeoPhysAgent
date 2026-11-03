from __future__ import annotations

import codecs
import json
import tempfile
import unittest
from pathlib import Path

from geophysagent.las import LasParseError, parse_las
from geophysagent.las_plot import render_las_figure
from geophysagent.las_quality import evaluate_las_quality, normalize_curve_identity
from geophysagent.quality_contracts import (
    FigureArtifactMetadata,
    FigureAxis,
    ReportSemanticContract,
)


FIXTURES = Path(__file__).parent / "fixtures" / "las_reference"


class LasReferenceProductTests(unittest.TestCase):
    def test_synthetic_corpus_matches_manifest(self) -> None:
        manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(manifest["cases"]), 10)
        for case in manifest["cases"]:
            source = FIXTURES / case["file"]
            if case["expect_parse"] == "error":
                with self.assertRaises(LasParseError, msg=case["file"]):
                    parse_las(source)
            else:
                dataset = parse_las(source)
                self.assertGreater(len(dataset.rows), 0, case["file"])

    def test_wrapped_delimited_vendor_and_descending_variants(self) -> None:
        wrapped = parse_las(FIXTURES / "wrapped.las")
        comma = parse_las(FIXTURES / "comma.las")
        vendor = parse_las(FIXTURES / "utf8_vendor.las")
        descending = parse_las(FIXTURES / "descending.las")
        self.assertTrue(wrapped.wrap)
        self.assertEqual(len(wrapped.rows), 4)
        self.assertEqual(comma.delimiter, "COMMA")
        self.assertIn("DLM:COMMA", comma.vendor_extensions)
        self.assertEqual(vendor.well["WELL"], "合成井-A")
        self.assertIn("VENDOR", vendor.vendor_extensions)
        self.assertLess(descending.rows[-1][0], descending.rows[0][0])

    def test_utf16_bom_is_supported(self) -> None:
        content = (FIXTURES / "normal.las").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "utf16.las"
            source.write_bytes(codecs.BOM_UTF16_LE + content.encode("utf-16-le"))
            dataset = parse_las(source)
        self.assertEqual(dataset.encoding, "utf-16-le-bom")
        self.assertEqual(len(dataset.rows), 8)

    def test_rules_find_missing_flatline_spike_jump_and_range(self) -> None:
        expectations = {
            "null_heavy.las": {"las.curve.missing"},
            "flatline.las": {"las.curve.flatline"},
            "spike.las": {"las.curve.spike", "las.curve.jump"},
            "out_of_range.las": {
                "las.curve.range",
                "las.curve.spike",
                "las.curve.jump",
            },
        }
        for name, expected in expectations.items():
            quality = evaluate_las_quality(parse_las(FIXTURES / name))
            observed = {
                rule["rule_id"]
                for curve in quality.data["curves"]
                for rule in curve["rule_results"]
                if rule["status"] == "warning"
            }
            self.assertTrue(expected.issubset(observed), name)
            for curve in quality.data["curves"]:
                for rule in curve["rule_results"]:
                    self.assertIn("rule_version", rule)
                    self.assertIn("source", rule)
                    self.assertIn("parameters", rule)

    def test_alias_and_unit_normalization_is_traceable(self) -> None:
        family, unit = normalize_curve_identity("DEN", "K/M3")
        self.assertEqual((family, unit), ("bulk_density", "kg/m3"))

    def test_figure_metadata_contract_is_domain_neutral(self) -> None:
        metadata = FigureArtifactMetadata(
            figure_id="seismic-amplitude",
            figure_type="line-chart",
            title="Amplitude",
            tool_name="seismic.render",
            tool_version="1.0.0",
            source_result_ids=("segy.inspect",),
            evidence_references=("segy-analysis#/amplitude",),
            axes=(FigureAxis("x", "Trace", "index", 0, 10),),
            series=({"series_id": "amplitude"},),
        )
        self.assertEqual(metadata.to_dict()["figure_type"], "line-chart")
        report_contract = ReportSemanticContract()
        self.assertIn("观测事实", report_contract.sections)
        self.assertFalse(report_contract.model_interpretation_is_evidence)

    def test_svg_has_axes_units_sources_and_evidence(self) -> None:
        svg, metadata = render_las_figure(parse_las(FIXTURES / "normal.las"))
        self.assertIn("<svg", svg)
        self.assertGreaterEqual(len(metadata.axes), 2)
        self.assertTrue(metadata.source_result_ids)
        self.assertTrue(metadata.evidence_references)

    def test_malformed_version_duplicate_section_and_nonfinite_are_rejected(self) -> None:
        samples = (
            "~Version\nVERS. 3.0\nWRAP. NO\n~Well\nNULL. -999\n~Curve\nDEPT.M\n~Ascii\n1\n",
            "~Version\nVERS. 2.0\nWRAP. NO\n~Well\nNULL. -999\n~Well\nNULL. -999\n~Curve\nDEPT.M\n~Ascii\n1\n",
            "~Version\nVERS. 2.0\nWRAP. NO\n~Well\nNULL. -999\n~Curve\nDEPT.M\n~Ascii\nnan\n",
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, content in enumerate(samples):
                path = Path(directory) / f"broken-{index}.las"
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(LasParseError):
                    parse_las(path)


if __name__ == "__main__":
    unittest.main()

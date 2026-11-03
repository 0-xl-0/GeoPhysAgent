from __future__ import annotations

import unittest
from pathlib import Path

from geophysagent.assets import register_asset
from geophysagent.tabular import inspect_tabular_asset, load_tabular_asset


FIXTURES = Path(__file__).parent / "fixtures"


class AssetAndTabularTests(unittest.TestCase):
    def test_detect_whitespace_well_log(self) -> None:
        asset = register_asset(FIXTURES / "well_log.txt")
        profile = inspect_tabular_asset(asset)
        self.assertEqual(profile.delimiter, "whitespace")
        self.assertEqual(profile.columns, ("DEPTH", "GR", "RHOB"))
        self.assertEqual(profile.depth_candidates, ("DEPTH",))
        dataset = load_tabular_asset(asset, null_values=[-999.25])
        self.assertIsNone(dataset.rows[2]["GR"])

    def test_detect_csv_and_missing_value(self) -> None:
        asset = register_asset(FIXTURES / "well_log.csv")
        profile = inspect_tabular_asset(asset)
        self.assertEqual(profile.delimiter, ",")
        self.assertEqual(profile.depth_candidates, ("MD",))
        dataset = load_tabular_asset(asset)
        self.assertIsNone(dataset.rows[2]["GR"])

    def test_las_is_exposed_as_tabular_asset(self) -> None:
        asset = register_asset(FIXTURES / "basic.las")
        dataset = load_tabular_asset(asset)
        self.assertEqual(dataset.columns, ("DEPT", "GR", "RHOB"))
        self.assertEqual(len(dataset.rows), 4)


if __name__ == "__main__":
    unittest.main()

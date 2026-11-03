from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from geophysagent.asset_adapters import (
    ASSET_INSPECT,
    EXPLORE_PYTHON,
    SEGY_INSPECT,
    TABULAR_READ,
    UnsupportedAssetCapabilityError,
)
from geophysagent.assets import register_asset
from geophysagent.explore_agent import run_explore_agent
from geophysagent.tabular import inspect_tabular_asset, load_tabular_asset


class NeverCalledModel:
    provider = "never"
    model = "never"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete(self, **request: Any):
        self.calls.append(request)
        raise AssertionError("Model must not be called for unsupported asset types")


class AssetAdapterTests(unittest.TestCase):
    def test_segy_is_recognized_but_not_misrouted_as_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.sgy"
            path.write_bytes(b"\xc3\x40\xf1" + b"\x00" * 4093)
            asset = register_asset(path)
            self.assertEqual(asset.format, "segy")
            self.assertEqual(asset.data_type, "seismic")
            self.assertEqual(asset.adapter_id, "seismic-segy")
            self.assertEqual(asset.capabilities, (ASSET_INSPECT, SEGY_INSPECT))
            with self.assertRaises(UnsupportedAssetCapabilityError) as raised:
                load_tabular_asset(asset)
            self.assertIn(TABULAR_READ, str(raised.exception))
            self.assertIn("No parser, tool, or model was invoked", str(raised.exception))

    def test_explore_rejects_segy_before_calling_model(self) -> None:
        client = NeverCalledModel()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "synthetic.segy"
            path.write_bytes(b"C 1 CLIENT" + b"\x00" * 4000)
            with self.assertRaises(UnsupportedAssetCapabilityError) as raised:
                run_explore_agent(
                    client=client,
                    user_request="分析这个地震文件",
                    source_path=path,
                    output_dir=root / "output",
                )
            self.assertIn(EXPLORE_PYTHON, str(raised.exception))
            self.assertEqual(client.calls, [])

    def test_binary_content_with_txt_extension_is_not_decoded_as_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "not-really-text.txt"
            path.write_bytes(b"DEPTH GR\x00\x01\x02" * 50)
            asset = register_asset(path)
            self.assertEqual(asset.data_type, "unknown_binary")
            with self.assertRaises(UnsupportedAssetCapabilityError):
                inspect_tabular_asset(asset)


if __name__ == "__main__":
    unittest.main()

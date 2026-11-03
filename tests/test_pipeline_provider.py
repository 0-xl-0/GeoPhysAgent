from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geophysagent.assets import register_asset
from geophysagent.builtin_provider import create_builtin_registry
from geophysagent.pipeline import execute_pipeline, load_pipeline_spec
from geophysagent.providers import ToolContext, ToolRegistryError
from geophysagent.tabular import load_tabular_asset


FIXTURES = Path(__file__).parent / "fixtures"


class PipelineAndProviderTests(unittest.TestCase):
    def test_parameterized_pipeline(self) -> None:
        asset = register_asset(FIXTURES / "well_log.csv")
        dataset = load_tabular_asset(asset)
        spec = load_pipeline_spec(FIXTURES / "pipeline.json")
        result = execute_pipeline(dataset, spec)
        self.assertEqual(result.dataset.columns, ("DEPTH", "GR", "DT"))
        self.assertEqual([row["DEPTH"] for row in result.dataset.rows], [1000.0, 1000.5, 1001.0])
        self.assertEqual(result.dataset.rows[1]["GR"], 47.5)

    def test_registry_routes_builtin_tool(self) -> None:
        asset = register_asset(FIXTURES / "well_log.csv")
        registry = create_builtin_registry()
        with tempfile.TemporaryDirectory() as directory:
            execution = registry.invoke(
                "table.run_pipeline",
                {
                    "asset_id": "primary",
                    "pipeline": load_pipeline_spec(FIXTURES / "pipeline.json").to_dict(),
                },
                ToolContext({"primary": asset}, Path(directory)),
            )
            self.assertTrue((Path(directory) / "preprocessed.csv").is_file())
        self.assertEqual(execution.result.tool_name, "table.run_pipeline")
        self.assertEqual(execution.result.status, "success")

    def test_registry_rejects_unknown_argument_and_asset(self) -> None:
        registry = create_builtin_registry()
        context = ToolContext({}, Path("unused"))
        with self.assertRaises(ToolRegistryError):
            registry.invoke(
                "asset.inspect_table", {"asset_id": "primary", "path": "secret"}, context
            )
        with self.assertRaises(ToolRegistryError):
            registry.invoke("asset.inspect_table", {"asset_id": "primary"}, context)


if __name__ == "__main__":
    unittest.main()

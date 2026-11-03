from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from geophysagent.application import execute_las_qc_task
from geophysagent.assets import register_asset
from geophysagent.cli import main
from geophysagent.config import ModelProfile
from geophysagent.harness import GeoDomainHarness
from geophysagent.model import ModelResponse
from geophysagent.run_store import FileRunStore

from segy_factory import write_synthetic_segy


class CliTests(unittest.TestCase):
    def _profile(self, key_env: str = "GEOPHYSAGENT_TEST_KEY") -> ModelProfile:
        return ModelProfile(
            provider="fake-provider",
            model="fake-model",
            protocol="anthropic-compatible",
            base_url="https://example.invalid",
            api_key_env=key_env,
            timeout_seconds=30,
            max_tokens=1000,
        )

    def test_general_run_cli_uses_llm_first_runtime(self) -> None:
        class FakeClient:
            provider = "fake-provider"
            model = "fake-model"
            protocol = "fake-protocol"

            def __init__(self):
                self.responses = [
                    ModelResponse(
                        content=(
                            {
                                "type": "tool_use",
                                "id": "plan",
                                "name": "submit_execution_plan",
                                "input": {
                                    "objective": "识别资产",
                                    "steps": [
                                        {
                                            "step_id": "identify",
                                            "tool": "inspect_asset",
                                            "purpose": "识别资产",
                                        }
                                    ],
                                },
                            },
                        ),
                        stop_reason="tool_use",
                        model=self.model,
                    ),
                    ModelResponse(
                        content=(
                            {
                                "type": "tool_use",
                                "id": "inspect",
                                "name": "inspect_asset",
                                "input": {"asset_id": "primary"},
                            },
                        ),
                        stop_reason="tool_use",
                        model=self.model,
                    ),
                    ModelResponse(
                        content=(
                            {
                                "type": "tool_use",
                                "id": "final",
                                "name": "submit_final_answer",
                                "input": {
                                    "interpretation": "资产已识别。",
                                    "claims": [
                                        {
                                            "evidence_id": "identify-result",
                                            "selector": "/data/asset/format",
                                        }
                                    ],
                                    "limitations": ["仅完成资产识别。"],
                                },
                            },
                        ),
                        stop_reason="tool_use",
                        model=self.model,
                    ),
                ]

            def complete(self, **request):
                return self.responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            stdout = io.StringIO()
            with (
                mock.patch("geophysagent.cli.load_model_profile", return_value=self._profile()),
                mock.patch("geophysagent.cli.create_model_client", return_value=FakeClient()),
                mock.patch.dict("os.environ", {"GEOPHYSAGENT_TEST_KEY": "secret"}),
                contextlib.redirect_stdout(stdout),
            ):
                status = main(
                    [
                        "run",
                        str(Path(__file__).parent / "fixtures" / "basic.las"),
                        "--request",
                        "识别资产",
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(status, 0)
            self.assertIn("Status: completed", stdout.getvalue())
            self.assertTrue((output / "candidate-plan.json").is_file())
            self.assertTrue((output / "agent-response.md").is_file())

    def test_general_run_missing_key_creates_waiting_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "waiting"
            stderr = io.StringIO()
            with (
                mock.patch(
                    "geophysagent.cli.load_model_profile",
                    return_value=self._profile("DEFINITELY_MISSING_GEOPHYS_KEY"),
                ),
                mock.patch.dict("os.environ", {}, clear=True),
                contextlib.redirect_stderr(stderr),
            ):
                status = main(
                    [
                        "run",
                        str(Path(__file__).parent / "fixtures" / "basic.las"),
                        "--request",
                        "检查文件",
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(status, 2)
            record = FileRunStore(output).load()
            assert record is not None
            self.assertEqual(record.task_status, "waiting_for_user")
            self.assertEqual(record.attempts, [])
            self.assertIn("resume", stderr.getvalue())

    def test_models_command_lists_default_and_credential_state_without_secret(self) -> None:
        config = """[models]
default = "go-m3"
[models.profiles.go-m3]
provider = "opencode-go"
model = "minimax-m3"
protocol = "anthropic-compatible"
base_url = "https://opencode.ai/zen/go"
api_key_env = "OPENCODE_API_KEY"
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "geophysagent.toml"
            path.write_text(config, encoding="utf-8")
            stdout = io.StringIO()
            with (
                mock.patch.dict("os.environ", {"OPENCODE_API_KEY": "never-print-this"}),
                contextlib.redirect_stdout(stdout),
            ):
                status = main(["models", "--config", str(path)])
        rendered = stdout.getvalue()
        self.assertEqual(status, 0)
        self.assertIn("Default model profile: go-m3", rendered)
        self.assertIn("credential configured: OPENCODE_API_KEY", rendered)
        self.assertNotIn("never-print-this", rendered)

    def test_resume_and_cancel_commands_use_persisted_lifecycle(self) -> None:
        class Terminated(BaseException):
            pass

        asset = register_asset(Path(__file__).parent / "fixtures" / "basic.las")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resumable = root / "resumable"

            def stop_before_validation(self, phase, record):
                if phase == "before_validation":
                    raise Terminated()

            with mock.patch.object(GeoDomainHarness, "_fault", new=stop_before_validation):
                with self.assertRaises(Terminated):
                    execute_las_qc_task(
                        asset=asset,
                        request="resume CLI test",
                        output_dir=resumable,
                    )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["resume", str(resumable)]), 0)
            self.assertIn("Task status: completed", stdout.getvalue())

            cancellable = root / "cancellable"

            def stop_during_execution(self, phase, record):
                if phase == "after_state_executing":
                    raise Terminated()

            with mock.patch.object(GeoDomainHarness, "_fault", new=stop_during_execution):
                with self.assertRaises(Terminated):
                    execute_las_qc_task(
                        asset=asset,
                        request="cancel CLI test",
                        output_dir=cancellable,
                    )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["cancel", str(cancellable)]), 0)
            self.assertIn("Task status: cancelled", stdout.getvalue())

    def test_segy_command_runs_through_harness_without_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = write_synthetic_segy(
                root / "synthetic.sgy",
                traces=[[1.0, 2.0, 3.0], [2.0, 3.0, 4.0]],
                binary_samples=3,
            )
            output = root / "output"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                status = main(["segy", str(source), "--output", str(output)])
            self.assertEqual(status, 0)
            self.assertIn("Status: completed", stdout.getvalue())
            self.assertTrue((output / "segy-result.json").is_file())
            self.assertTrue((output / "segy-report.md").is_file())
            self.assertTrue((output / "run-record.json").is_file())
            self.assertTrue((output / "artifact-manifest.json").is_file())
            status_output = io.StringIO()
            with contextlib.redirect_stdout(status_output):
                self.assertEqual(main(["status", str(output), "--json"]), 0)
            self.assertIn('"task_status": "completed', status_output.getvalue())
            validation_output = io.StringIO()
            with contextlib.redirect_stdout(validation_output):
                self.assertEqual(main(["validate", str(output)]), 0)
            self.assertIn("Run integrity: valid", validation_output.getvalue())

    def test_explore_rejects_unsupported_asset_before_model_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "synthetic.sgy"
            source.write_bytes(b"C 1 TEST" + b"\x00" * 4000)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status = main(
                    [
                        "explore",
                        str(source),
                        "--request",
                        "分析这个文件",
                        "--output",
                        str(root / "output"),
                        "--config",
                        str(root / "missing.toml"),
                    ]
                )
            self.assertEqual(status, 2)
            message = stderr.getvalue()
            self.assertIn("explore.python", message)
            self.assertNotIn("Configuration", message)


if __name__ == "__main__":
    unittest.main()

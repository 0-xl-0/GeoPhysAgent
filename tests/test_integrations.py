from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import mock

from geophysagent.cli import main
from geophysagent.config import ModelProfile
from geophysagent.general_agent import create_general_agent_registry, run_general_agent
from geophysagent.integrations import IntegrationPackageError, IntegrationPackageManager
from geophysagent.model import ModelResponse
from geophysagent.run_inspection import validate_run_directory
from geophysagent.reference_eval import ReferenceCase, evaluate_reference_run


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/integrations/software-a"


class _SoftwareAHandler(BaseHTTPRequestHandler):
    authorization = ""

    def do_GET(self) -> None:
        type(self).authorization = self.headers.get("Authorization", "")
        if self.path != "/projects/XX/seismic":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(
            {
                "datasets": [
                    {"name": "cube-main", "samples": 1200},
                    {"name": "cube-angle", "samples": 900},
                ]
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class _FakeModel:
    provider = "fake"
    model = "fake-model"
    protocol = "fake"

    def __init__(self, responses: list[ModelResponse]):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def complete(self, **request: Any) -> ModelResponse:
        self.calls.append(copy.deepcopy(request))
        if not self.responses:
            raise AssertionError(
                "Fake Model responses exhausted; last messages="
                + json.dumps(request.get("messages", [])[-4:], ensure_ascii=False)
            )
        return self.responses.pop(0)


def _tool_call(identifier: str, name: str, arguments: dict[str, Any]) -> ModelResponse:
    return ModelResponse(
        content=({"type": "tool_use", "id": identifier, "name": name, "input": arguments},),
        stop_reason="tool_use",
        model="fake-model",
        response_id=identifier,
    )


def _dynamic_package(root: Path, origin: str) -> Path:
    source = root / "source-package"
    shutil.copytree(EXAMPLE, source)
    manifest_path = source / "software-a.integration.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["network_policy"]["allowed_origins"] = [origin]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return source


class IntegrationPackageTests(unittest.TestCase):
    def test_install_enable_disable_integrity_and_remove(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = IntegrationPackageManager(root / "installed")
            record = manager.install(EXAMPLE)
            self.assertTrue(record.enabled)
            self.assertEqual(
                manager.enabled_manifest_paths()[0].name, "software-a.integration.json"
            )
            manager.set_enabled(record.package_id, False)
            self.assertEqual(manager.enabled_manifest_paths(), ())
            manager.set_enabled(record.package_id, True)
            server = manager.root / record.package_id / "server.py"
            server.write_text(
                server.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8"
            )
            with self.assertRaises(IntegrationPackageError):
                manager.enabled_manifest_paths()
            server.write_text((EXAMPLE / "server.py").read_text(encoding="utf-8"), encoding="utf-8")
            self.assertEqual(manager.remove(record.package_id).package_id, record.package_id)
            self.assertEqual(manager.list(), ())

    def test_package_rejects_undeclared_and_credential_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "package"
            shutil.copytree(EXAMPLE, source)
            (source / "extra.py").write_text("pass\n", encoding="utf-8")
            with self.assertRaises(IntegrationPackageError):
                IntegrationPackageManager(root / "installed").install(source)
            (source / "extra.py").unlink()
            (source / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
            with self.assertRaises(IntegrationPackageError):
                IntegrationPackageManager(root / "installed").install(source)

    def test_cli_manages_local_integration_packages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "registry"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    main(["integration", "install", str(EXAMPLE), "--root", str(registry)]),
                    0,
                )
                self.assertEqual(main(["integration", "list", "--root", str(registry)]), 0)
                self.assertEqual(
                    main(
                        [
                            "integration",
                            "disable",
                            "software-a-readonly",
                            "--root",
                            str(registry),
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    main(
                        [
                            "integration",
                            "remove",
                            "software-a-readonly",
                            "--root",
                            str(registry),
                        ]
                    ),
                    0,
                )
            self.assertIn("Installed Integration", output.getvalue())
            self.assertFalse((registry / "software-a-readonly").exists())

    def test_external_only_task_calls_installed_readonly_api(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SoftwareAHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = _dynamic_package(root, origin)
                manager = IntegrationPackageManager(root / "installed")
                manager.install(source)
                client = _FakeModel(
                    [
                        _tool_call(
                            "plan",
                            "submit_execution_plan",
                            {
                                "objective": "查看软件 A 的 XX 项目地震数据",
                                "steps": [
                                    {
                                        "step_id": "list-seismic",
                                        "tool": "software_a_list_seismic_datasets",
                                        "purpose": "读取项目中的地震数据",
                                    }
                                ],
                            },
                        ),
                        _tool_call(
                            "list",
                            "software_a_list_seismic_datasets",
                            {"project": "XX"},
                        ),
                        _tool_call(
                            "final",
                            "submit_final_answer",
                            {
                                "interpretation": "XX 项目包含 2 个地震数据对象。",
                                "claims": [
                                    {
                                        "evidence_id": "list-seismic-result",
                                        "selector": "/data/structured_content/dataset_count",
                                    }
                                ],
                                "limitations": ["仅查询只读概要。"],
                            },
                        ),
                    ]
                )
                output = root / "run"
                environment = {
                    "SOFTWARE_A_API_BASE": origin,
                    "SOFTWARE_A_API_TOKEN": "software-a-secret",
                }
                with mock.patch.dict(os.environ, environment, clear=False):
                    registry = create_general_agent_registry(manager.enabled_manifest_paths())
                    result = run_general_agent(
                        client=client,
                        user_request="查看软件A中XX项目下的地震数据信息",
                        source_path=None,
                        output_dir=output,
                        registry=registry,
                    )
                self.assertEqual(result.harness.record.task_spec.input_asset_ids, ())
                self.assertEqual(result.harness.record.task_status, "completed")
                binding = next(
                    item
                    for item in result.harness.record.tool_bindings
                    if item.provider_id == "mcp:software-a-readonly"
                )
                self.assertEqual(binding.network_origins, (origin,))
                self.assertEqual(_SoftwareAHandler.authorization, "Bearer software-a-secret")
                self.assertNotIn(
                    "software-a-secret", (output / "events.jsonl").read_text(encoding="utf-8")
                )
                self.assertTrue(validate_run_directory(output).valid)
                reference = evaluate_reference_run(
                    ReferenceCase(
                        case_id="external-software-a",
                        run_directory="run",
                        expected_workflow="general-llm-agent",
                        input_kind="external",
                        allowed_tools=("software_a.list_seismic_datasets",),
                        required_tools=("software_a.list_seismic_datasets",),
                        required_evidence_ids=("list-seismic-result",),
                        require_context_snapshots=True,
                        required_validator_ids=(
                            "core.data.validation_evidence",
                            "core.process.required_evidence",
                            "core.result.artifact_integrity",
                            "core.result.final_artifact_evidence",
                        ),
                    ),
                    output,
                )
                self.assertTrue(reference.passed, [item.message for item in reference.checks])
                request_text = json.dumps(client.calls, ensure_ascii=False)
                self.assertIn("external_only", request_text)
                self.assertNotIn("software-a-secret", request_text)
                self.assertNotIn("run_well_log_qc", request_text)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_cli_runs_external_only_task_from_enabled_package(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SoftwareAHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manager = IntegrationPackageManager(root / "installed")
                manager.install(_dynamic_package(root, origin))
                client = _FakeModel(
                    [
                        _tool_call(
                            "plan",
                            "submit_execution_plan",
                            {
                                "objective": "查询软件 A",
                                "steps": [
                                    {
                                        "step_id": "query",
                                        "tool": "software_a_list_seismic_datasets",
                                        "purpose": "查询 XX 项目",
                                    }
                                ],
                            },
                        ),
                        _tool_call(
                            "query",
                            "software_a_list_seismic_datasets",
                            {"project": "XX"},
                        ),
                        _tool_call(
                            "final",
                            "submit_final_answer",
                            {
                                "interpretation": "查询到 2 个数据对象。",
                                "claims": [
                                    {
                                        "evidence_id": "query-result",
                                        "selector": "/data/structured_content/dataset_count",
                                    }
                                ],
                                "limitations": ["只读查询。"],
                            },
                        ),
                    ]
                )
                profile = ModelProfile(
                    provider="fake",
                    model="fake-model",
                    protocol="anthropic-compatible",
                    base_url="https://example.invalid",
                    api_key_env="MODEL_TEST_KEY",
                )
                output = root / "run"
                stdout = io.StringIO()
                environment = {
                    "MODEL_TEST_KEY": "model-secret",
                    "SOFTWARE_A_API_BASE": origin,
                    "SOFTWARE_A_API_TOKEN": "api-secret",
                }
                with (
                    mock.patch.dict(os.environ, environment, clear=False),
                    mock.patch("geophysagent.cli.load_model_profile", return_value=profile),
                    mock.patch("geophysagent.cli.create_model_client", return_value=client),
                    contextlib.redirect_stdout(stdout),
                ):
                    status = main(
                        [
                            "run",
                            "--request",
                            "查看软件A中XX项目下的地震数据信息",
                            "--integration-root",
                            str(manager.root),
                            "--output",
                            str(output),
                        ]
                    )
                self.assertEqual(status, 0)
                self.assertIn("Status: completed", stdout.getvalue())
                self.assertTrue((output / "agent-response.md").is_file())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

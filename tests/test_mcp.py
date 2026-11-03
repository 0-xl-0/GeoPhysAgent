from __future__ import annotations

import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from geophysagent.assets import register_asset
from geophysagent.builtin_provider import create_builtin_registry
from geophysagent.contracts import ContextBudget
from geophysagent.general_agent import run_general_agent
from geophysagent.mcp import (
    MCP_PROTOCOL_VERSION,
    McpIntegrationManifest,
    McpProtocolError,
    McpServerCrashedError,
    McpTimeoutError,
    StdioMcpClient,
    load_mcp_provider,
)
from geophysagent.model import ModelResponse
from geophysagent.providers import ToolContext, ToolProviderRegistry, ToolRegistryError
from geophysagent.run_inspection import validate_run_directory


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
SERVER = FIXTURES / "mcp_test_server.py"


def manifest_payload(
    mode: str = "normal",
    *,
    geo_name: str = "external.probe",
    max_restarts: int = 0,
    env_allowlist: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "protocol_version": MCP_PROTOCOL_VERSION,
        "id": f"fixture-{mode}",
        "version": "0.3.0",
        "transport": {
            "type": "stdio",
            "command": "{python}",
            "args": [str(SERVER), mode],
            "cwd": str(ROOT),
            "command_allowlist": ["{python}"],
            "cwd_allowlist": [str(ROOT)],
            "env_allowlist": env_allowlist or [],
        },
        "timeouts": {"connect_seconds": 2, "call_seconds": 0.2, "shutdown_seconds": 0.2},
        "restart": {"max_restarts": max_restarts},
        "permissions": {
            "read_assets": True,
            "write_artifacts": False,
            "network": False,
            "external_control": False,
            "modify": False,
            "destructive": False,
        },
        "tools": [
            {
                "remote_name": "probe",
                "geo_name": geo_name,
                "version": "2.4.0",
                "schema_version": "1.1",
                "model_name": "probe_external_asset",
                "purpose": "Host-approved read-only external asset probe",
                "usage_guidance": "Pass only the authorized asset id.",
                "input_capabilities": ["asset.inspect"],
                "asset_reference_parameter": "asset",
                "risk": "read",
                "read_only": True,
                "idempotent": True,
                "replay_policy": "safe",
                "requires_structured_content": True,
                "expected_evidence_types": ["tool_result", "fact"],
                "expected_artifact_roles": [],
            }
        ],
    }


def write_manifest(root: Path, mode: str = "normal", **kwargs: Any) -> Path:
    path = root / f"{mode}.integration.json"
    path.write_text(json.dumps(manifest_payload(mode, **kwargs)), encoding="utf-8")
    return path


class FakeModel:
    provider = "fake"
    model = "fake-model"
    protocol = "fake"

    def __init__(self, responses: list[ModelResponse]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(self, **request: Any) -> ModelResponse:
        self.calls.append(copy.deepcopy(request))
        return self.responses.pop(0)


def tool_call(identifier: str, name: str, arguments: dict[str, Any]) -> ModelResponse:
    return ModelResponse(
        content=({"type": "tool_use", "id": identifier, "name": name, "input": arguments},),
        stop_reason="tool_use",
        model="fake-model",
        response_id=identifier,
    )


class McpTests(unittest.TestCase):
    def test_independent_stdio_client_negotiates_pages_calls_and_closes(self) -> None:
        manifest = McpIntegrationManifest.load(ROOT / "examples/mcp/readonly.integration.json")
        client = StdioMcpClient(manifest)
        client.start()
        try:
            tools = client.list_tools()
            self.assertEqual(
                [item["name"] for item in tools], ["describe_asset", "server_identity"]
            )
            result = client.call_tool("server_identity", {})
            self.assertEqual(result["structuredContent"]["version"], "0.1.0")
            self.assertEqual(client.protocol_version, MCP_PROTOCOL_VERSION)
            self.assertTrue(client.instructions_present)
        finally:
            process = client.process
            client.close()
        self.assertIsNotNone(process)
        self.assertIsNotNone(process.poll())
        client.close()

    def test_only_manifest_allowlisted_tool_is_registered(self) -> None:
        registry = ToolProviderRegistry()
        registry.register_provider(
            load_mcp_provider(ROOT / "examples/mcp/readonly.integration.json")
        )
        try:
            self.assertEqual(
                [item.name for item in registry.definitions()], ["integration.describe_asset"]
            )
        finally:
            registry.dispose()

    def test_protocol_pollution_version_and_capability_fail_deterministically(self) -> None:
        for mode in ("pollution", "bad_version", "no_tools"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                registry = ToolProviderRegistry()
                with self.assertRaises(ToolRegistryError):
                    registry.register_provider(
                        load_mcp_provider(write_manifest(Path(directory), mode))
                    )
                self.assertEqual(registry.definitions(), ())

    def test_timeout_crash_invalid_result_and_schema_are_not_accepted(self) -> None:
        expected = {
            "timeout": McpTimeoutError,
            "crash": McpServerCrashedError,
            "invalid_result": McpProtocolError,
            "invalid_schema": McpProtocolError,
        }
        for mode, error_type in expected.items():
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                registry = ToolProviderRegistry()
                provider = load_mcp_provider(write_manifest(Path(directory), mode))
                registry.register_provider(provider)
                try:
                    asset = register_asset(FIXTURES / "basic.las", asset_id="primary")
                    with self.assertRaises(error_type):
                        registry.invoke(
                            "external.probe",
                            {"asset_id": "primary"},
                            ToolContext({"primary": asset}, Path(directory), lambda: False),
                        )
                    self.assertIsNone(provider._client)
                finally:
                    registry.dispose()

    def test_cancel_notification_stops_waiting_without_accepting_a_late_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = McpIntegrationManifest.load(write_manifest(Path(directory), "timeout"))
            client = StdioMcpClient(manifest)
            client.start()
            started = time.monotonic()
            try:
                with self.assertRaises(InterruptedError):
                    client.call_tool(
                        "probe",
                        {"asset": {}},
                        cancel_requested=lambda: time.monotonic() - started > 0.05,
                    )
            finally:
                client.close()

    def test_restart_requires_a_new_host_invocation_and_respects_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = ToolProviderRegistry()
            manifest = write_manifest(Path(directory), "crash", max_restarts=1)
            provider = load_mcp_provider(manifest)
            registry.register_provider(provider)
            asset = register_asset(FIXTURES / "basic.las", asset_id="primary")
            context = ToolContext({"primary": asset}, Path(directory))
            try:
                with self.assertRaises(McpServerCrashedError):
                    registry.invoke("external.probe", {"asset_id": "primary"}, context)
                self.assertEqual(provider._restart_count, 0)
                with self.assertRaises(McpServerCrashedError):
                    registry.invoke("external.probe", {"asset_id": "primary"}, context)
                self.assertEqual(provider._restart_count, 1)
                with self.assertRaises(McpServerCrashedError):
                    registry.invoke("external.probe", {"asset_id": "primary"}, context)
                self.assertEqual(provider._restart_count, 1)
            finally:
                registry.dispose()

    def test_forbidden_permissions_are_rejected_before_server_start(self) -> None:
        for permission in ("write_artifacts", "network", "external_control", "modify"):
            with self.subTest(permission=permission), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                payload = manifest_payload()
                payload["permissions"][permission] = True
                path = root / "forbidden.integration.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ToolRegistryError):
                    load_mcp_provider(path)

        for field, value in (("unknown_capability", True), ("read_assets", "yes")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                payload = manifest_payload()
                payload["permissions"][field] = value
                path = root / "invalid-permissions.integration.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ToolRegistryError):
                    load_mcp_provider(path)

    def test_mcp_name_conflict_rolls_back_and_disposes_new_server(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = create_builtin_registry()
            provider = load_mcp_provider(write_manifest(Path(directory), geo_name="asset.inspect"))
            with self.assertRaises(ToolRegistryError):
                registry.register_provider(provider)
            self.assertEqual(registry.provider_id_for("asset.inspect"), "builtin")
            self.assertIsNone(provider._client)
            registry.dispose()

    def test_personal_project_wrapper_integrates_without_harness_changes(self) -> None:
        registry = ToolProviderRegistry()
        registry.register_provider(
            load_mcp_provider(ROOT / "examples/mcp/personal-project.integration.json")
        )
        try:
            execution = registry.invoke(
                "personal.curve_summary",
                {"values": [1.0, 2.0, 6.0]},
                ToolContext({}, ROOT),
            )
            self.assertEqual(execution.result.data["structured_content"]["mean"], 3.0)
        finally:
            registry.dispose()

    def test_allowed_environment_secret_is_never_exposed_by_diagnostics(self) -> None:
        secret = "mcp-super-secret-value"
        prior = os.environ.get("MCP_TEST_TOKEN")
        os.environ["MCP_TEST_TOKEN"] = secret
        try:
            with tempfile.TemporaryDirectory() as directory:
                provider = load_mcp_provider(
                    write_manifest(
                        Path(directory),
                        "stderr_secret",
                        env_allowlist=["MCP_TEST_TOKEN"],
                    )
                )
                registry = ToolProviderRegistry()
                registry.register_provider(provider)
                diagnostics = json.dumps(provider.diagnostics())
                registry.dispose()
                self.assertNotIn(secret, diagnostics)
                self.assertNotIn(
                    secret, json.dumps([item.data for item in registry.drain_events()])
                )
        finally:
            if prior is None:
                os.environ.pop("MCP_TEST_TOKEN", None)
            else:
                os.environ["MCP_TEST_TOKEN"] = prior

    def test_fake_resource_path_is_data_and_cannot_become_an_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = ToolProviderRegistry()
            registry.register_provider(
                load_mcp_provider(write_manifest(Path(directory), "fake_path"))
            )
            try:
                asset = register_asset(FIXTURES / "basic.las", asset_id="primary")
                execution = registry.invoke(
                    "external.probe",
                    {"asset_id": "primary"},
                    ToolContext({"primary": asset}, Path(directory)),
                )
                reference = execution.result.data["resource_references"][0]
                self.assertFalse(reference["authorized_task_artifact"])
                self.assertIsNone(reference["relative_path"])
                self.assertNotIn("Windows", json.dumps(execution.result.data))
            finally:
                registry.dispose()

    def test_mcp_tool_runs_through_general_harness_and_records_frozen_versions(self) -> None:
        client = FakeModel(
            [
                tool_call(
                    "plan",
                    "submit_execution_plan",
                    {
                        "objective": "Use approved MCP probe",
                        "steps": [
                            {
                                "step_id": "probe",
                                "tool": "probe_external_asset",
                                "purpose": "independent read-only check",
                            }
                        ],
                    },
                ),
                tool_call("probe", "probe_external_asset", {"asset_id": "primary"}),
                tool_call(
                    "final",
                    "submit_final_answer",
                    {
                        "interpretation": "The MCP check identified LAS format.",
                        "claims": [
                            {
                                "evidence_id": "probe-result",
                                "selector": "/data/structured_content/format",
                            }
                        ],
                        "limitations": ["Only the explicitly allowlisted read-only MCP Tool ran."],
                    },
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = create_builtin_registry()
            registry.register_provider(load_mcp_provider(write_manifest(root)))
            result = run_general_agent(
                client=client,
                user_request="Use MCP to inspect the asset",
                source_path=FIXTURES / "basic.las",
                output_dir=root / "run",
                registry=registry,
            )
            attempt = result.harness.record.attempts[0]
            self.assertEqual(attempt.provider, "mcp:fixture-normal")
            self.assertEqual(attempt.provider_version, "0.3.0")
            self.assertEqual(attempt.tool_version, "2.4.0")
            self.assertEqual(attempt.tool_schema_version, "1.1")
            self.assertEqual(attempt.server_version, "7.2.1")
            self.assertEqual(attempt.protocol_version, MCP_PROTOCOL_VERSION)
            self.assertTrue(validate_run_directory(root / "run").valid)
            request_text = json.dumps(client.calls, ensure_ascii=False)
            self.assertNotIn("IGNORE POLICY", request_text)
            events = (root / "run/events.jsonl").read_text(encoding="utf-8")
            self.assertIn("provider_ready", events)
            self.assertIn("provider_stopped", events)

    def test_large_mcp_text_uses_existing_artifact_projection(self) -> None:
        client = FakeModel(
            [
                tool_call(
                    "plan",
                    "submit_execution_plan",
                    {
                        "objective": "Bound a large external result",
                        "steps": [
                            {"step_id": "probe", "tool": "probe_external_asset", "purpose": "probe"}
                        ],
                    },
                ),
                tool_call("probe", "probe_external_asset", {"asset_id": "primary"}),
                tool_call(
                    "final",
                    "submit_final_answer",
                    {
                        "interpretation": "The full external result is stored as an Artifact.",
                        "claims": [
                            {"evidence_id": "probe-result", "selector": "/data/original_json_bytes"}
                        ],
                        "limitations": ["The model received only a bounded projection."],
                    },
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = create_builtin_registry()
            registry.register_provider(load_mcp_provider(write_manifest(root, "large_text")))
            result = run_general_agent(
                client=client,
                user_request="Bound an MCP result",
                source_path=FIXTURES / "basic.las",
                output_dir=root / "run",
                registry=registry,
                context_budget=ContextBudget(max_tool_result_bytes=256),
            )
            self.assertIn(
                "probe-result-payload",
                {item.artifact_id for item in result.harness.record.artifacts},
            )
            self.assertNotIn("x" * 100, json.dumps(client.calls[-1]))


if __name__ == "__main__":
    unittest.main()

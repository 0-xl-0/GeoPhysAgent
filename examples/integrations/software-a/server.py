"""Example MCP adapter for a hypothetical Software A read-only HTTP API."""

from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.parse import quote

from geophysagent.readonly_api import ReadOnlyApiClient, ReadOnlyApiError


PROTOCOL_VERSION = "2025-11-25"


TOOLS = [
    {
        "name": "list_seismic_datasets",
        "description": "List read-only seismic dataset summaries in one Software A project.",
        "inputSchema": {
            "type": "object",
            "properties": {"project": {"type": "string", "minLength": 1, "maxLength": 200}},
            "required": ["project"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "dataset_count": {"type": "integer"},
                "datasets": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["project", "dataset_count", "datasets"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    },
    {
        "name": "inspect_seismic_dataset",
        "description": "Read metadata for one seismic dataset in a Software A project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "minLength": 1, "maxLength": 200},
                "dataset": {"type": "string", "minLength": 1, "maxLength": 200},
            },
            "required": ["project", "dataset"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "dataset": {"type": "string"},
                "metadata": {"type": "object"},
            },
            "required": ["project", "dataset", "metadata"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    },
]


def send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def send_result(request_id: Any, value: dict[str, Any]) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": value})


def send_error(request_id: Any, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def tool_result(request_id: Any, structured: dict[str, Any]) -> None:
    send_result(
        request_id,
        {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(structured, ensure_ascii=False, separators=(",", ":")),
                }
            ],
            "structuredContent": structured,
            "isError": False,
        },
    )


def tool_failure(request_id: Any, message: str) -> None:
    send_result(
        request_id,
        {"content": [{"type": "text", "text": message}], "isError": True},
    )


def api_client() -> tuple[ReadOnlyApiClient, str]:
    base_url = os.environ.get("SOFTWARE_A_API_BASE", "").rstrip("/")
    if not base_url:
        raise ReadOnlyApiError("SOFTWARE_A_API_BASE is not configured")
    return (
        ReadOnlyApiClient.from_environment(credential_env="SOFTWARE_A_API_TOKEN"),
        base_url,
    )


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    client, base_url = api_client()
    project = str(arguments.get("project", ""))
    if not project:
        raise ReadOnlyApiError("project is required")
    if name == "list_seismic_datasets":
        response = client.get_json(f"{base_url}/projects/{quote(project, safe='')}/seismic").data
        if not isinstance(response, dict) or not isinstance(response.get("datasets"), list):
            raise ReadOnlyApiError("Software A returned an invalid dataset list")
        return {
            "project": project,
            "dataset_count": len(response["datasets"]),
            "datasets": response["datasets"],
        }
    if name == "inspect_seismic_dataset":
        dataset = str(arguments.get("dataset", ""))
        if not dataset:
            raise ReadOnlyApiError("dataset is required")
        response = client.get_json(
            f"{base_url}/projects/{quote(project, safe='')}/seismic/{quote(dataset, safe='')}"
        ).data
        if not isinstance(response, dict):
            raise ReadOnlyApiError("Software A returned invalid dataset metadata")
        return {"project": project, "dataset": dataset, "metadata": response}
    raise ReadOnlyApiError("Unknown Tool")


def main() -> None:
    initialized = False
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        request_id = message.get("id")
        method = message.get("method")
        params = message.get("params", {})
        if method == "initialize":
            send_result(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "software-a-readonly", "version": "0.1.0"},
                },
            )
        elif method == "notifications/initialized":
            initialized = True
        elif method == "notifications/cancelled":
            continue
        elif not initialized:
            send_error(request_id, -32002, "Server is not initialized")
        elif method == "tools/list":
            send_result(request_id, {"tools": TOOLS})
        elif method == "tools/call" and isinstance(params, dict):
            try:
                arguments = params.get("arguments", {})
                if not isinstance(arguments, dict):
                    raise ReadOnlyApiError("Tool arguments must be an object")
                tool_result(request_id, call_tool(str(params.get("name", "")), arguments))
            except ReadOnlyApiError as exc:
                tool_failure(request_id, str(exc))
        elif request_id is not None:
            send_error(request_id, -32601, "Method not found")


if __name__ == "__main__":
    main()

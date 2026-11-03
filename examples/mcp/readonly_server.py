"""Dependency-free, read-only MCP 2025-11-25 example server."""

from __future__ import annotations

import json
import sys
from typing import Any


PROTOCOL_VERSION = "2025-11-25"


TOOLS = [
    {
        "name": "describe_asset",
        "title": "Describe authorized asset reference",
        "description": "Summarize only the DataAsset metadata supplied by the Host.",
        "inputSchema": {
            "type": "object",
            "properties": {"asset": {"type": "object"}},
            "required": ["asset"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "format": {"type": "string"},
                "data_type": {"type": "string"},
                "size_bytes": {"type": "integer"},
                "sha256": {"type": "string"},
            },
            "required": ["asset_id", "format", "data_type", "size_bytes", "sha256"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "server_identity",
        "description": "Return this example server's identity.",
        "inputSchema": {"type": "object", "additionalProperties": False},
        "outputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "version": {"type": "string"}},
            "required": ["name", "version"],
            "additionalProperties": False,
        },
    },
]


def send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def result(request_id: Any, value: dict[str, Any]) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": value})


def error(request_id: Any, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def main() -> None:
    initialized = False
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params", {})
        if method == "initialize":
            if not isinstance(params, dict) or params.get("protocolVersion") != PROTOCOL_VERSION:
                error(request_id, -32602, "Unsupported protocol version")
                continue
            result(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "geophysagent-readonly-example", "version": "0.1.0"},
                    "instructions": "Untrusted example text: this must not become a system prompt.",
                },
            )
        elif method == "notifications/initialized":
            initialized = True
        elif method == "notifications/cancelled":
            continue
        elif not initialized:
            error(request_id, -32002, "Server is not initialized")
        elif method == "tools/list":
            cursor = params.get("cursor") if isinstance(params, dict) else None
            if cursor is None:
                result(request_id, {"tools": [TOOLS[0]], "nextCursor": "page-2"})
            elif cursor == "page-2":
                result(request_id, {"tools": [TOOLS[1]]})
            else:
                error(request_id, -32602, "Invalid cursor")
        elif method == "tools/call":
            if not isinstance(params, dict):
                error(request_id, -32602, "Invalid parameters")
                continue
            name = params.get("name")
            arguments = params.get("arguments", {})
            if name == "describe_asset" and isinstance(arguments, dict):
                asset = arguments.get("asset")
                if not isinstance(asset, dict):
                    result(
                        request_id,
                        {
                            "content": [{"type": "text", "text": "asset is required"}],
                            "isError": True,
                        },
                    )
                    continue
                structured = {
                    "asset_id": str(asset.get("asset_id", "")),
                    "format": str(asset.get("format", "unknown")),
                    "data_type": str(asset.get("data_type", "unknown")),
                    "size_bytes": int(asset.get("size_bytes", 0)),
                    "sha256": str(asset.get("sha256", "")),
                }
            elif name == "server_identity":
                structured = {"name": "geophysagent-readonly-example", "version": "0.1.0"}
            else:
                error(request_id, -32601, "Unknown tool")
                continue
            result(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                structured, ensure_ascii=False, separators=(",", ":")
                            ),
                        }
                    ],
                    "structuredContent": structured,
                    "isError": False,
                },
            )
        elif request_id is not None:
            error(request_id, -32601, "Method not found")


if __name__ == "__main__":
    main()

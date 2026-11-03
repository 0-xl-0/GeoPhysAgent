"""Example wrapper around a personal project's stable pure function."""

from __future__ import annotations

import json
import statistics
import sys
from typing import Any


def personal_curve_summary(values: list[float]) -> dict[str, Any]:
    """This function represents an existing personal-project API."""
    return {
        "count": len(values),
        "minimum": min(values),
        "maximum": max(values),
        "mean": statistics.fmean(values),
    }


TOOL = {
    "name": "curve_summary",
    "description": "Call a personal project's deterministic curve summary function.",
    "inputSchema": {
        "type": "object",
        "properties": {"values": {"type": "array", "items": {"type": "number"}}},
        "required": ["values"],
        "additionalProperties": False,
    },
    "outputSchema": {
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "minimum": {"type": "number"},
            "maximum": {"type": "number"},
            "mean": {"type": "number"},
        },
        "required": ["count", "minimum", "maximum", "mean"],
        "additionalProperties": False,
    },
}


def send(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def main() -> None:
    initialized = False
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params", {})
        if method == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "personal-project-example", "version": "0.1.0"},
                    },
                }
            )
        elif method == "notifications/initialized":
            initialized = True
        elif method == "tools/list" and initialized:
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [TOOL]}})
        elif method == "tools/call" and initialized and params.get("name") == "curve_summary":
            try:
                values = [float(item) for item in params["arguments"]["values"]]
                if not values or len(values) > 10000:
                    raise ValueError("values must contain 1..10000 numbers")
                structured = personal_curve_summary(values)
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [{"type": "text", "text": json.dumps(structured)}],
                            "structuredContent": structured,
                        },
                    }
                )
            except (KeyError, TypeError, ValueError) as exc:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [{"type": "text", "text": str(exc)}],
                            "isError": True,
                        },
                    }
                )
        elif request_id is not None:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "Method not found"},
                }
            )


if __name__ == "__main__":
    main()

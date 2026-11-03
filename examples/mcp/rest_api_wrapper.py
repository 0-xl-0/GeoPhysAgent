"""Design-only example: adapt an approved read-only REST endpoint behind an MCP Tool.

GeoPhysAgent v0.1 intentionally rejects manifests that request network permission, so this
module is not activated by the shipped example manifest. A future reviewed sidecar can call
``fetch_json`` internally while exposing only stable MCP Tool schemas to the Host.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.request import Request, urlopen


def fetch_json(url: str, *, bearer_token: str, timeout: float = 10.0) -> dict[str, Any]:
    request = Request(url, headers={"Authorization": f"Bearer {bearer_token}"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - design-only allowlisted URL
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("REST response must be a JSON object")
    return payload

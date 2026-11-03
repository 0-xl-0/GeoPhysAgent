from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from geophysagent.domain import GeoToolResult
from geophysagent.providers import (
    ProviderBinding,
    ProviderHealth,
    ProviderResourceScope,
    ToolContext,
    ToolDefinition,
    ToolExecution,
    ToolRegistryError,
)
from geophysagent.readonly_api import (
    POLICY_MODE_ENV,
    POLICY_ORIGINS_ENV,
    READ_ONLY_NETWORK_MODE,
    ReadOnlyApiError,
    normalize_http_origin,
)
from geophysagent.schema import SchemaValidationError, validate_json_schema


MCP_PROTOCOL_VERSION = "2025-11-25"
MCP_CLIENT_VERSION = "0.1.0"
MCP_MANIFEST_SCHEMA_VERSION = "1.0"
_SUPPORTED_PROTOCOLS = (MCP_PROTOCOL_VERSION,)
_SECRET_NAME = re.compile(r"(key|token|secret|password|credential|authorization)", re.I)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class McpError(ToolRegistryError):
    """Base error for the bounded MCP Provider implementation."""


class McpProtocolError(McpError):
    pass


class McpTimeoutError(TimeoutError, McpError):
    pass


class McpServerCrashedError(ConnectionError, McpError):
    pass


class McpCancelledError(InterruptedError, McpError):
    pass


class McpToolExecutionError(McpError):
    pass


@dataclass(frozen=True)
class McpToolPolicy:
    remote_name: str
    geo_name: str
    version: str
    model_name: str | None
    purpose: str
    usage_guidance: str
    input_capabilities: tuple[str, ...]
    risk: str
    read_only: bool
    idempotent: bool
    replay_policy: str
    requires_structured_content: bool
    expected_evidence_types: tuple[str, ...]
    expected_artifact_roles: tuple[str, ...]
    asset_reference_parameter: str | None = None
    schema_version: str = "1.0"


@dataclass(frozen=True)
class McpIntegrationManifest:
    source_path: Path
    server_id: str
    version: str
    display_name: str
    command: str
    args: tuple[str, ...]
    cwd: Path
    env_allowlist: tuple[str, ...]
    connect_timeout_seconds: float
    call_timeout_seconds: float
    shutdown_timeout_seconds: float
    max_restarts: int
    permissions: tuple[str, ...]
    network_origins: tuple[str, ...]
    tools: tuple[McpToolPolicy, ...]
    schema_version: str = MCP_MANIFEST_SCHEMA_VERSION
    protocol_version: str = MCP_PROTOCOL_VERSION

    @classmethod
    def load(cls, path: str | Path) -> "McpIntegrationManifest":
        source = Path(path).resolve()
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise McpError(f"Cannot read MCP Integration Manifest: {type(exc).__name__}") from exc
        if not isinstance(payload, dict):
            raise McpError("MCP Integration Manifest must be a JSON object")
        if payload.get("schema_version") != MCP_MANIFEST_SCHEMA_VERSION:
            raise McpError("Unsupported MCP Integration Manifest schema_version")
        if payload.get("protocol_version") != MCP_PROTOCOL_VERSION:
            raise McpError(f"MCP manifest must pin protocol_version={MCP_PROTOCOL_VERSION}")
        server_id = _identifier(payload.get("id"), "server id")
        version = _version(payload.get("version"), "server version")
        transport = payload.get("transport")
        if not isinstance(transport, dict) or transport.get("type") != "stdio":
            raise McpError("v0.1 supports only local MCP stdio transport")
        command = _resolve_command(transport.get("command"), source.parent)
        args_payload = transport.get("args", [])
        if not isinstance(args_payload, list) or not all(
            isinstance(item, str) and len(item) <= 4096 for item in args_payload
        ):
            raise McpError("MCP transport args must be a bounded string array")
        args = tuple(_expand_arg(item, source.parent) for item in args_payload)
        cwd = _resolve_path(transport.get("cwd", "."), source.parent, "MCP cwd")
        command_allowlist = transport.get("command_allowlist")
        if not isinstance(command_allowlist, list) or not command_allowlist:
            raise McpError("MCP transport requires an explicit command_allowlist")
        allowed_commands = {_resolve_command(item, source.parent) for item in command_allowlist}
        if command not in allowed_commands:
            raise McpError("MCP command is not present in command_allowlist")
        cwd_allowlist = transport.get("cwd_allowlist")
        if not isinstance(cwd_allowlist, list) or not cwd_allowlist:
            raise McpError("MCP transport requires an explicit cwd_allowlist")
        roots = tuple(
            _resolve_path(item, source.parent, "MCP cwd allowlist") for item in cwd_allowlist
        )
        if not any(_is_within(cwd, root) for root in roots):
            raise McpError("MCP cwd is outside cwd_allowlist")
        env_allowlist = transport.get("env_allowlist", [])
        if not isinstance(env_allowlist, list) or not all(
            isinstance(item, str) and _ENV_NAME.fullmatch(item) for item in env_allowlist
        ):
            raise McpError("MCP env_allowlist contains an invalid environment variable name")
        if len(env_allowlist) != len(set(env_allowlist)):
            raise McpError("MCP env_allowlist entries must be unique")
        reserved_environment = {POLICY_MODE_ENV, POLICY_ORIGINS_ENV}
        if reserved_environment.intersection(env_allowlist):
            raise McpError("MCP env_allowlist cannot override Host network policy variables")
        permissions_payload = payload.get("permissions", {})
        if not isinstance(permissions_payload, dict):
            raise McpError("MCP permissions must be an object")
        supported_permissions = {
            "read_assets",
            "compute",
            "network_read",
            "write_artifacts",
            "network",
            "external_control",
            "modify",
            "destructive",
        }
        unknown_permissions = sorted(set(permissions_payload) - supported_permissions)
        if unknown_permissions:
            raise McpError(
                "MCP permissions contain unknown fields: " + ", ".join(unknown_permissions)
            )
        if any(not isinstance(enabled, bool) for enabled in permissions_payload.values()):
            raise McpError("MCP permission values must be booleans")
        forbidden = ("write_artifacts", "network", "external_control", "modify", "destructive")
        enabled_forbidden = [name for name in forbidden if permissions_payload.get(name) is True]
        if enabled_forbidden:
            raise McpError("v0.1 MCP disables permission(s): " + ", ".join(enabled_forbidden))
        permissions = tuple(
            sorted(str(name) for name, enabled in permissions_payload.items() if enabled is True)
        )
        network_read = permissions_payload.get("network_read") is True
        network_policy = payload.get("network_policy", {})
        if not isinstance(network_policy, dict):
            raise McpError("MCP network_policy must be an object")
        unknown_network_fields = set(network_policy) - {"allowed_origins"}
        if unknown_network_fields:
            raise McpError(
                "MCP network_policy contains unknown fields: "
                + ", ".join(sorted(unknown_network_fields))
            )
        origins_payload = network_policy.get("allowed_origins", [])
        if not isinstance(origins_payload, list) or not all(
            isinstance(item, str) for item in origins_payload
        ):
            raise McpError("MCP network_policy.allowed_origins must be a string array")
        try:
            network_origins = tuple(normalize_http_origin(item) for item in origins_payload)
        except ReadOnlyApiError as exc:
            raise McpError(str(exc)) from exc
        if len(network_origins) != len(set(network_origins)):
            raise McpError("MCP network origins must be unique")
        if len(network_origins) > 16:
            raise McpError("MCP network policy allows at most 16 origins")
        if network_read and not network_origins:
            raise McpError("MCP network_read permission requires an explicit origin allowlist")
        if network_origins and not network_read:
            raise McpError("MCP network origins require network_read permission")
        timeouts = payload.get("timeouts", {})
        if not isinstance(timeouts, dict):
            raise McpError("MCP timeouts must be an object")
        restart = payload.get("restart", {})
        if not isinstance(restart, dict):
            raise McpError("MCP restart policy must be an object")
        max_restarts = int(restart.get("max_restarts", 0))
        if max_restarts < 0 or max_restarts > 1:
            raise McpError("v0.1 MCP max_restarts must be 0 or 1")
        tools_payload = payload.get("tools")
        if not isinstance(tools_payload, list) or not tools_payload:
            raise McpError("MCP manifest requires a non-empty Tool allowlist")
        tools = tuple(_tool_policy(item) for item in tools_payload)
        if len({item.remote_name for item in tools}) != len(tools):
            raise McpError("MCP remote Tool allowlist entries must be unique")
        if len({item.geo_name for item in tools}) != len(tools):
            raise McpError("MCP Geo Tool names must be unique")
        if len({item.model_name for item in tools if item.model_name}) != len(
            [item for item in tools if item.model_name]
        ):
            raise McpError("MCP model Tool names must be unique")
        return cls(
            source_path=source,
            server_id=server_id,
            version=version,
            display_name=str(payload.get("display_name", server_id))[:200],
            command=command,
            args=args,
            cwd=cwd,
            env_allowlist=tuple(env_allowlist),
            connect_timeout_seconds=_timeout(timeouts, "connect_seconds", 10.0),
            call_timeout_seconds=_timeout(timeouts, "call_seconds", 30.0),
            shutdown_timeout_seconds=_timeout(timeouts, "shutdown_seconds", 3.0),
            max_restarts=max_restarts,
            permissions=permissions,
            network_origins=network_origins,
            tools=tools,
        )

    def sanitized_snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.server_id,
            "version": self.version,
            "transport": "stdio",
            "protocol_version": self.protocol_version,
            "command_sha256": _sha256_text(self.command),
            "args_sha256": _sha256_json(self.args),
            "cwd_sha256": _sha256_text(str(self.cwd)),
            "env_names": list(self.env_allowlist),
            "permissions": list(self.permissions),
            "network_origins": list(self.network_origins),
            "tool_allowlist": [item.geo_name for item in self.tools],
        }


class StdioMcpClient:
    """Small synchronous MCP 2025-11-25 client with strict stdio framing."""

    def __init__(self, manifest: McpIntegrationManifest):
        self.manifest = manifest
        self.process: subprocess.Popen[str] | None = None
        self.protocol_version: str | None = None
        self.server_info: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}
        self.instructions_present = False
        self._messages: queue.Queue[dict[str, Any] | BaseException | None] = queue.Queue()
        self._stderr: list[str] = []
        self._scope = ProviderResourceScope()
        self._request_id = 0
        self._active_request_id: int | None = None
        self._write_lock = threading.Lock()
        self._closed = False

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        if self._closed:
            self._scope = ProviderResourceScope()
            self._closed = False
        env = _minimal_environment(self.manifest.env_allowlist)
        if self.manifest.network_origins:
            env[POLICY_MODE_ENV] = READ_ONLY_NETWORK_MODE
            env[POLICY_ORIGINS_ENV] = json.dumps(
                self.manifest.network_origins,
                ensure_ascii=True,
                separators=(",", ":"),
            )
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                [self.manifest.command, *self.manifest.args],
                cwd=self.manifest.cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise McpError(f"Cannot start MCP server: {type(exc).__name__}") from exc
        self.process = process
        stdout_reader = threading.Thread(target=self._read_stdout, daemon=True)
        stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        stdout_reader.start()
        stderr_reader.start()
        self._scope.own(lambda: stdout_reader.join(timeout=0.5))
        self._scope.own(lambda: stderr_reader.join(timeout=0.5))
        # Resource scopes release in reverse registration order. Close the
        # process streams before joining the reader threads so blocked reads
        # are released promptly during normal shutdown and start rollback.
        self._scope.own(self._close_process)
        result = self.request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "GeoPhysAgent", "version": MCP_CLIENT_VERSION},
            },
            timeout=self.manifest.connect_timeout_seconds,
        )
        protocol = result.get("protocolVersion")
        if protocol not in _SUPPORTED_PROTOCOLS:
            raise McpProtocolError("MCP server selected an unsupported protocol version")
        capabilities = result.get("capabilities")
        if not isinstance(capabilities, dict) or not isinstance(capabilities.get("tools"), dict):
            raise McpProtocolError("MCP server did not negotiate the required tools capability")
        server_info = result.get("serverInfo")
        if not isinstance(server_info, dict) or not isinstance(server_info.get("name"), str):
            raise McpProtocolError("MCP initialize response has invalid serverInfo")
        self.protocol_version = protocol
        self.capabilities = dict(capabilities)
        self.server_info = {
            "name": str(server_info["name"])[:200],
            "version": str(server_info.get("version", "unknown"))[:100],
        }
        self.instructions_present = bool(result.get("instructions"))
        self.notify("notifications/initialized", {})

    def list_tools(self) -> tuple[dict[str, Any], ...]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(100):
            params = {"cursor": cursor} if cursor is not None else {}
            result = self.request(
                "tools/list", params, timeout=self.manifest.connect_timeout_seconds
            )
            page = result.get("tools")
            if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
                raise McpProtocolError("MCP tools/list returned an invalid tools array")
            tools.extend(dict(item) for item in page)
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                return tuple(tools)
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen:
                raise McpProtocolError("MCP tools/list returned an invalid pagination cursor")
            seen.add(next_cursor)
            cursor = next_cursor
        raise McpProtocolError("MCP tools/list exceeded the pagination limit")

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        cancel_requested: Any = None,
    ) -> dict[str, Any]:
        return self.request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout=self.manifest.call_timeout_seconds,
            cancel_requested=cancel_requested,
        )

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
        cancel_requested: Any = None,
    ) -> dict[str, Any]:
        self._ensure_running()
        self._request_id += 1
        request_id = self._request_id
        self._active_request_id = request_id
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        try:
            while True:
                if callable(cancel_requested) and cancel_requested():
                    self.notify(
                        "notifications/cancelled",
                        {"requestId": request_id, "reason": "Host task cancellation requested"},
                    )
                    raise McpCancelledError("MCP request was cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.notify(
                        "notifications/cancelled",
                        {"requestId": request_id, "reason": "Host request timeout"},
                    )
                    raise McpTimeoutError(f"MCP request timed out: {method}")
                try:
                    message = self._messages.get(timeout=min(remaining, 0.1))
                except queue.Empty:
                    self._ensure_running()
                    continue
                if message is None:
                    raise McpServerCrashedError("MCP server closed stdout")
                if isinstance(message, BaseException):
                    raise message
                if "method" in message:
                    self._handle_server_message(message)
                    continue
                if message.get("id") != request_id:
                    raise McpProtocolError("MCP response id does not match the active request")
                if "error" in message:
                    error = message.get("error")
                    code = error.get("code") if isinstance(error, dict) else "unknown"
                    raise McpProtocolError(f"MCP JSON-RPC error code: {code}")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise McpProtocolError("MCP response result must be an object")
                return dict(result)
        finally:
            self._active_request_id = None

    def notify(self, method: str, params: dict[str, Any]) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def cancel_active(self) -> None:
        if self._active_request_id is not None:
            self.notify(
                "notifications/cancelled",
                {"requestId": self._active_request_id, "reason": "Provider cancellation"},
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._scope.dispose()
        finally:
            self.process = None

    def diagnostic_snapshot(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "server_info": dict(self.server_info),
            "capabilities": sorted(self.capabilities),
            "instructions_ignored": self.instructions_present,
            "stderr_line_count": len(self._stderr),
            "stderr_sha256": _sha256_text("\n".join(self._stderr)),
        }

    def _write(self, message: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise McpServerCrashedError("MCP server is not running")
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        with self._write_lock:
            try:
                process.stdin.write(encoded + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise McpServerCrashedError("MCP server stdin is closed") from exc

    def _read_stdout(self) -> None:
        process = self.process
        assert process is not None and process.stdout is not None
        try:
            for line in process.stdout:
                if not line.endswith("\n") or not line.strip():
                    self._messages.put(McpProtocolError("MCP stdout contains invalid framing"))
                    return
                if len(line.encode("utf-8")) > 2 * 1024 * 1024:
                    self._messages.put(McpProtocolError("MCP stdout message exceeds size limit"))
                    return
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._messages.put(McpProtocolError("MCP stdout contains non-JSON data"))
                    return
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    self._messages.put(McpProtocolError("MCP stdout contains invalid JSON-RPC"))
                    return
                self._messages.put(message)
        except (OSError, UnicodeError) as exc:
            self._messages.put(McpProtocolError(f"MCP stdout reader failed: {type(exc).__name__}"))
        finally:
            self._messages.put(None)

    def _read_stderr(self) -> None:
        process = self.process
        assert process is not None and process.stderr is not None
        try:
            for line in process.stderr:
                safe = _redact_diagnostic(line.rstrip())
                if safe:
                    self._stderr.append(safe[:1000])
                    if len(self._stderr) > 100:
                        del self._stderr[:-100]
        except (OSError, UnicodeError):
            return

    def _handle_server_message(self, message: dict[str, Any]) -> None:
        # v0.1 advertises no roots/sampling/elicitation capability. Reject Server requests;
        # notifications (including list_changed/logging) are diagnostic-only and not promoted.
        if "id" in message:
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {"code": -32601, "message": "Client capability disabled"},
                }
            )

    def _ensure_running(self) -> None:
        if self.process is None:
            raise McpServerCrashedError("MCP server has not started")
        code = self.process.poll()
        if code is not None:
            raise McpServerCrashedError(f"MCP server exited with code {code}")

    def _close_process(self) -> None:
        process = self.process
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=self.manifest.shutdown_timeout_seconds)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=self.manifest.shutdown_timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=self.manifest.shutdown_timeout_seconds)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


class McpToolProvider:
    """MCP is one Provider implementation; the Harness has no MCP-specific branch."""

    def __init__(self, manifest: McpIntegrationManifest):
        self.manifest = manifest
        self.provider_id = f"mcp:{manifest.server_id}"
        self.provider_version = manifest.version
        self._client: StdioMcpClient | None = None
        self._definitions: tuple[ToolDefinition, ...] = ()
        self._remote_by_geo: dict[str, dict[str, Any]] = {}
        self._policy_by_geo = {item.geo_name: item for item in manifest.tools}
        self._restart_count = 0
        self._disposed = False

    def prepare(self) -> None:
        if self._client is not None and self._client.process is not None:
            return
        client = StdioMcpClient(self.manifest)
        try:
            client.start()
            remote_tools = client.list_tools()
            definitions, remote_by_geo = self._map_tools(remote_tools)
        except Exception:
            client.close()
            raise
        self._client = client
        self._definitions = definitions
        self._remote_by_geo = remote_by_geo
        self._disposed = False

    def health(self) -> ProviderHealth:
        if self._client is None or self._client.process is None:
            return ProviderHealth("degraded", "MCP client is not prepared")
        if self._client.process.poll() is not None:
            return ProviderHealth("degraded", "MCP server process exited")
        return ProviderHealth("ready")

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._definitions

    def execute(self, name: str, arguments: dict[str, Any], context: ToolContext) -> ToolExecution:
        policy = self._policy_by_geo.get(name)
        remote = self._remote_by_geo.get(name)
        if policy is None or remote is None:
            raise McpError(f"MCP Tool is not authorized: {name}")
        client = self._ready_client()
        try:
            remote_arguments = dict(arguments)
            if policy.asset_reference_parameter is not None:
                asset_id = remote_arguments.pop("asset_id", None)
                asset = context.assets.get(str(asset_id))
                if asset is None:
                    raise McpError(f"Asset is not authorized: {asset_id}")
                remote_arguments[policy.asset_reference_parameter] = {
                    "asset_id": asset.asset_id,
                    "media_type": asset.media_type,
                    "format": asset.format,
                    "data_type": asset.data_type,
                    "size_bytes": asset.size_bytes,
                    "sha256": asset.sha256,
                    "capabilities": list(asset.capabilities),
                }
            result = client.call_tool(
                policy.remote_name,
                remote_arguments,
                cancel_requested=context.cancel_requested,
            )
            data = self._map_result(name, policy, remote, result, context)
        except (McpServerCrashedError, McpTimeoutError, McpCancelledError, McpProtocolError):
            # A timeout, cancellation or protocol failure can leave an unread late response
            # on stdout. Quarantine that connection and never replay the ambiguous call here;
            # only a later Harness Attempt may consume the bounded restart budget.
            self.stop()
            raise
        geo_result = GeoToolResult(
            tool_name=name,
            tool_version=policy.version,
            status="success",
            provider=self.provider_id,
            data=data,
        )
        return ToolExecution(geo_result, data.get("structured_content"))

    def cancel(self) -> None:
        if self._client is not None:
            self._client.cancel_active()

    def stop(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self.stop()

    def binding_for(self, definition: ToolDefinition) -> ProviderBinding:
        client = self._client
        server_version = (
            str(client.server_info.get("version", "unknown")) if client is not None else None
        )
        return ProviderBinding(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            tool_name=definition.name,
            tool_version=definition.version,
            tool_schema_version=definition.schema_version,
            permissions=self.manifest.permissions,
            network_origins=self.manifest.network_origins,
            server_id=self.manifest.server_id,
            server_version=server_version,
            protocol_version=(client.protocol_version if client is not None else None),
            cwd_fingerprint=_sha256_text(str(self.manifest.cwd)),
        )

    def diagnostics(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "manifest": self.manifest.sanitized_snapshot(),
            "client": self._client.diagnostic_snapshot() if self._client else None,
        }

    def _ready_client(self) -> StdioMcpClient:
        if self._client is not None and self.health().ready:
            return self._client
        if self._restart_count >= self.manifest.max_restarts:
            raise McpServerCrashedError("MCP server is unavailable and restart budget is exhausted")
        self._restart_count += 1
        self.stop()
        self.prepare()
        assert self._client is not None
        return self._client

    def _map_tools(
        self, remote_tools: tuple[dict[str, Any], ...]
    ) -> tuple[tuple[ToolDefinition, ...], dict[str, dict[str, Any]]]:
        by_name: dict[str, dict[str, Any]] = {}
        for remote in remote_tools:
            name = remote.get("name")
            if not isinstance(name, str) or not name or name in by_name:
                raise McpProtocolError("MCP tools/list contains invalid or duplicate Tool names")
            by_name[name] = remote
        definitions: list[ToolDefinition] = []
        mapped: dict[str, dict[str, Any]] = {}
        for policy in self.manifest.tools:
            remote = by_name.get(policy.remote_name)
            if remote is None:
                raise McpError(f"Authorized MCP Tool was not discovered: {policy.remote_name}")
            remote_input_schema = remote.get("inputSchema")
            if (
                not isinstance(remote_input_schema, dict)
                or remote_input_schema.get("type") != "object"
            ):
                raise McpProtocolError(f"MCP Tool {policy.remote_name} has invalid inputSchema")
            input_schema = json.loads(json.dumps(remote_input_schema))
            if policy.asset_reference_parameter is not None:
                properties = input_schema.setdefault("properties", {})
                if policy.asset_reference_parameter not in properties:
                    raise McpProtocolError(
                        f"MCP Tool {policy.remote_name} does not declare its asset reference"
                    )
                properties.pop(policy.asset_reference_parameter)
                properties["asset_id"] = {
                    "type": "string",
                    "description": "Harness-authorized DataAsset identifier",
                }
                required = [
                    item
                    for item in input_schema.get("required", [])
                    if item != policy.asset_reference_parameter
                ]
                if "asset_id" not in required:
                    required.append("asset_id")
                input_schema["required"] = required
            output_schema = remote.get("outputSchema")
            if output_schema is not None and (
                not isinstance(output_schema, dict) or output_schema.get("type") != "object"
            ):
                raise McpProtocolError(f"MCP Tool {policy.remote_name} has invalid outputSchema")
            definition = ToolDefinition(
                name=policy.geo_name,
                version=policy.version,
                description=policy.purpose or policy.geo_name,
                input_schema=input_schema,
                output_schema={
                    "type": "object",
                    "properties": {
                        "structured_content": output_schema or {"type": "object"},
                        "content_summary": {"type": "string"},
                        "mcp": {"type": "object"},
                        "resource_references": {"type": "array"},
                    },
                    "required": ["structured_content", "content_summary", "mcp"],
                    "additionalProperties": False,
                },
                risk=policy.risk,
                read_only=policy.read_only,
                idempotent=policy.idempotent,
                replay_policy=policy.replay_policy,
                model_name=policy.model_name,
                purpose=policy.purpose,
                usage_guidance=policy.usage_guidance,
                input_capabilities=policy.input_capabilities,
                expected_evidence_types=policy.expected_evidence_types,
                expected_artifact_roles=policy.expected_artifact_roles,
                schema_version=policy.schema_version,
            )
            definitions.append(definition)
            mapped[policy.geo_name] = dict(remote)
        return tuple(definitions), mapped

    def _map_result(
        self,
        name: str,
        policy: McpToolPolicy,
        remote: dict[str, Any],
        result: dict[str, Any],
        context: ToolContext,
    ) -> dict[str, Any]:
        content = result.get("content", [])
        if not isinstance(content, list) or not all(isinstance(item, dict) for item in content):
            raise McpProtocolError("MCP tools/call returned invalid content")
        if result.get("isError") is True:
            raise McpToolExecutionError(
                "MCP Tool reported an execution error: " + _content_summary(content, 500)
            )
        structured = result.get("structuredContent")
        if policy.requires_structured_content and not isinstance(structured, dict):
            raise McpProtocolError("MCP Tool result requires object structuredContent")
        if structured is None:
            structured = {}
        if not isinstance(structured, dict):
            raise McpProtocolError("MCP 2025-11-25 structuredContent must be a JSON object")
        encoded = json.dumps(structured, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > 1024 * 1024:
            raise McpProtocolError("MCP structuredContent exceeds the Host result limit")
        output_schema = remote.get("outputSchema")
        if isinstance(output_schema, dict):
            try:
                validate_json_schema(
                    structured,
                    output_schema,
                    subject=f"MCP Tool {policy.remote_name} structuredContent",
                )
            except SchemaValidationError as exc:
                raise McpProtocolError(str(exc)) from exc
        references = _resource_references(content, context.output_dir)
        client = self._client
        assert client is not None
        return {
            "structured_content": structured,
            "content_summary": _content_summary(content, 8 * 1024),
            "mcp": {
                "server_id": self.manifest.server_id,
                "server_version": str(client.server_info.get("version", "unknown")),
                "protocol_version": client.protocol_version,
                "remote_tool": policy.remote_name,
                "geo_tool": name,
                "tool_version": policy.version,
                "schema_version": policy.schema_version,
                "instructions_ignored": client.instructions_present,
            },
            "resource_references": references,
        }


def load_mcp_provider(path: str | Path) -> McpToolProvider:
    return McpToolProvider(McpIntegrationManifest.load(path))


def _tool_policy(value: Any) -> McpToolPolicy:
    if not isinstance(value, dict):
        raise McpError("MCP Tool allowlist entry must be an object")
    remote_name = _identifier(value.get("remote_name"), "remote Tool name")
    geo_name = _identifier(value.get("geo_name"), "Geo Tool name")
    model_value = value.get("model_name")
    model_name = _identifier(model_value, "model Tool name") if model_value is not None else None
    risk = str(value.get("risk", "read"))
    read_only = bool(value.get("read_only", True))
    if risk not in {"read", "compute"} or not read_only:
        raise McpError("v0.1 MCP authorizes only read-only read/compute Tools")
    replay_policy = str(value.get("replay_policy", "safe"))
    idempotent = bool(value.get("idempotent", True))
    if replay_policy not in {"safe", "manual", "never"}:
        raise McpError("MCP Tool replay_policy is invalid")
    if replay_policy == "safe" and not idempotent:
        raise McpError("A safe MCP Tool must be idempotent")
    return McpToolPolicy(
        remote_name=remote_name,
        geo_name=geo_name,
        version=_version(value.get("version"), "Tool version"),
        model_name=model_name,
        purpose=str(value.get("purpose", ""))[:1000],
        usage_guidance=str(value.get("usage_guidance", ""))[:2000],
        input_capabilities=_string_tuple(value.get("input_capabilities", [])),
        risk=risk,
        read_only=read_only,
        idempotent=idempotent,
        replay_policy=replay_policy,
        requires_structured_content=bool(value.get("requires_structured_content", True)),
        expected_evidence_types=_string_tuple(
            value.get("expected_evidence_types", ["tool_result"])
        ),
        expected_artifact_roles=_string_tuple(value.get("expected_artifact_roles", [])),
        asset_reference_parameter=(
            _identifier(value["asset_reference_parameter"], "asset reference parameter")
            if value.get("asset_reference_parameter") is not None
            else None
        ),
        schema_version=_version(value.get("schema_version", "1.0"), "Tool schema version"),
    )


def _minimal_environment(names: tuple[str, ...]) -> dict[str, str]:
    result = {name: os.environ[name] for name in names if name in os.environ}
    if os.name == "nt" and "SystemRoot" in os.environ:
        result.setdefault("SystemRoot", os.environ["SystemRoot"])
    result["PYTHONUNBUFFERED"] = "1"
    result["PYTHONDONTWRITEBYTECODE"] = "1"
    return result


def _resolve_command(value: Any, base: Path) -> str:
    if value == "{python}":
        return str(Path(sys.executable).resolve())
    if not isinstance(value, str) or not value:
        raise McpError("MCP command must be an explicit string")
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    resolved = path.resolve()
    if not resolved.is_file():
        raise McpError("MCP command does not exist")
    return str(resolved)


def _expand_arg(value: str, base: Path) -> str:
    return value.replace("{manifest_dir}", str(base)).replace("{python}", sys.executable)


def _resolve_path(value: Any, base: Path, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise McpError(f"{label} must be a non-empty path")
    path = Path(value)
    resolved = (base / path).resolve() if not path.is_absolute() else path.resolve()
    if not resolved.is_dir():
        raise McpError(f"{label} does not exist")
    return resolved


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise McpError(f"Invalid {label}")
    return value


def _version(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 100:
        raise McpError(f"Invalid {label}")
    return value


def _timeout(payload: dict[str, Any], name: str, default: float) -> float:
    try:
        value = float(payload.get(name, default))
    except (TypeError, ValueError) as exc:
        raise McpError(f"MCP timeout {name} is invalid") from exc
    if value <= 0 or value > 3600:
        raise McpError(f"MCP timeout {name} is outside the allowed range")
    return value


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item and len(item) <= 200 for item in value
    ):
        raise McpError("MCP manifest metadata must be a bounded string array")
    return tuple(value)


def _redact_diagnostic(value: str) -> str:
    safe = value
    for name, secret in os.environ.items():
        if secret and len(secret) >= 8 and _SECRET_NAME.search(name):
            safe = safe.replace(secret, "[redacted]")
    safe = re.sub(
        r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        safe,
    )
    return safe


def _content_summary(content: list[dict[str, Any]], limit: int) -> str:
    parts: list[str] = []
    for item in content:
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            parts.append(str(item["text"]))
    encoded = "\n".join(parts).encode("utf-8")
    if len(encoded) <= limit:
        return encoded.decode("utf-8")
    return (
        encoded[:limit].decode("utf-8", errors="ignore")
        + f"\n[truncated; utf8_bytes={len(encoded)}; sha256={hashlib.sha256(encoded).hexdigest()}]"
    )


def _resource_references(content: list[dict[str, Any]], output_dir: Path) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    output = output_dir.resolve()
    for item in content:
        uri: str | None = None
        media_type: str | None = None
        if item.get("type") == "resource_link" and isinstance(item.get("uri"), str):
            uri = str(item["uri"])
            media_type = str(item.get("mimeType", "application/octet-stream"))
        elif item.get("type") == "resource" and isinstance(item.get("resource"), dict):
            resource = item["resource"]
            if isinstance(resource.get("uri"), str):
                uri = str(resource["uri"])
                media_type = str(resource.get("mimeType", "application/octet-stream"))
        if uri is None:
            continue
        parsed = urlparse(uri)
        authorized = False
        relative_path: str | None = None
        if parsed.scheme == "file":
            try:
                candidate = Path(unquote(parsed.path.lstrip("/"))).resolve()
                candidate.relative_to(output)
                authorized = candidate.is_file()
                if authorized:
                    relative_path = candidate.relative_to(output).as_posix()
            except (OSError, ValueError):
                authorized = False
        references.append(
            {
                "uri_sha256": _sha256_text(uri),
                "media_type": media_type,
                "authorized_task_artifact": authorized,
                "relative_path": relative_path,
            }
        )
    return references


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_text(json.dumps(value, sort_keys=True, separators=(",", ":")))

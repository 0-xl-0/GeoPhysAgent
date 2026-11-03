from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from geophysagent.assets import DataAsset
from geophysagent.domain import GeoToolResult
from geophysagent.schema import (
    SchemaValidationError,
    validate_json_schema,
    validate_json_schema_definition,
)


class ToolRegistryError(ValueError):
    """Raised for invalid registration, invocation or provider behavior."""


@dataclass(frozen=True)
class ToolDefinition:
    """Versioned Host-owned contract presented to both the model and Harness."""

    name: str
    version: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    risk: str = "read"
    read_only: bool = True
    idempotent: bool = True
    replay_policy: str = "safe"
    model_name: str | None = None
    purpose: str = ""
    usage_guidance: str = ""
    input_capabilities: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()
    expected_evidence_types: tuple[str, ...] = ("tool_result",)
    expected_artifact_roles: tuple[str, ...] = ()
    schema_version: str = "1.0"

    def model_description(self) -> str:
        parts = [self.description.strip()]
        if self.purpose.strip() and self.purpose.strip() not in parts:
            parts.append(f"用途：{self.purpose.strip()}")
        if self.usage_guidance.strip():
            parts.append(f"使用约束：{self.usage_guidance.strip()}")
        parts.append(
            "风险与副作用：" + self.risk + ("；只读" if self.read_only else "；可能产生副作用")
        )
        return " ".join(item for item in parts if item)


@dataclass(frozen=True)
class ProviderHealth:
    status: str
    message: str = ""

    @property
    def ready(self) -> bool:
        return self.status == "ready"


@dataclass(frozen=True)
class ProviderEvent:
    provider_id: str
    state: str
    data: dict[str, Any]


@dataclass(frozen=True)
class ProviderBinding:
    provider_id: str
    provider_version: str
    tool_name: str
    tool_version: str
    tool_schema_version: str
    permissions: tuple[str, ...] = ()
    network_origins: tuple[str, ...] = ()
    server_id: str | None = None
    server_version: str | None = None
    protocol_version: str | None = None
    cwd_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProviderResourceScope:
    """LIFO ownership boundary for Provider processes, readers and temporary resources."""

    def __init__(self) -> None:
        self._releases: list[Callable[[], None]] = []
        self._disposed = False

    def own(self, release: Callable[[], None]) -> None:
        if self._disposed:
            raise ToolRegistryError("Provider resource scope is already disposed")
        self._releases.append(release)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        first_error: Exception | None = None
        for release in reversed(self._releases):
            try:
                release()
            except Exception as exc:  # cleanup is best-effort, but the first issue is reportable
                if first_error is None:
                    first_error = exc
        self._releases.clear()
        if first_error is not None:
            raise ToolRegistryError(f"Provider cleanup failed: {type(first_error).__name__}")


@dataclass
class ToolContext:
    assets: dict[str, DataAsset]
    output_dir: Path
    cancel_requested: Callable[[], bool] | None = None


@dataclass(frozen=True)
class ToolExecution:
    result: GeoToolResult
    value: Any = None


class ToolProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    def definitions(self) -> tuple[ToolDefinition, ...]: ...

    def execute(
        self, name: str, arguments: dict[str, Any], context: ToolContext
    ) -> ToolExecution: ...


ToolHandler = Callable[[dict[str, Any], ToolContext], ToolExecution]


class BuiltInToolProvider:
    provider_id = "builtin"
    provider_version = "0.1.0"

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def prepare(self) -> None:
        """Built-ins have no external resources to acquire."""

    def health(self) -> ProviderHealth:
        return ProviderHealth("ready")

    def cancel(self) -> None:
        """Built-ins observe cancellation only at Harness safe boundaries."""

    def stop(self) -> None:
        """Built-ins have no external resources to release."""

    def dispose(self) -> None:
        self.stop()

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        _validate_definition(definition)
        if definition.name in self._definitions:
            raise ToolRegistryError(f"Tool already registered: {definition.name}")
        self._definitions[definition.name] = definition
        self._handlers[definition.name] = handler

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._definitions.values())

    def execute(self, name: str, arguments: dict[str, Any], context: ToolContext) -> ToolExecution:
        handler = self._handlers.get(name)
        if handler is None:
            raise ToolRegistryError(f"Unknown built-in tool: {name}")
        return handler(arguments, context)

    def binding_for(self, definition: ToolDefinition) -> ProviderBinding:
        return ProviderBinding(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            tool_name=definition.name,
            tool_version=definition.version,
            tool_schema_version=definition.schema_version,
            permissions=("read",) if definition.read_only else (definition.risk,),
        )


class ToolProviderRegistry:
    """Atomically activates Providers and exposes an immutable-per-run Tool view."""

    def __init__(self) -> None:
        self._providers: dict[str, ToolProvider] = {}
        self._tools: dict[str, tuple[ToolProvider, ToolDefinition]] = {}
        self._events: list[ProviderEvent] = []
        self._disposed = False

    def register_provider(self, provider: ToolProvider) -> None:
        self.activate_provider(provider)

    def activate_provider(self, provider: ToolProvider) -> None:
        """Prepare, check and validate a Provider before publishing any of its Tools."""

        if self._disposed:
            raise ToolRegistryError("Tool Provider Registry has been disposed")
        provider_id = str(getattr(provider, "provider_id", "")).strip()
        if not provider_id:
            raise ToolRegistryError("Provider id cannot be empty")
        if provider_id in self._providers:
            raise ToolRegistryError(f"Provider already registered: {provider_id}")
        self._emit(provider_id, "discovering")
        prepared = False
        try:
            self._emit(provider_id, "starting")
            prepare = getattr(provider, "prepare", None)
            if callable(prepare):
                prepared = True
                prepare()
            else:
                prepared = True
            health = _provider_health(provider)
            if not health.ready:
                raise ToolRegistryError(
                    f"Provider {provider_id} health check failed: {health.message or health.status}"
                )
            definitions = tuple(provider.definitions())
            if not definitions:
                raise ToolRegistryError(f"Provider {provider_id} exposes no authorized Tools")
            staged: dict[str, tuple[ToolProvider, ToolDefinition]] = {}
            staged_model_names: set[str] = set()
            current_model_names = {
                definition.model_name
                for _, definition in self._tools.values()
                if definition.model_name is not None
            }
            for definition in definitions:
                _validate_definition(definition)
                if definition.name in self._tools or definition.name in staged:
                    raise ToolRegistryError(f"Tool name conflict: {definition.name}")
                if definition.model_name is not None:
                    if (
                        definition.model_name in current_model_names
                        or definition.model_name in staged_model_names
                    ):
                        raise ToolRegistryError(
                            f"Model Tool name conflict: {definition.model_name}"
                        )
                    staged_model_names.add(definition.model_name)
                staged[definition.name] = (provider, definition)
            # Atomic publication: no visible Registry mutation occurs before this boundary.
            self._providers = {**self._providers, provider_id: provider}
            self._tools = {**self._tools, **staged}
            self._emit(provider_id, "ready", tool_count=len(definitions))
        except Exception as exc:
            self._emit(provider_id, "degraded", category=type(exc).__name__)
            if prepared:
                _dispose_provider(provider)
            self._emit(provider_id, "stopped")
            if isinstance(exc, ToolRegistryError):
                raise
            raise ToolRegistryError(f"Provider {provider_id} activation failed: {exc}") from exc

    def freeze(self) -> "ToolProviderRegistry":
        """Return a Registry whose visible Tool/Provider bindings cannot change."""

        snapshot = ToolProviderRegistry()
        snapshot._providers = dict(self._providers)
        snapshot._tools = dict(self._tools)
        return snapshot

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(entry[1] for entry in self._tools.values())

    def model_definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(item for item in self.definitions() if item.model_name is not None)

    def definition(self, name: str) -> ToolDefinition:
        entry = self._tools.get(name)
        if entry is None:
            raise ToolRegistryError(f"Tool is not registered: {name}")
        return entry[1]

    def provider_id_for(self, name: str) -> str:
        entry = self._tools.get(name)
        if entry is None:
            raise ToolRegistryError(f"Tool is not registered: {name}")
        return entry[0].provider_id

    def binding_for(self, name: str) -> ProviderBinding:
        entry = self._tools.get(name)
        if entry is None:
            raise ToolRegistryError(f"Tool is not registered: {name}")
        provider, definition = entry
        factory = getattr(provider, "binding_for", None)
        if callable(factory):
            binding = factory(definition)
            if isinstance(binding, ProviderBinding):
                return binding
        return ProviderBinding(
            provider_id=provider.provider_id,
            provider_version=str(getattr(provider, "provider_version", "unknown")),
            tool_name=definition.name,
            tool_version=definition.version,
            tool_schema_version=definition.schema_version,
            permissions=("read",) if definition.read_only else (definition.risk,),
        )

    def binding_snapshot(self, names: tuple[str, ...] | None = None) -> tuple[ProviderBinding, ...]:
        selected = names if names is not None else tuple(self._tools)
        return tuple(self.binding_for(name) for name in selected)

    def invoke(self, name: str, arguments: dict[str, Any], context: ToolContext) -> ToolExecution:
        entry = self._tools.get(name)
        if entry is None:
            raise ToolRegistryError(f"Tool is not registered: {name}")
        provider, definition = entry
        if context.cancel_requested is not None and context.cancel_requested():
            self.cancel(provider.provider_id)
            raise InterruptedError("Tool invocation was cancelled before execution")
        try:
            validate_json_schema(
                arguments,
                definition.input_schema,
                subject=f"Tool {name} arguments",
            )
        except SchemaValidationError as exc:
            raise ToolRegistryError(str(exc)) from exc
        execution = provider.execute(name, arguments, context)
        if execution.result.tool_name != name:
            raise ToolRegistryError(
                f"Provider {provider.provider_id} returned a result for {execution.result.tool_name}"
            )
        if execution.result.provider != provider.provider_id:
            raise ToolRegistryError(
                f"Provider {provider.provider_id} returned provider identity "
                f"{execution.result.provider}"
            )
        try:
            json.dumps(execution.result.data, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ToolRegistryError(
                f"Tool {name} result data must be finite JSON-serializable content: {exc}"
            ) from exc
        if definition.output_schema is not None:
            try:
                validate_json_schema(
                    execution.result.data,
                    definition.output_schema,
                    subject=f"Tool {name} result",
                )
            except SchemaValidationError as exc:
                raise ToolRegistryError(str(exc)) from exc
        return execution

    def cancel(self, provider_id: str | None = None) -> None:
        providers = (
            [self._providers[provider_id]]
            if provider_id is not None and provider_id in self._providers
            else list(self._providers.values())
        )
        for provider in providers:
            cancel = getattr(provider, "cancel", None)
            if callable(cancel):
                cancel()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        for provider_id, provider in reversed(tuple(self._providers.items())):
            self._emit(provider_id, "stopping")
            _dispose_provider(provider)
            self._emit(provider_id, "stopped")

    def drain_events(self) -> tuple[ProviderEvent, ...]:
        events = tuple(self._events)
        self._events.clear()
        return events

    def _emit(self, provider_id: str, state: str, **data: Any) -> None:
        self._events.append(ProviderEvent(provider_id, state, dict(data)))


def _provider_health(provider: ToolProvider) -> ProviderHealth:
    callback = getattr(provider, "health", None)
    if not callable(callback):
        return ProviderHealth("ready", "legacy lifecycle adapter")
    result = callback()
    if isinstance(result, ProviderHealth):
        return result
    if result is True:
        return ProviderHealth("ready")
    if result is False:
        return ProviderHealth("degraded")
    raise ToolRegistryError(f"Provider {provider.provider_id} returned invalid health status")


def _dispose_provider(provider: ToolProvider) -> None:
    dispose = getattr(provider, "dispose", None)
    if callable(dispose):
        dispose()
        return
    stop = getattr(provider, "stop", None)
    if callable(stop):
        stop()


def _validate_definition(definition: ToolDefinition) -> None:
    if not definition.name or any(char.isspace() for char in definition.name):
        raise ToolRegistryError("Tool name must be a stable non-empty identifier")
    if not definition.version or not definition.schema_version:
        raise ToolRegistryError(f"Tool {definition.name} requires version metadata")
    if definition.model_name is not None and (
        not definition.model_name or any(char.isspace() for char in definition.model_name)
    ):
        raise ToolRegistryError(f"Tool {definition.name} has invalid model_name")
    if definition.replay_policy not in {"safe", "manual", "never"}:
        raise ToolRegistryError(
            f"Tool {definition.name} has invalid replay_policy: {definition.replay_policy}"
        )
    if definition.replay_policy == "safe" and not definition.idempotent:
        raise ToolRegistryError(
            f"Tool {definition.name} cannot be safely replayed when idempotent is false"
        )
    if definition.risk not in {
        "read",
        "compute",
        "code_execution",
        "export",
        "modify",
        "destructive",
    }:
        raise ToolRegistryError(f"Tool {definition.name} has unsupported risk: {definition.risk}")
    for label, schema in (("input", definition.input_schema), ("output", definition.output_schema)):
        if schema is None:
            continue
        try:
            encoded = json.dumps(schema, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ToolRegistryError(
                f"Tool {definition.name} {label} schema is not JSON-serializable"
            ) from exc
        if len(encoded.encode("utf-8")) > 256 * 1024:
            raise ToolRegistryError(f"Tool {definition.name} {label} schema is too large")
        try:
            validate_json_schema_definition(
                schema,
                subject=f"Tool {definition.name} {label} schema",
            )
        except SchemaValidationError as exc:
            raise ToolRegistryError(str(exc)) from exc

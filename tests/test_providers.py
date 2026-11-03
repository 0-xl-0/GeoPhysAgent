from __future__ import annotations

import unittest

from geophysagent.domain import GeoToolResult
from geophysagent.providers import (
    ProviderHealth,
    ToolDefinition,
    ToolExecution,
    ToolProviderRegistry,
    ToolRegistryError,
)


def definition(name: str, model_name: str | None = None) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        version="1.0.0",
        description="test",
        input_schema={"type": "object"},
        model_name=model_name,
    )


class FakeProvider:
    provider_version = "1.0.0"

    def __init__(
        self,
        provider_id: str,
        definitions: tuple[ToolDefinition, ...],
        *,
        fail_prepare: bool = False,
        healthy: bool = True,
    ):
        self.provider_id = provider_id
        self._definitions = definitions
        self.fail_prepare = fail_prepare
        self.healthy = healthy
        self.prepared = 0
        self.cancelled = 0
        self.disposed = 0

    def prepare(self) -> None:
        self.prepared += 1
        if self.fail_prepare:
            raise RuntimeError("partial start")

    def health(self) -> ProviderHealth:
        return ProviderHealth("ready" if self.healthy else "degraded", "fake health")

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._definitions

    def execute(self, name, arguments, context):
        return ToolExecution(GeoToolResult(name, "1.0.0", "success", {}, provider=self.provider_id))

    def cancel(self) -> None:
        self.cancelled += 1

    def dispose(self) -> None:
        self.disposed += 1


class ProviderLifecycleTests(unittest.TestCase):
    def test_activation_is_atomic_on_health_failure_and_releases_resources(self) -> None:
        registry = ToolProviderRegistry()
        provider = FakeProvider("bad", (definition("bad.tool"),), healthy=False)
        with self.assertRaises(ToolRegistryError):
            registry.register_provider(provider)
        self.assertEqual(registry.definitions(), ())
        self.assertEqual(provider.disposed, 1)
        self.assertEqual(
            [item.state for item in registry.drain_events()],
            ["discovering", "starting", "degraded", "stopped"],
        )

    def test_partial_start_and_conflict_roll_back_without_partial_visibility(self) -> None:
        registry = ToolProviderRegistry()
        first = FakeProvider("first", (definition("same.tool", "same_model"),))
        registry.register_provider(first)
        partial = FakeProvider("partial", (definition("never.visible"),), fail_prepare=True)
        with self.assertRaises(ToolRegistryError):
            registry.register_provider(partial)
        conflict = FakeProvider("conflict", (definition("same.tool"),))
        with self.assertRaises(ToolRegistryError):
            registry.register_provider(conflict)
        self.assertEqual([item.name for item in registry.definitions()], ["same.tool"])
        self.assertEqual(partial.disposed, 1)
        self.assertEqual(conflict.disposed, 1)

    def test_cancel_and_repeated_dispose_are_idempotent(self) -> None:
        registry = ToolProviderRegistry()
        provider = FakeProvider("fake", (definition("fake.tool"),))
        registry.register_provider(provider)
        registry.cancel()
        registry.dispose()
        registry.dispose()
        self.assertEqual(provider.cancelled, 1)
        self.assertEqual(provider.disposed, 1)

    def test_freeze_prevents_later_registration_from_changing_visible_tools(self) -> None:
        registry = ToolProviderRegistry()
        registry.register_provider(FakeProvider("one", (definition("one.tool"),)))
        frozen = registry.freeze()
        registry.register_provider(FakeProvider("two", (definition("two.tool"),)))
        self.assertEqual([item.name for item in frozen.definitions()], ["one.tool"])

    def test_unsupported_schema_is_rejected_before_tool_becomes_visible(self) -> None:
        registry = ToolProviderRegistry()
        invalid = ToolDefinition(
            name="invalid.schema",
            version="1.0.0",
            description="test",
            input_schema={"type": "object", "$ref": "#/$defs/input"},
        )
        provider = FakeProvider("invalid", (invalid,))
        with self.assertRaises(ToolRegistryError):
            registry.register_provider(provider)
        self.assertEqual(registry.definitions(), ())
        self.assertEqual(provider.disposed, 1)


if __name__ == "__main__":
    unittest.main()

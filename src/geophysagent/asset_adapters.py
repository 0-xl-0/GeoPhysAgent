from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


ASSET_INSPECT = "asset.inspect"
TABULAR_INSPECT = "tabular.inspect"
TABULAR_READ = "tabular.read"
TABLE_PREPROCESS = "table.preprocess"
EXPLORE_PYTHON = "explore.python"
WELL_LOG_QC = "well_log.qc"
SEGY_INSPECT = "seismic.segy.inspect"


class AssetAdapterError(ValueError):
    """Base error for asset detection and capability routing."""


class UnsupportedAssetCapabilityError(AssetAdapterError):
    def __init__(
        self,
        *,
        format_name: str,
        data_type: str,
        adapter_id: str,
        capability: str,
        available: tuple[str, ...],
    ) -> None:
        self.format_name = format_name
        self.data_type = data_type
        self.adapter_id = adapter_id
        self.capability = capability
        self.available = available
        available_text = ", ".join(available) if available else "registration only"
        super().__init__(
            f"Asset format '{format_name}' is recognized as {data_type} by adapter "
            f"'{adapter_id}', but capability '{capability}' is not implemented. "
            f"Available capabilities: {available_text}. No parser, tool, or model was invoked."
        )


@dataclass(frozen=True)
class AssetDescriptor:
    adapter_id: str
    data_type: str
    format: str
    media_type: str
    capabilities: tuple[str, ...]
    confidence: str = "extension"


class AssetAdapter(Protocol):
    @property
    def adapter_id(self) -> str: ...

    def detect(self, path: Path, sample: bytes) -> AssetDescriptor | None: ...


class AssetAdapterRegistry:
    """Routes an asset by type before a format-specific parser is allowed to run."""

    def __init__(self) -> None:
        self._adapters: dict[str, AssetAdapter] = {}

    def register(self, adapter: AssetAdapter) -> None:
        if adapter.adapter_id in self._adapters:
            raise AssetAdapterError(f"Asset adapter already registered: {adapter.adapter_id}")
        self._adapters[adapter.adapter_id] = adapter

    def detect(self, path: Path) -> AssetDescriptor:
        try:
            with path.open("rb") as handle:
                sample = handle.read(8192)
        except OSError as exc:
            raise AssetAdapterError(f"Cannot inspect input asset: {exc}") from exc
        for adapter in self._adapters.values():
            descriptor = adapter.detect(path, sample)
            if descriptor is not None:
                return descriptor
        suffix = path.suffix.lower().lstrip(".") or "unknown"
        return AssetDescriptor(
            adapter_id="unknown",
            data_type="unknown",
            format=suffix,
            media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            capabilities=(ASSET_INSPECT,),
            confidence="fallback",
        )

    def require_capability(self, asset: object, capability: str) -> None:
        available = tuple(getattr(asset, "capabilities", ()))
        if capability in available:
            return
        raise UnsupportedAssetCapabilityError(
            format_name=str(getattr(asset, "format", "unknown")),
            data_type=str(getattr(asset, "data_type", "unknown")),
            adapter_id=str(getattr(asset, "adapter_id", "unknown")),
            capability=capability,
            available=available,
        )

    def adapter_ids(self) -> tuple[str, ...]:
        return tuple(self._adapters)


class LasAssetAdapter:
    adapter_id = "well-log-las"

    def detect(self, path: Path, sample: bytes) -> AssetDescriptor | None:
        if path.suffix.lower() != ".las":
            return None
        return AssetDescriptor(
            adapter_id=self.adapter_id,
            data_type="well_log",
            format="las",
            media_type="application/x-las",
            capabilities=(
                ASSET_INSPECT,
                TABULAR_INSPECT,
                TABULAR_READ,
                TABLE_PREPROCESS,
                EXPLORE_PYTHON,
                WELL_LOG_QC,
            ),
        )


class DelimitedTextAssetAdapter:
    adapter_id = "delimited-text"
    _formats = {
        ".csv": ("csv", "text/csv"),
        ".tsv": ("tsv", "text/tab-separated-values"),
        ".txt": ("txt", "text/plain"),
    }

    def detect(self, path: Path, sample: bytes) -> AssetDescriptor | None:
        selected = self._formats.get(path.suffix.lower())
        if selected is None:
            return None
        if not _looks_like_text(sample):
            return AssetDescriptor(
                adapter_id="binary-with-text-extension",
                data_type="unknown_binary",
                format=selected[0],
                media_type="application/octet-stream",
                capabilities=(ASSET_INSPECT,),
                confidence="content",
            )
        format_name, media_type = selected
        return AssetDescriptor(
            adapter_id=self.adapter_id,
            data_type="tabular",
            format=format_name,
            media_type=media_type,
            capabilities=(
                ASSET_INSPECT,
                TABULAR_INSPECT,
                TABULAR_READ,
                TABLE_PREPROCESS,
                EXPLORE_PYTHON,
            ),
            confidence="content+extension",
        )


class SegyAssetAdapter:
    """SEG-Y adapter exposing the bounded read-only Increment 5 capability."""

    adapter_id = "seismic-segy"

    def detect(self, path: Path, sample: bytes) -> AssetDescriptor | None:
        if path.suffix.lower() not in {".sgy", ".segy"}:
            return None
        return AssetDescriptor(
            adapter_id=self.adapter_id,
            data_type="seismic",
            format="segy",
            media_type="application/x-segy",
            capabilities=(ASSET_INSPECT, SEGY_INSPECT),
            confidence="extension",
        )


def _looks_like_text(sample: bytes) -> bool:
    if not sample:
        return True
    if b"\x00" in sample:
        return False
    controls = sum(byte < 32 and byte not in {9, 10, 12, 13} for byte in sample)
    return controls / len(sample) < 0.02


_DEFAULT_REGISTRY: AssetAdapterRegistry | None = None


def get_default_asset_adapter_registry() -> AssetAdapterRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        registry = AssetAdapterRegistry()
        # Specific binary formats must be checked before generic text adapters.
        registry.register(SegyAssetAdapter())
        registry.register(LasAssetAdapter())
        registry.register(DelimitedTextAssetAdapter())
        _DEFAULT_REGISTRY = registry
    return _DEFAULT_REGISTRY

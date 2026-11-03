from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


class AssetError(ValueError):
    """Raised when an input asset cannot be registered or inspected."""


@dataclass(frozen=True)
class DataAsset:
    asset_id: str
    source_path: Path
    media_type: str
    format: str
    size_bytes: int
    sha256: str
    data_type: str = "unknown"
    adapter_id: str = "unknown"
    capabilities: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0"

    def to_dict(self, *, disclose_path: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_path"] = (
            str(self.source_path) if disclose_path else f"asset://{self.asset_id}"
        )
        return payload


def register_asset(path: str | Path, *, asset_id: str = "primary") -> DataAsset:
    source = Path(path).resolve()
    if not source.is_file():
        raise AssetError(f"Input asset does not exist: {source}")
    if not asset_id or not asset_id.replace("-", "").replace("_", "").isalnum():
        raise AssetError("asset_id must contain only letters, numbers, '-' or '_'")
    from geophysagent.asset_adapters import get_default_asset_adapter_registry

    descriptor = get_default_asset_adapter_registry().detect(source)
    return DataAsset(
        asset_id=asset_id,
        source_path=source,
        media_type=descriptor.media_type,
        format=descriptor.format,
        size_bytes=source.stat().st_size,
        sha256=_sha256(source),
        data_type=descriptor.data_type,
        adapter_id=descriptor.adapter_id,
        capabilities=descriptor.capabilities,
        metadata={"detection_confidence": descriptor.confidence},
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

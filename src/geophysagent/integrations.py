from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from geophysagent.mcp import McpIntegrationManifest


PACKAGE_SCHEMA_VERSION = "1.0"
REGISTRY_SCHEMA_VERSION = "1.0"
PACKAGE_MANIFEST_NAME = "package.json"
INTEGRATION_ROOT_ENV = "GEOPHYSAGENT_INTEGRATION_ROOT"
_MAX_FILES = 256
_MAX_PACKAGE_BYTES = 64 * 1024 * 1024
_BLOCKED_NAMES = {".env", "credentials.json", "secrets.json"}
_BLOCKED_SUFFIXES = {".key", ".pem", ".pfx"}


class IntegrationPackageError(RuntimeError):
    """An Integration Package is invalid, unavailable or has changed."""


@dataclass(frozen=True)
class IntegrationPackageDefinition:
    package_id: str
    version: str
    display_name: str
    description: str
    mcp_manifest: str
    files: tuple[str, ...]
    schema_version: str = PACKAGE_SCHEMA_VERSION


@dataclass(frozen=True)
class InstalledIntegration:
    package_id: str
    version: str
    display_name: str
    description: str
    relative_path: str
    mcp_manifest: str
    enabled: bool
    package_sha256: str
    installed_at: str
    schema_version: str = PACKAGE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class IntegrationPackageManager:
    """Install and select immutable, reviewed local MCP integration bundles."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root).resolve() if root is not None else default_integration_root()
        self.registry_path = self.root / "registry.json"

    def install(self, source: str | Path, *, enabled: bool = True) -> InstalledIntegration:
        source_root = Path(source).resolve()
        definition = load_package_definition(source_root)
        records = list(self.list())
        if any(item.package_id == definition.package_id for item in records):
            raise IntegrationPackageError(
                f"Integration is already installed: {definition.package_id}"
            )
        self.root.mkdir(parents=True, exist_ok=True)
        target = self._package_path(definition.package_id)
        if target.exists():
            raise IntegrationPackageError(
                f"Integration destination already exists: {definition.package_id}"
            )
        staging = self.root / f".staging-{definition.package_id}-{uuid.uuid4().hex}"
        installed: InstalledIntegration | None = None
        try:
            staging.mkdir(parents=False, exist_ok=False)
            _copy_declared_package(source_root, staging, definition)
            staged_definition = load_package_definition(staging)
            if staged_definition != definition:
                raise IntegrationPackageError("Integration package changed while being installed")
            McpIntegrationManifest.load(staging / definition.mcp_manifest)
            package_sha256 = _package_digest(staging, definition)
            staging.replace(target)
            installed = InstalledIntegration(
                package_id=definition.package_id,
                version=definition.version,
                display_name=definition.display_name,
                description=definition.description,
                relative_path=definition.package_id,
                mcp_manifest=definition.mcp_manifest,
                enabled=enabled,
                package_sha256=package_sha256,
                installed_at=datetime.now(UTC).isoformat(),
            )
            self._save_registry([*records, installed])
            return installed
        except Exception:
            for candidate in (staging, target if installed is not None else None):
                if candidate is not None and candidate.exists():
                    _remove_owned_tree(candidate, self.root)
            raise

    def list(self) -> tuple[InstalledIntegration, ...]:
        if not self.registry_path.is_file():
            return ()
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IntegrationPackageError(
                f"Cannot read Integration registry: {type(exc).__name__}"
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != REGISTRY_SCHEMA_VERSION
        ):
            raise IntegrationPackageError("Unsupported Integration registry schema")
        items = payload.get("packages")
        if not isinstance(items, list):
            raise IntegrationPackageError("Integration registry packages must be an array")
        records: list[InstalledIntegration] = []
        for item in items:
            if not isinstance(item, dict):
                raise IntegrationPackageError("Integration registry contains an invalid record")
            try:
                record = InstalledIntegration(**item)
            except TypeError as exc:
                raise IntegrationPackageError("Integration registry record is invalid") from exc
            if record.schema_version != PACKAGE_SCHEMA_VERSION:
                raise IntegrationPackageError("Unsupported installed Integration schema")
            if record.relative_path != record.package_id:
                raise IntegrationPackageError("Installed Integration path is invalid")
            self._package_path(record.package_id)
            records.append(record)
        if len({item.package_id for item in records}) != len(records):
            raise IntegrationPackageError("Integration registry contains duplicate package ids")
        return tuple(records)

    def set_enabled(self, package_id: str, enabled: bool) -> InstalledIntegration:
        records = list(self.list())
        for index, record in enumerate(records):
            if record.package_id != package_id:
                continue
            updated = replace(record, enabled=enabled)
            records[index] = updated
            self._save_registry(records)
            return updated
        raise IntegrationPackageError(f"Integration is not installed: {package_id}")

    def remove(self, package_id: str) -> InstalledIntegration:
        records = list(self.list())
        selected = next((item for item in records if item.package_id == package_id), None)
        if selected is None:
            raise IntegrationPackageError(f"Integration is not installed: {package_id}")
        target = self._package_path(package_id)
        quarantine = self.root / f".removing-{package_id}-{uuid.uuid4().hex}"
        if not target.is_dir():
            raise IntegrationPackageError(
                f"Installed Integration directory is missing: {package_id}"
            )
        target.replace(quarantine)
        try:
            self._save_registry([item for item in records if item.package_id != package_id])
        except Exception:
            quarantine.replace(target)
            raise
        _remove_owned_tree(quarantine, self.root)
        return selected

    def enabled_manifest_paths(
        self, selected_ids: tuple[str, ...] | None = None
    ) -> tuple[Path, ...]:
        requested = set(selected_ids or ())
        records = self.list()
        if requested:
            missing = requested - {item.package_id for item in records}
            if missing:
                raise IntegrationPackageError(
                    "Integration is not installed: " + ", ".join(sorted(missing))
                )
        selected: list[Path] = []
        for record in records:
            if not record.enabled or (requested and record.package_id not in requested):
                continue
            package_root = self._package_path(record.package_id)
            definition = load_package_definition(package_root)
            if definition.version != record.version:
                raise IntegrationPackageError(
                    f"Installed Integration version changed: {record.package_id}"
                )
            if _package_digest(package_root, definition) != record.package_sha256:
                raise IntegrationPackageError(
                    f"Installed Integration failed integrity verification: {record.package_id}"
                )
            manifest = package_root / record.mcp_manifest
            McpIntegrationManifest.load(manifest)
            selected.append(manifest)
        if requested:
            disabled = requested - {item.package_id for item in records if item.enabled}
            if disabled:
                raise IntegrationPackageError(
                    "Integration is disabled: " + ", ".join(sorted(disabled))
                )
        return tuple(selected)

    def _package_path(self, package_id: str) -> Path:
        _identifier(package_id, "package id")
        candidate = (self.root / package_id).resolve()
        if candidate.parent != self.root:
            raise IntegrationPackageError("Integration package path escapes the registry root")
        return candidate

    def _save_registry(self, records: list[InstalledIntegration]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "packages": [
                item.to_dict() for item in sorted(records, key=lambda item: item.package_id)
            ],
        }
        descriptor, temporary = tempfile.mkstemp(prefix=".registry-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.registry_path)
        except Exception:
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass
            raise


def default_integration_root() -> Path:
    configured = os.environ.get(INTEGRATION_ROOT_ENV, "").strip()
    if configured:
        return Path(configured).resolve()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return (Path(os.environ["LOCALAPPDATA"]) / "GeoPhysAgent" / "integrations").resolve()
    data_root = os.environ.get("XDG_DATA_HOME", "").strip()
    if data_root:
        return (Path(data_root) / "geophysagent" / "integrations").resolve()
    try:
        home = Path.home()
    except RuntimeError:
        # Minimal/service environments may intentionally have no home directory.
        # The fallback is read-only until the user explicitly installs a package.
        return (Path.cwd() / ".geophysagent" / "integrations").resolve()
    return (home / ".local" / "share" / "geophysagent" / "integrations").resolve()


def load_package_definition(source: str | Path) -> IntegrationPackageDefinition:
    root = Path(source).resolve()
    if not root.is_dir():
        raise IntegrationPackageError(f"Integration package directory does not exist: {root}")
    manifest_path = root / PACKAGE_MANIFEST_NAME
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrationPackageError(
            f"Cannot read Integration package manifest: {type(exc).__name__}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise IntegrationPackageError("Unsupported Integration package schema_version")
    package_id = _identifier(payload.get("id"), "package id")
    version = _bounded_string(payload.get("version"), "package version", 100)
    display_name = _bounded_string(
        payload.get("display_name", package_id), "package display_name", 200
    )
    description = _bounded_string(payload.get("description", ""), "package description", 2000)
    mcp_manifest = _relative_file(payload.get("mcp_manifest"), "mcp_manifest")
    files_payload = payload.get("files")
    if not isinstance(files_payload, list) or not files_payload or len(files_payload) > _MAX_FILES:
        raise IntegrationPackageError("Integration package files must be a non-empty bounded array")
    files = tuple(_relative_file(item, "package file") for item in files_payload)
    if len(files) != len(set(files)):
        raise IntegrationPackageError("Integration package files must be unique")
    if mcp_manifest not in files:
        raise IntegrationPackageError("Integration package must declare its MCP manifest file")
    declared = {PACKAGE_MANIFEST_NAME, *files}
    actual: set[str] = set()
    total_bytes = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise IntegrationPackageError("Integration packages cannot contain symbolic links")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            actual.add(relative)
            total_bytes += path.stat().st_size
            if _secret_like(path.name):
                raise IntegrationPackageError(
                    f"Integration package cannot contain credential-like file: {relative}"
                )
    if actual != declared:
        missing = sorted(declared - actual)
        extra = sorted(actual - declared)
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if extra:
            details.append("undeclared=" + ",".join(extra))
        raise IntegrationPackageError("Package file manifest mismatch: " + "; ".join(details))
    if total_bytes > _MAX_PACKAGE_BYTES:
        raise IntegrationPackageError("Integration package exceeds the 64 MiB size limit")
    return IntegrationPackageDefinition(
        package_id,
        version,
        display_name,
        description,
        mcp_manifest,
        files,
    )


def _copy_declared_package(
    source: Path, target: Path, definition: IntegrationPackageDefinition
) -> None:
    for relative in (PACKAGE_MANIFEST_NAME, *definition.files):
        source_file = source / relative
        target_file = target / relative
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file, follow_symlinks=False)


def _package_digest(root: Path, definition: IntegrationPackageDefinition) -> str:
    digest = hashlib.sha256()
    for relative in sorted((PACKAGE_MANIFEST_NAME, *definition.files)):
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise IntegrationPackageError(f"Installed package file is missing: {relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _remove_owned_tree(path: Path, root: Path) -> None:
    resolved = path.resolve()
    if resolved.parent != root.resolve() or not resolved.name.startswith(
        (".staging-", ".removing-")
    ):
        if resolved.parent != root.resolve() or resolved.name in {"", ".", ".."}:
            raise IntegrationPackageError("Refusing to remove a path outside the Integration root")
    shutil.rmtree(resolved)


def _relative_file(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise IntegrationPackageError(f"Integration {label} must use a relative POSIX path")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise IntegrationPackageError(f"Integration {label} is unsafe")
    return path.as_posix()


def _identifier(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or not value.isascii()
        or not value[0].isalnum()
        or any(not (char.isalnum() or char in "._-") for char in value)
    ):
        raise IntegrationPackageError(f"Integration {label} is invalid")
    return value


def _bounded_string(value: Any, label: str, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise IntegrationPackageError(f"Integration {label} is invalid")
    return value


def _secret_like(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in _BLOCKED_NAMES
        or Path(lowered).suffix in _BLOCKED_SUFFIXES
        or lowered.startswith("credentials.")
        or lowered.startswith("secrets.")
    )

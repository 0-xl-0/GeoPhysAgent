from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


ToolStatus = Literal["success", "warning", "failed"]
ValidationStatus = Literal["pass", "warning", "fail"]
RunStatus = Literal["completed", "completed_with_warnings", "failed"]


@dataclass(frozen=True)
class CurveInfo:
    mnemonic: str
    unit: str
    description: str


@dataclass(frozen=True)
class LasDataset:
    source_path: Path
    null_value: float | None
    curves: tuple[CurveInfo, ...]
    rows: tuple[tuple[float | None, ...], ...]
    well: dict[str, str]
    well_units: dict[str, str] = field(default_factory=dict)
    version: str = "2.0"
    wrap: bool = False
    delimiter: str = "SPACE"
    encoding: str = "unknown"
    vendor_extensions: tuple[str, ...] = ()


@dataclass(frozen=True)
class GeoToolResult:
    tool_name: str
    tool_version: str
    status: ToolStatus
    data: dict[str, Any]
    warnings: tuple[str, ...] = ()
    schema_version: str = "1.0"
    provider: str = "builtin"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationCheck:
    code: str
    status: ValidationStatus
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationReport:
    status: ValidationStatus
    checks: tuple[ValidationCheck, ...]
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnalysisResult:
    run_id: str
    status: RunStatus
    source_path: str
    source_sha256: str
    tool_results: tuple[GeoToolResult, ...]
    validation: ValidationReport
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from geophysagent.tabular import TabularDataset, write_csv_dataset


class PipelineError(ValueError):
    """Raised when a preprocessing pipeline is invalid or cannot be executed."""


@dataclass(frozen=True)
class PipelineStep:
    operation: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class PipelineSpec:
    steps: tuple[PipelineStep, ...]
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "steps": [{"operation": step.operation, **step.parameters} for step in self.steps],
        }


@dataclass(frozen=True)
class StepRecord:
    index: int
    operation: str
    parameters: dict[str, Any]
    input_rows: int
    output_rows: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PipelineResult:
    dataset: TabularDataset
    records: tuple[StepRecord, ...]
    pipeline_sha256: str


SUPPORTED_OPERATIONS = {
    "rename_columns",
    "select_columns",
    "sort_rows",
    "deduplicate",
    "drop_null_rows",
    "interpolate_linear",
}


def load_pipeline_spec(source: str | Path | dict[str, Any]) -> PipelineSpec:
    if isinstance(source, dict):
        payload = source
    else:
        path = Path(source).resolve()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PipelineError(f"Cannot read pipeline: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version", "1.0") != "1.0":
        raise PipelineError("Pipeline requires schema_version = '1.0'")
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PipelineError("Pipeline requires a non-empty steps list")
    steps: list[PipelineStep] = []
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, dict) or not isinstance(raw.get("operation"), str):
            raise PipelineError(f"Pipeline step {index + 1} requires an operation")
        operation = raw["operation"]
        if operation not in SUPPORTED_OPERATIONS:
            raise PipelineError(f"Unsupported operation at step {index + 1}: {operation}")
        parameters = {key: value for key, value in raw.items() if key != "operation"}
        steps.append(PipelineStep(operation, parameters))
    return PipelineSpec(tuple(steps))


def execute_pipeline(dataset: TabularDataset, spec: PipelineSpec) -> PipelineResult:
    current = dataset
    records: list[StepRecord] = []
    for index, step in enumerate(spec.steps, start=1):
        before = len(current.rows)
        current, warnings = _execute_step(current, step)
        records.append(
            StepRecord(index, step.operation, step.parameters, before, len(current.rows), warnings)
        )
    canonical = json.dumps(
        spec.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return PipelineResult(
        dataset=current,
        records=tuple(records),
        pipeline_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def write_pipeline_result(result: PipelineResult, output_dir: str | Path) -> tuple[Path, Path]:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    data_path = write_csv_dataset(result.dataset, output / "preprocessed.csv")
    manifest_path = output / "pipeline-result.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "source_asset": result.dataset.asset.to_dict(disclose_path=False),
                "pipeline_sha256": result.pipeline_sha256,
                "columns": list(result.dataset.columns),
                "row_count": len(result.dataset.rows),
                "steps": [asdict(record) for record in result.records],
                "artifact": str(data_path),
                "evidence_ids": [
                    "pipeline-row-count",
                    "pipeline-definition-hash",
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return data_path, manifest_path


def _execute_step(
    dataset: TabularDataset, step: PipelineStep
) -> tuple[TabularDataset, tuple[str, ...]]:
    handlers = {
        "rename_columns": _rename_columns,
        "select_columns": _select_columns,
        "sort_rows": _sort_rows,
        "deduplicate": _deduplicate,
        "drop_null_rows": _drop_null_rows,
        "interpolate_linear": _interpolate_linear,
    }
    return handlers[step.operation](dataset, step.parameters)


def _replace_dataset(
    dataset: TabularDataset,
    *,
    columns: tuple[str, ...] | None = None,
    rows: tuple[dict[str, Any], ...] | None = None,
) -> TabularDataset:
    return TabularDataset(
        asset=dataset.asset,
        columns=columns or dataset.columns,
        rows=rows if rows is not None else dataset.rows,
        profile=dataset.profile,
    )


def _require_columns(dataset: TabularDataset, names: list[str]) -> None:
    missing = [name for name in names if name not in dataset.columns]
    if missing:
        raise PipelineError(f"Unknown columns: {', '.join(missing)}")


def _rename_columns(
    dataset: TabularDataset, params: dict[str, Any]
) -> tuple[TabularDataset, tuple[str, ...]]:
    mapping = params.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        raise PipelineError("rename_columns requires a non-empty mapping")
    _require_columns(dataset, [str(name) for name in mapping])
    columns = tuple(str(mapping.get(column, column)) for column in dataset.columns)
    if len(set(columns)) != len(columns):
        raise PipelineError("rename_columns would create duplicate column names")
    rows = tuple(
        {str(mapping.get(column, column)): row[column] for column in dataset.columns}
        for row in dataset.rows
    )
    return _replace_dataset(dataset, columns=columns, rows=rows), ()


def _select_columns(
    dataset: TabularDataset, params: dict[str, Any]
) -> tuple[TabularDataset, tuple[str, ...]]:
    raw = params.get("columns")
    if not isinstance(raw, list) or not raw:
        raise PipelineError("select_columns requires a non-empty columns list")
    columns = [str(name) for name in raw]
    _require_columns(dataset, columns)
    rows = tuple({column: row[column] for column in columns} for row in dataset.rows)
    return _replace_dataset(dataset, columns=tuple(columns), rows=rows), ()


def _sort_rows(
    dataset: TabularDataset, params: dict[str, Any]
) -> tuple[TabularDataset, tuple[str, ...]]:
    column = str(params.get("by", ""))
    direction = params.get("direction", "ascending")
    _require_columns(dataset, [column])
    if direction not in ("ascending", "descending"):
        raise PipelineError("sort_rows direction must be ascending or descending")
    if any(row[column] is None for row in dataset.rows):
        raise PipelineError(f"Cannot sort by {column}: it contains NULL values")
    try:
        rows = tuple(
            sorted(dataset.rows, key=lambda row: row[column], reverse=direction == "descending")
        )
    except TypeError as exc:
        raise PipelineError(f"Column {column} contains incompatible value types") from exc
    return _replace_dataset(dataset, rows=rows), ()


def _deduplicate(
    dataset: TabularDataset, params: dict[str, Any]
) -> tuple[TabularDataset, tuple[str, ...]]:
    column = str(params.get("by", ""))
    keep = params.get("keep", "first")
    _require_columns(dataset, [column])
    if keep not in ("first", "last"):
        raise PipelineError("deduplicate keep must be first or last")
    source = dataset.rows if keep == "first" else tuple(reversed(dataset.rows))
    seen: set[Any] = set()
    kept: list[dict[str, Any]] = []
    for row in source:
        key = row[column]
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    if keep == "last":
        kept.reverse()
    removed = len(dataset.rows) - len(kept)
    warnings = (f"Removed {removed} duplicate rows by {column}",) if removed else ()
    return _replace_dataset(dataset, rows=tuple(kept)), warnings


def _drop_null_rows(
    dataset: TabularDataset, params: dict[str, Any]
) -> tuple[TabularDataset, tuple[str, ...]]:
    raw = params.get("columns")
    if not isinstance(raw, list) or not raw:
        raise PipelineError("drop_null_rows requires a non-empty columns list")
    columns = [str(name) for name in raw]
    _require_columns(dataset, columns)
    rows = tuple(row for row in dataset.rows if all(row[column] is not None for column in columns))
    removed = len(dataset.rows) - len(rows)
    warnings = (f"Dropped {removed} rows containing NULL values",) if removed else ()
    return _replace_dataset(dataset, rows=rows), warnings


def _interpolate_linear(
    dataset: TabularDataset, params: dict[str, Any]
) -> tuple[TabularDataset, tuple[str, ...]]:
    x_name = str(params.get("x", ""))
    raw_columns = params.get("columns")
    max_gap = params.get("max_gap")
    if not isinstance(raw_columns, list) or not raw_columns:
        raise PipelineError("interpolate_linear requires a non-empty columns list")
    columns = [str(name) for name in raw_columns]
    _require_columns(dataset, [x_name, *columns])
    if max_gap is not None:
        try:
            max_gap = float(max_gap)
        except (TypeError, ValueError) as exc:
            raise PipelineError("interpolate_linear max_gap must be numeric") from exc
        if max_gap <= 0:
            raise PipelineError("interpolate_linear max_gap must be positive")
    x_values = [row[x_name] for row in dataset.rows]
    if any(not isinstance(value, (int, float)) for value in x_values):
        raise PipelineError(f"Interpolation axis {x_name} must be fully numeric")
    if any(b <= a for a, b in zip(x_values, x_values[1:])):
        raise PipelineError(f"Interpolation axis {x_name} must be strictly ascending")
    rows = [dict(row) for row in dataset.rows]
    filled = 0
    for column in columns:
        for index, row in enumerate(rows):
            if row[column] is not None:
                if not isinstance(row[column], (int, float)):
                    raise PipelineError(
                        f"Interpolation column {column} contains non-numeric values"
                    )
                continue
            left = next((i for i in range(index - 1, -1, -1) if rows[i][column] is not None), None)
            right = next(
                (i for i in range(index + 1, len(rows)) if rows[i][column] is not None), None
            )
            if left is None or right is None:
                continue
            x_left, x_current, x_right = x_values[left], x_values[index], x_values[right]
            if max_gap is not None and x_right - x_left > max_gap:
                continue
            y_left, y_right = rows[left][column], rows[right][column]
            if not isinstance(y_left, (int, float)) or not isinstance(y_right, (int, float)):
                continue
            row[column] = y_left + (y_right - y_left) * (x_current - x_left) / (x_right - x_left)
            filled += 1
    warnings = (
        (f"Interpolated {filled} NULL cells",) if filled else ("No NULL cells were interpolated",)
    )
    return _replace_dataset(dataset, rows=tuple(rows)), warnings

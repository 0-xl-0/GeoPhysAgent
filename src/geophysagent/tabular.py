from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from geophysagent.asset_adapters import (
    TABULAR_INSPECT,
    TABULAR_READ,
    get_default_asset_adapter_registry,
)
from geophysagent.assets import DataAsset
from geophysagent.las import parse_las


class TabularError(ValueError):
    """Raised when tabular data cannot be detected or loaded safely."""


Cell = float | str | None


@dataclass(frozen=True)
class TabularProfile:
    encoding: str
    delimiter: str
    has_header: bool
    columns: tuple[str, ...]
    sampled_rows: int
    numeric_columns: tuple[str, ...]
    depth_candidates: tuple[str, ...]
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TabularDataset:
    asset: DataAsset
    columns: tuple[str, ...]
    rows: tuple[dict[str, Cell], ...]
    profile: TabularProfile
    schema_version: str = "1.0"

    def to_model_data(self, *, max_rows: int | None = None) -> dict[str, Any]:
        rows = self.rows if max_rows is None else self.rows[:max_rows]
        return {
            "asset": self.asset.to_dict(disclose_path=False),
            "columns": list(self.columns),
            "rows": [dict(row) for row in rows],
            "row_count": len(self.rows),
            "profile": self.profile.to_dict(),
        }


_DEPTH_NAMES = {"DEPTH", "DEPT", "MD", "TVD", "TVDSS", "Z", "深度"}
_NULL_TOKENS = {"", "NA", "N/A", "NAN", "NULL", "NONE"}


def inspect_tabular_asset(asset: DataAsset, *, sample_size: int = 100) -> TabularProfile:
    get_default_asset_adapter_registry().require_capability(asset, TABULAR_INSPECT)
    if asset.format == "las":
        dataset = parse_las(asset.source_path)
        columns = tuple(curve.mnemonic for curve in dataset.curves)
        return TabularProfile(
            encoding="las",
            delimiter="whitespace",
            has_header=True,
            columns=columns,
            sampled_rows=min(sample_size, len(dataset.rows)),
            numeric_columns=columns,
            depth_candidates=tuple(name for name in columns if name.upper() in _DEPTH_NAMES),
        )

    text, encoding = _read_text_sample(asset.source_path)
    raw_lines = _data_lines(text)
    if not raw_lines:
        raise TabularError("Text asset contains no data lines")
    delimiter = _detect_delimiter(raw_lines[:20])
    token_rows = [_split(line, delimiter) for line in raw_lines[: sample_size + 1]]
    width = max(len(row) for row in token_rows)
    if width < 1:
        raise TabularError("Cannot detect any columns")
    has_header = _looks_like_header(token_rows[0], token_rows[1:])
    if has_header:
        columns = _unique_columns(token_rows[0])
        samples = token_rows[1:]
    else:
        columns = tuple(f"column_{index + 1}" for index in range(width))
        samples = token_rows
    if len(columns) != width:
        raise TabularError("Detected header width does not match sampled data")
    rectangular = [row for row in samples if len(row) == width]
    numeric = tuple(
        column
        for index, column in enumerate(columns)
        if _numeric_ratio(row[index] for row in rectangular) >= 0.8
    )
    depths = tuple(name for name in columns if name.strip().upper() in _DEPTH_NAMES)
    return TabularProfile(
        encoding=encoding,
        delimiter=delimiter,
        has_header=has_header,
        columns=columns,
        sampled_rows=len(rectangular),
        numeric_columns=numeric,
        depth_candidates=depths,
    )


def load_tabular_asset(
    asset: DataAsset,
    *,
    null_values: Iterable[str | float] = (),
) -> TabularDataset:
    get_default_asset_adapter_registry().require_capability(asset, TABULAR_READ)
    if asset.format == "las":
        las = parse_las(asset.source_path)
        columns = tuple(curve.mnemonic for curve in las.curves)
        profile = inspect_tabular_asset(asset)
        rows = tuple(
            {column: value for column, value in zip(columns, values)} for values in las.rows
        )
        return TabularDataset(asset, columns, rows, profile)

    profile = inspect_tabular_asset(asset)
    text = _read_text(asset.source_path, profile.encoding)
    lines = _data_lines(text)
    if profile.has_header:
        lines = lines[1:]
    configured_nulls = {str(value).strip().upper() for value in null_values}
    null_tokens = _NULL_TOKENS | configured_nulls
    rows: list[dict[str, Cell]] = []
    for line_number, line in enumerate(lines, start=2 if profile.has_header else 1):
        values = _split(line, profile.delimiter)
        if len(values) != len(profile.columns):
            raise TabularError(
                f"Data row {line_number} has {len(values)} columns; expected {len(profile.columns)}"
            )
        rows.append(
            {
                column: _convert_cell(value, null_tokens)
                for column, value in zip(profile.columns, values)
            }
        )
    if not rows:
        raise TabularError("Text asset contains no data rows")
    return TabularDataset(asset, profile.columns, tuple(rows), profile)


def write_csv_dataset(dataset: TabularDataset, path: str | Path) -> Path:
    output = Path(path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(dataset.columns))
        writer.writeheader()
        for row in dataset.rows:
            writer.writerow(
                {column: "" if row[column] is None else row[column] for column in dataset.columns}
            )
    return output


def _read_text_sample(path: Path, limit: int = 256 * 1024) -> tuple[str, str]:
    raw = path.read_bytes()[:limit]
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise TabularError("Cannot detect text encoding")


def _read_text(path: Path, encoding: str) -> str:
    try:
        return path.read_text(encoding=encoding)
    except UnicodeDecodeError as exc:
        raise TabularError(f"Cannot decode the complete file as {encoding}") from exc


def _data_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "//", ";"))
    ]


def _detect_delimiter(lines: list[str]) -> str:
    candidates = {",": 0, "\t": 0, ";": 0}
    for line in lines:
        for delimiter in candidates:
            candidates[delimiter] += line.count(delimiter)
    delimiter, count = max(candidates.items(), key=lambda item: item[1])
    return delimiter if count > 0 else "whitespace"


def _split(line: str, delimiter: str) -> list[str]:
    if delimiter == "whitespace":
        return re.split(r"\s+", line.strip())
    return next(csv.reader(io.StringIO(line), delimiter=delimiter))


def _looks_like_header(first: list[str], remaining: list[list[str]]) -> bool:
    first_numeric = sum(_is_number(value) for value in first)
    if first_numeric == len(first):
        return False
    comparable = [row for row in remaining if len(row) == len(first)]
    if not comparable:
        return any(not _is_number(value) for value in first)
    later_numeric = sum(_is_number(value) for row in comparable[:10] for value in row) / (
        len(comparable[:10]) * len(first)
    )
    return later_numeric > first_numeric / max(len(first), 1)


def _unique_columns(values: list[str]) -> tuple[str, ...]:
    result: list[str] = []
    counts: dict[str, int] = {}
    for index, value in enumerate(values):
        base = value.strip() or f"column_{index + 1}"
        counts[base] = counts.get(base, 0) + 1
        result.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    return tuple(result)


def _numeric_ratio(values: Iterable[str]) -> float:
    samples = [value for value in values if value.strip().upper() not in _NULL_TOKENS]
    return sum(_is_number(value) for value in samples) / len(samples) if samples else 0.0


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def _convert_cell(value: str, null_tokens: set[str]) -> Cell:
    stripped = value.strip()
    if stripped.upper() in null_tokens:
        return None
    try:
        return float(stripped)
    except ValueError:
        return stripped

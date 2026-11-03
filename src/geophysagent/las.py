from __future__ import annotations

import codecs
import math
from pathlib import Path

from geophysagent.domain import CurveInfo, LasDataset


class LasParseError(ValueError):
    """Raised when a LAS file cannot be parsed safely."""


_SECTION_ALIASES = {
    "V": "VERSION",
    "VERSION": "VERSION",
    "W": "WELL",
    "WELL": "WELL",
    "C": "CURVE",
    "CURVE": "CURVE",
    "A": "ASCII",
    "ASCII": "ASCII",
    "P": "PARAMETER",
    "PARAMETER": "PARAMETER",
    "O": "OTHER",
    "OTHER": "OTHER",
}
_UNIQUE_SECTIONS = {"VERSION", "WELL", "CURVE", "ASCII"}
_SUPPORTED_DELIMITERS = {"SPACE", "COMMA", "TAB"}


def _parse_header_line(line: str) -> tuple[str, str, str, str] | None:
    """Parse the CWLS mnemonic.unit value : description record.

    LAS 2.0 defines the delimiters as the first dot, first space after that dot,
    and last colon. Using the last colon preserves colons inside a value.
    """

    colon = line.rfind(":")
    content = line[:colon] if colon >= 0 else line
    description = line[colon + 1 :].strip() if colon >= 0 else ""
    dot = content.find(".")
    if dot < 0:
        return None
    mnemonic = content[:dot].strip().upper()
    if not mnemonic:
        return None
    remainder = content[dot + 1 :]
    if remainder[:1].isspace():
        unit = ""
        value = remainder.strip()
    else:
        split = next((index for index, char in enumerate(remainder) if char.isspace()), -1)
        if split < 0:
            unit = remainder.strip()
            value = ""
        else:
            unit = remainder[:split].strip()
            value = remainder[split:].strip()
    return mnemonic, unit, value, description


def parse_las(path: str | Path) -> LasDataset:
    source = Path(path).resolve()
    if not source.is_file():
        raise LasParseError(f"LAS file does not exist: {source}")

    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise LasParseError(f"Cannot read LAS file: {type(exc).__name__}") from exc
    text, encoding = _decode_las(raw)
    if "\x00" in text:
        raise LasParseError("LAS text contains NUL bytes")

    sections: dict[str, list[str]] = {}
    section_order: list[str] = []
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("~"):
            token = line[1:].split(maxsplit=1)[0].upper()
            current = _SECTION_ALIASES.get(token, token)
            if current in _UNIQUE_SECTIONS and current in sections:
                raise LasParseError(f"LAS file contains duplicate ~{current} sections")
            sections.setdefault(current, [])
            section_order.append(current)
            continue
        if current is not None:
            sections[current].append(raw_line)

    if not section_order or section_order[0] != "VERSION":
        raise LasParseError("LAS 2.0 requires ~Version as the first section")
    for required in ("VERSION", "WELL", "CURVE", "ASCII"):
        if required not in sections:
            raise LasParseError(f"LAS file has no ~{required.title()} section")

    version_values, _version_units = _header_values(sections["VERSION"])
    version = version_values.get("VERS", "").strip()
    try:
        supported_version = math.isclose(float(version), 2.0, abs_tol=1e-9)
    except ValueError:
        supported_version = False
    if not supported_version:
        raise LasParseError(f"Unsupported or missing LAS VERS value: {version or '<missing>'}")
    wrap_value = version_values.get("WRAP", "").strip().upper()
    if wrap_value not in {"YES", "NO"}:
        raise LasParseError("LAS 2.0 WRAP must be explicitly YES or NO")
    delimiter = version_values.get("DLM", "SPACE").strip().upper() or "SPACE"
    if delimiter not in _SUPPORTED_DELIMITERS:
        raise LasParseError(f"Unsupported LAS data delimiter: {delimiter}")

    curves: list[CurveInfo] = []
    for line in sections["CURVE"]:
        parsed = _parse_header_line(line)
        if parsed:
            mnemonic, unit, _value, description = parsed
            curves.append(CurveInfo(mnemonic, unit, description))
    if not curves:
        raise LasParseError("~Curve section contains no readable curve definitions")
    mnemonics = [curve.mnemonic for curve in curves]
    if len(set(mnemonics)) != len(mnemonics):
        raise LasParseError("~Curve section contains duplicate curve mnemonics")

    well, well_units = _header_values(sections["WELL"])
    null_value: float | None = None
    if "NULL" in well:
        try:
            null_value = float(well["NULL"])
        except ValueError as exc:
            raise LasParseError(f"Invalid NULL value: {well['NULL']}") from exc
        if not math.isfinite(null_value):
            raise LasParseError("LAS NULL value must be finite")

    data_lines = [line for line in sections["ASCII"] if line.strip()]
    rows = _parse_ascii_rows(
        data_lines,
        curve_count=len(curves),
        delimiter=delimiter,
        wrapped=wrap_value == "YES",
        null_value=null_value,
    )
    if not rows:
        raise LasParseError("~Ascii section contains no data rows")

    extensions = [name for name in section_order if name not in _SECTION_ALIASES.values()]
    if "DLM" in version_values:
        extensions.append(f"DLM:{delimiter}")
    return LasDataset(
        source_path=source,
        null_value=null_value,
        curves=tuple(curves),
        rows=tuple(rows),
        well=well,
        well_units=well_units,
        version=version,
        wrap=wrap_value == "YES",
        delimiter=delimiter,
        encoding=encoding,
        vendor_extensions=tuple(dict.fromkeys(extensions)),
    )


def _decode_las(raw: bytes) -> tuple[str, str]:
    candidates: list[tuple[str, str]] = []
    if raw.startswith(codecs.BOM_UTF8):
        candidates.append(("utf-8-sig", "utf-8-sig"))
    elif raw.startswith(codecs.BOM_UTF16_LE):
        candidates.append(("utf-16-le", "utf-16-le-bom"))
        raw = raw[len(codecs.BOM_UTF16_LE) :]
    elif raw.startswith(codecs.BOM_UTF16_BE):
        candidates.append(("utf-16-be", "utf-16-be-bom"))
        raw = raw[len(codecs.BOM_UTF16_BE) :]
    else:
        candidates.extend((("utf-8", "utf-8"), ("cp1252", "cp1252"), ("latin-1", "latin-1")))
    for codec, label in candidates:
        try:
            return raw.decode(codec), label
        except UnicodeDecodeError:
            continue
    raise LasParseError("LAS text encoding is unsupported")


def _header_values(lines: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    values: dict[str, str] = {}
    units: dict[str, str] = {}
    for line in lines:
        parsed = _parse_header_line(line)
        if not parsed:
            continue
        mnemonic, unit, value, _description = parsed
        values[mnemonic] = value
        units[mnemonic] = unit
    return values, units


def _parse_ascii_rows(
    lines: list[str],
    *,
    curve_count: int,
    delimiter: str,
    wrapped: bool,
    null_value: float | None,
) -> list[tuple[float | None, ...]]:
    token_lines = [
        _data_tokens(line, delimiter) for line in lines if not line.lstrip().startswith("#")
    ]
    if wrapped:
        flattened = [token for tokens in token_lines for token in tokens]
        if len(flattened) % curve_count:
            raise LasParseError(
                f"Wrapped ~Ascii data has {len(flattened)} values; expected a multiple of "
                f"{curve_count}"
            )
        grouped = [
            flattened[index : index + curve_count]
            for index in range(0, len(flattened), curve_count)
        ]
    else:
        grouped = []
        for line_number, tokens in enumerate(token_lines, start=1):
            if not tokens:
                continue
            if len(tokens) != curve_count:
                raise LasParseError(
                    f"~Ascii row {line_number} has {len(tokens)} values; expected {curve_count}"
                )
            grouped.append(tokens)

    rows: list[tuple[float | None, ...]] = []
    for row_number, tokens in enumerate(grouped, start=1):
        row: list[float | None] = []
        for value in tokens:
            try:
                number = float(value)
            except ValueError as exc:
                raise LasParseError(
                    f"~Ascii row {row_number} contains a non-numeric value: {value}"
                ) from exc
            if not math.isfinite(number):
                raise LasParseError(f"~Ascii row {row_number} contains a non-finite value")
            row.append(None if null_value is not None and number == null_value else number)
        rows.append(tuple(row))
    return rows


def _data_tokens(line: str, delimiter: str) -> list[str]:
    stripped = line.strip()
    if not stripped:
        return []
    if delimiter == "COMMA":
        tokens = [item.strip() for item in stripped.split(",")]
    elif delimiter == "TAB":
        tokens = [item.strip() for item in stripped.split("\t")]
    else:
        tokens = stripped.split()
    if any(not token for token in tokens):
        raise LasParseError("~Ascii data contains an empty delimited field")
    return tokens

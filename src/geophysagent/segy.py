from __future__ import annotations

import math
import random
import re
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any, BinaryIO


class SegyError(ValueError):
    """A SEG-Y file cannot be inspected safely."""


class SegyTruncatedError(SegyError):
    """A SEG-Y header or trace ends before its declared length."""


class SegyUnsupportedError(SegyError):
    """The file uses a valid SEG-Y feature outside the v0.1 support envelope."""


@dataclass(frozen=True)
class SegyPolicy:
    max_extended_text_headers: int = 1024
    max_trace_header_extensions: int = 64
    max_samples_per_trace: int = 50_000_000
    sample_chunk_bytes: int = 1024 * 1024
    anomaly_reservoir_size: int = 4096
    max_examples: int = 20
    policy_version: str = "0.1"


@dataclass(frozen=True)
class SegyBinaryHeader:
    byte_order: str
    job_id: int
    line_number: int
    reel_number: int
    sample_interval: float
    samples_per_trace: int
    sample_format_code: int
    sample_format_name: str
    bytes_per_sample: int
    trace_sorting_code: int
    measurement_system: int
    revision_major: int
    revision_minor: int
    fixed_length_trace_flag: int
    extended_text_header_count: int
    trace_header_extension_count: int


@dataclass(frozen=True)
class SegyInspection:
    textual_header_encoding: str
    textual_header_lines: tuple[str, ...]
    binary_header: SegyBinaryHeader
    trace_summary: dict[str, Any]
    geometry: dict[str, Any]
    amplitude: dict[str, Any]
    qc_observations: dict[str, Any]
    policy_version: str
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SAMPLE_FORMATS = {
    1: ("4-byte IBM floating-point", 4),
    2: ("4-byte signed integer", 4),
    3: ("2-byte signed integer", 2),
    4: ("4-byte fixed-point with gain (obsolete)", 4),
    5: ("4-byte IEEE floating-point", 4),
    6: ("8-byte IEEE floating-point", 8),
    7: ("3-byte signed integer", 3),
    8: ("1-byte signed integer", 1),
    9: ("8-byte signed integer", 8),
    10: ("4-byte unsigned integer", 4),
    11: ("2-byte unsigned integer", 2),
    12: ("8-byte unsigned integer", 8),
    15: ("3-byte unsigned integer", 3),
    16: ("1-byte unsigned integer", 1),
}
SUPPORTED_SAMPLE_FORMAT_CODES = (1, 2, 3, 5, 6, 8)


def inspect_segy(
    source_path: str | Path,
    *,
    policy: SegyPolicy | None = None,
) -> SegyInspection:
    selected = policy or SegyPolicy()
    source = Path(source_path).resolve()
    if not source.is_file():
        raise SegyError(f"SEG-Y input does not exist: {source}")
    file_size = source.stat().st_size
    if file_size < 3600:
        raise SegyTruncatedError(
            f"SEG-Y file is {file_size} bytes; at least 3600 bytes are required"
        )

    with source.open("rb") as handle:
        textual_raw = _read_exact(handle, 3200, "3200-byte textual header")
        textual_encoding, textual_text = _decode_text_header(textual_raw)
        binary_raw = _read_exact(handle, 400, "400-byte binary header")
        binary = _parse_binary_header(binary_raw)
        if binary.sample_format_code not in SUPPORTED_SAMPLE_FORMAT_CODES:
            known = _SAMPLE_FORMATS.get(binary.sample_format_code, ("unknown", 0))[0]
            raise SegyUnsupportedError(
                f"SEG-Y sample format code {binary.sample_format_code} ({known}) is not "
                f"supported by policy {selected.policy_version}; supported codes are "
                f"{', '.join(str(code) for code in SUPPORTED_SAMPLE_FORMAT_CODES)}"
            )
        if binary.trace_header_extension_count > selected.max_trace_header_extensions:
            raise SegyUnsupportedError(
                f"SEG-Y declares {binary.trace_header_extension_count} trace-header extensions; "
                f"policy limit is {selected.max_trace_header_extensions}"
            )
        extended_count = _skip_extended_text_headers(
            handle,
            binary.extended_text_header_count,
            selected,
            file_size,
        )
        trace_start = handle.tell()
        trace_header_bytes = 240 * (1 + binary.trace_header_extension_count)
        scan = _scan_traces(
            handle,
            file_size=file_size,
            trace_start=trace_start,
            trace_header_bytes=trace_header_bytes,
            binary=binary,
            policy=selected,
        )
        if scan["trace_count"] == 0:
            raise SegyError("SEG-Y contains no data traces")

        anomaly = _derive_anomaly_threshold(scan.pop("_rms_reservoir"))
        if anomaly["threshold"] is not None:
            anomaly.update(
                _scan_amplitude_outliers(
                    handle,
                    file_size=file_size,
                    trace_start=trace_start,
                    trace_header_bytes=trace_header_bytes,
                    binary=binary,
                    policy=selected,
                    threshold=float(anomaly["threshold"]),
                )
            )
        else:
            anomaly.update({"candidate_count": 0, "examples": []})

    trace_summary = {
        key: value
        for key, value in scan.items()
        if key
        not in {
            "geometry",
            "amplitude",
            "dead_trace_examples",
            "constant_trace_examples",
            "nonfinite_trace_examples",
        }
    }
    amplitude = dict(scan["amplitude"])
    amplitude["trace_rms_anomaly"] = anomaly
    observations = {
        "dead_trace_count": scan["dead_trace_count"],
        "dead_trace_examples": scan["dead_trace_examples"],
        "constant_trace_count": scan["constant_trace_count"],
        "constant_trace_examples": scan["constant_trace_examples"],
        "nonfinite_sample_count": scan["nonfinite_sample_count"],
        "nonfinite_trace_examples": scan["nonfinite_trace_examples"],
        "abnormal_amplitude_trace_count": anomaly["candidate_count"],
        "abnormal_amplitude_examples": anomaly["examples"],
    }
    trace_summary["actual_extended_text_header_count"] = extended_count
    return SegyInspection(
        textual_header_encoding=textual_encoding,
        textual_header_lines=tuple(
            textual_text[index : index + 80].rstrip() for index in range(0, 3200, 80)
        ),
        binary_header=binary,
        trace_summary=trace_summary,
        geometry=scan["geometry"],
        amplitude=amplitude,
        qc_observations=observations,
        policy_version=selected.policy_version,
    )


def _parse_binary_header(raw: bytes) -> SegyBinaryHeader:
    major = raw[300]
    minor = raw[301]
    signature = raw[96:100]
    if major < 2 or signature == b"\x00\x00\x00\x00":
        endian = ">"
        byte_order = "big"
    elif signature == b"\x01\x02\x03\x04":
        endian = ">"
        byte_order = "big"
    elif signature == b"\x04\x03\x02\x01":
        endian = "<"
        byte_order = "little"
    elif signature in {b"\x02\x01\x04\x03", b"\x03\x04\x01\x02"}:
        raise SegyUnsupportedError("Pair-wise byte-swapped SEG-Y is not supported")
    else:
        raise SegyUnsupportedError("SEG-Y revision 2 byte-order constant is missing or invalid")

    sample_interval = float(_u16(raw, 16, endian))
    samples_per_trace = _u16(raw, 20, endian)
    if major >= 2:
        extended_samples = _u32(raw, 68, endian)
        extended_interval = _f64(raw, 72, endian)
        if extended_samples:
            samples_per_trace = extended_samples
        if extended_interval and math.isfinite(extended_interval):
            sample_interval = extended_interval
    format_code = _u16(raw, 24, endian)
    format_name, bytes_per_sample = _SAMPLE_FORMATS.get(format_code, ("unknown", 0))
    fixed_flag = _u16(raw, 302, endian)
    if fixed_flag not in {0, 1}:
        raise SegyError(f"Invalid fixed-length trace flag: {fixed_flag}")
    extended_text_count = _i16(raw, 304, endian)
    trace_header_extensions = _u16(raw, 306, endian) if major >= 2 else 0
    if trace_header_extensions == 0xFFFF:
        raise SegyUnsupportedError("Variable per-trace header extension counts are not supported")
    if samples_per_trace <= 0:
        raise SegyError("Binary Header declares zero samples per trace")
    if sample_interval <= 0 or not math.isfinite(sample_interval):
        raise SegyError("Binary Header declares an invalid sample interval")
    return SegyBinaryHeader(
        byte_order=byte_order,
        job_id=_i32(raw, 0, endian),
        line_number=_i32(raw, 4, endian),
        reel_number=_i32(raw, 8, endian),
        sample_interval=sample_interval,
        samples_per_trace=samples_per_trace,
        sample_format_code=format_code,
        sample_format_name=format_name,
        bytes_per_sample=bytes_per_sample,
        trace_sorting_code=_i16(raw, 28, endian),
        measurement_system=_i16(raw, 54, endian),
        revision_major=major,
        revision_minor=minor,
        fixed_length_trace_flag=fixed_flag,
        extended_text_header_count=extended_text_count,
        trace_header_extension_count=trace_header_extensions,
    )


def _scan_traces(
    handle: BinaryIO,
    *,
    file_size: int,
    trace_start: int,
    trace_header_bytes: int,
    binary: SegyBinaryHeader,
    policy: SegyPolicy,
) -> dict[str, Any]:
    handle.seek(trace_start)
    endian = ">" if binary.byte_order == "big" else "<"
    trace_count = 0
    total_declared_samples = 0
    binary_sample_mismatch_count = 0
    binary_interval_mismatch_count = 0
    dead_count = 0
    constant_count = 0
    nonfinite_sample_count = 0
    dead_examples: list[int] = []
    constant_examples: list[int] = []
    nonfinite_examples: list[int] = []
    sample_counts = _RangeTracker()
    sample_intervals = _RangeTracker()
    inline_values = _RangeTracker(track_unique=True)
    crossline_values = _RangeTracker(track_unique=True)
    coordinate_scalar_values = _RangeTracker(track_unique=True)
    coordinate_unit_codes = _RangeTracker(track_unique=True)
    source_x = _RangeTracker()
    source_y = _RangeTracker()
    group_x = _RangeTracker()
    group_y = _RangeTracker()
    cdp_x = _RangeTracker()
    cdp_y = _RangeTracker()
    global_stats = _OnlineStats()
    reservoir: list[float] = []
    reservoir_random = random.Random(0)

    while handle.tell() < file_size:
        trace_offset = handle.tell()
        remaining = file_size - trace_offset
        if remaining < trace_header_bytes:
            raise SegyTruncatedError(
                f"Trailing {remaining} bytes at offset {trace_offset} cannot contain a "
                f"{trace_header_bytes}-byte trace header"
            )
        standard = _read_exact(handle, 240, f"trace {trace_count + 1} standard header")
        if trace_header_bytes > 240:
            _read_exact(
                handle,
                trace_header_bytes - 240,
                f"trace {trace_count + 1} header extensions",
            )
        trace_count += 1
        header_samples = _u16(standard, 114, endian)
        header_interval = _u16(standard, 116, endian)
        if header_samples and header_samples != binary.samples_per_trace:
            binary_sample_mismatch_count += 1
        if header_interval and header_interval != binary.sample_interval:
            binary_interval_mismatch_count += 1
        samples = (
            binary.samples_per_trace
            if binary.fixed_length_trace_flag == 1
            else header_samples or binary.samples_per_trace
        )
        if (
            binary.revision_major >= 2
            and binary.fixed_length_trace_flag == 0
            and header_samples == 0
            and binary.trace_header_extension_count > 0
        ):
            raise SegyUnsupportedError(
                f"Trace {trace_count} requires the extended trace sample-count field, "
                "which is outside the v0.1 support envelope"
            )
        interval = (
            binary.sample_interval
            if binary.fixed_length_trace_flag == 1
            else float(header_interval or binary.sample_interval)
        )
        if samples <= 0 or samples > policy.max_samples_per_trace:
            raise SegyError(
                f"Trace {trace_count} declares {samples} samples; policy range is "
                f"1..{policy.max_samples_per_trace}"
            )
        sample_bytes = samples * binary.bytes_per_sample
        if file_size - handle.tell() < sample_bytes:
            raise SegyTruncatedError(
                f"Trace {trace_count} at offset {trace_offset} declares {samples} samples "
                f"({sample_bytes} bytes), but only {file_size - handle.tell()} bytes remain"
            )
        trace_stats = _read_sample_stats(
            handle,
            samples=samples,
            format_code=binary.sample_format_code,
            byte_order=binary.byte_order,
            chunk_bytes=policy.sample_chunk_bytes,
            aggregate=global_stats,
        )
        total_declared_samples += samples
        sample_counts.add(samples)
        sample_intervals.add(interval)
        nonfinite_sample_count += trace_stats.nonfinite_count
        if trace_stats.nonfinite_count and len(nonfinite_examples) < policy.max_examples:
            nonfinite_examples.append(trace_count)

        trace_identification = _i16(standard, 28, endian)
        is_dead = trace_identification == 2 or (
            trace_stats.count > 0 and trace_stats.maximum_abs == 0.0
        )
        is_constant = (
            trace_stats.count > 1
            and trace_stats.minimum is not None
            and trace_stats.maximum is not None
            and math.isclose(
                trace_stats.minimum,
                trace_stats.maximum,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
        )
        if is_dead:
            dead_count += 1
            if len(dead_examples) < policy.max_examples:
                dead_examples.append(trace_count)
        if is_constant:
            constant_count += 1
            if len(constant_examples) < policy.max_examples:
                constant_examples.append(trace_count)
        if trace_stats.rms is not None:
            _reservoir_add(
                reservoir,
                trace_stats.rms,
                trace_count,
                policy.anomaly_reservoir_size,
                reservoir_random,
            )

        scalar = _i16(standard, 70, endian)
        coordinate_scalar_values.add(scalar)
        coordinate_unit_codes.add(_i16(standard, 88, endian))
        factor = _scalar_factor(scalar)
        source_x.add(_i32(standard, 72, endian) * factor)
        source_y.add(_i32(standard, 76, endian) * factor)
        group_x.add(_i32(standard, 80, endian) * factor)
        group_y.add(_i32(standard, 84, endian) * factor)
        cdp_x.add(_i32(standard, 180, endian) * factor)
        cdp_y.add(_i32(standard, 184, endian) * factor)
        inline = _i32(standard, 188, endian)
        crossline = _i32(standard, 192, endian)
        if inline:
            inline_values.add(inline)
        if crossline:
            crossline_values.add(crossline)

    return {
        "trace_count": trace_count,
        "trace_header_bytes": trace_header_bytes,
        "trace_data_start_offset": trace_start,
        "file_bytes_consumed": handle.tell(),
        "total_declared_samples": total_declared_samples,
        "samples_per_trace": sample_counts.to_dict(),
        "sample_intervals": sample_intervals.to_dict(),
        "binary_header_sample_count_mismatch_count": binary_sample_mismatch_count,
        "binary_header_sample_interval_mismatch_count": binary_interval_mismatch_count,
        "dead_trace_count": dead_count,
        "constant_trace_count": constant_count,
        "nonfinite_sample_count": nonfinite_sample_count,
        "dead_trace_examples": dead_examples,
        "constant_trace_examples": constant_examples,
        "nonfinite_trace_examples": nonfinite_examples,
        "geometry": {
            "coordinate_units": _coordinate_units_label(
                coordinate_unit_codes.minimum,
                coordinate_unit_codes.maximum,
                binary.measurement_system,
            ),
            "coordinate_unit_codes": coordinate_unit_codes.to_dict(),
            "coordinate_scalars": coordinate_scalar_values.to_dict(),
            "source_x": source_x.to_dict(),
            "source_y": source_y.to_dict(),
            "group_x": group_x.to_dict(),
            "group_y": group_y.to_dict(),
            "cdp_x": cdp_x.to_dict(),
            "cdp_y": cdp_y.to_dict(),
            "inline_candidate": inline_values.to_dict(),
            "crossline_candidate": crossline_values.to_dict(),
        },
        "amplitude": global_stats.to_dict(),
        "_rms_reservoir": reservoir,
    }


def _scan_amplitude_outliers(
    handle: BinaryIO,
    *,
    file_size: int,
    trace_start: int,
    trace_header_bytes: int,
    binary: SegyBinaryHeader,
    policy: SegyPolicy,
    threshold: float,
) -> dict[str, Any]:
    handle.seek(trace_start)
    endian = ">" if binary.byte_order == "big" else "<"
    trace_index = 0
    count = 0
    examples: list[dict[str, Any]] = []
    while handle.tell() < file_size:
        standard = _read_exact(handle, 240, "trace header during anomaly pass")
        if trace_header_bytes > 240:
            _read_exact(
                handle,
                trace_header_bytes - 240,
                "trace header extensions during anomaly pass",
            )
        trace_index += 1
        header_samples = _u16(standard, 114, endian)
        samples = (
            binary.samples_per_trace
            if binary.fixed_length_trace_flag == 1
            else header_samples or binary.samples_per_trace
        )
        stats = _read_sample_stats(
            handle,
            samples=samples,
            format_code=binary.sample_format_code,
            byte_order=binary.byte_order,
            chunk_bytes=policy.sample_chunk_bytes,
        )
        if stats.rms is not None and stats.rms > threshold:
            count += 1
            if len(examples) < policy.max_examples:
                examples.append({"trace_index": trace_index, "rms": stats.rms})
    return {"candidate_count": count, "examples": examples}


def _read_sample_stats(
    handle: BinaryIO,
    *,
    samples: int,
    format_code: int,
    byte_order: str,
    chunk_bytes: int,
    aggregate: "_OnlineStats | None" = None,
) -> "_OnlineStats":
    bytes_per_sample = _SAMPLE_FORMATS[format_code][1]
    per_chunk = max(1, chunk_bytes // bytes_per_sample)
    remaining = samples
    stats = _OnlineStats()
    while remaining:
        count = min(remaining, per_chunk)
        raw = _read_exact(handle, count * bytes_per_sample, "trace sample data")
        for value in _decode_samples(raw, format_code, byte_order):
            stats.add(value)
            if aggregate is not None:
                aggregate.add(value)
        remaining -= count
    return stats


def _decode_samples(raw: bytes, format_code: int, byte_order: str):
    endian = ">" if byte_order == "big" else "<"
    formats = {2: "i", 3: "h", 5: "f", 6: "d", 8: "b"}
    if format_code in formats:
        yield from (item[0] for item in struct.iter_unpack(endian + formats[format_code], raw))
        return
    if format_code == 1:
        integer_format = endian + "I"
        for (bits,) in struct.iter_unpack(integer_format, raw):
            yield _ibm_to_float(bits)
        return
    raise SegyUnsupportedError(f"Unsupported sample format code: {format_code}")


def _ibm_to_float(bits: int) -> float:
    if bits & 0x7FFFFFFF == 0:
        return -0.0 if bits & 0x80000000 else 0.0
    sign = -1.0 if bits & 0x80000000 else 1.0
    exponent = (bits >> 24) & 0x7F
    fraction = bits & 0x00FFFFFF
    return sign * (fraction / float(1 << 24)) * (16.0 ** (exponent - 64))


class _OnlineStats:
    def __init__(self) -> None:
        self.count = 0
        self.nonfinite_count = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.minimum: float | None = None
        self.maximum: float | None = None
        self.maximum_abs = 0.0
        self._scale = 0.0
        self._scaled_squares = 1.0

    def add(self, value: int | float) -> None:
        numeric = float(value)
        if not math.isfinite(numeric):
            self.nonfinite_count += 1
            return
        self.count += 1
        delta = numeric - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (numeric - self.mean)
        self.minimum = numeric if self.minimum is None else min(self.minimum, numeric)
        self.maximum = numeric if self.maximum is None else max(self.maximum, numeric)
        absolute = abs(numeric)
        self.maximum_abs = max(self.maximum_abs, absolute)
        if absolute:
            if self._scale < absolute:
                self._scaled_squares = 1.0 + self._scaled_squares * (self._scale / absolute) ** 2
                self._scale = absolute
            else:
                self._scaled_squares += (absolute / self._scale) ** 2

    @property
    def rms(self) -> float | None:
        if not self.count:
            return None
        return self._scale * math.sqrt(self._scaled_squares / self.count) if self._scale else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "finite_sample_count": self.count,
            "nonfinite_sample_count": self.nonfinite_count,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "mean": self.mean if self.count else None,
            "standard_deviation": (
                math.sqrt(max(self.m2, 0.0) / self.count) if self.count else None
            ),
            "rms": self.rms,
            "maximum_absolute": self.maximum_abs if self.count else None,
        }


class _RangeTracker:
    def __init__(self, *, track_unique: bool = False, unique_limit: int = 10000) -> None:
        self.count = 0
        self.minimum: float | int | None = None
        self.maximum: float | int | None = None
        self._unique: set[float | int] | None = set() if track_unique else None
        self._unique_limit = unique_limit
        self._unique_truncated = False

    def add(self, value: float | int) -> None:
        self.count += 1
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        if self._unique is not None:
            if len(self._unique) < self._unique_limit:
                self._unique.add(value)
            elif value not in self._unique:
                self._unique_truncated = True

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "count": self.count,
            "minimum": self.minimum,
            "maximum": self.maximum,
        }
        if self._unique is not None:
            payload["unique_count"] = None if self._unique_truncated else len(self._unique)
            payload["unique_count_truncated"] = self._unique_truncated
        return payload


def _derive_anomaly_threshold(values: list[float]) -> dict[str, Any]:
    finite = [value for value in values if math.isfinite(value)]
    if len(finite) < 3:
        return {
            "method": "median_plus_6_scaled_mad",
            "reservoir_size": len(finite),
            "median_rms": median(finite) if finite else None,
            "scaled_mad": None,
            "threshold": None,
        }
    center = median(finite)
    mad = median(abs(value - center) for value in finite)
    scaled_mad = 1.4826 * mad
    threshold = center + 6.0 * scaled_mad
    if scaled_mad == 0.0:
        threshold = center * 10.0 if center > 0 else None
    return {
        "method": "median_plus_6_scaled_mad",
        "reservoir_size": len(finite),
        "median_rms": center,
        "scaled_mad": scaled_mad,
        "threshold": threshold,
    }


def _reservoir_add(
    reservoir: list[float],
    value: float,
    index: int,
    limit: int,
    generator: random.Random,
) -> None:
    if limit <= 0:
        return
    if len(reservoir) < limit:
        reservoir.append(value)
        return
    replacement = generator.randrange(index)
    if replacement < limit:
        reservoir[replacement] = value


def _skip_extended_text_headers(
    handle: BinaryIO,
    declared_count: int,
    policy: SegyPolicy,
    file_size: int,
) -> int:
    if declared_count < -1:
        raise SegyError(f"Invalid extended textual header count: {declared_count}")
    if declared_count >= 0:
        if declared_count > policy.max_extended_text_headers:
            raise SegyUnsupportedError(
                f"SEG-Y declares {declared_count} extended textual headers; policy limit "
                f"is {policy.max_extended_text_headers}"
            )
        _read_exact(
            handle,
            declared_count * 3200,
            f"{declared_count} extended textual headers",
        )
        return declared_count

    count = 0
    while count < policy.max_extended_text_headers:
        if file_size - handle.tell() < 3200:
            raise SegyTruncatedError(
                "Variable extended textual headers do not contain an ((SEG: EndText)) stanza"
            )
        raw = _read_exact(handle, 3200, "variable extended textual header")
        count += 1
        _, decoded = _decode_text_header(raw)
        normalized = re.sub(r"\s+", "", decoded).upper()
        if "((SEG:ENDTEXT))" in normalized:
            return count
    raise SegyUnsupportedError(
        f"Variable extended textual header count exceeds policy limit "
        f"{policy.max_extended_text_headers}"
    )


def _decode_text_header(raw: bytes) -> tuple[str, str]:
    candidates: list[tuple[float, str, str]] = []
    for encoding in ("ascii", "cp500"):
        decoded = raw.decode(encoding, errors="replace")
        printable = sum(character.isprintable() or character in "\r\n\t" for character in decoded)
        replacement = decoded.count("\ufffd")
        score = (printable - replacement * 4) / max(len(decoded), 1)
        candidates.append((score, encoding, decoded))
    score, encoding, decoded = max(candidates, key=lambda item: item[0])
    if score < 0.75:
        raise SegyError("Cannot identify SEG-Y textual header as ASCII or EBCDIC")
    return encoding, decoded


def _scalar_factor(value: int) -> float:
    if value == 0:
        return 1.0
    return float(value) if value > 0 else 1.0 / abs(value)


def _coordinate_units_label(
    minimum_code: float | int | None,
    maximum_code: float | int | None,
    measurement_system: int,
) -> str:
    if minimum_code == maximum_code == 1:
        return {1: "length (meters)", 2: "length (feet)"}.get(
            measurement_system, "length (unit not declared in Binary Header)"
        )
    if minimum_code == maximum_code == 2:
        return "seconds of arc (deprecated)"
    if minimum_code == maximum_code == 3:
        return "decimal degrees"
    if minimum_code == maximum_code == 4:
        return "degrees/minutes/seconds"
    return "unknown or mixed"


def _read_exact(handle: BinaryIO, size: int, label: str) -> bytes:
    raw = handle.read(size)
    if len(raw) != size:
        raise SegyTruncatedError(f"Truncated {label}: expected {size} bytes, received {len(raw)}")
    return raw


def _i16(raw: bytes, offset: int, endian: str) -> int:
    return struct.unpack_from(endian + "h", raw, offset)[0]


def _u16(raw: bytes, offset: int, endian: str) -> int:
    return struct.unpack_from(endian + "H", raw, offset)[0]


def _i32(raw: bytes, offset: int, endian: str) -> int:
    return struct.unpack_from(endian + "i", raw, offset)[0]


def _u32(raw: bytes, offset: int, endian: str) -> int:
    return struct.unpack_from(endian + "I", raw, offset)[0]


def _f64(raw: bytes, offset: int, endian: str) -> float:
    return struct.unpack_from(endian + "d", raw, offset)[0]

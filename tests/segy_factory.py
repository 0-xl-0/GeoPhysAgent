from __future__ import annotations

import struct
from pathlib import Path


def write_synthetic_segy(
    path: Path,
    *,
    traces: list[list[float | int]],
    format_code: int = 5,
    sample_interval: int = 2000,
    fixed_length: int = 1,
    textual_encoding: str = "ascii",
    byte_order: str = "big",
    revision_major: int = 1,
    binary_samples: int | None = None,
    trace_sample_intervals: list[int] | None = None,
    truncate_bytes: int = 0,
) -> Path:
    if not traces:
        raise ValueError("Synthetic SEG-Y requires traces")
    binary_sample_count = binary_samples or len(traces[0])
    endian = ">" if byte_order == "big" else "<"
    cards = [
        f"C{index:02d} SYNTHETIC SEG-Y GOLDEN FIXTURE" if index == 1 else f"C{index:02d}"
        for index in range(1, 41)
    ]
    textual = "".join(card.ljust(80)[:80] for card in cards).encode(textual_encoding)
    binary = bytearray(400)
    struct.pack_into(endian + "i", binary, 0, 1234)
    struct.pack_into(endian + "i", binary, 4, 77)
    struct.pack_into(endian + "i", binary, 8, 1)
    struct.pack_into(endian + "H", binary, 16, sample_interval)
    struct.pack_into(endian + "H", binary, 20, binary_sample_count)
    struct.pack_into(endian + "H", binary, 24, format_code)
    struct.pack_into(endian + "h", binary, 28, 3)
    struct.pack_into(endian + "h", binary, 54, 1)
    if revision_major >= 2:
        struct.pack_into(endian + "I", binary, 96, 0x01020304)
    binary[300] = revision_major
    binary[301] = 0
    struct.pack_into(endian + "H", binary, 302, fixed_length)
    struct.pack_into(endian + "h", binary, 304, 0)
    if revision_major >= 2:
        struct.pack_into(endian + "H", binary, 306, 0)

    payload = bytearray(textual)
    payload.extend(binary)
    for index, values in enumerate(traces, start=1):
        header = bytearray(240)
        struct.pack_into(endian + "i", header, 0, index)
        struct.pack_into(endian + "i", header, 4, index)
        struct.pack_into(endian + "i", header, 20, index)
        struct.pack_into(endian + "h", header, 28, 2 if index == 1 else 1)
        struct.pack_into(endian + "h", header, 70, -10)
        struct.pack_into(endian + "i", header, 72, 10000 + index * 10)
        struct.pack_into(endian + "i", header, 76, 20000 + index * 10)
        struct.pack_into(endian + "i", header, 80, 11000 + index * 10)
        struct.pack_into(endian + "i", header, 84, 21000 + index * 10)
        struct.pack_into(endian + "h", header, 88, 1)
        struct.pack_into(endian + "H", header, 114, len(values))
        trace_interval = (
            trace_sample_intervals[index - 1]
            if trace_sample_intervals is not None
            else sample_interval
        )
        struct.pack_into(endian + "H", header, 116, trace_interval)
        struct.pack_into(endian + "i", header, 180, 12000 + index * 10)
        struct.pack_into(endian + "i", header, 184, 22000 + index * 10)
        struct.pack_into(endian + "i", header, 188, 100 + index)
        struct.pack_into(endian + "i", header, 192, 200 + index)
        payload.extend(header)
        payload.extend(_pack_samples(values, format_code, endian))

    if truncate_bytes:
        payload = payload[:-truncate_bytes]
    path.write_bytes(payload)
    return path


def _pack_samples(values: list[float | int], format_code: int, endian: str) -> bytes:
    format_char = {2: "i", 3: "h", 5: "f", 6: "d", 8: "b"}.get(format_code)
    if format_char is not None:
        return b"".join(struct.pack(endian + format_char, value) for value in values)
    if format_code == 1:
        return b"".join(struct.pack(endian + "I", _float_to_ibm(float(value))) for value in values)
    # Unknown/unsupported codes still need deterministic bytes for parser rejection tests.
    return b"\x00\x00\x00\x00" * len(values)


def _float_to_ibm(value: float) -> int:
    if value == 0.0:
        return 0
    sign = 0x80000000 if value < 0 else 0
    fraction = abs(value)
    exponent = 64
    while fraction < 0.0625:
        fraction *= 16.0
        exponent -= 1
    while fraction >= 1.0:
        fraction /= 16.0
        exponent += 1
    mantissa = min(int(fraction * (1 << 24) + 0.5), 0xFFFFFF)
    return sign | ((exponent & 0x7F) << 24) | mantissa

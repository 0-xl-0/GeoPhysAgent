from __future__ import annotations

from statistics import fmean

from geophysagent.domain import GeoToolResult, LasDataset
from geophysagent.las_quality import load_las_rule_pack, normalize_curve_identity


def inspect_las(dataset: LasDataset) -> GeoToolResult:
    pack = load_las_rule_pack()
    return GeoToolResult(
        tool_name="las.inspect",
        tool_version="0.2.0",
        status="success",
        data={
            "row_count": len(dataset.rows),
            "curve_count": len(dataset.curves),
            "null_value": dataset.null_value,
            "format": {
                "version": dataset.version,
                "wrap": dataset.wrap,
                "delimiter": dataset.delimiter,
                "encoding": dataset.encoding,
                "vendor_extensions": list(dataset.vendor_extensions),
            },
            "well": dataset.well,
            "curves": [
                {
                    "mnemonic": curve.mnemonic,
                    "unit": curve.unit,
                    "family": normalize_curve_identity(curve.mnemonic, curve.unit, pack)[0],
                    "canonical_unit": normalize_curve_identity(curve.mnemonic, curve.unit, pack)[1],
                    "description": curve.description,
                }
                for curve in dataset.curves
            ],
        },
    )


def compute_curve_statistics(dataset: LasDataset) -> GeoToolResult:
    pack = load_las_rule_pack()
    statistics: list[dict[str, object]] = []
    row_count = len(dataset.rows)
    for index, curve in enumerate(dataset.curves):
        family, canonical_unit = normalize_curve_identity(curve.mnemonic, curve.unit, pack)
        values = [row[index] for row in dataset.rows if row[index] is not None]
        missing_count = row_count - len(values)
        statistics.append(
            {
                "mnemonic": curve.mnemonic,
                "unit": curve.unit,
                "family": family,
                "canonical_unit": canonical_unit,
                "valid_count": len(values),
                "missing_count": missing_count,
                "missing_ratio": missing_count / row_count,
                "minimum": min(values) if values else None,
                "maximum": max(values) if values else None,
                "mean": fmean(values) if values else None,
            }
        )
    return GeoToolResult(
        tool_name="las.curve-statistics",
        tool_version="0.2.0",
        status="success",
        data={"curves": statistics},
    )

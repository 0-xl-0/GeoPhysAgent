from __future__ import annotations

import json
import math
from importlib.resources import files
from statistics import median
from typing import Any

from geophysagent.domain import GeoToolResult, LasDataset
from geophysagent.quality_contracts import RuleDefinition, RulePack


def load_las_rule_pack() -> RulePack:
    resource = files("geophysagent").joinpath("resources/las-qc-rules.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    rules = tuple(
        RuleDefinition(
            rule_id=str(item["rule_id"]),
            version=str(item["version"]),
            title=str(item["title"]),
            source=str(item["source"]),
            parameters=dict(item.get("parameters", {})),
            applicability=str(item.get("applicability", "")),
        )
        for item in payload["rules"]
    )
    return RulePack(
        pack_id=str(payload["pack_id"]),
        version=str(payload["version"]),
        title=str(payload["title"]),
        source=str(payload["source"]),
        rules=rules,
        metadata=dict(payload.get("metadata", {})),
    )


def normalize_curve_identity(
    mnemonic: str, unit: str, pack: RulePack | None = None
) -> tuple[str | None, str]:
    selected = pack or load_las_rule_pack()
    aliases = selected.metadata.get("aliases", {})
    family = next(
        (
            str(name)
            for name, values in aliases.items()
            if mnemonic.upper() in {str(item).upper() for item in values}
        ),
        None,
    )
    units = selected.metadata.get("unit_aliases", {})
    canonical_unit = str(units.get(unit.strip().upper(), unit.strip()))
    return family, canonical_unit


def evaluate_las_quality(dataset: LasDataset, pack: RulePack | None = None) -> GeoToolResult:
    selected = pack or load_las_rule_pack()
    curves: list[dict[str, Any]] = []
    warning_count = 0
    observation_count = 0
    for index, curve in enumerate(dataset.curves):
        family, canonical_unit = normalize_curve_identity(curve.mnemonic, curve.unit, selected)
        results: list[dict[str, Any]] = []
        values = [row[index] for row in dataset.rows]
        if index > 0:
            missing = _missing_result(values, selected)
            results.append(missing)
            range_result = _range_result(dataset, index, family, canonical_unit, selected)
            results.append(range_result)
            results.append(_flatline_result(dataset, index, selected))
            results.append(_spike_result(dataset, index, selected))
            results.append(_jump_result(dataset, index, selected))
        for result in results:
            if result["status"] == "warning":
                warning_count += 1
                observation_count += int(result.get("count", 0))
        curves.append(
            {
                "mnemonic": curve.mnemonic,
                "family": family,
                "unit": curve.unit,
                "canonical_unit": canonical_unit,
                "rule_results": results,
            }
        )
    status = "warning" if warning_count else "success"
    return GeoToolResult(
        tool_name="las.quality-rules",
        tool_version=selected.version,
        status=status,
        data={
            "rule_pack": {
                "pack_id": selected.pack_id,
                "version": selected.version,
                "title": selected.title,
                "source": selected.source,
            },
            "summary": {
                "curve_count": len(dataset.curves),
                "warning_rule_count": warning_count,
                "observation_count": observation_count,
            },
            "curves": curves,
        },
        warnings=(
            (f"{warning_count} LAS curve rule checks reported observations",)
            if warning_count
            else ()
        ),
    )


def _rule_base(pack: RulePack, rule_id: str) -> dict[str, Any]:
    rule = pack.definition(rule_id)
    return {
        "rule_id": rule.rule_id,
        "rule_version": rule.version,
        "rule_pack_id": pack.pack_id,
        "rule_pack_version": pack.version,
        "source": rule.source,
        "parameters": rule.parameters,
    }


def _missing_result(values: list[float | None], pack: RulePack) -> dict[str, Any]:
    base = _rule_base(pack, "las.curve.missing")
    count = sum(value is None for value in values)
    ratio = count / len(values) if values else 1.0
    threshold = float(base["parameters"]["warning_ratio"])
    return {
        **base,
        "status": "warning" if ratio >= threshold else "pass",
        "count": count,
        "missing_ratio": ratio,
        "message": (
            f"Missing ratio {ratio:.2%} is at or above {threshold:.2%}"
            if ratio >= threshold
            else f"Missing ratio {ratio:.2%} is below {threshold:.2%}"
        ),
        "examples": [],
    }


def _range_result(
    dataset: LasDataset,
    curve_index: int,
    family: str | None,
    canonical_unit: str,
    pack: RulePack,
) -> dict[str, Any]:
    base = _rule_base(pack, "las.curve.range")
    configured = pack.metadata.get("physical_ranges", {}).get(family or "")
    target_unit = str(configured.get("unit", "")) if isinstance(configured, dict) else ""
    if not isinstance(configured, dict) or not _range_compatible(
        family or "", canonical_unit, target_unit
    ):
        return {
            **base,
            "status": "not_applicable",
            "count": 0,
            "message": "No reviewed range is available for this curve family and unit",
            "examples": [],
        }
    minimum = float(configured["minimum"])
    maximum = float(configured["maximum"])
    examples = []
    count = 0
    for row_index, row in enumerate(dataset.rows):
        raw = row[curve_index]
        if raw is None:
            continue
        value = _canonical_value(float(raw), family or "", canonical_unit)
        if value < minimum or value > maximum:
            count += 1
            if len(examples) < int(base["parameters"]["maximum_examples"]):
                examples.append(
                    {
                        "index": row_index,
                        "depth": row[0],
                        "value": raw,
                        "canonical_value": value,
                    }
                )
    return {
        **base,
        "status": "warning" if count else "pass",
        "count": count,
        "canonical_unit": target_unit,
        "minimum": minimum,
        "maximum": maximum,
        "message": f"Detected {count} broad-range observations",
        "examples": examples,
    }


def _canonical_value(value: float, family: str, unit: str) -> float:
    if family == "bulk_density" and unit == "kg/m3":
        return value / 1000.0
    if family == "neutron_porosity" and unit in {"%", "pu"}:
        return value / 100.0
    if family == "caliper" and unit == "mm":
        return value / 25.4
    if family == "sonic_slowness" and unit == "us/m":
        return value / 3.280839895013123
    return value


def _range_compatible(family: str, source_unit: str, target_unit: str) -> bool:
    if source_unit == target_unit:
        return True
    return (family, source_unit, target_unit) in {
        ("bulk_density", "kg/m3", "g/cm3"),
        ("neutron_porosity", "%", "v/v"),
        ("neutron_porosity", "pu", "v/v"),
        ("caliper", "mm", "in"),
        ("sonic_slowness", "us/m", "us/ft"),
    }


def _flatline_result(dataset: LasDataset, curve_index: int, pack: RulePack) -> dict[str, Any]:
    base = _rule_base(pack, "las.curve.flatline")
    minimum_run = int(base["parameters"]["minimum_run"])
    tolerance = float(base["parameters"]["relative_tolerance"])
    examples: list[dict[str, Any]] = []
    start = 0
    rows = dataset.rows
    for index in range(1, len(rows) + 1):
        same = False
        if index < len(rows):
            left, right = rows[index - 1][curve_index], rows[index][curve_index]
            same = (
                left is not None
                and right is not None
                and math.isclose(float(left), float(right), rel_tol=tolerance, abs_tol=1e-12)
            )
        if same:
            continue
        length = index - start
        value = rows[start][curve_index] if rows else None
        if value is not None and length >= minimum_run:
            examples.append(
                {
                    "start_index": start,
                    "end_index": index - 1,
                    "start_depth": rows[start][0],
                    "end_depth": rows[index - 1][0],
                    "sample_count": length,
                    "value": value,
                }
            )
        start = index
    return {
        **base,
        "status": "warning" if examples else "pass",
        "count": len(examples),
        "message": f"Detected {len(examples)} constant-value runs",
        "examples": examples[:10],
    }


def _spike_result(dataset: LasDataset, curve_index: int, pack: RulePack) -> dict[str, Any]:
    base = _rule_base(pack, "las.curve.spike")
    radius = int(base["parameters"]["window_radius"])
    multiplier = float(base["parameters"]["mad_multiplier"])
    examples: list[dict[str, Any]] = []
    values = [row[curve_index] for row in dataset.rows]
    for index in range(radius, len(values) - radius):
        value = values[index]
        neighbors = values[index - radius : index] + values[index + 1 : index + radius + 1]
        if value is None or any(item is None for item in neighbors):
            continue
        numeric = [float(item) for item in neighbors if item is not None]
        center = median(numeric)
        mad = median(abs(item - center) for item in numeric)
        scale = max(abs(center), max((abs(item) for item in numeric), default=0.0), 1.0)
        threshold = max(multiplier * mad, scale * 1e-6)
        if abs(float(value) - center) > threshold:
            examples.append(
                {
                    "index": index,
                    "depth": dataset.rows[index][0],
                    "value": value,
                    "local_median": center,
                    "threshold": threshold,
                }
            )
    return {
        **base,
        "status": "warning" if examples else "pass",
        "count": len(examples),
        "message": f"Detected {len(examples)} isolated spike candidates",
        "examples": examples[:10],
    }


def _jump_result(dataset: LasDataset, curve_index: int, pack: RulePack) -> dict[str, Any]:
    base = _rule_base(pack, "las.curve.jump")
    multiplier = float(base["parameters"]["mad_multiplier"])
    differences: list[tuple[int, float]] = []
    for index in range(1, len(dataset.rows)):
        left = dataset.rows[index - 1][curve_index]
        right = dataset.rows[index][curve_index]
        if left is not None and right is not None:
            differences.append((index, float(right) - float(left)))
    if len(differences) < 4:
        return {
            **base,
            "status": "not_applicable",
            "count": 0,
            "message": "At least four valid differences are required",
            "examples": [],
        }
    values = [value for _index, value in differences]
    center = median(values)
    mad = median(abs(value - center) for value in values)
    scale = max(abs(center), max((abs(value) for value in values), default=0.0), 1.0)
    threshold = max(multiplier * mad, scale * 1e-6)
    examples = [
        {
            "from_index": index - 1,
            "to_index": index,
            "from_depth": dataset.rows[index - 1][0],
            "to_depth": dataset.rows[index][0],
            "difference": difference,
            "median_difference": center,
            "threshold": threshold,
        }
        for index, difference in differences
        if abs(difference - center) > threshold
    ]
    return {
        **base,
        "status": "warning" if examples else "pass",
        "count": len(examples),
        "message": f"Detected {len(examples)} abrupt first-difference candidates",
        "examples": examples[:10],
    }

from __future__ import annotations

from math import isclose
from statistics import median

from geophysagent.domain import GeoToolResult, LasDataset, ValidationCheck, ValidationReport
from geophysagent.segy import SUPPORTED_SAMPLE_FORMAT_CODES, SegyInspection


def validate_las(
    dataset: LasDataset, quality_result: GeoToolResult | None = None
) -> ValidationReport:
    checks: list[ValidationCheck] = []
    checks.append(
        ValidationCheck(
            "las.format.version",
            "pass" if dataset.version.startswith("2.") else "fail",
            f"LAS version {dataset.version}; WRAP={'YES' if dataset.wrap else 'NO'}; "
            f"encoding={dataset.encoding}; delimiter={dataset.delimiter}",
            {
                "version": dataset.version,
                "wrap": dataset.wrap,
                "encoding": dataset.encoding,
                "delimiter": dataset.delimiter,
                "vendor_extensions": list(dataset.vendor_extensions),
            },
        )
    )
    index_mnemonic = dataset.curves[0].mnemonic
    standard_indexes = {"DEPT", "DEPTH", "TIME", "INDEX"}
    checks.append(
        ValidationCheck(
            "las.index.mnemonic",
            "pass" if index_mnemonic in standard_indexes else "warning",
            (
                f"Index mnemonic {index_mnemonic} is a CWLS LAS 2.0 standard index"
                if index_mnemonic in standard_indexes
                else f"Index mnemonic {index_mnemonic} is treated as a vendor alias"
            ),
            {"mnemonic": index_mnemonic, "standard": index_mnemonic in standard_indexes},
        )
    )
    required_well = ("STRT", "STOP", "STEP", "NULL")
    missing_well = [name for name in required_well if name not in dataset.well]
    checks.append(
        ValidationCheck(
            "las.well.required",
            "warning" if missing_well else "pass",
            (
                "Missing recommended ~Well values: " + ", ".join(missing_well)
                if missing_well
                else "STRT, STOP, STEP and NULL are present"
            ),
            {"missing": missing_well},
        )
    )
    depths = [row[0] for row in dataset.rows]
    if any(value is None for value in depths):
        checks.append(ValidationCheck("depth.null", "fail", "Depth curve contains NULL values"))
        return ValidationReport("fail", tuple(checks))

    numeric_depths = [float(value) for value in depths if value is not None]
    differences = [b - a for a, b in zip(numeric_depths, numeric_depths[1:])]
    duplicates = [
        numeric_depths[index + 1]
        for index, difference in enumerate(differences)
        if isclose(difference, 0.0, abs_tol=1e-12)
    ]
    nonzero = [difference for difference in differences if not isclose(difference, 0.0)]
    ascending = bool(nonzero) and all(value > 0 for value in nonzero)
    descending = bool(nonzero) and all(value < 0 for value in nonzero)

    if duplicates:
        checks.append(
            ValidationCheck(
                "depth.duplicate",
                "warning",
                "Depth curve contains duplicate samples",
                {"count": len(duplicates), "examples": duplicates[:10]},
            )
        )
    else:
        checks.append(ValidationCheck("depth.duplicate", "pass", "No duplicate depths"))

    if len(numeric_depths) == 1:
        checks.append(
            ValidationCheck(
                "depth.direction",
                "warning",
                "A single index sample cannot establish direction",
            )
        )
    elif ascending or descending:
        checks.append(
            ValidationCheck(
                "depth.direction",
                "pass",
                f"Depth is strictly {'ascending' if ascending else 'descending'}",
            )
        )
    else:
        checks.append(
            ValidationCheck("depth.direction", "fail", "Depth changes direction within the file")
        )

    if nonzero:
        typical_step = median(abs(value) for value in nonzero)
        irregular = [
            abs(value)
            for value in nonzero
            if not isclose(abs(value), typical_step, rel_tol=1e-6, abs_tol=1e-9)
        ]
        checks.append(
            ValidationCheck(
                "depth.sampling",
                "warning" if irregular else "pass",
                "Depth sampling is irregular" if irregular else "Depth sampling is regular",
                {"typical_step": typical_step, "irregular_interval_count": len(irregular)},
            )
        )

    checks.extend(_validate_las_header_consistency(dataset, numeric_depths, differences))
    if quality_result is not None:
        pack = quality_result.data.get("rule_pack", {})
        checks.append(
            ValidationCheck(
                "las.rule_pack",
                "pass",
                f"Applied Rule Pack {pack.get('pack_id')}@{pack.get('version')}",
                dict(pack) if isinstance(pack, dict) else {},
            )
        )
        for curve in quality_result.data.get("curves", []):
            if not isinstance(curve, dict):
                continue
            mnemonic = str(curve.get("mnemonic", "unknown"))
            for result in curve.get("rule_results", []):
                if not isinstance(result, dict) or result.get("status") != "warning":
                    continue
                checks.append(
                    ValidationCheck(
                        f"{result.get('rule_id')}.{mnemonic.lower()}",
                        "warning",
                        str(result.get("message", "LAS curve QC observation")),
                        {
                            "curve": mnemonic,
                            "rule_id": result.get("rule_id"),
                            "rule_version": result.get("rule_version"),
                            "rule_pack_id": result.get("rule_pack_id"),
                            "rule_pack_version": result.get("rule_pack_version"),
                            "count": result.get("count", 0),
                            "examples": result.get("examples", []),
                        },
                    )
                )

    statuses = {check.status for check in checks}
    overall = "fail" if "fail" in statuses else "warning" if "warning" in statuses else "pass"
    return ValidationReport(overall, tuple(checks))


def _validate_las_header_consistency(
    dataset: LasDataset,
    depths: list[float],
    differences: list[float],
) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    for name, observed in (("STRT", depths[0]), ("STOP", depths[-1])):
        raw = dataset.well.get(name)
        if raw is None:
            continue
        try:
            expected = float(raw)
        except ValueError:
            checks.append(ValidationCheck(f"las.well.{name.lower()}", "fail", f"Invalid {name}"))
            continue
        matches = isclose(expected, observed, rel_tol=1e-9, abs_tol=1e-9)
        checks.append(
            ValidationCheck(
                f"las.well.{name.lower()}",
                "pass" if matches else "warning",
                f"{name} {'matches' if matches else 'does not match'} the data index",
                {"header": expected, "observed": observed},
            )
        )
    if differences and "STEP" in dataset.well:
        try:
            expected_step = float(dataset.well["STEP"])
            observed_step = median(differences)
            matches = isclose(expected_step, observed_step, rel_tol=1e-6, abs_tol=1e-9)
            checks.append(
                ValidationCheck(
                    "las.well.step",
                    "pass" if matches else "warning",
                    f"STEP {'matches' if matches else 'does not match'} the median data step",
                    {"header": expected_step, "observed": observed_step},
                )
            )
        except ValueError:
            checks.append(ValidationCheck("las.well.step", "fail", "Invalid STEP value"))

    index_unit = dataset.curves[0].unit.strip().upper()
    unit_mismatches = {
        name: unit
        for name in ("STRT", "STOP", "STEP")
        if (unit := dataset.well_units.get(name, "").strip().upper()) and unit != index_unit
    }
    checks.append(
        ValidationCheck(
            "las.index.units",
            "warning" if unit_mismatches else "pass",
            (
                "Index units differ between ~Well and ~Curve"
                if unit_mismatches
                else "Index units agree between ~Well and ~Curve"
            ),
            {"index_unit": index_unit, "mismatches": unit_mismatches},
        )
    )
    return checks


def validate_segy(inspection: SegyInspection) -> ValidationReport:
    """Validate independently derived SEG-Y structure and basic QC observations."""
    checks: list[ValidationCheck] = []
    binary = inspection.binary_header
    traces = inspection.trace_summary
    observations = inspection.qc_observations

    checks.append(
        ValidationCheck(
            "segy.sample_format",
            "pass" if binary.sample_format_code in SUPPORTED_SAMPLE_FORMAT_CODES else "fail",
            f"Sample format code {binary.sample_format_code}: {binary.sample_format_name}",
            {
                "format_code": binary.sample_format_code,
                "bytes_per_sample": binary.bytes_per_sample,
            },
        )
    )
    trace_count = int(traces["trace_count"])
    checks.append(
        ValidationCheck(
            "segy.trace_count",
            "pass" if trace_count > 0 else "fail",
            f"Safely traversed {trace_count} data traces",
            {"trace_count": trace_count},
        )
    )
    sample_mismatches = int(traces["binary_header_sample_count_mismatch_count"])
    checks.append(
        ValidationCheck(
            "segy.sample_count_consistency",
            "warning" if sample_mismatches else "pass",
            (
                "Trace sample counts differ from the Binary Header"
                if sample_mismatches
                else "Trace sample counts agree with the Binary Header"
            ),
            {
                "mismatch_trace_count": sample_mismatches,
                "fixed_length_trace_flag": binary.fixed_length_trace_flag,
                "observed_range": traces["samples_per_trace"],
            },
        )
    )
    interval_mismatches = int(traces["binary_header_sample_interval_mismatch_count"])
    checks.append(
        ValidationCheck(
            "segy.sample_interval_consistency",
            "warning" if interval_mismatches else "pass",
            (
                "Trace sample intervals differ from the Binary Header"
                if interval_mismatches
                else "Trace sample intervals agree with the Binary Header"
            ),
            {
                "mismatch_trace_count": interval_mismatches,
                "observed_range": traces["sample_intervals"],
            },
        )
    )

    for code, count_key, examples_key, label in (
        ("segy.dead_traces", "dead_trace_count", "dead_trace_examples", "dead or zero traces"),
        (
            "segy.constant_traces",
            "constant_trace_count",
            "constant_trace_examples",
            "constant traces",
        ),
        (
            "segy.nonfinite_samples",
            "nonfinite_sample_count",
            "nonfinite_trace_examples",
            "non-finite samples",
        ),
        (
            "segy.amplitude_outliers",
            "abnormal_amplitude_trace_count",
            "abnormal_amplitude_examples",
            "abnormal-amplitude trace candidates",
        ),
    ):
        count = int(observations[count_key])
        checks.append(
            ValidationCheck(
                code,
                "warning" if count else "pass",
                f"Detected {count} {label}" if count else f"No {label} detected",
                {"count": count, "examples": observations[examples_key]},
            )
        )

    geometry = inspection.geometry
    coordinate_ranges = [
        geometry[name] for name in ("source_x", "source_y", "group_x", "group_y", "cdp_x", "cdp_y")
    ]
    populated = any(
        item["minimum"] not in {None, 0, 0.0} or item["maximum"] not in {None, 0, 0.0}
        for item in coordinate_ranges
    )
    checks.append(
        ValidationCheck(
            "segy.coordinates",
            "pass" if populated else "warning",
            (
                "At least one standard coordinate field is populated"
                if populated
                else "Standard coordinate fields are all zero or unavailable"
            ),
            {
                "coordinate_units": geometry["coordinate_units"],
                "inline_candidate": geometry["inline_candidate"],
                "crossline_candidate": geometry["crossline_candidate"],
            },
        )
    )

    statuses = {check.status for check in checks}
    overall = "fail" if "fail" in statuses else "warning" if "warning" in statuses else "pass"
    return ValidationReport(overall, tuple(checks))

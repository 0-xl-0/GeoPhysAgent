from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from geophysagent.domain import LasDataset
from geophysagent.las_quality import load_las_rule_pack, normalize_curve_identity
from geophysagent.quality_contracts import FigureArtifactMetadata, FigureAxis


PLOT_TOOL_NAME = "las.render-well-log-svg"
PLOT_TOOL_VERSION = "0.1.0"
_COLORS = ("#2563eb", "#dc2626", "#059669", "#7c3aed", "#d97706", "#0891b2")


def write_las_figure(
    dataset: LasDataset, output_dir: str | Path
) -> tuple[Path, Path, FigureArtifactMetadata]:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    figure_path = output / "well-log.svg"
    metadata_path = output / "well-log.figure.json"
    svg, metadata = render_las_figure(dataset)
    figure_path.write_text(svg, encoding="utf-8", newline="\n")
    metadata_path.write_text(
        json.dumps(metadata.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return figure_path, metadata_path, metadata


def render_las_figure(dataset: LasDataset) -> tuple[str, FigureArtifactMetadata]:
    depths = [float(row[0]) for row in dataset.rows if row[0] is not None]
    if not depths:
        raise ValueError("LAS figure requires a numeric index curve")
    depth_min, depth_max = min(depths), max(depths)
    if depth_min == depth_max:
        depth_min -= 0.5
        depth_max += 0.5

    curve_indexes = list(range(1, min(len(dataset.curves), 7)))
    if not curve_indexes:
        curve_indexes = [0]
    track_width = 180
    left = 90
    top = 70
    plot_height = 620
    bottom = 65
    width = left + track_width * len(curve_indexes) + 30
    height = top + plot_height + bottom
    pack = load_las_rule_pack()
    axes = [
        FigureAxis(
            "index",
            dataset.curves[0].mnemonic,
            normalize_curve_identity(dataset.curves[0].mnemonic, dataset.curves[0].unit, pack)[1],
            depth_min,
            depth_max,
            "increasing_down",
        )
    ]
    series: list[dict[str, Any]] = []
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">LAS well-log quality-control plot</title>',
        '<desc id="desc">Deterministic track plot generated from the authorized LAS asset.</desc>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="20" y="30" font-family="sans-serif" font-size="18" font-weight="600">'
        "LAS Well Log — deterministic QC view</text>",
    ]
    for tick in range(6):
        depth = depth_min + (depth_max - depth_min) * tick / 5
        y = top + plot_height * tick / 5
        elements.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{width - 30}" y2="{y:.2f}" '
            'stroke="#e5e7eb" stroke-width="1"/>'
        )
        elements.append(
            f'<text x="{left - 8}" y="{y + 4:.2f}" text-anchor="end" '
            f'font-family="monospace" font-size="10" fill="#374151">{depth:.6g}</text>'
        )

    for track, curve_index in enumerate(curve_indexes):
        curve = dataset.curves[curve_index]
        family, canonical_unit = normalize_curve_identity(curve.mnemonic, curve.unit, pack)
        valid = [float(row[curve_index]) for row in dataset.rows if row[curve_index] is not None]
        value_min = min(valid) if valid else 0.0
        value_max = max(valid) if valid else 1.0
        if value_min == value_max:
            padding = max(abs(value_min) * 0.05, 0.5)
            value_min -= padding
            value_max += padding
        x0 = left + track * track_width
        x1 = x0 + track_width
        color = _COLORS[track % len(_COLORS)]
        elements.extend(
            [
                f'<rect x="{x0}" y="{top}" width="{track_width}" height="{plot_height}" '
                'fill="none" stroke="#9ca3af" stroke-width="1"/>',
                f'<text x="{(x0 + x1) / 2:.2f}" y="{top - 30}" text-anchor="middle" '
                f'font-family="sans-serif" font-size="12" font-weight="600" fill="{color}">'
                f"{html.escape(curve.mnemonic)}</text>",
                f'<text x="{(x0 + x1) / 2:.2f}" y="{top - 14}" text-anchor="middle" '
                f'font-family="sans-serif" font-size="10" fill="#4b5563">'
                f"{html.escape(canonical_unit or 'unitless')}</text>",
                f'<text x="{x0 + 4}" y="{top + plot_height + 18}" font-family="monospace" '
                f'font-size="9">{value_min:.5g}</text>',
                f'<text x="{x1 - 4}" y="{top + plot_height + 18}" text-anchor="end" '
                f'font-family="monospace" font-size="9">{value_max:.5g}</text>',
            ]
        )
        for points in _segments(
            dataset,
            curve_index,
            x0,
            x1,
            top,
            plot_height,
            depth_min,
            depth_max,
            value_min,
            value_max,
        ):
            elements.append(
                f'<polyline points="{points}" fill="none" stroke="{color}" '
                'stroke-width="1.5" stroke-linejoin="round"/>'
            )
        axis_id = f"track-{track + 1}"
        axes.append(
            FigureAxis(
                axis_id,
                curve.mnemonic,
                canonical_unit,
                value_min,
                value_max,
            )
        )
        series.append(
            {
                "series_id": f"curve-{track + 1}",
                "curve_index": curve_index,
                "mnemonic": curve.mnemonic,
                "family": family,
                "source_unit": curve.unit,
                "display_unit": canonical_unit,
                "axis_id": axis_id,
                "valid_count": len(valid),
                "missing_count": len(dataset.rows) - len(valid),
                "color": color,
            }
        )
    elements.append(
        f'<text x="20" y="{top + plot_height / 2:.2f}" '
        'transform="rotate(-90 20 '
        f'{top + plot_height / 2:.2f})" text-anchor="middle" font-family="sans-serif" '
        f'font-size="11">{html.escape(dataset.curves[0].mnemonic)} '
        f"({html.escape(axes[0].unit or 'unitless')})</text>"
    )
    elements.append("</svg>")
    metadata = FigureArtifactMetadata(
        figure_id="las-well-log",
        figure_type="well-log-tracks",
        title="LAS Well Log — deterministic QC view",
        tool_name=PLOT_TOOL_NAME,
        tool_version=PLOT_TOOL_VERSION,
        source_result_ids=("las.inspect", "las.curve-statistics", "las.quality-rules"),
        evidence_references=(
            "las-analysis#/tool_results/0",
            "las-analysis#/tool_results/1",
            "las-analysis#/tool_results/2",
        ),
        axes=tuple(axes),
        series=tuple(series),
    )
    return "\n".join(elements) + "\n", metadata


def _segments(
    dataset: LasDataset,
    curve_index: int,
    x0: float,
    x1: float,
    top: float,
    plot_height: float,
    depth_min: float,
    depth_max: float,
    value_min: float,
    value_max: float,
) -> list[str]:
    result: list[str] = []
    current: list[str] = []
    for row in dataset.rows:
        depth, value = row[0], row[curve_index]
        if depth is None or value is None:
            if current:
                result.append(" ".join(current))
                current = []
            continue
        x = x0 + (float(value) - value_min) / (value_max - value_min) * (x1 - x0)
        y = top + (float(depth) - depth_min) / (depth_max - depth_min) * plot_height
        current.append(f"{x:.2f},{y:.2f}")
    if current:
        result.append(" ".join(current))
    return result

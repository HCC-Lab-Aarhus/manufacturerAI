from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import FLOOR_MM
from ._common import DEBUG_CONFIG, load_slicer_params, run_debug_pipeline

router = APIRouter()


def _calibration_scad_and_traces(
    box: float,
    pad: float,
    sq: float,
    z_height: float,
) -> tuple[str, list[list[tuple[float, float]]], tuple[float, float]]:
    """Build SCAD + trace paths for the calibration pattern.

    Three squares in corners of a ``box``-sized area (top-right
    intentionally omitted for orientation).  Everything is in
    model-local coordinates with origin at (0, 0).
    """
    corners = [
        (pad, pad),
        (box - pad - sq, pad),
        (pad, box - pad - sq),
    ]

    lines = [
        "$fn = 32;",
        "union() {",
    ]
    for cx, cy in corners:
        lines.append(f"  translate([{cx:.3f}, {cy:.3f}, 0])")
        lines.append(f"    cube([{sq:.3f}, {sq:.3f}, {z_height:.3f}]);")
    lines.append("}")
    scad_src = "\n".join(lines)

    trace_paths: list[list[tuple[float, float]]] = []
    for cx, cy in corners:
        mid_x = cx + sq / 2
        mid_y = cy + sq / 2
        trace_paths.append([
            (cx, mid_y), (cx + sq, mid_y),
        ])
        trace_paths.append([
            (mid_x, cy), (mid_x, cy + sq),
        ])

    model_center = (box / 2, box / 2)
    return scad_src, trace_paths, model_center


@router.post("/calibrate")
async def generate_calibration(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
) -> dict[str, Any]:
    cfg = DEBUG_CONFIG
    sp = load_slicer_params(printer)
    z_height = max(sp.layer_height * cfg.layers, FLOOR_MM + sp.layer_height)

    scad_src, trace_paths, model_center = _calibration_scad_and_traces(
        cfg.cal_box_size, cfg.padding, cfg.cal_square_size, z_height,
    )

    return run_debug_pipeline(
        scad_src, trace_paths, model_center,
        printer, filament,
        shell_height=z_height,
    )

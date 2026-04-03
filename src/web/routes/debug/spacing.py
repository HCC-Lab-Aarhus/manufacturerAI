from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import get_printer, bed_bitmap, FLOOR_MM
from ._common import DEBUG_CONFIG, load_slicer_params, run_debug_pipeline

router = APIRouter()

SPACING_TRACE_W_PX: int = 10
SPACING_MAX_GAP: int = 30
SPACING_EDGE_PAD_PX: int = 5


def spacing_plate_width_px(
    min_gap: int = 1,
    max_gap: int = SPACING_MAX_GAP,
    trace_w_px: int = SPACING_TRACE_W_PX,
) -> int:
    n_traces = (max_gap - min_gap + 1) + 1
    total_gaps = sum(range(min_gap, max_gap + 1))
    return n_traces * trace_w_px + total_gaps + 2 * SPACING_EDGE_PAD_PX


def _spacing_trace_paths(
    plate_w: float,
    plate_h: float,
    px_mm: float,
    min_gap: int = 1,
    max_gap: int = SPACING_MAX_GAP,
    trace_w_px: int = SPACING_TRACE_W_PX,
) -> list[list[tuple[float, float]]]:
    """Build trace paths for vertical lines with incrementing gaps."""
    paths: list[list[tuple[float, float]]] = []
    x_pos_px = SPACING_EDGE_PAD_PX
    for gap_size in range(min_gap, max_gap + 1):
        center_px = x_pos_px + trace_w_px / 2
        x_mm = center_px * px_mm
        paths.append([(x_mm, 0.0), (x_mm, plate_h)])
        x_pos_px += trace_w_px + gap_size

    center_px = x_pos_px + trace_w_px / 2
    x_mm = center_px * px_mm
    paths.append([(x_mm, 0.0), (x_mm, plate_h)])

    return paths


def _spacing_scad(plate_w: float, plate_h: float, z_height: float) -> str:
    return (
        "$fn = 32;\n"
        f"cube([{plate_w:.3f}, {plate_h:.3f}, {z_height:.3f}]);\n"
    )


@router.post("/spacing")
async def generate_spacing(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
) -> dict[str, Any]:
    pdef = get_printer(printer)
    grid = bed_bitmap(pdef)
    sp = load_slicer_params(printer)

    px = grid.pixel_size_mm
    plate_w = spacing_plate_width_px() * px
    plate_h = DEBUG_CONFIG.landscape_height
    z_height = max(sp.layer_height * DEBUG_CONFIG.layers, FLOOR_MM + sp.layer_height)

    scad_src = _spacing_scad(plate_w, plate_h, z_height)
    trace_paths = _spacing_trace_paths(plate_w, plate_h, px)
    model_center = (plate_w / 2, plate_h / 2)

    return run_debug_pipeline(
        scad_src, trace_paths, model_center,
        printer, filament,
        shell_height=z_height,
    )

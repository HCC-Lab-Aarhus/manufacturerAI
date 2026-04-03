from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import get_printer, bed_bitmap, FLOOR_MM
from ._common import DEBUG_CONFIG, load_slicer_params, run_debug_pipeline

router = APIRouter()

WIDTH_WIDTHS: list[int] = list(range(1, 31))
WIDTH_GAP_PX: int = 20
WIDTH_EDGE_PAD_PX: int = 5


def width_plate_width_px(
    widths: list[int] | None = None,
    gap_px: int = WIDTH_GAP_PX,
) -> int:
    if widths is None:
        widths = WIDTH_WIDTHS
    return sum(widths) + (len(widths) - 1) * gap_px + 2 * WIDTH_EDGE_PAD_PX


def _width_trace_paths(
    plate_w: float,
    plate_h: float,
    px_mm: float,
    widths: list[int] | None = None,
    gap_px: int = WIDTH_GAP_PX,
) -> list[list[tuple[float, float]]]:
    """Build trace paths for vertical lines of increasing width.

    Each trace is a single vertical line segment in model-local coords.
    """
    if widths is None:
        widths = WIDTH_WIDTHS

    paths: list[list[tuple[float, float]]] = []
    x_pos_px = WIDTH_EDGE_PAD_PX
    for w in widths:
        center_px = x_pos_px + w / 2
        x_mm = center_px * px_mm
        paths.append([(x_mm, 0.0), (x_mm, plate_h)])
        x_pos_px += w + gap_px
    return paths


def _width_scad(plate_w: float, plate_h: float, z_height: float) -> str:
    return (
        "$fn = 32;\n"
        f"cube([{plate_w:.3f}, {plate_h:.3f}, {z_height:.3f}]);\n"
    )


@router.post("/width")
async def generate_width(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
) -> dict[str, Any]:
    pdef = get_printer(printer)
    grid = bed_bitmap(pdef)
    sp = load_slicer_params(printer)

    px = grid.pixel_size_mm
    plate_w = width_plate_width_px() * px
    plate_h = DEBUG_CONFIG.landscape_height
    z_height = max(sp.layer_height * DEBUG_CONFIG.layers, FLOOR_MM + sp.layer_height)

    scad_src = _width_scad(plate_w, plate_h, z_height)
    trace_paths = _width_trace_paths(plate_w, plate_h, px)
    model_center = (plate_w / 2, plate_h / 2)

    return run_debug_pipeline(
        scad_src, trace_paths, model_center,
        printer, filament,
        shell_height=z_height,
    )

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import get_printer, SweepGrid, sweep_grid
from src.pipeline.gcode.filaments import get_filament
from src.pipeline.manifest import generate_manifest

from ._common import DEBUG_CONFIG, load_slicer_params, render_bitmap, slice_debug_boxes

router = APIRouter()

WIDTH_WIDTHS: list[int] = list(range(1, 31))
WIDTH_GAP_PX: int = 20
WIDTH_EDGE_PAD_PX: int = 5


def width_box_ink_cells(
    grid: SweepGrid,
    bed_x: float,
    bed_y: float,
    box_w: float,
    box_h: float,
    trace_w_px: int,
) -> set[tuple[int, int]]:
    """Ink cells for a single centered vertical trace in one rectangle."""
    px = grid.pixel_size_mm

    bx0_bm, by0_bm = grid.bed_to_bitmap(bed_x, bed_y)
    bx1_bm, by1_bm = grid.bed_to_bitmap(bed_x + box_w, bed_y + box_h)

    r0 = max(0, int(math.floor(by0_bm / px)))
    r1 = min(grid.data_rows - 1, int(math.floor(by1_bm / px)))
    c_center = int(math.floor((bx0_bm + bx1_bm) / (2 * px)))
    half_w = trace_w_px // 2

    cells: set[tuple[int, int]] = set()
    for dc in range(-half_w, half_w + trace_w_px % 2):
        c = c_center + dc
        if 0 <= c < grid.data_cols:
            for r in range(r0, r1 + 1):
                cells.add((r, c))
    return cells


def width_all_ink_cells(
    grid: SweepGrid,
    bed_x: float,
    bed_y: float,
    box_w: float,
    box_h: float,
    widths: list[int] | None = None,
    gap_px: int = WIDTH_GAP_PX,
) -> set[tuple[int, int]]:
    """Ink cells for all width traces drawn left-to-right in one rectangle."""
    if widths is None:
        widths = WIDTH_WIDTHS
    px = grid.pixel_size_mm

    bx0_bm, by0_bm = grid.bed_to_bitmap(bed_x, bed_y)
    _, by1_bm = grid.bed_to_bitmap(bed_x, bed_y + box_h)

    r0 = max(0, int(math.floor(by0_bm / px)))
    r1 = min(grid.data_rows - 1, int(math.floor(by1_bm / px)))
    c_start = max(0, int(math.floor(bx0_bm / px))) + WIDTH_EDGE_PAD_PX

    cells: set[tuple[int, int]] = set()
    c_pos = c_start
    for w in widths:
        for dc in range(w):
            c = c_pos + dc
            if 0 <= c < grid.data_cols:
                for r in range(r0, r1 + 1):
                    cells.add((r, c))
        c_pos += w + gap_px
    return cells


def width_plate_width_px(
    widths: list[int] | None = None,
    gap_px: int = WIDTH_GAP_PX,
) -> int:
    if widths is None:
        widths = WIDTH_WIDTHS
    return sum(widths) + (len(widths) - 1) * gap_px + 2 * WIDTH_EDGE_PAD_PX


@router.post("/width")
async def generate_width(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
) -> dict[str, Any]:
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)
    sp = load_slicer_params(printer)

    px = grid.pixel_size_mm
    plate_w_mm = width_plate_width_px() * px
    plate_h_mm = DEBUG_CONFIG.landscape_height
    z_height = sp.layer_height * DEBUG_CONFIG.layers

    nom_w = pdef.nominal_bed_width
    x_left = (nom_w - plate_w_mm) / 2
    y_bottom = abs(pdef.inkjet_offset_y) + DEBUG_CONFIG.padding

    boxes = [(x_left, y_bottom, plate_w_mm, plate_h_mm, z_height)]
    gcode = slice_debug_boxes(pdef, fdef, boxes, printer_id=printer)
    bitmap = render_bitmap(
        grid.data_rows, grid.data_cols,
        width_all_ink_cells(grid, x_left, y_bottom, plate_w_mm, plate_h_mm),
    )

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=x_left,
        part_origin_y_mm=y_bottom,
        part_width_mm=plate_w_mm,
        part_depth_mm=plate_h_mm,
        gcode_file="width.gcode",
        bitmap_file="width.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        "bitmap": bitmap,
        "contract": manifest.to_dict(),
    }

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import (
    get_printer, SweepGrid, sweep_grid,
)
from src.pipeline.gcode.filaments import get_filament
from src.pipeline.manifest import generate_manifest

from ._common import DEBUG_CONFIG, load_slicer_params, render_bitmap, slice_debug_boxes

router = APIRouter()

SPACING_TRACE_W_PX: int = 10
SPACING_MAX_GAP: int = 30
SPACING_EDGE_PAD_PX: int = 5


def spacing_box_ink_cells(
    grid: SweepGrid,
    bed_x: float,
    bed_y: float,
    box_w: float,
    box_h: float,
    min_gap: int = 1,
    max_gap: int = SPACING_MAX_GAP,
    trace_w_px: int = SPACING_TRACE_W_PX,
) -> set[tuple[int, int]]:
    """Ink cells for vertical lines with incrementing gaps in one rectangle.

    Draws: line, min_gap px gap, line, (min_gap+1) px gap, ... up to max_gap px,
    then a final closing line.
    """
    px = grid.pixel_size_mm

    bx0_bm, by0_bm = grid.bed_to_bitmap(bed_x, bed_y)
    bx1_bm, by1_bm = grid.bed_to_bitmap(bed_x + box_w, bed_y + box_h)

    r0 = max(0, int(math.floor(by0_bm / px)))
    r1 = min(grid.data_rows - 1, int(math.floor(by1_bm / px)))
    c_start = max(0, int(math.floor(bx0_bm / px))) + SPACING_EDGE_PAD_PX

    cells: set[tuple[int, int]] = set()
    c_pos = c_start
    for gap_size in range(min_gap, max_gap + 1):
        for dc in range(trace_w_px):
            c = c_pos + dc
            if 0 <= c < grid.data_cols:
                for r in range(r0, r1 + 1):
                    cells.add((r, c))
        c_pos += trace_w_px + gap_size

    for dc in range(trace_w_px):
        c = c_pos + dc
        if 0 <= c < grid.data_cols:
            for r in range(r0, r1 + 1):
                cells.add((r, c))

    return cells


def spacing_plate_width_px(
    min_gap: int = 1,
    max_gap: int = SPACING_MAX_GAP,
    trace_w_px: int = SPACING_TRACE_W_PX,
) -> int:
    n_traces = (max_gap - min_gap + 1) + 1
    total_gaps = sum(range(min_gap, max_gap + 1))
    return n_traces * trace_w_px + total_gaps + 2 * SPACING_EDGE_PAD_PX


@router.post("/spacing")
async def generate_spacing(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
) -> dict[str, Any]:
    """Generate G-code + bitmap for the spacing test."""
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)
    sp = load_slicer_params(printer)

    px = grid.pixel_size_mm
    plate_w_mm = spacing_plate_width_px() * px
    plate_h_mm = DEBUG_CONFIG.landscape_height
    z_height = sp.layer_height * DEBUG_CONFIG.layers

    nom_w = pdef.nominal_bed_width
    x_base = (nom_w - plate_w_mm) / 2
    y_bottom = abs(pdef.inkjet_offset_y) + DEBUG_CONFIG.padding

    boxes = [(x_base, y_bottom, plate_w_mm, plate_h_mm, z_height)]
    gcode = slice_debug_boxes(pdef, fdef, boxes, printer_id=printer)
    bitmap = render_bitmap(
        grid.data_rows, grid.data_cols,
        spacing_box_ink_cells(grid, x_base, y_bottom, plate_w_mm, plate_h_mm),
    )

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=x_base,
        part_origin_y_mm=y_bottom,
        part_width_mm=plate_w_mm,
        part_depth_mm=plate_h_mm,
        gcode_file="spacing.gcode",
        bitmap_file="spacing.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        "bitmap": bitmap,
        "contract": manifest.to_dict(),
    }

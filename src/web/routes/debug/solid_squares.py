from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import (
    get_printer, PrinterDef, SweepGrid, sweep_grid,
)
from src.pipeline.gcode.filaments import get_filament
from src.pipeline.manifest import generate_manifest

from ._common import DEBUG_CONFIG, load_slicer_params, slice_debug_boxes

router = APIRouter()


def _solid_squares_bitmap(
    pdef: PrinterDef,
    grid: SweepGrid,
    pad: float,
    rect_w: float,
    rect_h: float,
) -> str:
    """Generate a bitmap with the full surface of each rectangle filled.

    Nine rectangles in a 3×3 grid.  Each is fully covered with
    ink ('1') — the entire footprint is projected onto the bitmap.
    """
    px = grid.pixel_size_mm
    cols = grid.data_cols
    rows = grid.data_rows

    nom_d = pdef.nominal_bed_depth

    gap = pad
    total_w = 3 * rect_w + 2 * gap
    total_h = 3 * rect_h + 2 * gap
    x_left = abs(pdef.inkjet_offset_x) + pad
    y_base = (nom_d - total_h) / 2

    corners_bed = [
        (x_left + col * (rect_w + gap), y_base + row * (rect_h + gap))
        for row in range(3) for col in range(3)
    ]

    ink_cells: set[tuple[int, int]] = set()

    for bed_x, bed_y in corners_bed:
        bx0, by0 = grid.bed_to_bitmap(bed_x, bed_y)
        bx1, by1 = grid.bed_to_bitmap(bed_x + rect_w, bed_y + rect_h)

        c0 = max(0, int(math.floor(bx0 / px)))
        c1 = min(cols - 1, int(math.floor(bx1 / px)))
        r0 = max(0, int(math.floor(by0 / px)))
        r1 = min(rows - 1, int(math.floor(by1 / px)))

        for c in range(c0, c1 + 1):
            for r in range(r0, r1 + 1):
                ink_cells.add((r, c))

    result: list[str] = []
    for r in range(rows - 1, -1, -1):
        line_chars = []
        for c in range(cols):
            line_chars.append('1' if (r, c) in ink_cells else '0')
        result.append(''.join(line_chars))

    return "\n".join(result)


@router.post("/solid-squares")
async def generate_solid_squares(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
) -> dict[str, Any]:
    """Generate G-code + bitmap for filled-trace square coverage test."""
    cfg = DEBUG_CONFIG
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)

    sp = load_slicer_params(printer)
    rect_w, rect_h = cfg.portrait_width, cfg.portrait_height
    gap = cfg.padding
    total_w = 3 * rect_w + 2 * gap
    total_h = 3 * rect_h + 2 * gap
    x_left = abs(pdef.inkjet_offset_x) + cfg.padding
    y_base = (pdef.nominal_bed_depth - total_h) / 2
    z_height = sp.layer_height * cfg.layers

    corners = [
        (x_left + col * (rect_w + gap), y_base + row * (rect_h + gap))
        for row in range(3) for col in range(3)
    ]
    boxes = [(x, y, rect_w, rect_h, z_height) for x, y in corners]
    gcode = slice_debug_boxes(pdef, fdef, boxes, printer_id=printer)

    bitmap = _solid_squares_bitmap(pdef, grid, cfg.padding, rect_w, rect_h)

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=x_left,
        part_origin_y_mm=y_base,
        part_width_mm=total_w,
        part_depth_mm=total_h,
        gcode_file="solid_squares.gcode",
        bitmap_file="solid_squares.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        "bitmap": bitmap,
        "contract": manifest.to_dict(),
    }

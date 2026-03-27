from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import (
    get_printer, PrinterDef, SweepGrid, sweep_grid, TRACE_RULES,
)
from src.pipeline.gcode.filaments import get_filament
from src.pipeline.manifest import generate_manifest

from ._common import DEBUG_CONFIG, load_slicer_params, slice_debug_boxes

router = APIRouter()


def _silverink_test_bitmap(
    pdef: PrinterDef,
    grid: SweepGrid,
    pad: float,
    rect_w: float,
    rect_h: float,
) -> str:
    """Generate a bitmap with a single trace through each rectangle's centre.

    Three rectangles stacked vertically on the left wall.  Each gets
    a thin vertical trace (Y direction) running its full height,
    centred in X.
    """
    px = grid.pixel_size_mm
    cols = grid.data_cols
    rows = grid.data_rows

    trace_width_nozzles = max(1, int(round(TRACE_RULES.trace_width_mm / px)))

    nom_d = pdef.nominal_bed_depth

    gap = pad
    total_h = 3 * rect_h + 2 * gap
    y_base = (nom_d - total_h) / 2
    x_left = abs(pdef.inkjet_offset_x) + pad

    corners_bed = [
        (x_left, y_base),
        (x_left, y_base + rect_h + gap),
        (x_left, y_base + 2 * (rect_h + gap)),
    ]

    ink_cells: set[tuple[int, int]] = set()
    half_trace = trace_width_nozzles // 2

    for bed_x, bed_y in corners_bed:
        center_x = bed_x + rect_w / 2
        bx_center, by0 = grid.bed_to_bitmap(center_x, bed_y)
        _, by1 = grid.bed_to_bitmap(center_x, bed_y + rect_h)

        c_center = int(round(bx_center / px))
        r0 = max(0, int(math.floor(by0 / px)))
        r1 = min(rows - 1, int(math.floor(by1 / px)))

        for dc in range(-half_trace, half_trace + 1):
            c = c_center + dc
            if 0 <= c < cols:
                for r in range(r0, r1 + 1):
                    ink_cells.add((r, c))

    result: list[str] = []
    for r in range(rows - 1, -1, -1):
        line_chars = []
        for c in range(cols):
            line_chars.append('1' if (r, c) in ink_cells else '0')
        result.append(''.join(line_chars))

    return "\n".join(result)


@router.post("/silverink-test")
async def generate_silverink_test(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
) -> dict[str, Any]:
    """Generate G-code + bitmap for a silverink adhesion/conductivity test."""
    cfg = DEBUG_CONFIG
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)

    sp = load_slicer_params(printer)
    gap = cfg.padding
    rect_w, rect_h = cfg.portrait_width, cfg.portrait_height
    total_h = 3 * rect_h + 2 * gap
    y_base = (pdef.nominal_bed_depth - total_h) / 2
    x_left = abs(pdef.inkjet_offset_x) + cfg.padding
    z_height = sp.layer_height * cfg.layers

    corners = [
        (x_left, y_base),
        (x_left, y_base + rect_h + gap),
        (x_left, y_base + 2 * (rect_h + gap)),
    ]
    boxes = [(x, y, rect_w, rect_h, z_height) for x, y in corners]
    gcode = slice_debug_boxes(pdef, fdef, boxes, printer_id=printer)

    bitmap = _silverink_test_bitmap(pdef, grid, cfg.padding, rect_w, rect_h)

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=x_left,
        part_origin_y_mm=y_base,
        part_width_mm=rect_w,
        part_depth_mm=total_h,
        gcode_file="silverink_test.gcode",
        bitmap_file="silverink_test_trace.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        "bitmap": bitmap,
        "contract": manifest.to_dict(),
    }

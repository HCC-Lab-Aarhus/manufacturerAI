from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import get_printer, PrinterDef, SweepGrid, sweep_grid
from src.pipeline.gcode.filaments import get_filament
from src.pipeline.manifest import generate_manifest

from ._common import DEBUG_CONFIG, load_slicer_params, slice_debug_boxes

router = APIRouter()


def _trace_width_bitmap(
    pdef: PrinterDef,
    grid: SweepGrid,
    pad: float,
    rect_w: float,
    rect_h: float,
) -> str:
    px = grid.pixel_size_mm
    cols = grid.data_cols
    rows = grid.data_rows
    gap_px = 10

    x_left = abs(pdef.inkjet_offset_x) + pad
    y_bottom = abs(pdef.inkjet_offset_y) + pad

    bx0, by0 = grid.bed_to_bitmap(x_left, y_bottom)
    _, by1 = grid.bed_to_bitmap(x_left, y_bottom + rect_h)

    r0 = max(0, int(math.floor(by0 / px)))
    r1 = min(rows - 1, int(math.floor(by1 / px)))

    c_start = max(0, int(math.floor(bx0 / px)))

    ink_cells: set[tuple[int, int]] = set()

    c_pos = c_start
    for width in [5, 10, 15, 20, 25, 30]:
        if c_pos >= cols:
            break
        for dc in range(width):
            c = c_pos + dc
            if 0 <= c < cols:
                for r in range(r0, r1 + 1):
                    ink_cells.add((r, c))
        c_pos += width + gap_px

    result: list[str] = []
    for r in range(rows - 1, -1, -1):
        line_chars = []
        for c in range(cols):
            line_chars.append('1' if (r, c) in ink_cells else '0')
        result.append(''.join(line_chars))

    return "\n".join(result)


@router.post("/width")
async def generate_width(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
) -> dict[str, Any]:
    cfg = DEBUG_CONFIG
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)

    sp = load_slicer_params(printer)
    rect_w, rect_h = cfg.landscape_width, cfg.landscape_height
    x_left = abs(pdef.inkjet_offset_x) + cfg.padding
    y_bottom = abs(pdef.inkjet_offset_y) + cfg.padding
    z_height = sp.layer_height * cfg.layers

    boxes = [(x_left, y_bottom, rect_w, rect_h, z_height)]
    gcode = slice_debug_boxes(pdef, fdef, boxes, printer_id=printer)

    bitmap = _trace_width_bitmap(pdef, grid, cfg.padding, rect_w, rect_h)

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=x_left,
        part_origin_y_mm=y_bottom,
        part_width_mm=rect_w,
        part_depth_mm=rect_h,
        gcode_file="width.gcode",
        bitmap_file="width.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        "bitmap": bitmap,
        "contract": manifest.to_dict(),
    }

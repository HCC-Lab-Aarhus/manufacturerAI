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


def _progressive_trace_bitmap(
    pdef: PrinterDef,
    grid: SweepGrid,
    pad: float,
    rect_w: float,
    rect_h: float,
    trace_count: int,
) -> str:
    """Bitmap with a centre trace on the first *trace_count* rectangles."""
    px = grid.pixel_size_mm
    cols = grid.data_cols
    rows = grid.data_rows
    trace_width_nozzles = max(1, int(round(TRACE_RULES.trace_width_mm / px)))
    half_trace = trace_width_nozzles // 2

    reps = 3
    nom_d = pdef.nominal_bed_depth
    gap = pad
    total_h = 3 * rect_h + 2 * gap
    y_base = (nom_d - total_h) / 2
    x_left = abs(pdef.inkjet_offset_x) + pad

    corners_bed: list[tuple[float, float, int]] = []
    for rep in range(reps):
        x_off = x_left + rep * (rect_w + gap)
        for row in range(3):
            corners_bed.append((x_off, y_base + row * (rect_h + gap), row))

    ink_cells: set[tuple[int, int]] = set()

    for bed_x, bed_y, row in corners_bed:
        if row >= trace_count:
            continue
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


@router.post("/layers")
async def generate_layers(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
) -> dict[str, Any]:
    """Generate G-code + 3 bitmaps for the layers test."""
    cfg = DEBUG_CONFIG
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)

    sp = load_slicer_params(printer)
    rect_w, rect_h = cfg.portrait_width, cfg.portrait_height
    reps = 3
    gap = cfg.padding
    total_h = 3 * rect_h + 2 * gap
    total_w = reps * rect_w + (reps - 1) * gap
    x_left = abs(pdef.inkjet_offset_x) + cfg.padding
    y_base = (pdef.nominal_bed_depth - total_h) / 2
    z_height = sp.layer_height * cfg.layers

    corners: list[tuple[float, float]] = []
    for rep in range(reps):
        x_off = x_left + rep * (rect_w + gap)
        for row in range(3):
            corners.append((x_off, y_base + row * (rect_h + gap)))
    boxes = [(x, y, rect_w, rect_h, z_height) for x, y in corners]
    gcode = slice_debug_boxes(pdef, fdef, boxes, printer_id=printer)

    bitmaps = {}
    for n in (1, 2, 3):
        bitmaps[f"bitmap_{n}"] = _progressive_trace_bitmap(
            pdef, grid, cfg.padding, rect_w, rect_h, n)

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=x_left,
        part_origin_y_mm=y_base,
        part_width_mm=total_w,
        part_depth_mm=total_h,
        gcode_file="layers.gcode",
        bitmap_file="layers_1.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        **bitmaps,
        "contract": manifest.to_dict(),
    }

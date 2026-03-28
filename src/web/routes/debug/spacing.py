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


def _parallel_lines_bitmap(
    pdef: PrinterDef,
    grid: SweepGrid,
    pad: float,
    rect_w: float,
    rect_h: float,
    max_gap: int = 10,
) -> str:
    """Bitmap with parallel vertical lines at increasing pixel spacing.

    Each rectangle gets the same pattern: line, 1px gap, line, 2px gap,
    line, 3px gap, ... up to max_gap px.  Lines use trace_width from
    TRACE_RULES, gaps are edge-to-edge.
    """
    px = grid.pixel_size_mm
    cols = grid.data_cols
    rows = grid.data_rows
    trace_w = max(1, int(round(TRACE_RULES.trace_width_mm / px)))

    nom_w = pdef.nominal_bed_width

    gap_pad = pad
    total_w = 3 * rect_w + 2 * gap_pad
    x_base = (nom_w - total_w) / 2
    y_bottom = abs(pdef.inkjet_offset_y) + pad

    corners_bed = [
        (x_base, y_bottom),
        (x_base + rect_w + gap_pad, y_bottom),
        (x_base + 2 * (rect_w + gap_pad), y_bottom),
    ]

    ink_cells: set[tuple[int, int]] = set()

    for bed_x, bed_y in corners_bed:
        bx0, by0 = grid.bed_to_bitmap(bed_x, bed_y)
        _, by1 = grid.bed_to_bitmap(bed_x, bed_y + rect_h)

        r0 = max(0, int(math.floor(by0 / px)))
        r1 = min(rows - 1, int(math.floor(by1 / px)))

        c_start = max(0, int(math.floor(bx0 / px)))
        c_pos = c_start

        for gap_size in range(1, max_gap + 1):
            if c_pos >= cols:
                break
            for dc in range(trace_w):
                c = c_pos + dc
                if 0 <= c < cols:
                    for r in range(r0, r1 + 1):
                        ink_cells.add((r, c))
            c_pos += trace_w + gap_size

        if c_pos < cols:
            for dc in range(trace_w):
                c = c_pos + dc
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


@router.post("/spacing")
async def generate_spacing(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
) -> dict[str, Any]:
    """Generate G-code + bitmap for the spacing test."""
    cfg = DEBUG_CONFIG
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)

    sp = load_slicer_params(printer)
    rect_w, rect_h = cfg.landscape_width, cfg.landscape_height
    gap = cfg.padding
    total_w = 3 * rect_w + 2 * gap
    x_base = (pdef.nominal_bed_width - total_w) / 2
    y_bottom = abs(pdef.inkjet_offset_y) + cfg.padding
    z_height = sp.layer_height * cfg.layers

    corners = [
        (x_base, y_bottom),
        (x_base + rect_w + gap, y_bottom),
        (x_base + 2 * (rect_w + gap), y_bottom),
    ]
    boxes = [(x, y, rect_w, rect_h, z_height) for x, y in corners]
    gcode = slice_debug_boxes(pdef, fdef, boxes, printer_id=printer)

    bitmap = _parallel_lines_bitmap(pdef, grid, cfg.padding, rect_w, rect_h)

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=x_base,
        part_origin_y_mm=y_bottom,
        part_width_mm=total_w,
        part_depth_mm=rect_h,
        gcode_file="spacing.gcode",
        bitmap_file="spacing.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        "bitmap": bitmap,
        "contract": manifest.to_dict(),
    }

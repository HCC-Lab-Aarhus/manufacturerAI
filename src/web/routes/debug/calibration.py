from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import get_printer, PrinterDef, SweepGrid, sweep_grid
from src.pipeline.gcode.filaments import get_filament
from src.pipeline.manifest import generate_manifest

from ._common import DEBUG_CONFIG, load_slicer_params, slice_debug_boxes

router = APIRouter()


def _calibration_bitmap(
    pdef: PrinterDef,
    grid: SweepGrid,
    box: float,
    pad: float,
    sq: float,
) -> str:
    """Generate a full sweep-grid bitmap with three filled squares.

    Top-right corner is omitted for orientation.

    The bitmap spans the entire sweep grid so that rasp_main.py's
    sliding-window slicing maps columns 1:1 to physical sweep lanes —
    exactly like the real pipeline's bitmap.py.

    Square positions are in absolute bed coordinates, converted to bitmap
    pixels via ``grid.bed_to_bitmap()``.
    """
    px = grid.pixel_size_mm
    cols = grid.data_cols
    rows = grid.data_rows

    nom_w = pdef.nominal_bed_width
    nom_d = pdef.nominal_bed_depth
    cx, cy = nom_w / 2, nom_d / 2
    half = box / 2

    corners_bed = [
        (cx - half + pad, cy - half + pad),
        (cx + half - pad - sq, cy - half + pad),
        (cx - half + pad, cy + half - pad - sq),
    ]

    ink_cells: set[tuple[int, int]] = set()
    for bed_x, bed_y in corners_bed:
        bx0, by0 = grid.bed_to_bitmap(bed_x, bed_y)
        c0 = max(0, int(math.floor(bx0 / px)))
        c1 = min(cols - 1, int(math.floor((bx0 + sq) / px)))
        r0 = max(0, int(math.floor(by0 / px)))
        r1 = min(rows - 1, int(math.floor((by0 + sq) / px)))
        for c in range(c0, c1 + 1):
            for r in range(r0, r1 + 1):
                ink_cells.add((r, c))

    lines: list[str] = []
    for r in range(rows - 1, -1, -1):
        line_chars = []
        for c in range(cols):
            line_chars.append('1' if (r, c) in ink_cells else '0')
        lines.append(''.join(line_chars))

    return "\n".join(lines)


@router.post("/calibrate")
async def generate_calibration(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
) -> dict[str, Any]:
    """Generate alignment G-code + bitmap for inkjet offset calibration."""
    cfg = DEBUG_CONFIG
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)

    sp = load_slicer_params(printer)
    cx, cy = pdef.nominal_bed_width / 2, pdef.nominal_bed_depth / 2
    half = cfg.cal_box_size / 2
    z_height = sp.first_layer_height

    corners = [
        (cx - half + cfg.padding, cy - half + cfg.padding),
        (cx + half - cfg.padding - cfg.cal_square_size, cy - half + cfg.padding),
        (cx - half + cfg.padding, cy + half - cfg.padding - cfg.cal_square_size),
    ]
    boxes = [(x, y, cfg.cal_square_size, cfg.cal_square_size, z_height) for x, y in corners]
    gcode = slice_debug_boxes(pdef, fdef, boxes, printer_id=printer)

    bitmap = _calibration_bitmap(pdef, grid, cfg.cal_box_size, cfg.padding, cfg.cal_square_size)

    part_origin_x = cx - half
    part_origin_y = cy - half

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=part_origin_x,
        part_origin_y_mm=part_origin_y,
        part_width_mm=cfg.cal_box_size,
        part_depth_mm=cfg.cal_box_size,
        gcode_file="calibration.gcode",
        bitmap_file="calibration_bitmap.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        "bitmap": bitmap,
        "contract": manifest.to_dict(),
    }

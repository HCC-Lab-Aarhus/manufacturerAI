from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import (
    get_printer, SweepGrid, sweep_grid,
)
from src.pipeline.manifest import generate_manifest

router = APIRouter()

N_PADS = 10
PAD_WIDTH_MM = 9.545
PAD_HEIGHT_MM = 9.4
PAD_PITCH_MM = 13.0
PAD_X_MIN_BED = 128.684
PAD_Y_FIRST_BED = 34.205

PAD_POSITIONS_BED: list[tuple[float, float, float, float]] = [
    (PAD_X_MIN_BED, PAD_Y_FIRST_BED + i * PAD_PITCH_MM,
     PAD_WIDTH_MM, PAD_HEIGHT_MM)
    for i in range(N_PADS)
]

STRIP_X_MIN = PAD_X_MIN_BED
STRIP_Y_MIN = PAD_Y_FIRST_BED
STRIP_WIDTH = PAD_WIDTH_MM
STRIP_DEPTH = (N_PADS - 1) * PAD_PITCH_MM + PAD_HEIGHT_MM


def _surface_test_bitmap(
    grid: SweepGrid,
) -> str:
    """Generate a bitmap covering all 10 pad surfaces of the test strip.

    Pad positions are taken from the actual PrusaSlicer GCode ironing
    coordinates for the surface test strip.
    """
    px = grid.pixel_size_mm
    cols = grid.data_cols
    rows = grid.data_rows

    ink_cells: set[tuple[int, int]] = set()

    for bed_x, bed_y, w, h in PAD_POSITIONS_BED:
        bx0, by0 = grid.bed_to_bitmap(bed_x, bed_y)
        bx1, by1 = grid.bed_to_bitmap(bed_x + w, bed_y + h)

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


@router.post("/surface-test")
async def generate_surface_test(
    printer: str = Query("mk3s"),
) -> dict[str, Any]:
    """Generate a bitmap for the surface conductivity test strip.

    The bitmap covers all 10 protruding pads, aligned to the exact
    positions from the PrusaSlicer GCode ironing passes.  The user
    supplies their own GCode with ``;silverink`` already injected.
    """
    pdef = get_printer(printer)
    grid = sweep_grid(pdef)

    bitmap = _surface_test_bitmap(grid)

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=STRIP_X_MIN,
        part_origin_y_mm=STRIP_Y_MIN,
        part_width_mm=STRIP_WIDTH,
        part_depth_mm=STRIP_DEPTH,
        bitmap_file="surface_test.txt",
        printer=pdef,
    )

    return {
        "bitmap": bitmap,
        "contract": manifest.to_dict(),
    }

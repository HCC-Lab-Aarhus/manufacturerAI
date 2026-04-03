from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import (
    get_printer, BedBitmap, bed_bitmap,
)
router = APIRouter()

N_PADS = 10
PAD_WIDTH_MM = 9.545
PAD_HEIGHT_MM = 9.4
PAD_PITCH_MM = 13.0
PAD_X_MIN_BED = 143.284
PAD_Y_FIRST_BED = 34.705

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
    grid: BedBitmap,
) -> str:
    """Generate a bitmap covering all 10 pad surfaces of the test strip."""
    cols = grid.cols
    rows = grid.rows

    ink_cells: set[tuple[int, int]] = set()

    for bed_x, bed_y, w, h in PAD_POSITIONS_BED:
        px0, py0 = grid.bed_to_pixel(bed_x, bed_y)
        px1, py1 = grid.bed_to_pixel(bed_x + w, bed_y + h)

        c0 = max(0, int(math.floor(px0)))
        c1 = min(cols - 1, int(math.floor(px1)))
        r0 = max(0, int(math.floor(py0)))
        r1 = min(rows - 1, int(math.floor(py1)))

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
    pdef = get_printer(printer)
    grid = bed_bitmap(pdef)

    bitmap = _surface_test_bitmap(grid)

    return {
        "bitmap": bitmap,
    }

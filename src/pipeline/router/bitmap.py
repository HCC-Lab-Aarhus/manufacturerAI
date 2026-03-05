"""Bitmap generation — renders routed traces to a fixed-resolution text bitmap.

The bitmap is a 1536×1383 text grid (one character per cell) that maps
directly onto the build plate.  A '1' means "deposit conductive ink here",
a '0' means "no ink".  The file is a plain .txt with one row per line
(top row = highest Y, so the bitmap is right-side-up when viewed in a
text editor).
"""

from __future__ import annotations

import math
from pathlib import Path

from src.pipeline.config import BUILD_PLATE, BuildPlate
from .models import RoutingResult


def _trace_cells(
    path: list[tuple[float, float]],
    trace_width_mm: float,
    plate: BuildPlate,
) -> set[tuple[int, int]]:
    """Rasterize a Manhattan trace path into bitmap cell coordinates.

    For each segment, every bitmap cell whose centre falls within
    half the trace width of the segment axis is marked.
    """
    cols, rows = plate.bitmap_cols, plate.bitmap_rows
    cell_w = plate.cell_width_mm
    cell_h = plate.cell_height_mm
    half_w = trace_width_mm / 2.0

    cells: set[tuple[int, int]] = set()

    for i in range(len(path) - 1):
        x0, y0 = path[i]
        x1, y1 = path[i + 1]

        if abs(x1 - x0) < 1e-9:
            # Vertical segment
            col_center = x0
            col_min = max(0, int(math.floor((col_center - half_w) / cell_w)))
            col_max = min(cols - 1, int(math.floor((col_center + half_w) / cell_w)))

            y_lo, y_hi = (min(y0, y1), max(y0, y1))
            row_min = max(0, int(math.floor(y_lo / cell_h)))
            row_max = min(rows - 1, int(math.floor(y_hi / cell_h)))

            for r in range(row_min, row_max + 1):
                for c in range(col_min, col_max + 1):
                    cells.add((r, c))
        else:
            # Horizontal segment
            row_center = y0
            row_min = max(0, int(math.floor((row_center - half_w) / cell_h)))
            row_max = min(rows - 1, int(math.floor((row_center + half_w) / cell_h)))

            x_lo, x_hi = (min(x0, x1), max(x0, x1))
            col_min = max(0, int(math.floor(x_lo / cell_w)))
            col_max = min(cols - 1, int(math.floor(x_hi / cell_w)))

            for r in range(row_min, row_max + 1):
                for c in range(col_min, col_max + 1):
                    cells.add((r, c))

    return cells


def generate_trace_bitmap(
    result: RoutingResult,
    trace_width_mm: float,
    *,
    plate: BuildPlate = BUILD_PLATE,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> list[str]:
    """Render all traces into a list of text rows (top row = max Y).

    Parameters
    ----------
    result : RoutingResult
        The completed routing result with trace paths in world mm.
    trace_width_mm : float
        Physical width of a conductive-ink trace.
    plate : BuildPlate
        Build plate dimensions and bitmap resolution.
    origin_x, origin_y : float
        World-space origin of the board outline's bounding-box lower-left
        corner.  Trace coordinates are shifted by this offset so the
        board maps onto the build plate starting at (0, 0).

    Returns
    -------
    list[str]
        One string per row, each exactly ``plate.bitmap_cols`` characters
        of '0' or '1'.  Index 0 is the top row (highest Y).
    """
    cols, rows = plate.bitmap_cols, plate.bitmap_rows
    ink_cells: set[tuple[int, int]] = set()

    for trace in result.traces:
        shifted_path = [
            (x - origin_x, y - origin_y) for x, y in trace.path
        ]
        ink_cells |= _trace_cells(shifted_path, trace_width_mm, plate)

    lines: list[str] = []
    for r in range(rows - 1, -1, -1):
        row_chars = []
        for c in range(cols):
            row_chars.append('1' if (r, c) in ink_cells else '0')
        lines.append(''.join(row_chars))

    return lines


def write_trace_bitmap(
    result: RoutingResult,
    trace_width_mm: float,
    output_path: Path | str,
    *,
    plate: BuildPlate = BUILD_PLATE,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> Path:
    """Generate the trace bitmap and write it to a text file.

    Returns the path written to.
    """
    output_path = Path(output_path)
    lines = generate_trace_bitmap(
        result, trace_width_mm,
        plate=plate,
        origin_x=origin_x,
        origin_y=origin_y,
    )
    output_path.write_text('\n'.join(lines), encoding='utf-8')
    return output_path

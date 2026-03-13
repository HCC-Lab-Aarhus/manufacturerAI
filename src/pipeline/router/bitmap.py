"""Bitmap generation — renders routed traces to a fixed-resolution text bitmap.

The bitmap is a text grid (one character per cell) that maps
directly onto the printer bed.  A '1' means "deposit conductive ink here",
a '0' means "no ink".  The file is a plain .txt with one row per line
(top row = highest Y, so the bitmap is right-side-up when viewed in a
text editor).
"""

from __future__ import annotations

import math
from pathlib import Path

from src.pipeline.config import BITMAP_CONFIG, BitmapConfig, PrinterDef, get_printer
from .models import RoutingResult


def _trace_cells(
    path: list[tuple[float, float]],
    trace_width_mm: float,
    bed_width: float,
    bed_depth: float,
    cols: int,
    rows: int,
) -> set[tuple[int, int]]:
    """Rasterize a Manhattan trace path into bitmap cell coordinates."""
    cell_w = bed_width / cols
    cell_h = bed_depth / rows
    half_w = trace_width_mm / 2.0

    cells: set[tuple[int, int]] = set()

    for i in range(len(path) - 1):
        x0, y0 = path[i]
        x1, y1 = path[i + 1]

        if abs(x1 - x0) < 1e-9:
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
    printer: PrinterDef | None = None,
    bitmap: BitmapConfig = BITMAP_CONFIG,
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
    printer : PrinterDef, optional
        Printer definition (bed dimensions).  Falls back to default.
    bitmap : BitmapConfig
        Bitmap resolution (cols × rows).
    origin_x, origin_y : float
        World-space origin of the board outline's bounding-box lower-left
        corner.  Trace coordinates are shifted by this offset so the
        board maps onto the printer bed starting at (0, 0).

    Returns
    -------
    list[str]
        One string per row, each exactly ``bitmap.cols`` characters
        of '0' or '1'.  Index 0 is the top row (highest Y).
    """
    pdef = printer or get_printer()
    cols = bitmap.cols
    rows = bitmap.rows_for_bed(pdef.bed_width, pdef.bed_depth)
    ink_cells: set[tuple[int, int]] = set()

    # Scale factor: enlarge traces by 5% around the bed centre
    # so the printed bitmap slightly oversizes for alignment margin.
    SCALE = 1.05
    bed_cx = pdef.bed_width / 2
    bed_cy = pdef.bed_depth / 2

    for trace in result.traces:
        shifted_path = [
            (x - origin_x, y - origin_y) for x, y in trace.path
        ]
        # Scale around bed centre
        scaled_path = [
            (bed_cx + (x - bed_cx) * SCALE,
             bed_cy + (y - bed_cy) * SCALE)
            for x, y in shifted_path
        ]
        ink_cells |= _trace_cells(
            scaled_path, trace_width_mm,
            pdef.bed_width, pdef.bed_depth,
            cols, rows,
        )

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
    printer: PrinterDef | None = None,
    bitmap: BitmapConfig = BITMAP_CONFIG,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> Path:
    """Generate the trace bitmap and write it to a text file."""
    output_path = Path(output_path)
    lines = generate_trace_bitmap(
        result, trace_width_mm,
        printer=printer,
        bitmap=bitmap,
        origin_x=origin_x,
        origin_y=origin_y,
    )
    output_path.write_text('\n'.join(lines), encoding='utf-8')
    return output_path

"""Bitmap generation — renders routed traces to a nozzle-native resolution bitmap.

The bitmap is a text grid (one character per cell) sized so each pixel
maps 1:1 to the printhead's nozzle pitch in both X and Y (square pixels).
A '1' means "deposit conductive ink here", a '0' means "no ink".

The bitmap covers only the part's bounding box, not the full bed.
Part placement on the bed is recorded in the print-job manifest so
the printer can position the sweeps correctly.
"""

from __future__ import annotations

import math
from pathlib import Path

from src.pipeline.config import (
    BITMAP_CONFIG, BITMAP_CALIBRATION, BitmapConfig,
    PrinterDef, PrintheadConfig, PRINTHEAD, get_printer,
)
from .models import RoutingResult


def _trace_cells(
    path: list[tuple[float, float]],
    trace_width_mm: float,
    pixel_size: float,
    cols: int,
    rows: int,
) -> set[tuple[int, int]]:
    """Rasterize a Manhattan trace path into bitmap cell coordinates.

    Coordinates are relative to the part origin (0, 0 = lower-left of
    the part bounding box).  Each cell is ``pixel_size`` mm square.
    """
    half_w = trace_width_mm / 2.0

    cells: set[tuple[int, int]] = set()

    for i in range(len(path) - 1):
        x0, y0 = path[i]
        x1, y1 = path[i + 1]

        if abs(x1 - x0) < 1e-9:
            col_center = x0
            col_min = max(0, int(math.floor((col_center - half_w) / pixel_size)))
            col_max = min(cols - 1, int(math.floor((col_center + half_w) / pixel_size)))

            y_lo, y_hi = (min(y0, y1), max(y0, y1))
            row_min = max(0, int(math.floor(y_lo / pixel_size)))
            row_max = min(rows - 1, int(math.floor(y_hi / pixel_size)))

            for r in range(row_min, row_max + 1):
                for c in range(col_min, col_max + 1):
                    cells.add((r, c))
        else:
            row_center = y0
            row_min = max(0, int(math.floor((row_center - half_w) / pixel_size)))
            row_max = min(rows - 1, int(math.floor((row_center + half_w) / pixel_size)))

            x_lo, x_hi = (min(x0, x1), max(x0, x1))
            col_min = max(0, int(math.floor(x_lo / pixel_size)))
            col_max = min(cols - 1, int(math.floor(x_hi / pixel_size)))

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
    printhead: PrintheadConfig = PRINTHEAD,
    part_width_mm: float | None = None,
    part_depth_mm: float | None = None,
) -> list[str]:
    """Render all traces into a list of text rows (top row = max Y).

    When ``part_width_mm`` and ``part_depth_mm`` are provided, the bitmap
    is dynamically sized at nozzle-native resolution (one pixel per
    nozzle pitch in both axes).  The ``bitmap`` parameter is ignored
    in that case.

    Parameters
    ----------
    result : RoutingResult
        The completed routing result with trace paths in world mm.
    trace_width_mm : float
        Physical width of a conductive-ink trace.
    printer : PrinterDef, optional
        Printer definition (bed dimensions).  Falls back to default.
    bitmap : BitmapConfig
        Bitmap resolution (cols × rows).  Used only when part dimensions
        are not provided (legacy mode).
    origin_x, origin_y : float
        World-space origin of the board outline's bounding-box lower-left
        corner.  Trace coordinates are shifted to part-local coords.
    printhead : PrintheadConfig
        Printhead hardware parameters (nozzle pitch, count).
    part_width_mm, part_depth_mm : float, optional
        Part bounding-box dimensions.  When given, bitmap is sized at
        nozzle-native resolution and calibration offsets are bypassed.

    Returns
    -------
    list[str]
        One string per row, each exactly ``cols`` characters of '0' or '1'.
        Index 0 is the top row (highest Y).
    """
    pdef = printer or get_printer()

    native_mode = part_width_mm is not None and part_depth_mm is not None

    if native_mode:
        pixel_size = printhead.pixel_size_mm
        cols, rows = printhead.bitmap_dims_for_part(part_width_mm, part_depth_mm)
        part_w = part_width_mm
        part_d = part_depth_mm
    else:
        cols = bitmap.cols
        rows = bitmap.rows_for_bed(pdef.bed_width, pdef.bed_depth)
        part_w = pdef.bed_width
        part_d = pdef.bed_depth
        pixel_size = part_w / cols

    ink_cells: set[tuple[int, int]] = set()

    cal = BITMAP_CALIBRATION

    for trace in result.traces:
        shifted_path = [
            (x - origin_x, y - origin_y) for x, y in trace.path
        ]
        if not native_mode:
            bed_cx = part_w / 2
            bed_cy = part_d / 2
            shifted_path = [
                (bed_cx + ((x - bed_cx) + cal.offset_x) * cal.scale_x,
                 bed_cy + ((y - bed_cy) + cal.offset_y) * cal.scale_y)
                for x, y in shifted_path
            ]
        ink_cells |= _trace_cells(
            shifted_path, trace_width_mm,
            pixel_size, cols, rows,
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
    printhead: PrintheadConfig = PRINTHEAD,
    part_width_mm: float | None = None,
    part_depth_mm: float | None = None,
) -> Path:
    """Generate the trace bitmap and write it to a text file."""
    output_path = Path(output_path)
    lines = generate_trace_bitmap(
        result, trace_width_mm,
        printer=printer,
        bitmap=bitmap,
        origin_x=origin_x,
        origin_y=origin_y,
        printhead=printhead,
        part_width_mm=part_width_mm,
        part_depth_mm=part_depth_mm,
    )
    output_path.write_text('\n'.join(lines), encoding='utf-8')
    return output_path

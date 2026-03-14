"""Bitmap generation — renders routed traces to a nozzle-native resolution bitmap.

The bitmap covers the full sweep grid of the silver3dprinter so that
the sliding-window slicing in ``rasp_main.py`` produces combined
slices aligned 1:1 with the physical sweep lanes.

Pixel size is exactly the nozzle pitch (square pixels).  One character
in the text file = one nozzle position on the X axis; one text line =
one firing position along the Y sweep.

  - text rows  → Y positions (sweep direction, high→low in file)
  - text cols  → X positions (nozzle direction, low→high)

A '1' means "deposit conductive ink here", a '0' means "no ink".
"""

from __future__ import annotations

import math
from pathlib import Path

from src.pipeline.config import (
    BITMAP_CONFIG, BITMAP_CALIBRATION, BitmapConfig,
    PrinterDef, PrintheadConfig, PRINTHEAD, get_printer,
    BITMAP_DATA_X_START_MM, BITMAP_DATA_COLS, BITMAP_DATA_ROWS,
    SWEEP_Y_START_MM,
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
    bed_offset: tuple[float, float] | None = None,
) -> list[str]:
    """Render all traces into a sweep-grid-aligned bitmap.

    The bitmap spans the full sweep area (X: SWEEP_X_START → SWEEP_X_END,
    Y: SWEEP_Y_START → SWEEP_Y_END) at nozzle-native resolution so that
    silver3dprinter's sliding-window slicing maps columns directly to
    physical nozzle positions during each sweep lane.

    Pixel size is exactly the nozzle pitch in both axes (square pixels).
    Width is rounded up to a multiple of 32 so the bitmap can be evenly
    sliced into 32-pixel strips by ``rasp_main.py``.

    Parameters
    ----------
    result : RoutingResult
        The completed routing result with trace paths in world mm.
    trace_width_mm : float
        Physical width of a conductive-ink trace.
    printer : PrinterDef, optional
        Printer definition (bed dimensions).  Falls back to default.
    bitmap : BitmapConfig
        Legacy parameter — ignored when part dimensions are provided.
    origin_x, origin_y : float
        World-space origin of the board outline's bounding-box lower-left
        corner.  Trace coordinates are shifted relative to this.
    printhead : PrintheadConfig
        Printhead hardware parameters (nozzle pitch, count).
    part_width_mm, part_depth_mm : float, optional
        Part bounding-box dimensions.
    bed_offset : tuple[float, float], optional
        ``(dx, dy)`` from model-local coordinates to absolute bed
        coordinates.  PrusaSlicer centres the STL on the bed, so
        ``bed_pos = model_pos + bed_offset``.  When provided, traces
        are placed at their correct absolute bed position within the
        sweep grid.

    Returns
    -------
    list[str]
        Each text line corresponds to one Y position (sweep direction),
        emitted from highest Y to lowest Y.  rasp_main.py reverses on
        load so row 0 = lowest Y = start of increasing-Y sweep.
    """
    pdef = printer or get_printer()
    pixel_size = printhead.pixel_size_mm

    cols = BITMAP_DATA_COLS
    rows = BITMAP_DATA_ROWS

    offset_x = bed_offset[0] if bed_offset else 0.0
    offset_y = bed_offset[1] if bed_offset else 0.0

    ink_cells: set[tuple[int, int]] = set()

    for trace in result.traces:
        bed_path = [
            (x - origin_x + offset_x, y - origin_y + offset_y)
            for x, y in trace.path
        ]
        bitmap_path = [
            (x - BITMAP_DATA_X_START_MM, y - SWEEP_Y_START_MM)
            for x, y in bed_path
        ]
        ink_cells |= _trace_cells(
            bitmap_path, trace_width_mm,
            pixel_size, cols, rows,
        )

    lines: list[str] = []
    for r in range(rows - 1, -1, -1):
        line_chars = []
        for c in range(cols):
            line_chars.append('1' if (r, c) in ink_cells else '0')
        lines.append(''.join(line_chars))

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
    bed_offset: tuple[float, float] | None = None,
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
        bed_offset=bed_offset,
    )
    output_path.write_text('\n'.join(lines), encoding='utf-8')
    return output_path

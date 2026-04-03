"""Bitmap generation — renders routed traces to a full-bed bitmap.

The bitmap covers the entire nominal build plate at nozzle-pitch
resolution, with each pixel being one nozzle pitch wide/tall.
Column 0 = bed X = 0, row 0 = bed Y = 0.

No offset calculations happen here.  The printer applies its own
calibrated FDM-to-inkjet offset when interpreting the bitmap during
sweeps.

  - text rows  → Y positions (low→high, so row 0 in the file
    corresponds to bed Y = 0)
  - text cols  → X positions (low→high)

A '1' means "deposit conductive ink here", a '0' means "no ink".
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

from src.pipeline.config import BedBitmap
from .models import RoutingResult

log = logging.getLogger(__name__)


def _trace_cells(
    path: list[tuple[float, float]],
    trace_width_mm: float,
    pixel_size: float,
    cols: int,
    rows: int,
) -> set[tuple[int, int]]:
    """Rasterize a Manhattan trace path into bitmap cell coordinates.

    Coordinates are in pixel-space (already transformed via
    ``BedBitmap.bed_to_pixel``).  Each cell is ``pixel_size`` mm square.
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
    grid: BedBitmap,
    model_to_bed: tuple[float, float] = (0.0, 0.0),
) -> list[str]:
    """Render all traces into a full-bed bitmap.

    Parameters
    ----------
    result : RoutingResult
        Completed routing result with trace paths in model-local mm.
    trace_width_mm : float
        Physical width of a conductive-ink trace.
    grid : BedBitmap
        Bed bitmap geometry (from ``bed_bitmap(printer_def)``).
    model_to_bed : (float, float)
        Translation from model-local coordinates to absolute bed
        coordinates: ``bed_pos = model_pos + model_to_bed``.

    Returns
    -------
    list[str]
        Each text line corresponds to one Y position,
        emitted from lowest Y (row 0) to highest Y.
    """
    pixel_size = grid.pixel_size_mm
    cols = grid.cols
    rows = grid.rows
    dx, dy = model_to_bed

    ink_cells: set[tuple[int, int]] = set()

    for trace in result.traces:
        bed_path = [(x + dx, y + dy) for x, y in trace.path]

        new_cells = _trace_cells(
            bed_path, trace_width_mm,
            pixel_size, cols, rows,
        )
        if not new_cells and bed_path:
            log.warning(
                "Trace net=%s clipped to zero pixels — may be outside bed",
                trace.net_id,
            )
        ink_cells |= new_cells

    lines: list[str] = []
    for r in range(rows):
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
    grid: BedBitmap,
    model_to_bed: tuple[float, float] = (0.0, 0.0),
) -> Path:
    """Generate the trace bitmap and write it to a text file."""
    output_path = Path(output_path)
    lines = generate_trace_bitmap(
        result, trace_width_mm,
        grid=grid,
        model_to_bed=model_to_bed,
    )
    output_path.write_text('\n'.join(lines), encoding='utf-8')
    return output_path

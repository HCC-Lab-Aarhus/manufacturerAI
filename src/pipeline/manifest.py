"""Print-job manifest generator.

Produces ``print_job.json`` — the contract between manufacturerAI (design)
and silver3dprinter (execution).  The manifest contains every physical
parameter needed to align the bitmap, gcode, and printhead sweeps.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from src.pipeline.config import (
    PrinterDef,
    PrintheadConfig,
    PRINTHEAD,
    FLOOR_MM,
    TRACE_HEIGHT_MM,
    get_printer,
)


@dataclass
class PrintJobManifest:
    """All physical parameters for a single print job."""

    # Bed
    bed_width_mm: float
    bed_depth_mm: float

    # Part bounding box on the bed (absolute bed coordinates)
    part_origin_x_mm: float
    part_origin_y_mm: float
    part_width_mm: float
    part_depth_mm: float

    # Printhead
    printhead_name: str
    nozzle_count: int
    nozzle_pitch_mm: float
    printhead_width_mm: float
    lane_step_nozzles: int
    lane_width_mm: float

    # Bitmap
    bitmap_file: str
    bitmap_cols: int
    bitmap_rows: int
    pixel_size_x_mm: float
    pixel_size_y_mm: float

    # Ink layer
    ink_z_mm: float
    trace_height_mm: float

    # Sweep (recommended parameters)
    recommended_speed_mm_per_min: float
    row_fire_rate_hz: float
    time_per_row_ms: float

    # Gcode
    gcode_file: str
    ink_pause_marker: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generate_manifest(
    *,
    part_origin_x_mm: float,
    part_origin_y_mm: float,
    part_width_mm: float,
    part_depth_mm: float,
    gcode_file: str = "enclosure_staged.gcode",
    bitmap_file: str = "trace_bitmap.txt",
    printer: PrinterDef | None = None,
    printhead: PrintheadConfig = PRINTHEAD,
    sweep_speed_mm_per_min: float = 2100.0,
) -> PrintJobManifest:
    """Build a manifest from design geometry and hardware config."""
    pdef = printer or get_printer()
    px = printhead.pixel_size_mm
    out_cols, out_rows = printhead.bitmap_output_dims(part_width_mm, part_depth_mm)

    speed_mm_per_s = sweep_speed_mm_per_min / 60.0
    row_rate = printhead.row_rate_for_speed(speed_mm_per_s)
    time_per_row_ms = 1000.0 / row_rate if row_rate > 0 else 0.0

    return PrintJobManifest(
        bed_width_mm=pdef.bed_width,
        bed_depth_mm=pdef.bed_depth,
        part_origin_x_mm=round(part_origin_x_mm, 4),
        part_origin_y_mm=round(part_origin_y_mm, 4),
        part_width_mm=round(part_width_mm, 4),
        part_depth_mm=round(part_depth_mm, 4),
        printhead_name="xaar128",
        nozzle_count=printhead.nozzle_count,
        nozzle_pitch_mm=printhead.nozzle_pitch_mm,
        printhead_width_mm=round(printhead.printhead_width_mm, 4),
        lane_step_nozzles=printhead.lane_step_nozzles,
        lane_width_mm=round(printhead.lane_width_mm, 4),
        bitmap_file=bitmap_file,
        bitmap_cols=out_cols,
        bitmap_rows=out_rows,
        pixel_size_x_mm=px,
        pixel_size_y_mm=px,
        ink_z_mm=FLOOR_MM,
        trace_height_mm=TRACE_HEIGHT_MM,
        recommended_speed_mm_per_min=sweep_speed_mm_per_min,
        row_fire_rate_hz=round(row_rate, 2),
        time_per_row_ms=round(time_per_row_ms, 4),
        gcode_file=gcode_file,
        ink_pause_marker=";silverink",
    )


def write_manifest(manifest: PrintJobManifest, output_path: Path | str) -> Path:
    """Write the manifest as JSON."""
    output_path = Path(output_path)
    output_path.write_text(
        json.dumps(manifest.to_dict(), indent=2),
        encoding="utf-8",
    )
    return output_path

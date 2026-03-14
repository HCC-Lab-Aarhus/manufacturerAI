"""Print-job manifest generator (optional).

Produces ``print_job.json`` — a convenience file that bundles the
physical parameters of a print job.  silver3dprinter does **not**
require this file; it only needs the trace bitmap and the embedded
``;silverink`` pause marker in the G-code.

The manifest is still generated when explicitly requested (e.g. by the
calibration debug endpoint) but is no longer part of the mandatory
manufacturing pipeline.
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
    BITMAP_DATA_COLS,
    BITMAP_DATA_ROWS,
    get_printer,
)


@dataclass
class PrintJobManifest:
    """Physical parameters for a single print job."""

    # Bed
    bed_width_mm: float
    bed_depth_mm: float
    nominal_bed_width_mm: float
    nominal_bed_depth_mm: float

    # Inkjet offset
    inkjet_offset_x_mm: float
    inkjet_offset_y_mm: float

    # Part bounding box on the bed (absolute bed coordinates)
    part_origin_x_mm: float
    part_origin_y_mm: float
    part_width_mm: float
    part_depth_mm: float

    # Printhead geometry
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
) -> PrintJobManifest:
    """Build a manifest from design geometry and hardware config."""
    pdef = printer or get_printer()
    px = printhead.pixel_size_mm

    return PrintJobManifest(
        bed_width_mm=pdef.bed_width,
        bed_depth_mm=pdef.bed_depth,
        nominal_bed_width_mm=pdef.nominal_bed_width,
        nominal_bed_depth_mm=pdef.nominal_bed_depth,
        inkjet_offset_x_mm=pdef.inkjet_offset_x,
        inkjet_offset_y_mm=pdef.inkjet_offset_y,
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
        bitmap_cols=BITMAP_DATA_COLS,
        bitmap_rows=BITMAP_DATA_ROWS,
        pixel_size_x_mm=px,
        pixel_size_y_mm=px,
        ink_z_mm=FLOOR_MM,
        trace_height_mm=TRACE_HEIGHT_MM,
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

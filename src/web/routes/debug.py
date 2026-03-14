"""Debug / calibration routes.

Generates alignment G-code and bitmap files used to measure and verify
the inkjet-to-PLA nozzle offset.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import get_printer, PRINTHEAD

router = APIRouter(prefix="/debug", tags=["debug"])


def _calibration_gcode(
    bed_w: float,
    bed_d: float,
    box: float,
    pad: float,
    sq: float,
    z: float = 0.3,
    feed: float = 1200,
) -> str:
    """Generate G-code that prints four alignment squares as PLA outlines.

    The squares sit at the four corners of a centred bounding box.
    After printing, the user deposits ink at the same positions and
    measures the physical offset between PLA and ink marks.
    """
    cx, cy = bed_w / 2, bed_d / 2
    half = box / 2

    corners = [
        (cx - half + pad, cy - half + pad),
        (cx + half - pad - sq, cy - half + pad),
        (cx - half + pad, cy + half - pad - sq),
        (cx + half - pad - sq, cy + half - pad - sq),
    ]

    lines = [
        "; Calibration alignment squares",
        f"; bed {bed_w}×{bed_d}  box {box}  pad {pad}  sq {sq}",
        "G28",
        "G90",
        f"G1 Z{z:.2f} F600",
    ]

    for i, (ox, oy) in enumerate(corners):
        lines.append(f"; Square {i + 1} at ({ox:.2f}, {oy:.2f})")
        lines.append(f"G1 X{ox:.2f} Y{oy:.2f} F3000")
        lines.append(f"G1 X{ox + sq:.2f} Y{oy:.2f} F{feed}")
        lines.append(f"G1 X{ox + sq:.2f} Y{oy + sq:.2f} F{feed}")
        lines.append(f"G1 X{ox:.2f} Y{oy + sq:.2f} F{feed}")
        lines.append(f"G1 X{ox:.2f} Y{oy:.2f} F{feed}")

    lines += [";silverink", "G28 X Y", "M84"]
    return "\n".join(lines)


def _calibration_bitmap(
    bed_w: float,
    bed_d: float,
    box: float,
    pad: float,
    sq: float,
) -> str:
    """Generate a text bitmap with four filled squares matching the G-code.

    Each character represents one nozzle-pitch pixel.  The bitmap is
    transposed (lines = X sweep, chars = Y nozzle array).
    """
    px = PRINTHEAD.pixel_size_mm
    cols_x = int(bed_w / px)
    rows_y = int(bed_d / px)

    cx, cy = bed_w / 2, bed_d / 2
    half = box / 2

    corners = [
        (cx - half + pad, cy - half + pad),
        (cx + half - pad - sq, cy - half + pad),
        (cx - half + pad, cy + half - pad - sq),
        (cx + half - pad - sq, cy + half - pad - sq),
    ]

    grid = [bytearray(rows_y) for _ in range(cols_x)]

    for ox, oy in corners:
        x0 = int(ox / px)
        x1 = int((ox + sq) / px)
        y0 = int(oy / px)
        y1 = int((oy + sq) / px)
        for xi in range(max(0, x0), min(cols_x, x1)):
            for yi in range(max(0, y0), min(rows_y, y1)):
                grid[xi][yi] = 1

    lines: list[str] = []
    for row in grid:
        lines.append("".join(chr(48 + b) for b in row))
    return "\n".join(lines)


@router.post("/calibrate")
async def generate_calibration(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
    bed_width: float = Query(219),
    bed_depth: float = Query(219),
    box_size: float = Query(100),
    padding: float = Query(5),
    square_size: float = Query(5),
) -> dict[str, Any]:
    """Generate alignment G-code + bitmap for inkjet offset calibration."""
    pdef = get_printer(printer)

    bw = bed_width or pdef.nominal_bed_width
    bd = bed_depth or pdef.nominal_bed_depth

    gcode = _calibration_gcode(bw, bd, box_size, padding, square_size)
    bitmap = _calibration_bitmap(bw, bd, box_size, padding, square_size)

    return {
        "gcode": gcode,
        "bitmap": bitmap,
        "nominal_bed_width": pdef.nominal_bed_width,
        "nominal_bed_depth": pdef.nominal_bed_depth,
        "inkjet_offset_x": pdef.inkjet_offset_x,
        "inkjet_offset_y": pdef.inkjet_offset_y,
        "usable_bed_width": pdef.bed_width,
        "usable_bed_depth": pdef.bed_depth,
    }

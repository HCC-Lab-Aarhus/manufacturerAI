"""Debug / calibration routes.

Generates alignment G-code and bitmap files used to measure and verify
the inkjet-to-PLA nozzle offset.

Follows the exact same coordinate conventions as the real pipeline:
- G-code is in nominal-bed coordinates (PrusaSlicer bed_shape).
- Part (calibration box) is centred on the nominal bed.
- Bitmap spans the full sweep grid in absolute bed coordinates.
- Bitmap transposition matches bitmap.py: lines = X sweep (high→low),
  chars = Y nozzle (low→high).
- Manifest records part_origin in absolute nominal-bed coordinates.
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import (
    get_printer, PrinterDef, PRINTHEAD,
    BITMAP_DATA_X_START_MM, BITMAP_DATA_COLS, BITMAP_DATA_ROWS,
    SWEEP_Y_START_MM,
)
from src.pipeline.gcode.filaments import get_filament, FilamentDef
from src.pipeline.manifest import generate_manifest

router = APIRouter(prefix="/debug", tags=["debug"])


def _calibration_gcode(
    pdef: PrinterDef,
    fdef: FilamentDef,
    box: float,
    pad: float,
    sq: float,
    z: float = 0.3,
    feed: float = 1200,
) -> str:
    """Generate G-code that prints three alignment squares plus a ruler.

    Three corners have squares; the top-right is intentionally missing so
    the operator can verify print orientation.

    Squares are centred on the **nominal** bed (matching PrusaSlicer
    bed_shape), just like the real pipeline centres parts.
    """
    nozzle_temp = int(fdef.overrides.get("first_layer_temperature",
                      fdef.overrides.get("temperature", "215")))
    bed_temp = int(fdef.overrides.get("first_layer_bed_temperature",
                   fdef.overrides.get("bed_temperature", "40")))

    nozzle_d = 0.4
    filament_d = 1.75
    extrusion_w = nozzle_d * 1.125  # 0.45 mm
    filament_area = math.pi * (filament_d / 2) ** 2
    e_per_mm = (z * extrusion_w) / filament_area

    nom_w = pdef.nominal_bed_width
    nom_d = pdef.nominal_bed_depth
    cx, cy = nom_w / 2, nom_d / 2
    half = box / 2

    corners = [
        (cx - half + pad, cy - half + pad),
        (cx + half - pad - sq, cy - half + pad),
        (cx - half + pad, cy + half - pad - sq),
    ]

    bw_i, bd_i = int(nom_w), int(nom_d)
    lines = [
        "; Calibration alignment squares",
        f"; Printer: {pdef.label}  Filament: {fdef.label}",
        f"; bed {nom_w}×{nom_d}  box {box}  pad {pad}  sq {sq}",
        f"; printer_model = {pdef.id}",
        f"; bed_shape = 0x0,{bw_i}x0,{bw_i}x{bd_i},0x{bd_i}",
        f"; nozzle_diameter = {nozzle_d}",
        f"; filament_diameter = {filament_d}",
        "",
        "; --- Start sequence ---",
        f"M140 S{bed_temp} ; set bed temp",
        f"M104 S{nozzle_temp} ; set nozzle temp (parallel heating)",
        f"M190 S{bed_temp} ; wait for bed temp",
        f"M109 S{nozzle_temp} ; wait for nozzle temp",
        "G28 ; home all axes",
    ]

    is_mk3 = "mk3" in pdef.id.lower()
    if is_mk3:
        lines += [
            "G1 Z0.20 F720",
            "G1 Y-3 F1000 ; go outside print area",
            "G92 E0",
            "G1 X60 E9 F1000 ; intro line",
            "G1 X100 E12.5 F1000 ; intro line",
            "G92 E0",
            "M221 S95",
        ]
    else:
        lines += [
            "G29 ; auto bed leveling",
            "G92 E0",
            "G1 Z2 F720",
            "G1 X5 Y5 F3000 ; move to purge start",
            f"G1 Z{z:.2f} F600",
            "G1 X60 E9 F1000 ; purge line",
            "G92 E0",
        ]

    lines += [
        "",
        "G90 ; absolute positioning",
        f"G1 Z{z:.2f} F600",
        "",
    ]

    e = 0.0
    prev_x, prev_y = 0.0, 0.0

    for i, (ox, oy) in enumerate(corners):
        lines.append(f"; Square {i + 1} at ({ox:.2f}, {oy:.2f})")
        e -= 0.8
        lines.append(f"G1 E{e:.4f} F2400 ; retract")
        lines.append(f"G1 X{ox:.2f} Y{oy:.2f} F3000 ; travel")
        e += 0.8
        lines.append(f"G1 E{e:.4f} F2400 ; unretract")

        sq_pts = [
            (ox + sq, oy),
            (ox + sq, oy + sq),
            (ox, oy + sq),
            (ox, oy),
        ]
        prev_x, prev_y = ox, oy
        for (nx, ny) in sq_pts:
            dist = math.hypot(nx - prev_x, ny - prev_y)
            e += dist * e_per_mm
            lines.append(f"G1 X{nx:.2f} Y{ny:.2f} E{e:.4f} F{feed}")
            prev_x, prev_y = nx, ny

    lines += [
        "",
        "G91 ; relative positioning",
        "G1 Z30 F1000 ; lift head before pause",
        "G90 ; absolute positioning",
        "M0 ; pause before silver ink",
        "G91 ; relative positioning",
        "G1 Z-30 F1000 ; lower head back down",
        "G90 ; absolute positioning",
        ";silverink",
        "",
        "; --- End sequence ---",
        "G4 ; wait for moves to finish",
        "M104 S0 ; turn off nozzle",
        "M140 S0 ; turn off heatbed",
        "M107 ; turn off fan",
        f"G1 X0 Y{nom_d:.0f} F3000 ; park head",
        "M84 ; disable motors",
    ]

    return "\n".join(lines)


def _calibration_bitmap(
    pdef: PrinterDef,
    box: float,
    pad: float,
    sq: float,
) -> str:
    """Generate a full sweep-grid bitmap with three filled squares.

    Top-right corner is omitted for orientation.

    The bitmap spans the entire sweep grid (BITMAP_DATA_COLS × BITMAP_DATA_ROWS)
    so that rasp_main.py's sliding-window slicing maps columns 1:1 to physical
    sweep lanes — exactly like the real pipeline's bitmap.py.

    Square positions are in absolute bed coordinates, converted to bitmap
    pixels by subtracting the sweep-grid origin.
    """
    px = PRINTHEAD.pixel_size_mm
    cols = BITMAP_DATA_COLS
    rows = BITMAP_DATA_ROWS

    nom_w = pdef.nominal_bed_width
    nom_d = pdef.nominal_bed_depth
    cx, cy = nom_w / 2, nom_d / 2
    half = box / 2

    corners_bed = [
        (cx - half + pad, cy - half + pad),
        (cx + half - pad - sq, cy - half + pad),
        (cx - half + pad, cy + half - pad - sq),
    ]

    ink_cells: set[tuple[int, int]] = set()
    for bed_x, bed_y in corners_bed:
        bx0 = bed_x - BITMAP_DATA_X_START_MM
        by0 = bed_y - SWEEP_Y_START_MM
        c0 = max(0, int(math.floor(bx0 / px)))
        c1 = min(cols - 1, int(math.floor((bx0 + sq) / px)))
        r0 = max(0, int(math.floor(by0 / px)))
        r1 = min(rows - 1, int(math.floor((by0 + sq) / px)))
        for c in range(c0, c1 + 1):
            for r in range(r0, r1 + 1):
                ink_cells.add((r, c))

    lines: list[str] = []
    for r in range(rows - 1, -1, -1):
        line_chars = []
        for c in range(cols):
            line_chars.append('1' if (r, c) in ink_cells else '0')
        lines.append(''.join(line_chars))

    return "\n".join(lines)


@router.post("/calibrate")
async def generate_calibration(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
    box_size: float = Query(100),
    padding: float = Query(5),
    square_size: float = Query(5),
) -> dict[str, Any]:
    """Generate alignment G-code + bitmap for inkjet offset calibration."""
    pdef = get_printer(printer)
    fdef = get_filament(filament)

    gcode = _calibration_gcode(pdef, fdef, box_size, padding, square_size)
    bitmap = _calibration_bitmap(pdef, box_size, padding, square_size)

    part_origin_x = pdef.nominal_bed_width / 2 - box_size / 2
    part_origin_y = pdef.nominal_bed_depth / 2 - box_size / 2

    manifest = generate_manifest(
        part_origin_x_mm=part_origin_x,
        part_origin_y_mm=part_origin_y,
        part_width_mm=box_size,
        part_depth_mm=box_size,
        gcode_file="calibration.gcode",
        bitmap_file="calibration_bitmap.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        "bitmap": bitmap,
        "contract": manifest.to_dict(),
    }

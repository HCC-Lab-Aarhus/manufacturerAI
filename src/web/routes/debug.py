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
    get_printer, PrinterDef,
    SweepGrid, sweep_grid,
    TRACE_RULES,
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
    grid: SweepGrid,
    box: float,
    pad: float,
    sq: float,
) -> str:
    """Generate a full sweep-grid bitmap with three filled squares.

    Top-right corner is omitted for orientation.

    The bitmap spans the entire sweep grid so that rasp_main.py's
    sliding-window slicing maps columns 1:1 to physical sweep lanes —
    exactly like the real pipeline's bitmap.py.

    Square positions are in absolute bed coordinates, converted to bitmap
    pixels via ``grid.bed_to_bitmap()``.
    """
    px = grid.pixel_size_mm
    cols = grid.data_cols
    rows = grid.data_rows

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
        bx0, by0 = grid.bed_to_bitmap(bed_x, bed_y)
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
    grid = sweep_grid(pdef)

    gcode = _calibration_gcode(pdef, fdef, box_size, padding, square_size)
    bitmap = _calibration_bitmap(pdef, grid, box_size, padding, square_size)

    part_origin_x = pdef.nominal_bed_width / 2 - box_size / 2
    part_origin_y = pdef.nominal_bed_depth / 2 - box_size / 2

    manifest = generate_manifest(
        grid=grid,
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


# ── Silverink test ────────────────────────────────────────────────
#
# Three 10x20 mm rectangles in the same L-pattern as calibration
# (top-right corner missing), printed with multiple layers and
# ironing on top.  The bitmap contains a single thin trace running
# lengthwise through the centre of each rectangle.


def _silverink_test_gcode(
    pdef: PrinterDef,
    fdef: FilamentDef,
    pad: float,
    rect_w: float,
    rect_h: float,
    layers: int = 4,
    z: float = 0.2,
    feed: float = 1200,
) -> str:
    """Generate G-code for silverink test rectangles with ironing.

    Three rectangles stacked vertically on the left wall of the bed,
    each with multiple layers and ironing on top.
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
    infill_spacing = extrusion_w
    iron_spacing = 0.1
    iron_flow = 0.05

    nom_w = pdef.nominal_bed_width
    nom_d = pdef.nominal_bed_depth

    gap = pad
    total_h = 3 * rect_h + 2 * gap
    y_base = (nom_d - total_h) / 2
    x_left = abs(pdef.inkjet_offset_x) + pad

    corners = [
        (x_left, y_base),
        (x_left, y_base + rect_h + gap),
        (x_left, y_base + 2 * (rect_h + gap)),
    ]

    bw_i, bd_i = int(nom_w), int(nom_d)
    lines = [
        "; Silverink test - 3 rectangles with traces",
        f"; Printer: {pdef.label}  Filament: {fdef.label}",
        f"; bed {nom_w}x{nom_d}  pad {pad}",
        f"; rect {rect_w}x{rect_h} mm, {layers} layers, ironing on top",
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

    lines += ["", "G90 ; absolute positioning", "M82 ; absolute extrusion", ""]

    e = 0.0

    for layer in range(layers):
        lz = z * (layer + 1)
        lines.append(f";LAYER_CHANGE")
        lines.append(f";Z:{lz:.1f}")
        lines.append(f"G1 Z{lz:.2f} F600")

        for ox, oy in corners:
            x0, y0 = ox, oy
            x1, y1 = ox + rect_w, oy + rect_h

            # Perimeter
            e -= 0.8
            lines.append(f"G1 E{e:.4f} F2400 ; retract")
            lines.append(f"G1 X{x0:.3f} Y{y0:.3f} F3000 ; travel")
            e += 0.8
            lines.append(f"G1 E{e:.4f} F2400 ; unretract")

            prev = (x0, y0)
            for nx, ny in [(x1, y0), (x1, y1), (x0, y1), (x0, y0)]:
                dist = math.hypot(nx - prev[0], ny - prev[1])
                e += dist * e_per_mm
                lines.append(f"G1 X{nx:.3f} Y{ny:.3f} E{e:.4f} F{feed}")
                prev = (nx, ny)

            # Rectilinear infill (Y-direction lines)
            inset = extrusion_w / 2
            ix0, iy0 = x0 + inset, y0 + inset
            ix1, iy1 = x1 - inset, y1 - inset

            x_pos = ix0
            going_up = True
            while x_pos <= ix1 + 0.001:
                if going_up:
                    lines.append(
                        f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.4f} F{feed}")
                    dist = iy1 - iy0
                    e += dist * e_per_mm
                    lines.append(
                        f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.4f} F{feed}")
                else:
                    lines.append(
                        f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.4f} F{feed}")
                    dist = iy1 - iy0
                    e += dist * e_per_mm
                    lines.append(
                        f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.4f} F{feed}")
                going_up = not going_up
                x_pos += infill_spacing

        # Ironing on last layer
        if layer == layers - 1:
            iron_epmm = e_per_mm * iron_flow
            lines.append("")
            lines.append(";TYPE:Ironing")

            for ox, oy in corners:
                x0, y0 = ox, oy
                x1, y1 = ox + rect_w, oy + rect_h
                inset = extrusion_w / 2
                ix0, iy0 = x0 + inset, y0 + inset
                ix1, iy1 = x1 - inset, y1 - inset

                e -= 0.8
                lines.append(f"G1 E{e:.4f} F2400 ; retract")
                lines.append(f"G1 X{ix1:.3f} Y{iy0:.3f} F3000 ; travel")
                e += 0.8
                lines.append(f"G1 E{e:.4f} F2400 ; unretract")

                x_pos = ix1
                going_down = True
                while x_pos >= ix0 - 0.001:
                    dist = iy1 - iy0
                    e += dist * iron_epmm
                    if going_down:
                        lines.append(
                            f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.5f} F900")
                    else:
                        lines.append(
                            f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.5f} F900")
                    going_down = not going_down
                    x_pos -= iron_spacing
                    if x_pos >= ix0 - 0.001:
                        e += iron_spacing * iron_epmm
                        lines.append(
                            f"G1 X{x_pos:.3f} E{e:.5f} F900")

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


def _silverink_test_bitmap(
    pdef: PrinterDef,
    grid: SweepGrid,
    pad: float,
    rect_w: float,
    rect_h: float,
) -> str:
    """Generate a bitmap with a single trace through each rectangle's centre.

    Three rectangles stacked vertically on the left wall.  Each gets
    a thin vertical trace (Y direction) running its full height,
    centred in X.
    """
    px = grid.pixel_size_mm
    cols = grid.data_cols
    rows = grid.data_rows

    trace_width_nozzles = max(1, int(round(TRACE_RULES.trace_width_mm / px)))

    nom_d = pdef.nominal_bed_depth

    gap = pad
    total_h = 3 * rect_h + 2 * gap
    y_base = (nom_d - total_h) / 2
    x_left = abs(pdef.inkjet_offset_x) + pad

    corners_bed = [
        (x_left, y_base),
        (x_left, y_base + rect_h + gap),
        (x_left, y_base + 2 * (rect_h + gap)),
    ]

    ink_cells: set[tuple[int, int]] = set()
    half_trace = trace_width_nozzles // 2

    for bed_x, bed_y in corners_bed:
        center_x = bed_x + rect_w / 2
        bx_center, by0 = grid.bed_to_bitmap(center_x, bed_y)
        _, by1 = grid.bed_to_bitmap(center_x, bed_y + rect_h)

        c_center = int(round(bx_center / px))
        r0 = max(0, int(math.floor(by0 / px)))
        r1 = min(rows - 1, int(math.floor(by1 / px)))

        for dc in range(-half_trace, half_trace + 1):
            c = c_center + dc
            if 0 <= c < cols:
                for r in range(r0, r1 + 1):
                    ink_cells.add((r, c))

    result: list[str] = []
    for r in range(rows - 1, -1, -1):
        line_chars = []
        for c in range(cols):
            line_chars.append('1' if (r, c) in ink_cells else '0')
        result.append(''.join(line_chars))

    return "\n".join(result)


@router.post("/silverink-test")
async def generate_silverink_test(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
    padding: float = Query(5),
    rect_width: float = Query(10),
    rect_height: float = Query(20),
    layers: int = Query(4),
) -> dict[str, Any]:
    """Generate G-code + bitmap for a silverink adhesion/conductivity test."""
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)

    gcode = _silverink_test_gcode(
        pdef, fdef, padding, rect_width, rect_height, layers)
    bitmap = _silverink_test_bitmap(
        pdef, grid, padding, rect_width, rect_height)

    gap = padding
    total_h = 3 * rect_height + 2 * gap
    part_origin_x = abs(pdef.inkjet_offset_x) + padding
    part_origin_y = (pdef.nominal_bed_depth - total_h) / 2

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=part_origin_x,
        part_origin_y_mm=part_origin_y,
        part_width_mm=rect_width,
        part_depth_mm=total_h,
        gcode_file="silverink_test.gcode",
        bitmap_file="silverink_test_trace.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        "bitmap": bitmap,
        "contract": manifest.to_dict(),
    }

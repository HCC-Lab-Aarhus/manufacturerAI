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
from src.pipeline.scad.traces import TRACE_WIDTH as SCAD_TRACE_WIDTH

PINHOLE_TAPER_D: float = 1.4

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


# ── Cube-trace test ───────────────────────────────────────────────
#
# A flat rectangular plate where the right half is extruded upward
# into a cube.  Two pin-holes descend through the cube to the plate
# surface.  Trace cutouts run from the flat-plate edge of the cube
# inward to each pin-hole.  The bitmap draws traces along the same
# channels.


def _cube_trace_gcode(
    pdef: PrinterDef,
    fdef: FilamentDef,
    pad: float,
    plate_w: float,
    plate_h: float,
    cube_size: float,
    z: float = 0.2,
    feed: float = 1200,
) -> str:
    nozzle_temp = int(fdef.overrides.get("first_layer_temperature",
                      fdef.overrides.get("temperature", "215")))
    bed_temp = int(fdef.overrides.get("first_layer_bed_temperature",
                   fdef.overrides.get("bed_temperature", "40")))

    nozzle_d = 0.4
    filament_d = 1.75
    extrusion_w = nozzle_d * 1.125
    filament_area = math.pi * (filament_d / 2) ** 2
    e_per_mm = (z * extrusion_w) / filament_area
    infill_spacing = extrusion_w
    iron_spacing = 0.1
    iron_flow = 0.05

    nom_w = pdef.nominal_bed_width
    nom_d = pdef.nominal_bed_depth

    plate_x = (nom_w - plate_w) / 2
    plate_y = (nom_d - plate_h) / 2

    cube_w = cube_size
    cube_h = cube_size
    cube_x = plate_x + plate_w - cube_w
    cube_y = plate_y + (plate_h - cube_h) / 2
    cube_layers = max(1, round(cube_size / z)) - 1

    bw_i, bd_i = int(nom_w), int(nom_d)
    lines = [
        "; Cube-trace test - plate with raised cube, pin-holes, trace cutouts",
        f"; Printer: {pdef.label}  Filament: {fdef.label}",
        f"; bed {nom_w}x{nom_d}  pad {pad}",
        f"; plate {plate_w}x{plate_h} mm, cube {cube_w}x{cube_h}x{cube_size} mm, {cube_layers} cube layers",
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
            "G1 Z0.20 F720", "G1 Y-3 F1000", "G92 E0",
            "G1 X60 E9 F1000", "G1 X100 E12.5 F1000", "G92 E0", "M221 S95",
        ]
    else:
        lines += [
            "G29 ; auto bed leveling", "G92 E0", "G1 Z2 F720",
            "G1 X5 Y5 F3000", f"G1 Z{z:.2f} F600",
            "G1 X60 E9 F1000 ; purge line", "G92 E0",
        ]

    lines += ["", "G90 ; absolute positioning", "M82 ; absolute extrusion", ""]

    e = 0.0

    def _rect_perimeter_infill_iron(
        x0: float, y0: float, w: float, h: float,
        do_iron: bool = False,
    ) -> None:
        nonlocal e
        x1, y1 = x0 + w, y0 + h
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
        # Infill
        inset = extrusion_w / 2
        ix0, iy0 = x0 + inset, y0 + inset
        ix1, iy1 = x1 - inset, y1 - inset
        x_pos = ix0
        going_up = True
        while x_pos <= ix1 + 0.001:
            if going_up:
                lines.append(f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.4f} F{feed}")
                e += (iy1 - iy0) * e_per_mm
                lines.append(f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.4f} F{feed}")
            else:
                lines.append(f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.4f} F{feed}")
                e += (iy1 - iy0) * e_per_mm
                lines.append(f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.4f} F{feed}")
            going_up = not going_up
            x_pos += infill_spacing
        if do_iron:
            iron_epmm = e_per_mm * iron_flow
            lines.append(";TYPE:Ironing")
            e -= 0.8
            lines.append(f"G1 E{e:.4f} F2400 ; retract")
            lines.append(f"G1 X{ix1:.3f} Y{iy0:.3f} F3000")
            e += 0.8
            lines.append(f"G1 E{e:.4f} F2400 ; unretract")
            x_pos = ix1
            going_down = True
            while x_pos >= ix0 - 0.001:
                e += (iy1 - iy0) * iron_epmm
                if going_down:
                    lines.append(f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.5f} F900")
                else:
                    lines.append(f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.5f} F900")
                going_down = not going_down
                x_pos -= iron_spacing
                if x_pos >= ix0 - 0.001:
                    e += iron_spacing * iron_epmm
                    lines.append(f"G1 X{x_pos:.3f} E{e:.5f} F900")

    plate_layers = 4
    for pl in range(1, plate_layers + 1):
        lz = z * pl
        lines.append(";LAYER_CHANGE")
        lines.append(f";Z:{lz:.1f}")
        lines.append(f"G1 Z{lz:.2f} F600")
        _rect_perimeter_infill_iron(plate_x, plate_y, plate_w, plate_h, do_iron=(pl == plate_layers))

    # ── Cutout geometry (matches SCAD pipeline dimensions) ──
    pin_half = PINHOLE_TAPER_D / 2
    cut_hw = SCAD_TRACE_WIDTH / 2
    cube_cx = cube_x + cube_w / 2
    pin_ys = [cube_y + cube_h * 0.3, cube_y + cube_h * 0.7]

    def _excl_at(x: float) -> list[tuple[float, float]]:
        raw: list[tuple[float, float]] = []
        for py in pin_ys:
            if abs(x - cube_cx) <= pin_half:
                raw.append((py - pin_half, py + pin_half))
            if cube_x - 0.001 <= x <= cube_cx + 0.001:
                raw.append((py - cut_hw, py + cut_hw))
        raw.sort()
        merged: list[tuple[float, float]] = []
        for a, b in raw:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        return merged

    def _split_y(
        lo: float, hi: float, excls: list[tuple[float, float]],
    ) -> list[tuple[float, float]]:
        segs = [(lo, hi)]
        for ea, eb in excls:
            ns: list[tuple[float, float]] = []
            for s0, s1 in segs:
                if eb <= s0 or ea >= s1:
                    ns.append((s0, s1))
                else:
                    if s0 < ea:
                        ns.append((s0, ea))
                    if eb < s1:
                        ns.append((eb, s1))
            segs = ns
        return segs

    def _cube_with_cutouts(
        x0: float, y0: float, w: float, h: float,
        do_iron: bool = False,
    ) -> None:
        nonlocal e
        x1, y1 = x0 + w, y0 + h
        inset = extrusion_w / 2

        # Perimeter — bottom / right / top are solid
        e -= 0.8
        lines.append(f"G1 E{e:.4f} F2400 ; retract")
        lines.append(f"G1 X{x0:.3f} Y{y0:.3f} F3000 ; travel")
        e += 0.8
        lines.append(f"G1 E{e:.4f} F2400 ; unretract")
        e += w * e_per_mm
        lines.append(f"G1 X{x1:.3f} Y{y0:.3f} E{e:.4f} F{feed}")
        e += h * e_per_mm
        lines.append(f"G1 X{x1:.3f} Y{y1:.3f} E{e:.4f} F{feed}")
        e += w * e_per_mm
        lines.append(f"G1 X{x0:.3f} Y{y1:.3f} E{e:.4f} F{feed}")

        # Left edge — gaps for trace cutout channels
        for seg in sorted(
            _split_y(y0, y1, _excl_at(x0)), key=lambda s: -s[1],
        ):
            e -= 0.8
            lines.append(f"G1 E{e:.4f} F2400 ; retract")
            lines.append(f"G1 X{x0:.3f} Y{seg[1]:.3f} F3000")
            e += 0.8
            lines.append(f"G1 E{e:.4f} F2400 ; unretract")
            e += (seg[1] - seg[0]) * e_per_mm
            lines.append(f"G1 X{x0:.3f} Y{seg[0]:.3f} E{e:.4f} F{feed}")

        # Infill with pin-hole and channel exclusions
        ix0, iy0 = x0 + inset, y0 + inset
        ix1, iy1 = x1 - inset, y1 - inset
        x_pos = ix0
        going_up = True
        while x_pos <= ix1 + 0.001:
            segs = _split_y(iy0, iy1, _excl_at(x_pos))
            ordered = segs if going_up else list(reversed(segs))
            for s in ordered:
                sy_a, sy_b = (s[0], s[1]) if going_up else (s[1], s[0])
                e -= 0.8
                lines.append(f"G1 E{e:.4f} F2400 ; retract")
                lines.append(f"G1 X{x_pos:.3f} Y{sy_a:.3f} F3000")
                e += 0.8
                lines.append(f"G1 E{e:.4f} F2400 ; unretract")
                e += abs(sy_b - sy_a) * e_per_mm
                lines.append(f"G1 X{x_pos:.3f} Y{sy_b:.3f} E{e:.4f} F{feed}")
            going_up = not going_up
            x_pos += infill_spacing

        if do_iron:
            iron_epmm = e_per_mm * iron_flow
            lines.append(";TYPE:Ironing")
            x_pos = ix1
            going_down = True
            while x_pos >= ix0 - 0.001:
                segs = _split_y(iy0, iy1, _excl_at(x_pos))
                ordered = list(reversed(segs)) if going_down else segs
                for s in ordered:
                    sy_a, sy_b = (s[1], s[0]) if going_down else (s[0], s[1])
                    e -= 0.8
                    lines.append(f"G1 E{e:.4f} F2400 ; retract")
                    lines.append(f"G1 X{x_pos:.3f} Y{sy_a:.3f} F3000")
                    e += 0.8
                    lines.append(f"G1 E{e:.4f} F2400 ; unretract")
                    e += abs(sy_b - sy_a) * iron_epmm
                    lines.append(f"G1 X{x_pos:.3f} Y{sy_b:.3f} E{e:.5f} F900")
                going_down = not going_down
                x_pos -= iron_spacing

    def _pinhole_excl_at(x: float) -> list[tuple[float, float]]:
        raw: list[tuple[float, float]] = []
        for py in pin_ys:
            if abs(x - cube_cx) <= pin_half:
                raw.append((py - pin_half, py + pin_half))
        return raw

    def _cube_with_pinholes(
        x0: float, y0: float, w: float, h: float,
        do_iron: bool = False,
    ) -> None:
        nonlocal e
        x1, y1 = x0 + w, y0 + h
        inset = extrusion_w / 2

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

        ix0, iy0 = x0 + inset, y0 + inset
        ix1, iy1 = x1 - inset, y1 - inset
        x_pos = ix0
        going_up = True
        while x_pos <= ix1 + 0.001:
            segs = _split_y(iy0, iy1, _pinhole_excl_at(x_pos))
            ordered = segs if going_up else list(reversed(segs))
            for s in ordered:
                sy_a, sy_b = (s[0], s[1]) if going_up else (s[1], s[0])
                e -= 0.8
                lines.append(f"G1 E{e:.4f} F2400 ; retract")
                lines.append(f"G1 X{x_pos:.3f} Y{sy_a:.3f} F3000")
                e += 0.8
                lines.append(f"G1 E{e:.4f} F2400 ; unretract")
                e += abs(sy_b - sy_a) * e_per_mm
                lines.append(f"G1 X{x_pos:.3f} Y{sy_b:.3f} E{e:.4f} F{feed}")
            going_up = not going_up
            x_pos += infill_spacing

        if do_iron:
            iron_epmm = e_per_mm * iron_flow
            lines.append(";TYPE:Ironing")
            x_pos = ix1
            going_down = True
            while x_pos >= ix0 - 0.001:
                segs = _split_y(iy0, iy1, _pinhole_excl_at(x_pos))
                ordered = list(reversed(segs)) if going_down else segs
                for s in ordered:
                    sy_a, sy_b = (s[1], s[0]) if going_down else (s[0], s[1])
                    e -= 0.8
                    lines.append(f"G1 E{e:.4f} F2400 ; retract")
                    lines.append(f"G1 X{x_pos:.3f} Y{sy_a:.3f} F3000")
                    e += 0.8
                    lines.append(f"G1 E{e:.4f} F2400 ; unretract")
                    e += abs(sy_b - sy_a) * iron_epmm
                    lines.append(f"G1 X{x_pos:.3f} Y{sy_b:.3f} E{e:.5f} F900")
                going_down = not going_down
                x_pos -= iron_spacing

    cutout_layers = max(1, round((cube_size / 2) / z))

    # Cube layers — channels+pinholes up to half height, pinholes-only above
    for layer in range(1, cube_layers + 1):
        lz = z * (plate_layers + layer)
        lines.append(";LAYER_CHANGE")
        lines.append(f";Z:{lz:.1f}")
        lines.append(f"G1 Z{lz:.2f} F600")
        is_last = layer == cube_layers
        if layer <= cutout_layers:
            _cube_with_cutouts(cube_x, cube_y, cube_w, cube_h, do_iron=is_last)
        else:
            _cube_with_pinholes(cube_x, cube_y, cube_w, cube_h, do_iron=is_last)

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


def _cube_trace_bitmap(
    pdef: PrinterDef,
    grid: SweepGrid,
    plate_w: float,
    plate_h: float,
    cube_size: float,
) -> str:
    px = grid.pixel_size_mm
    cols = grid.data_cols
    rows = grid.data_rows
    trace_width_nozzles = max(1, int(round(SCAD_TRACE_WIDTH / px)))
    half_trace = trace_width_nozzles // 2

    nom_w = pdef.nominal_bed_width
    nom_d = pdef.nominal_bed_depth

    plate_x = (nom_w - plate_w) / 2
    plate_y = (nom_d - plate_h) / 2

    cube_w = cube_size
    cube_h = cube_size
    cube_x = plate_x + plate_w - cube_w
    cube_y = plate_y + (plate_h - cube_h) / 2
    cube_center_x = cube_x + cube_w / 2
    pin1_y = cube_y + cube_h * 0.3
    pin2_y = cube_y + cube_h * 0.7

    ink_cells: set[tuple[int, int]] = set()

    for pin_y in (pin1_y, pin2_y):
        bx0, by = grid.bed_to_bitmap(plate_x, pin_y)
        bx1, _ = grid.bed_to_bitmap(cube_center_x, pin_y)

        c0 = max(0, int(math.floor(bx0 / px)))
        c1 = min(cols - 1, int(math.floor(bx1 / px)))
        r_center = int(round(by / px))

        for dc in range(-half_trace, half_trace + 1):
            r = r_center + dc
            if 0 <= r < rows:
                for c in range(c0, c1 + 1):
                    ink_cells.add((r, c))

    result: list[str] = []
    for r in range(rows - 1, -1, -1):
        line_chars = []
        for c in range(cols):
            line_chars.append('1' if (r, c) in ink_cells else '0')
        result.append(''.join(line_chars))

    return "\n".join(result)


@router.post("/cube-trace")
async def generate_cube_trace(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
    padding: float = Query(5),
    plate_width: float = Query(40),
    plate_height: float = Query(30),
    cube_size: float = Query(15),
) -> dict[str, Any]:
    """Generate G-code + bitmap for cube-trace test."""
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)

    gcode = _cube_trace_gcode(
        pdef, fdef, padding, plate_width, plate_height, cube_size)
    bitmap = _cube_trace_bitmap(pdef, grid, plate_width, plate_height, cube_size)

    plate_x = (pdef.nominal_bed_width - plate_width) / 2
    plate_y = (pdef.nominal_bed_depth - plate_height) / 2

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=plate_x,
        part_origin_y_mm=plate_y,
        part_width_mm=plate_width,
        part_depth_mm=plate_height,
        gcode_file="cube_trace.gcode",
        bitmap_file="cube_trace_bitmap.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        "bitmap": bitmap,
        "contract": manifest.to_dict(),
    }


# ── Progressive-trace test ────────────────────────────────────────
#
# Three flat rectangles with accompanying bitmaps.  Bitmap 1 has a
# trace on rect 1 only; bitmap 2 on rects 1-2; bitmap 3 on all three.
# Returns three bitmap strings (bitmap_1, bitmap_2, bitmap_3).


def _progressive_trace_gcode(
    pdef: PrinterDef,
    fdef: FilamentDef,
    pad: float,
    rect_w: float,
    rect_h: float,
    layers: int = 4,
    z: float = 0.2,
    feed: float = 1200,
) -> str:
    nozzle_temp = int(fdef.overrides.get("first_layer_temperature",
                      fdef.overrides.get("temperature", "215")))
    bed_temp = int(fdef.overrides.get("first_layer_bed_temperature",
                   fdef.overrides.get("bed_temperature", "40")))

    nozzle_d = 0.4
    filament_d = 1.75
    extrusion_w = nozzle_d * 1.125
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
        "; Progressive-trace test - 3 rectangles",
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
            "G1 Z0.20 F720", "G1 Y-3 F1000", "G92 E0",
            "G1 X60 E9 F1000", "G1 X100 E12.5 F1000", "G92 E0", "M221 S95",
        ]
    else:
        lines += [
            "G29", "G92 E0", "G1 Z2 F720",
            "G1 X5 Y5 F3000", f"G1 Z{z:.2f} F600",
            "G1 X60 E9 F1000", "G92 E0",
        ]

    lines += ["", "G90", "M82", ""]

    e = 0.0
    for layer in range(layers):
        lz = z * (layer + 1)
        lines.append(";LAYER_CHANGE")
        lines.append(f";Z:{lz:.1f}")
        lines.append(f"G1 Z{lz:.2f} F600")

        for ox, oy in corners:
            x0, y0 = ox, oy
            x1, y1 = ox + rect_w, oy + rect_h

            e -= 0.8
            lines.append(f"G1 E{e:.4f} F2400 ; retract")
            lines.append(f"G1 X{x0:.3f} Y{y0:.3f} F3000")
            e += 0.8
            lines.append(f"G1 E{e:.4f} F2400 ; unretract")

            prev = (x0, y0)
            for nx, ny in [(x1, y0), (x1, y1), (x0, y1), (x0, y0)]:
                dist = math.hypot(nx - prev[0], ny - prev[1])
                e += dist * e_per_mm
                lines.append(f"G1 X{nx:.3f} Y{ny:.3f} E{e:.4f} F{feed}")
                prev = (nx, ny)

            inset = extrusion_w / 2
            ix0, iy0 = x0 + inset, y0 + inset
            ix1, iy1 = x1 - inset, y1 - inset
            x_pos = ix0
            going_up = True
            while x_pos <= ix1 + 0.001:
                if going_up:
                    lines.append(f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.4f} F{feed}")
                    e += (iy1 - iy0) * e_per_mm
                    lines.append(f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.4f} F{feed}")
                else:
                    lines.append(f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.4f} F{feed}")
                    e += (iy1 - iy0) * e_per_mm
                    lines.append(f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.4f} F{feed}")
                going_up = not going_up
                x_pos += infill_spacing

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
                lines.append(f"G1 E{e:.4f} F2400")
                lines.append(f"G1 X{ix1:.3f} Y{iy0:.3f} F3000")
                e += 0.8
                lines.append(f"G1 E{e:.4f} F2400")
                x_pos = ix1
                going_down = True
                while x_pos >= ix0 - 0.001:
                    e += (iy1 - iy0) * iron_epmm
                    if going_down:
                        lines.append(f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.5f} F900")
                    else:
                        lines.append(f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.5f} F900")
                    going_down = not going_down
                    x_pos -= iron_spacing
                    if x_pos >= ix0 - 0.001:
                        e += iron_spacing * iron_epmm
                        lines.append(f"G1 X{x_pos:.3f} E{e:.5f} F900")

    lines += [
        "", "G91", "G1 Z30 F1000", "G90",
        "M0 ; pause before silver ink",
        "G91", "G1 Z-30 F1000", "G90",
        ";silverink", "",
        "; --- End sequence ---",
        "G4", "M104 S0", "M140 S0", "M107",
        f"G1 X0 Y{nom_d:.0f} F3000", "M84",
    ]

    return "\n".join(lines)


def _progressive_trace_bitmap(
    pdef: PrinterDef,
    grid: SweepGrid,
    pad: float,
    rect_w: float,
    rect_h: float,
    trace_count: int,
) -> str:
    """Bitmap with a centre trace on the first *trace_count* rectangles."""
    px = grid.pixel_size_mm
    cols = grid.data_cols
    rows = grid.data_rows
    trace_width_nozzles = max(1, int(round(TRACE_RULES.trace_width_mm / px)))
    half_trace = trace_width_nozzles // 2

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

    for i, (bed_x, bed_y) in enumerate(corners_bed):
        if i >= trace_count:
            break
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


@router.post("/progressive-trace")
async def generate_progressive_trace(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
    padding: float = Query(5),
    rect_width: float = Query(10),
    rect_height: float = Query(20),
    layers: int = Query(4),
) -> dict[str, Any]:
    """Generate G-code + 3 bitmaps for the progressive-trace test."""
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)

    gcode = _progressive_trace_gcode(
        pdef, fdef, padding, rect_width, rect_height, layers)

    bitmaps = {}
    for n in (1, 2, 3):
        bitmaps[f"bitmap_{n}"] = _progressive_trace_bitmap(
            pdef, grid, padding, rect_width, rect_height, n)

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
        gcode_file="progressive_trace.gcode",
        bitmap_file="progressive_trace_1.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        **bitmaps,
        "contract": manifest.to_dict(),
    }


# ── Parallel-lines test ──────────────────────────────────────────
#
# Three landscape rectangles (twice as large, rotated orientation)
# each with the same pattern of parallel lines.  The gap between
# consecutive lines increases from 1 px to 20 px.


def _parallel_lines_gcode(
    pdef: PrinterDef,
    fdef: FilamentDef,
    pad: float,
    rect_w: float,
    rect_h: float,
    layers: int = 4,
    z: float = 0.2,
    feed: float = 1200,
) -> str:
    nozzle_temp = int(fdef.overrides.get("first_layer_temperature",
                      fdef.overrides.get("temperature", "215")))
    bed_temp = int(fdef.overrides.get("first_layer_bed_temperature",
                   fdef.overrides.get("bed_temperature", "40")))

    nozzle_d = 0.4
    filament_d = 1.75
    extrusion_w = nozzle_d * 1.125
    filament_area = math.pi * (filament_d / 2) ** 2
    e_per_mm = (z * extrusion_w) / filament_area
    infill_spacing = extrusion_w
    iron_spacing = 0.1
    iron_flow = 0.05

    nom_w = pdef.nominal_bed_width
    nom_d = pdef.nominal_bed_depth

    gap = pad
    total_w = 3 * rect_w + 2 * gap
    x_base = (nom_w - total_w) / 2
    y_bottom = abs(pdef.inkjet_offset_y) + pad

    corners = [
        (x_base, y_bottom),
        (x_base + rect_w + gap, y_bottom),
        (x_base + 2 * (rect_w + gap), y_bottom),
    ]

    bw_i, bd_i = int(nom_w), int(nom_d)
    lines = [
        "; Parallel-lines test - 3 landscape rectangles with increasing-gap lines",
        f"; Printer: {pdef.label}  Filament: {fdef.label}",
        f"; bed {nom_w}x{nom_d}  pad {pad}",
        f"; rect {rect_w}x{rect_h} mm (landscape), {layers} layers",
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
            "G1 Z0.20 F720", "G1 Y-3 F1000", "G92 E0",
            "G1 X60 E9 F1000", "G1 X100 E12.5 F1000", "G92 E0", "M221 S95",
        ]
    else:
        lines += [
            "G29", "G92 E0", "G1 Z2 F720",
            "G1 X5 Y5 F3000", f"G1 Z{z:.2f} F600",
            "G1 X60 E9 F1000", "G92 E0",
        ]

    lines += ["", "G90", "M82", ""]

    e = 0.0
    for layer in range(layers):
        lz = z * (layer + 1)
        lines.append(";LAYER_CHANGE")
        lines.append(f";Z:{lz:.1f}")
        lines.append(f"G1 Z{lz:.2f} F600")

        for ox, oy in corners:
            x0, y0 = ox, oy
            x1, y1 = ox + rect_w, oy + rect_h

            e -= 0.8
            lines.append(f"G1 E{e:.4f} F2400 ; retract")
            lines.append(f"G1 X{x0:.3f} Y{y0:.3f} F3000")
            e += 0.8
            lines.append(f"G1 E{e:.4f} F2400 ; unretract")

            prev = (x0, y0)
            for nx, ny in [(x1, y0), (x1, y1), (x0, y1), (x0, y0)]:
                dist = math.hypot(nx - prev[0], ny - prev[1])
                e += dist * e_per_mm
                lines.append(f"G1 X{nx:.3f} Y{ny:.3f} E{e:.4f} F{feed}")
                prev = (nx, ny)

            inset = extrusion_w / 2
            ix0, iy0 = x0 + inset, y0 + inset
            ix1, iy1 = x1 - inset, y1 - inset
            x_pos = ix0
            going_up = True
            while x_pos <= ix1 + 0.001:
                if going_up:
                    lines.append(f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.4f} F{feed}")
                    e += (iy1 - iy0) * e_per_mm
                    lines.append(f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.4f} F{feed}")
                else:
                    lines.append(f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.4f} F{feed}")
                    e += (iy1 - iy0) * e_per_mm
                    lines.append(f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.4f} F{feed}")
                going_up = not going_up
                x_pos += infill_spacing

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
                lines.append(f"G1 E{e:.4f} F2400")
                lines.append(f"G1 X{ix1:.3f} Y{iy0:.3f} F3000")
                e += 0.8
                lines.append(f"G1 E{e:.4f} F2400")
                x_pos = ix1
                going_down = True
                while x_pos >= ix0 - 0.001:
                    e += (iy1 - iy0) * iron_epmm
                    if going_down:
                        lines.append(f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.5f} F900")
                    else:
                        lines.append(f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.5f} F900")
                    going_down = not going_down
                    x_pos -= iron_spacing
                    if x_pos >= ix0 - 0.001:
                        e += iron_spacing * iron_epmm
                        lines.append(f"G1 X{x_pos:.3f} E{e:.5f} F900")

    lines += [
        "", "G91", "G1 Z30 F1000", "G90",
        "M0 ; pause before silver ink",
        "G91", "G1 Z-30 F1000", "G90",
        ";silverink", "",
        "; --- End sequence ---",
        "G4", "M104 S0", "M140 S0", "M107",
        f"G1 X0 Y{nom_d:.0f} F3000", "M84",
    ]

    return "\n".join(lines)


def _parallel_lines_bitmap(
    pdef: PrinterDef,
    grid: SweepGrid,
    pad: float,
    rect_w: float,
    rect_h: float,
    max_gap: int = 10,
) -> str:
    """Bitmap with parallel vertical lines at increasing pixel spacing.

    Each rectangle gets the same pattern: line, 1px gap, line, 2px gap,
    line, 3px gap, ... up to max_gap px.  Lines use trace_width from
    TRACE_RULES, gaps are edge-to-edge.
    """
    px = grid.pixel_size_mm
    cols = grid.data_cols
    rows = grid.data_rows
    trace_w = max(1, int(round(TRACE_RULES.trace_width_mm / px)))

    nom_w = pdef.nominal_bed_width

    gap_pad = pad
    total_w = 3 * rect_w + 2 * gap_pad
    x_base = (nom_w - total_w) / 2
    y_bottom = abs(pdef.inkjet_offset_y) + pad

    corners_bed = [
        (x_base, y_bottom),
        (x_base + rect_w + gap_pad, y_bottom),
        (x_base + 2 * (rect_w + gap_pad), y_bottom),
    ]

    ink_cells: set[tuple[int, int]] = set()

    for bed_x, bed_y in corners_bed:
        bx0, by0 = grid.bed_to_bitmap(bed_x, bed_y)
        _, by1 = grid.bed_to_bitmap(bed_x, bed_y + rect_h)

        r0 = max(0, int(math.floor(by0 / px)))
        r1 = min(rows - 1, int(math.floor(by1 / px)))

        c_start = max(0, int(math.floor(bx0 / px)))
        c_pos = c_start

        for gap_size in range(1, max_gap + 1):
            if c_pos >= cols:
                break
            for dc in range(trace_w):
                c = c_pos + dc
                if 0 <= c < cols:
                    for r in range(r0, r1 + 1):
                        ink_cells.add((r, c))
            c_pos += trace_w + gap_size

        if c_pos < cols:
            for dc in range(trace_w):
                c = c_pos + dc
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


@router.post("/parallel-lines")
async def generate_parallel_lines(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
    padding: float = Query(5),
    rect_width: float = Query(40),
    rect_height: float = Query(20),
    layers: int = Query(4),
) -> dict[str, Any]:
    """Generate G-code + bitmap for the parallel-lines spacing test."""
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)

    gcode = _parallel_lines_gcode(
        pdef, fdef, padding, rect_width, rect_height, layers)
    bitmap = _parallel_lines_bitmap(
        pdef, grid, padding, rect_width, rect_height)

    gap = padding
    total_w = 3 * rect_width + 2 * gap
    x_base = (pdef.nominal_bed_width - total_w) / 2
    y_bottom = abs(pdef.inkjet_offset_y) + padding

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=x_base,
        part_origin_y_mm=y_bottom,
        part_width_mm=total_w,
        part_depth_mm=rect_height,
        gcode_file="parallel_lines.gcode",
        bitmap_file="parallel_lines_bitmap.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        "bitmap": bitmap,
        "contract": manifest.to_dict(),
    }


# ── Trace-width test ────────────────────────────────────────────────

def _trace_width_gcode(
    pdef: PrinterDef,
    fdef: FilamentDef,
    pad: float,
    rect_w: float,
    rect_h: float,
    layers: int = 4,
    z: float = 0.2,
    feed: float = 1200,
) -> str:
    nozzle_temp = int(fdef.overrides.get("first_layer_temperature",
                      fdef.overrides.get("temperature", "215")))
    bed_temp = int(fdef.overrides.get("first_layer_bed_temperature",
                   fdef.overrides.get("bed_temperature", "40")))

    nozzle_d = 0.4
    filament_d = 1.75
    extrusion_w = nozzle_d * 1.125
    filament_area = math.pi * (filament_d / 2) ** 2
    e_per_mm = (z * extrusion_w) / filament_area
    infill_spacing = extrusion_w
    iron_spacing = 0.1
    iron_flow = 0.05

    nom_w = pdef.nominal_bed_width
    nom_d = pdef.nominal_bed_depth

    x_left = abs(pdef.inkjet_offset_x) + pad
    y_bottom = abs(pdef.inkjet_offset_y) + pad

    bw_i, bd_i = int(nom_w), int(nom_d)
    lines: list[str] = [
        "; Trace-width test - single rectangle with varying-width lines",
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
            "G1 Z0.20 F720", "G1 Y-3 F1000", "G92 E0",
            "G1 X60 E9 F1000", "G1 X100 E12.5 F1000", "G92 E0", "M221 S95",
        ]
    else:
        lines += [
            "G29", "G92 E0", "G1 Z2 F720",
            "G1 X5 Y5 F3000", f"G1 Z{z:.2f} F600",
            "G1 X60 E9 F1000", "G92 E0",
        ]

    lines += ["", "G90", "M82", ""]

    e = 0.0
    for layer in range(layers):
        lz = z * (layer + 1)
        lines.append(";LAYER_CHANGE")
        lines.append(f";Z:{lz:.1f}")
        lines.append(f"G1 Z{lz:.2f} F600")

        x0, y0 = x_left, y_bottom
        x1, y1 = x_left + rect_w, y_bottom + rect_h

        e -= 0.8
        lines.append(f"G1 E{e:.4f} F2400 ; retract")
        lines.append(f"G1 X{x0:.3f} Y{y0:.3f} F3000")
        e += 0.8
        lines.append(f"G1 E{e:.4f} F2400 ; unretract")

        prev = (x0, y0)
        for nx, ny in [(x1, y0), (x1, y1), (x0, y1), (x0, y0)]:
            dist = math.hypot(nx - prev[0], ny - prev[1])
            e += dist * e_per_mm
            lines.append(f"G1 X{nx:.3f} Y{ny:.3f} E{e:.4f} F{feed}")
            prev = (nx, ny)

        inset = extrusion_w / 2
        ix0, iy0 = x0 + inset, y0 + inset
        ix1, iy1 = x1 - inset, y1 - inset
        x_pos = ix0
        going_up = True
        while x_pos <= ix1 + 0.001:
            if going_up:
                lines.append(f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.4f} F{feed}")
                e += (iy1 - iy0) * e_per_mm
                lines.append(f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.4f} F{feed}")
            else:
                lines.append(f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.4f} F{feed}")
                e += (iy1 - iy0) * e_per_mm
                lines.append(f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.4f} F{feed}")
            going_up = not going_up
            x_pos += infill_spacing

        if layer == layers - 1:
            iron_epmm = e_per_mm * iron_flow
            lines.append("")
            lines.append(";TYPE:Ironing")
            inset = extrusion_w / 2
            ix0, iy0 = x0 + inset, y0 + inset
            ix1, iy1 = x1 - inset, y1 - inset
            e -= 0.8
            lines.append(f"G1 E{e:.4f} F2400")
            lines.append(f"G1 X{ix1:.3f} Y{iy0:.3f} F3000")
            e += 0.8
            lines.append(f"G1 E{e:.4f} F2400")
            x_pos = ix1
            going_down = True
            while x_pos >= ix0 - 0.001:
                e += (iy1 - iy0) * iron_epmm
                if going_down:
                    lines.append(f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.5f} F900")
                else:
                    lines.append(f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.5f} F900")
                going_down = not going_down
                x_pos -= iron_spacing
                if x_pos >= ix0 - 0.001:
                    e += iron_spacing * iron_epmm
                    lines.append(f"G1 X{x_pos:.3f} E{e:.5f} F900")

    lines += [
        "", "G91", "G1 Z30 F1000", "G90",
        "M0 ; pause before silver ink",
        "G91", "G1 Z-30 F1000", "G90",
        ";silverink", "",
        "; --- End sequence ---",
        "G4", "M104 S0", "M140 S0", "M107",
        f"G1 X0 Y{nom_d:.0f} F3000", "M84",
    ]

    return "\n".join(lines)


def _trace_width_bitmap(
    pdef: PrinterDef,
    grid: SweepGrid,
    pad: float,
    rect_w: float,
    rect_h: float,
) -> str:
    px = grid.pixel_size_mm
    cols = grid.data_cols
    rows = grid.data_rows
    gap_px = 10

    x_left = abs(pdef.inkjet_offset_x) + pad
    y_bottom = abs(pdef.inkjet_offset_y) + pad

    bx0, by0 = grid.bed_to_bitmap(x_left, y_bottom)
    _, by1 = grid.bed_to_bitmap(x_left, y_bottom + rect_h)

    r0 = max(0, int(math.floor(by0 / px)))
    r1 = min(rows - 1, int(math.floor(by1 / px)))

    c_start = max(0, int(math.floor(bx0 / px)))

    ink_cells: set[tuple[int, int]] = set()

    c_pos = c_start
    for width in range(1, 11):
        if c_pos >= cols:
            break
        for dc in range(width):
            c = c_pos + dc
            if 0 <= c < cols:
                for r in range(r0, r1 + 1):
                    ink_cells.add((r, c))
        c_pos += width + gap_px

    result: list[str] = []
    for r in range(rows - 1, -1, -1):
        line_chars = []
        for c in range(cols):
            line_chars.append('1' if (r, c) in ink_cells else '0')
        result.append(''.join(line_chars))

    return "\n".join(result)


@router.post("/trace-width")
async def generate_trace_width(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
    padding: float = Query(5),
    rect_width: float = Query(40),
    rect_height: float = Query(20),
    layers: int = Query(4),
) -> dict[str, Any]:
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)

    gcode = _trace_width_gcode(
        pdef, fdef, padding, rect_width, rect_height, layers)
    bitmap = _trace_width_bitmap(
        pdef, grid, padding, rect_width, rect_height)

    x_left = abs(pdef.inkjet_offset_x) + padding
    y_bottom = abs(pdef.inkjet_offset_y) + padding

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=x_left,
        part_origin_y_mm=y_bottom,
        part_width_mm=rect_width,
        part_depth_mm=rect_height,
        gcode_file="trace_width.gcode",
        bitmap_file="trace_width_bitmap.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        "bitmap": bitmap,
        "contract": manifest.to_dict(),
    }

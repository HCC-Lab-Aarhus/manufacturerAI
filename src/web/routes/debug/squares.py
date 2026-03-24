from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import (
    get_printer, PrinterDef, SweepGrid, sweep_grid,
)
from src.pipeline.gcode.filaments import get_filament, FilamentDef
from src.pipeline.manifest import generate_manifest

from ._common import _Z_HOP

router = APIRouter()


def _squares_gcode(
    pdef: PrinterDef,
    fdef: FilamentDef,
    pad: float,
    rect_w: float,
    rect_h: float,
    layers: int,
    z: float = 0.2,
    feed: float = 1200,
) -> str:
    """Generate G-code for filled squares fully covered by trace.

    Nine 10×20 mm rectangles in a 3×3 grid, printed 2 mm tall
    (10 layers at 0.2 mm) with ironing on the top layer.
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
    total_w = 3 * rect_w + 2 * gap
    total_h = 3 * rect_h + 2 * gap
    x_left = abs(pdef.inkjet_offset_x) + pad
    y_base = (nom_d - total_h) / 2

    corners = [
        (x_left + col * (rect_w + gap), y_base + row * (rect_h + gap))
        for row in range(3) for col in range(3)
    ]

    bw_i, bd_i = int(nom_w), int(nom_d)
    lines = [
        "; Squares trace coverage test - 9 rectangles in 3x3 grid",
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

            e -= 0.8
            lines.append(f"G1 E{e:.4f} F2400 ; retract")
            lines.append(f"G1 Z{lz + _Z_HOP:.2f} F720 ; z-hop")
            lines.append(f"G1 X{x0:.3f} Y{y0:.3f} F3000 ; travel")
            lines.append(f"G1 Z{lz:.2f} F720 ; lower")
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
                lines.append(f"G1 Z{lz + _Z_HOP:.2f} F720 ; z-hop")
                lines.append(f"G1 X{ix1:.3f} Y{iy0:.3f} F3000 ; travel")
                lines.append(f"G1 Z{lz:.2f} F720 ; lower")
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

    e -= 4.0
    lines.append(f"G1 E{e:.4f} F3000 ; retract")
    lines += [
        "",
        "G91 ; relative positioning",
        "G1 Z1 F1000 ; lift head",
        "G90 ; absolute positioning",
        "",
        "G1 X0 Y0 F3000 ; move to home",
        "",
        "G91 ; relative positioning",
        "G1 Z-1 F1000 ; lower head back down",
        "G90 ; absolute positioning",
        "",
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


def _squares_bitmap(
    pdef: PrinterDef,
    grid: SweepGrid,
    pad: float,
    rect_w: float,
    rect_h: float,
) -> str:
    """Generate a bitmap with the full surface of each rectangle filled.

    Nine rectangles in a 3×3 grid.  Each is fully covered with
    ink ('1') — the entire footprint is projected onto the bitmap.
    """
    px = grid.pixel_size_mm
    cols = grid.data_cols
    rows = grid.data_rows

    nom_d = pdef.nominal_bed_depth

    gap = pad
    total_w = 3 * rect_w + 2 * gap
    total_h = 3 * rect_h + 2 * gap
    x_left = abs(pdef.inkjet_offset_x) + pad
    y_base = (nom_d - total_h) / 2

    corners_bed = [
        (x_left + col * (rect_w + gap), y_base + row * (rect_h + gap))
        for row in range(3) for col in range(3)
    ]

    ink_cells: set[tuple[int, int]] = set()

    for bed_x, bed_y in corners_bed:
        bx0, by0 = grid.bed_to_bitmap(bed_x, bed_y)
        bx1, by1 = grid.bed_to_bitmap(bed_x + rect_w, bed_y + rect_h)

        c0 = max(0, int(math.floor(bx0 / px)))
        c1 = min(cols - 1, int(math.floor(bx1 / px)))
        r0 = max(0, int(math.floor(by0 / px)))
        r1 = min(rows - 1, int(math.floor(by1 / px)))

        for c in range(c0, c1 + 1):
            for r in range(r0, r1 + 1):
                ink_cells.add((r, c))

    result: list[str] = []
    for r in range(rows - 1, -1, -1):
        line_chars = []
        for c in range(cols):
            line_chars.append('1' if (r, c) in ink_cells else '0')
        result.append(''.join(line_chars))

    return "\n".join(result)


@router.post("/squares")
async def generate_squares(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
    padding: float = Query(5),
    rect_width: float = Query(10),
    rect_height: float = Query(20),
    layers: int = Query(10),
) -> dict[str, Any]:
    """Generate G-code + bitmap for filled-trace square coverage test.

    Prints nine 10×20 mm rectangles in a 3×3 grid (2 mm tall) with
    their entire surface covered in silver-ink trace on the bitmap.
    """
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)

    gcode = _squares_gcode(
        pdef, fdef, padding, rect_width, rect_height, layers)
    bitmap = _squares_bitmap(
        pdef, grid, padding, rect_width, rect_height)

    gap = padding
    total_w = 3 * rect_width + 2 * gap
    total_h = 3 * rect_height + 2 * gap
    part_origin_x = abs(pdef.inkjet_offset_x) + padding
    part_origin_y = (pdef.nominal_bed_depth - total_h) / 2

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=part_origin_x,
        part_origin_y_mm=part_origin_y,
        part_width_mm=total_w,
        part_depth_mm=total_h,
        gcode_file="squares.gcode",
        bitmap_file="squares.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        "bitmap": bitmap,
        "contract": manifest.to_dict(),
    }

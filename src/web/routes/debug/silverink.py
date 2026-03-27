from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import (
    get_printer, PrinterDef, SweepGrid, sweep_grid, TRACE_RULES,
)
from src.pipeline.gcode.filaments import get_filament, FilamentDef
from src.pipeline.manifest import generate_manifest

from ._common import load_slicer_params, SlicerParams

router = APIRouter()


def _silverink_test_gcode(
    pdef: PrinterDef,
    fdef: FilamentDef,
    sp: SlicerParams,
    pad: float,
    rect_w: float,
    rect_h: float,
    layers: int = 4,
) -> str:
    """Generate G-code for silverink test rectangles with ironing.

    Three rectangles stacked vertically on the left wall of the bed,
    each with multiple layers and ironing on top.
    """
    nozzle_temp = int(fdef.overrides.get("first_layer_temperature",
                      fdef.overrides.get("temperature", "215")))
    bed_temp = int(fdef.overrides.get("first_layer_bed_temperature",
                   fdef.overrides.get("bed_temperature", "40")))

    z = sp.layer_height
    e_per_mm = sp.e_per_mm(z)
    extrusion_w = sp.extrusion_w
    infill_spacing = extrusion_w
    iron_spacing = sp.ironing_spacing
    iron_flow = sp.ironing_flowrate

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
        f"; nozzle_diameter = {sp.nozzle_d}",
        f"; filament_diameter = {sp.filament_d}",
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
        peri_f = sp.first_layer_feed if layer == 0 else sp.perimeter_feed
        fill_f = sp.first_layer_feed if layer == 0 else sp.infill_feed
        lines.append(f";LAYER_CHANGE")
        lines.append(f";Z:{lz:.1f}")
        lines.append(f"G1 Z{lz:.2f} F600")
        fan_pwm = sp.fan_pwm_for_layer(layer)
        if fan_pwm > 0:
            lines.append(f"M106 S{fan_pwm}")

        for ox, oy in corners:
            x0, y0 = ox, oy
            x1, y1 = ox + rect_w, oy + rect_h

            # Perimeter
            e -= sp.retract_length
            lines.append(f"G1 E{e:.4f} F{sp.retract_feed} ; retract")
            lines.append(f"G1 Z{lz + sp.retract_lift:.2f} F720 ; z-hop")
            lines.append(f"G1 X{x0:.3f} Y{y0:.3f} F{sp.travel_feed} ; travel")
            lines.append(f"G1 Z{lz:.2f} F720 ; lower")
            e += sp.retract_length
            lines.append(f"G1 E{e:.4f} F{sp.retract_feed} ; unretract")

            prev = (x0, y0)
            for nx, ny in [(x1, y0), (x1, y1), (x0, y1), (x0, y0)]:
                dist = math.hypot(nx - prev[0], ny - prev[1])
                e += dist * e_per_mm
                lines.append(f"G1 X{nx:.3f} Y{ny:.3f} E{e:.4f} F{peri_f}")
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
                        f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.4f} F{fill_f}")
                    dist = iy1 - iy0
                    e += dist * e_per_mm
                    lines.append(
                        f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.4f} F{fill_f}")
                else:
                    lines.append(
                        f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.4f} F{fill_f}")
                    dist = iy1 - iy0
                    e += dist * e_per_mm
                    lines.append(
                        f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.4f} F{fill_f}")
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

                e -= sp.retract_length
                lines.append(f"G1 E{e:.4f} F{sp.retract_feed} ; retract")
                lines.append(f"G1 Z{lz + sp.retract_lift:.2f} F720 ; z-hop")
                lines.append(f"G1 X{ix1:.3f} Y{iy0:.3f} F{sp.travel_feed} ; travel")
                lines.append(f"G1 Z{lz:.2f} F720 ; lower")
                e += sp.retract_length
                lines.append(f"G1 E{e:.4f} F{sp.retract_feed} ; unretract")

                x_pos = ix1
                going_down = True
                while x_pos >= ix0 - 0.001:
                    dist = iy1 - iy0
                    e += dist * iron_epmm
                    if going_down:
                        lines.append(
                            f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.5f} F{sp.ironing_feed}")
                    else:
                        lines.append(
                            f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.5f} F{sp.ironing_feed}")
                    going_down = not going_down
                    x_pos -= iron_spacing
                    if x_pos >= ix0 - 0.001:
                        e += iron_spacing * iron_epmm
                        lines.append(
                            f"G1 X{x_pos:.3f} E{e:.5f} F{sp.ironing_feed}")

    e -= 4.0
    lines.append(f"G1 E{e:.4f} F{sp.retract_feed} ; retract")
    lines += [
        "",
        "G91 ; relative positioning",
        "G1 Z1 F1000 ; lift head",
        "G90 ; absolute positioning",
        "",
        f"G1 X0 Y0 F{sp.travel_feed} ; move to home",
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
        f"G1 X0 Y{nom_d:.0f} F{sp.travel_feed} ; park head",
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
        pdef, fdef, load_slicer_params(printer), padding, rect_width, rect_height, layers)
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

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


def _parallel_lines_gcode(
    pdef: PrinterDef,
    fdef: FilamentDef,
    sp: SlicerParams,
    pad: float,
    rect_w: float,
    rect_h: float,
    layers: int = 4,
) -> str:
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
    if sp.fan_always_on:
        lines.append(f"M106 S{int(sp.min_fan_speed * 2.55)} ; fan on")
        lines.append("")

    e = 0.0
    for layer in range(layers):
        lz = z * (layer + 1)
        lines.append(";LAYER_CHANGE")
        lines.append(f";Z:{lz:.1f}")
        lines.append(f"G1 Z{lz:.2f} F600")

        for ox, oy in corners:
            x0, y0 = ox, oy
            x1, y1 = ox + rect_w, oy + rect_h

            e -= sp.retract_length
            lines.append(f"G1 E{e:.4f} F{sp.retract_feed} ; retract")
            lines.append(f"G1 Z{lz + sp.retract_lift:.2f} F720 ; z-hop")
            lines.append(f"G1 X{x0:.3f} Y{y0:.3f} F{sp.travel_feed}")
            lines.append(f"G1 Z{lz:.2f} F720 ; lower")
            e += sp.retract_length
            lines.append(f"G1 E{e:.4f} F{sp.retract_feed} ; unretract")

            prev = (x0, y0)
            for nx, ny in [(x1, y0), (x1, y1), (x0, y1), (x0, y0)]:
                dist = math.hypot(nx - prev[0], ny - prev[1])
                e += dist * e_per_mm
                lines.append(f"G1 X{nx:.3f} Y{ny:.3f} E{e:.4f} F{sp.perimeter_feed}")
                prev = (nx, ny)

            inset = extrusion_w / 2
            ix0, iy0 = x0 + inset, y0 + inset
            ix1, iy1 = x1 - inset, y1 - inset
            x_pos = ix0
            going_up = True
            while x_pos <= ix1 + 0.001:
                if going_up:
                    lines.append(f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.4f} F{sp.infill_feed}")
                    e += (iy1 - iy0) * e_per_mm
                    lines.append(f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.4f} F{sp.infill_feed}")
                else:
                    lines.append(f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.4f} F{sp.infill_feed}")
                    e += (iy1 - iy0) * e_per_mm
                    lines.append(f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.4f} F{sp.infill_feed}")
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
                e -= sp.retract_length
                lines.append(f"G1 E{e:.4f} F{sp.retract_feed}")
                lines.append(f"G1 Z{lz + sp.retract_lift:.2f} F720 ; z-hop")
                lines.append(f"G1 X{ix1:.3f} Y{iy0:.3f} F{sp.travel_feed}")
                lines.append(f"G1 Z{lz:.2f} F720 ; lower")
                e += sp.retract_length
                lines.append(f"G1 E{e:.4f} F{sp.retract_feed}")
                x_pos = ix1
                going_down = True
                while x_pos >= ix0 - 0.001:
                    e += (iy1 - iy0) * iron_epmm
                    if going_down:
                        lines.append(f"G1 X{x_pos:.3f} Y{iy1:.3f} E{e:.5f} F{sp.ironing_feed}")
                    else:
                        lines.append(f"G1 X{x_pos:.3f} Y{iy0:.3f} E{e:.5f} F{sp.ironing_feed}")
                    going_down = not going_down
                    x_pos -= iron_spacing
                    if x_pos >= ix0 - 0.001:
                        e += iron_spacing * iron_epmm
                        lines.append(f"G1 X{x_pos:.3f} E{e:.5f} F{sp.ironing_feed}")

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


@router.post("/spacing")
async def generate_spacing(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
    padding: float = Query(5),
    rect_width: float = Query(40),
    rect_height: float = Query(20),
    layers: int = Query(4),
) -> dict[str, Any]:
    """Generate G-code + bitmap for the spacing test."""
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)

    gcode = _parallel_lines_gcode(
        pdef, fdef, load_slicer_params(printer), padding, rect_width, rect_height, layers)
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
        gcode_file="spacing.gcode",
        bitmap_file="spacing.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        "bitmap": bitmap,
        "contract": manifest.to_dict(),
    }

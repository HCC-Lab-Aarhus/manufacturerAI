from __future__ import annotations

import math
import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import get_printer, SweepGrid, sweep_grid
from src.pipeline.gcode.filaments import get_filament
from src.pipeline.manifest import generate_manifest
from src.pipeline.scad.compiler import compile_scad
from src.pipeline.gcode.slicer import slice_stl

from ._common import load_slicer_params, render_bitmap

router = APIRouter()

BOX_SIZE_MM = 20.0
HOLE_DIAMETER_MM = 2.0
TRACE_WIDTH_MM = 1.0
TUNNEL_WIDTH_MM = 2.0
TUNNEL_HEIGHT_MM = 0.2
N_BASE_LAYERS = 4


def _build_via_scad(base_z: float, tunnel_h: float, layer_h: float) -> str:
    half_x = BOX_SIZE_MM / 2
    center_y = BOX_SIZE_MM / 2
    total_z = base_z + tunnel_h + layer_h
    tunnel_y_offset = center_y - TUNNEL_WIDTH_MM / 2
    return (
        "$fn = 32;\n"
        "difference() {\n"
        f"  cube([{BOX_SIZE_MM:.3f}, {BOX_SIZE_MM:.3f}, {total_z:.3f}]);\n"
        f"  translate([-0.01, {tunnel_y_offset:.3f}, {base_z:.3f}])\n"
        f"    cube([{half_x + 0.01:.3f}, {TUNNEL_WIDTH_MM:.3f}, {tunnel_h:.3f}]);\n"
        f"  translate([{half_x:.3f}, {center_y:.3f}, {base_z - 0.01:.3f}])\n"
        f"    cylinder(d={HOLE_DIAMETER_MM}, h={tunnel_h + layer_h + 0.02:.3f});\n"
        "}\n"
    )


def _silverink_block(n: int) -> list[str]:
    return [
        "",
        "G91 ; relative positioning",
        "G1 Z1 F1000 ; lift head",
        "G90 ; absolute positioning",
        "",
        "G1 X0 Y0 F6000 ; move to home",
        "",
        "G91 ; relative positioning",
        "G1 Z-1 F1000 ; lower head back down",
        "G90 ; absolute positioning",
        "",
        f";silverink{n}",
        "",
    ]


def _inject_via_silverink_markers(gcode: str, base_z: float) -> str:
    _Z_PAT = re.compile(r"^;Z:([\d.]+)")
    lines = gcode.split("\n")
    out: list[str] = []
    marker1_done = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        if not marker1_done and stripped == ";LAYER_CHANGE":
            for j in range(i + 1, min(i + 5, len(lines))):
                m = _Z_PAT.match(lines[j].strip())
                if m:
                    if float(m.group(1)) > base_z + 0.01:
                        out.extend(_silverink_block(1))
                        marker1_done = True
                    break

        out.append(line)

    for i in range(len(out) - 1, -1, -1):
        s = out[i].strip()
        if s.startswith("M104") and "S0" in s:
            out[i:i] = _silverink_block(2)
            return "\n".join(out)

    out.extend(_silverink_block(2))
    return "\n".join(out)


def _trace_cells(
    grid: SweepGrid,
    x_start: float,
    x_end: float,
    y_center: float,
    width_px: int,
) -> set[tuple[int, int]]:
    px = grid.pixel_size_mm
    bx0, by0 = grid.bed_to_bitmap(x_start, y_center)
    bx1, _ = grid.bed_to_bitmap(x_end, y_center)

    c0 = max(0, int(math.floor(min(bx0, bx1) / px)))
    c1 = min(grid.data_cols - 1, int(math.floor(max(bx0, bx1) / px)))
    r_center = int(math.floor(by0 / px))
    half = width_px // 2

    cells: set[tuple[int, int]] = set()
    for c in range(c0, c1 + 1):
        for dr in range(-half, half + width_px % 2):
            r = r_center + dr
            if 0 <= r < grid.data_rows:
                cells.add((r, c))
    return cells


@router.post("/via")
async def generate_via(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
) -> dict[str, Any]:
    """Generate a via test: single model with an internal tunnel for the trace.

    Cross-section (X-Z at Y=center):

        LEFT       CENTER        RIGHT
        ┌──────────┬──○──────────┐  top (ironed, bitmap2)
        │  ROOF    │    SOLID    │
        │  ┌───┘   │             │  tunnel 2mm wide, 1mm tall
        │  │TUNNEL  │             │
        ├──┴───────┴─────────────┤  base top (ironed, bitmap1)
        │     BASE (4 layers)    │
        └────────────────────────┘

    Single 20×20 box with downward-L shaped void:
    horizontal tunnel (left edge → center) + via hole (center → top).
    """
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)
    sp = load_slicer_params(printer)

    layer_h = sp.layer_height
    base_z = layer_h * N_BASE_LAYERS

    nom_w = pdef.nominal_bed_width
    nom_d = pdef.nominal_bed_depth
    cx, cy = nom_w / 2, nom_d / 2

    base_bed_x = cx - BOX_SIZE_MM / 2
    base_bed_y = cy - BOX_SIZE_MM / 2

    scad_src = _build_via_scad(base_z, TUNNEL_HEIGHT_MM, layer_h)

    with tempfile.TemporaryDirectory(prefix="debug_via_") as tmpdir:
        tmp = Path(tmpdir)
        scad_path = tmp / "via.scad"
        scad_path.write_text(scad_src, encoding="utf-8")

        ok, msg, stl_path = compile_scad(scad_path)
        if not ok or stl_path is None:
            raise RuntimeError(f"OpenSCAD compilation failed: {msg}")

        via_override = tmp / "via_override.ini"
        via_override.write_text(
            f"layer_height = {layer_h}\n"
            f"first_layer_height = {layer_h}\n"
            "ironing = 1\n"
            "ironing_type = top\n"
            "fill_density = 100%\n"
            "fill_pattern = rectilinear\n"
            "perimeters = 1\n"
            "top_solid_layers = 10\n"
            "bottom_solid_layers = 10\n"
            "support_material = 0\n"
            "brim_width = 0\n"
            "skirts = 0\n",
            encoding="utf-8",
        )

        gcode_path = tmp / "via.gcode"
        ok, msg, _ = slice_stl(
            stl_path,
            output_gcode=gcode_path,
            printer=printer,
            filament=fdef.id,
            center=(cx, cy),
            extra_overrides=[via_override],
        )
        if not ok:
            raise RuntimeError(f"PrusaSlicer failed: {msg}")

        gcode = _inject_via_silverink_markers(
            gcode_path.read_text(encoding="utf-8"),
            base_z,
        )

    trace_width_px = max(1, round(TRACE_WIDTH_MM / grid.pixel_size_mm))

    bitmap1_cells = _trace_cells(
        grid, base_bed_x, cx, cy, trace_width_px,
    )
    bitmap1 = render_bitmap(grid.data_rows, grid.data_cols, bitmap1_cells)

    box_right = base_bed_x + BOX_SIZE_MM
    bitmap2_cells = _trace_cells(
        grid, cx, box_right, cy, trace_width_px,
    )
    bitmap2 = render_bitmap(grid.data_rows, grid.data_cols, bitmap2_cells)

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=base_bed_x,
        part_origin_y_mm=base_bed_y,
        part_width_mm=BOX_SIZE_MM,
        part_depth_mm=BOX_SIZE_MM,
        gcode_file="via.gcode",
        bitmap_file="via_1.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        "bitmap1": bitmap1,
        "bitmap2": bitmap2,
        "contract": manifest.to_dict(),
    }

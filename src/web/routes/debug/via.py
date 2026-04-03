from __future__ import annotations

import math
import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import get_printer, BedBitmap, bed_bitmap
from src.pipeline.gcode.filaments import get_filament
from src.pipeline.scad.compiler import compile_scad
from src.pipeline.gcode.slicer import slice_stl

from ._common import load_slicer_params, render_bitmap

router = APIRouter()

BOX_SIZE_MM = 20.0
HOLE_DIAMETER_MM = 3.0
TRACE_WIDTH_MM = 1.0
TUNNEL_WIDTH_MM = 2.0
TUNNEL_HEIGHT_MM = 0.2
N_BASE_LAYERS = 4

GRID_COLS = 3
GRID_ROWS = 2
GRID_GAP_MM = 5.0


def _grid_offsets() -> list[tuple[float, float]]:
    offsets: list[tuple[float, float]] = []
    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            ox = col * (BOX_SIZE_MM + GRID_GAP_MM)
            oy = row * (BOX_SIZE_MM + GRID_GAP_MM)
            offsets.append((ox, oy))
    return offsets


def _build_via_scad(base_z: float, tunnel_h: float, layer_h: float) -> str:
    half_x = BOX_SIZE_MM / 2
    center_y = BOX_SIZE_MM / 2
    total_z = base_z + tunnel_h + layer_h
    tunnel_y_offset = center_y - TUNNEL_WIDTH_MM / 2

    single = (
        "module via_box() {\n"
        "  difference() {\n"
        f"    cube([{BOX_SIZE_MM:.3f}, {BOX_SIZE_MM:.3f}, {total_z:.3f}]);\n"
        f"    translate([-0.01, {tunnel_y_offset:.3f}, {base_z:.3f}])\n"
        f"      cube([{half_x + 0.01:.3f}, {TUNNEL_WIDTH_MM:.3f}, {tunnel_h:.3f}]);\n"
        f"    translate([{half_x:.3f}, {center_y:.3f}, {base_z - 0.01:.3f}])\n"
        f"      cylinder(d={HOLE_DIAMETER_MM}, h={tunnel_h + layer_h + 0.02:.3f});\n"
        "  }\n"
        "}\n"
    )

    lines = ["$fn = 32;\n", single]
    for ox, oy in _grid_offsets():
        lines.append(f"translate([{ox:.3f}, {oy:.3f}, 0]) via_box();\n")
    return "".join(lines)


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
            out[i:i] = _silverink_block(2) + _silverink_block(3)
            return "\n".join(out)

    out.extend(_silverink_block(2) + _silverink_block(3))
    return "\n".join(out)


def _trace_cells(
    grid: BedBitmap,
    x_start: float,
    x_end: float,
    y_center: float,
    width_px: int,
) -> set[tuple[int, int]]:
    px0, py0 = grid.bed_to_pixel(x_start, y_center)
    px1, _ = grid.bed_to_pixel(x_end, y_center)

    c0 = max(0, int(math.floor(min(px0, px1))))
    c1 = min(grid.cols - 1, int(math.floor(max(px0, px1))))
    r_center = int(math.floor(py0))
    half = width_px // 2

    cells: set[tuple[int, int]] = set()
    for c in range(c0, c1 + 1):
        for dr in range(-half, half + width_px % 2):
            r = r_center + dr
            if 0 <= r < grid.rows:
                cells.add((r, c))
    return cells


def _hole_cells(
    grid: BedBitmap,
    bed_cx: float,
    bed_cy: float,
    diameter_mm: float,
) -> set[tuple[int, int]]:
    px = grid.pixel_size_mm
    pix_x, pix_y = grid.bed_to_pixel(bed_cx, bed_cy)
    radius_px = diameter_mm / (2 * px)

    cells: set[tuple[int, int]] = set()
    r_lo = max(0, int(math.floor(pix_y - radius_px)))
    r_hi = min(grid.rows - 1, int(math.ceil(pix_y + radius_px)))
    c_lo = max(0, int(math.floor(pix_x - radius_px)))
    c_hi = min(grid.cols - 1, int(math.ceil(pix_x + radius_px)))
    for r in range(r_lo, r_hi + 1):
        for c in range(c_lo, c_hi + 1):
            if (r - pix_y) ** 2 + (c - pix_x) ** 2 <= radius_px ** 2:
                cells.add((r, c))
    return cells


@router.post("/via")
async def generate_via(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
) -> dict[str, Any]:
    """Generate a via test: 6 identical boxes (3×2 grid) on one build plate.

    Cross-section of each box (X-Z at Y=center):

        LEFT       CENTER        RIGHT
        ┌──────────┬──○──────────┐  top (ironed, bitmap2)
        │  ROOF    │    SOLID    │
        │  ┌───┘   │             │  tunnel 2mm wide, 0.2mm tall
        │  │TUNNEL  │             │
        ├──┴───────┴─────────────┤  base top (ironed, bitmap1)
        │     BASE (4 layers)    │
        └────────────────────────┘

    Each 20×20 box has a downward-L shaped void:
    horizontal tunnel (left edge → center) + via hole (center → top).
    All 3 bitmaps contain ink data for all 6 copies.
    """
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = bed_bitmap(pdef)
    sp = load_slicer_params(printer)

    layer_h = sp.layer_height
    base_z = layer_h * N_BASE_LAYERS

    nom_w = pdef.nominal_bed_width
    nom_d = pdef.nominal_bed_depth

    offsets = _grid_offsets()
    total_w = GRID_COLS * BOX_SIZE_MM + (GRID_COLS - 1) * GRID_GAP_MM
    total_d = GRID_ROWS * BOX_SIZE_MM + (GRID_ROWS - 1) * GRID_GAP_MM

    group_bed_x = nom_w / 2 - total_w / 2
    group_bed_y = nom_d / 2 - total_d / 2

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

        cx = nom_w / 2
        cy = nom_d / 2
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

    bitmap1_cells: set[tuple[int, int]] = set()
    bitmap2_cells: set[tuple[int, int]] = set()
    bitmap3_cells: set[tuple[int, int]] = set()

    for ox, oy in offsets:
        box_bed_x = group_bed_x + ox
        box_bed_y = group_bed_y + oy
        box_cx = box_bed_x + BOX_SIZE_MM / 2
        box_cy = box_bed_y + BOX_SIZE_MM / 2

        bitmap1_cells |= _trace_cells(
            grid, box_bed_x, box_cx, box_cy, trace_width_px,
        )

        box_right = box_bed_x + BOX_SIZE_MM
        bitmap2_cells |= _trace_cells(
            grid, box_cx, box_right, box_cy, trace_width_px,
        )

        bitmap3_cells |= _hole_cells(grid, box_cx, box_cy, HOLE_DIAMETER_MM)

    bitmap1 = render_bitmap(grid.rows, grid.cols, bitmap1_cells)
    bitmap2 = render_bitmap(grid.rows, grid.cols, bitmap2_cells)
    bitmap3 = render_bitmap(grid.rows, grid.cols, bitmap3_cells)

    return {
        "gcode": gcode,
        "bitmap1": bitmap1,
        "bitmap2": bitmap2,
        "bitmap3": bitmap3,
    }

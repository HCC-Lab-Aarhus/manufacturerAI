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

BASE_SIZE_MM = 40.0
PAD_SIZE_MM = 20.0
HOLE_DIAMETER_MM = 1.0
TRACE_WIDTH_MM = 1.0
N_BASE_LAYERS = 4

_Z_PARAM = re.compile(r"\bZ([\d.]+)")
_Z_COMMENT = re.compile(r";Z:([\d.]+)")


def _build_base_scad(base_z: float) -> str:
    return f"cube([{BASE_SIZE_MM:.3f}, {BASE_SIZE_MM:.3f}, {base_z:.3f}]);\n"


def _build_pad_scad(pad_z: float) -> str:
    return (
        "$fn = 32;\n"
        "difference() {\n"
        f"  cube([{PAD_SIZE_MM:.3f}, {PAD_SIZE_MM:.3f}, {pad_z:.3f}]);\n"
        f"  translate([{PAD_SIZE_MM / 2:.3f}, {PAD_SIZE_MM / 2:.3f}, -0.01])\n"
        f"    cylinder(d={HOLE_DIAMETER_MM}, h={pad_z + 0.02:.3f});\n"
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


def _extract_gcode_sections(gcode: str) -> tuple[str, str, str]:
    """Split PrusaSlicer gcode into (start, body, end)."""
    lines = gcode.split("\n")

    body_start = 0
    for i, line in enumerate(lines):
        if line.strip() == ";LAYER_CHANGE":
            body_start = i
            break

    body_end = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].strip()
        if s.startswith("M104") and "S0" in s:
            body_end = i
            break

    return (
        "\n".join(lines[:body_start]),
        "\n".join(lines[body_start:body_end]),
        "\n".join(lines[body_end:]),
    )


def _offset_gcode_z(body: str, z_offset: float) -> str:
    """Shift all Z positions and ;Z: comments in G-code by *z_offset*."""
    out: list[str] = []
    for line in body.split("\n"):
        s = line.strip()
        if s.startswith(("G0 ", "G1 ")):
            s = _Z_PARAM.sub(
                lambda m: f"Z{float(m.group(1)) + z_offset:.3f}", s
            )
        elif s.startswith(";Z:"):
            s = _Z_COMMENT.sub(
                lambda m: f";Z:{float(m.group(1)) + z_offset:.3f}", s
            )
        out.append(s)
    return "\n".join(out)


def _find_max_print_z(gcode: str) -> float:
    """Return the highest ;Z: value found in the gcode."""
    best = 0.0
    for line in gcode.split("\n"):
        m = _Z_COMMENT.search(line)
        if m:
            z = float(m.group(1))
            if z > best:
                best = z
    return best


def _stitch_via_gcode(base_gcode: str, pad_gcode: str, z_offset: float) -> str:
    """Combine independently-sliced base and pad gcode with silverink markers."""
    base_start, base_body, base_end = _extract_gcode_sections(base_gcode)
    _, pad_body, _ = _extract_gcode_sections(pad_gcode)

    adjusted_pad = _offset_gcode_z(pad_body, z_offset=z_offset)

    parts: list[str] = [
        base_start,
        base_body,
        *_silverink_block(1),
        "G92 E0",
        adjusted_pad,
        *_silverink_block(2),
        base_end,
    ]
    return "\n".join(parts)


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
    """Generate a via test: two ink layers connected through a plastic hole.

    The base and pad are sliced as independent STLs so that PrusaSlicer
    irons the full top surface of each piece.  The resulting gcode
    sections are stitched together with silverink markers in between.
    """
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)
    sp = load_slicer_params(printer)

    layer_h = sp.layer_height
    base_z = layer_h * N_BASE_LAYERS
    pad_z = layer_h * 2

    nom_w = pdef.nominal_bed_width
    nom_d = pdef.nominal_bed_depth
    cx, cy = nom_w / 2, nom_d / 2

    base_bed_x = cx - BASE_SIZE_MM / 2
    base_bed_y = cy - BASE_SIZE_MM / 2

    with tempfile.TemporaryDirectory(prefix="debug_via_") as tmpdir:
        tmp = Path(tmpdir)

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
        overrides = [via_override]

        # --- base (40 x 40, N_BASE_LAYERS layers, ironed top) ---
        base_scad = tmp / "base.scad"
        base_scad.write_text(_build_base_scad(base_z), encoding="utf-8")
        ok, msg, base_stl = compile_scad(base_scad)
        if not ok or base_stl is None:
            raise RuntimeError(f"OpenSCAD base failed: {msg}")

        base_gcode_path = tmp / "base.gcode"
        ok, msg, _ = slice_stl(
            base_stl,
            output_gcode=base_gcode_path,
            printer=printer,
            filament=fdef.id,
            center=(cx, cy),
            extra_overrides=overrides,
        )
        if not ok:
            raise RuntimeError(f"PrusaSlicer base failed: {msg}")

        # --- pad (20 x 20, 1 layer with 1 mm hole, ironed top) ---
        pad_scad = tmp / "pad.scad"
        pad_scad.write_text(_build_pad_scad(pad_z), encoding="utf-8")
        ok, msg, pad_stl = compile_scad(pad_scad)
        if not ok or pad_stl is None:
            raise RuntimeError(f"OpenSCAD pad failed: {msg}")

        pad_gcode_path = tmp / "pad.gcode"
        ok, msg, _ = slice_stl(
            pad_stl,
            output_gcode=pad_gcode_path,
            printer=printer,
            filament=fdef.id,
            center=(cx, cy),
            extra_overrides=overrides,
        )
        if not ok:
            raise RuntimeError(f"PrusaSlicer pad failed: {msg}")

        base_gcode_text = base_gcode_path.read_text(encoding="utf-8")
        actual_base_z = _find_max_print_z(base_gcode_text)

        gcode = _stitch_via_gcode(
            base_gcode_text,
            pad_gcode_path.read_text(encoding="utf-8"),
            actual_base_z,
        )

    trace_width_px = max(1, round(TRACE_WIDTH_MM / grid.pixel_size_mm))

    bitmap1_cells = _trace_cells(
        grid, base_bed_x, cx, cy, trace_width_px,
    )
    bitmap1 = render_bitmap(grid.data_rows, grid.data_cols, bitmap1_cells)

    pad_right = cx + PAD_SIZE_MM / 2
    bitmap2_cells = _trace_cells(
        grid, cx, pad_right, cy, trace_width_px,
    )
    bitmap2 = render_bitmap(grid.data_rows, grid.data_cols, bitmap2_cells)

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=base_bed_x,
        part_origin_y_mm=base_bed_y,
        part_width_mm=BASE_SIZE_MM,
        part_depth_mm=BASE_SIZE_MM,
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

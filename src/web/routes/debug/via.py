from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import (
    get_printer, bed_bitmap, FLOOR_MM, TRACE_RULES,
)
from src.pipeline.router.models import RoutingResult, Trace
from src.pipeline.router.bitmap import generate_trace_bitmap
from src.pipeline.scad.compiler import compile_scad
from src.pipeline.gcode.slicer import slice_stl
from src.pipeline.gcode.postprocessor import postprocess_gcode, compute_bed_offset

from ._common import load_slicer_params, DEBUG_OVERRIDE

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
    """Return full OpenSCAD source with module defs at top level and geometry
    in a mirror([0,1,0]) block."""
    half_x = BOX_SIZE_MM / 2
    center_y = BOX_SIZE_MM / 2
    total_z = base_z + tunnel_h + layer_h
    tunnel_y_offset = center_y - TUNNEL_WIDTH_MM / 2

    preamble = (
        "$fn = 32;\n"
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

    geometry_lines = []
    for ox, oy in _grid_offsets():
        geometry_lines.append(f"translate([{ox:.3f}, {oy:.3f}, 0]) via_box();\n")
    geometry = "".join(geometry_lines)

    return preamble + "mirror([0, 1, 0]) {\n" + geometry + "}\n"


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


def _via_trace_paths(
    offsets: list[tuple[float, float]],
) -> tuple[
    list[list[tuple[float, float]]],
    list[list[tuple[float, float]]],
]:
    """Build trace paths for the via test bitmaps.

    bitmap1 paths: horizontal traces from left edge to centre of each box.
    bitmap2 paths: horizontal traces from centre to right edge of each box.
    """
    paths1: list[list[tuple[float, float]]] = []
    paths2: list[list[tuple[float, float]]] = []

    for ox, oy in offsets:
        cx = ox + BOX_SIZE_MM / 2
        cy = oy + BOX_SIZE_MM / 2
        paths1.append([(ox, cy), (cx, cy)])
        paths2.append([(cx, cy), (ox + BOX_SIZE_MM, cy)])

    return paths1, paths2


@router.post("/via")
async def generate_via(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
) -> dict[str, Any]:
    """Generate via test: 6 boxes (3x2 grid) with tunnels on one plate.

    Uses the pipeline for SCAD mirror, compile, slice, and postprocess.
    Custom silverink marker injection handles the 3-bitmap multi-layer case.
    """
    pdef = get_printer(printer)
    grid = bed_bitmap(pdef)
    sp = load_slicer_params(printer)

    layer_h = sp.layer_height
    base_z = layer_h * N_BASE_LAYERS

    offsets = _grid_offsets()
    total_w = GRID_COLS * BOX_SIZE_MM + (GRID_COLS - 1) * GRID_GAP_MM
    total_d = GRID_ROWS * BOX_SIZE_MM + (GRID_ROWS - 1) * GRID_GAP_MM
    model_center = (total_w / 2, total_d / 2)

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

        overrides: list[Path] = []
        if DEBUG_OVERRIDE.exists():
            overrides.append(DEBUG_OVERRIDE)
        overrides.append(via_override)

        gcode_path = tmp / "via.gcode"
        ok, msg, _ = slice_stl(
            stl_path,
            output_gcode=gcode_path,
            printer=printer,
            filament=filament,
            center=pdef.usable_center,
            extra_overrides=overrides,
        )
        if not ok:
            raise RuntimeError(f"PrusaSlicer failed: {msg}")

        gcode = _inject_via_silverink_markers(
            gcode_path.read_text(encoding="utf-8"),
            base_z,
        )

    ucx, ucy = pdef.usable_center
    model_to_bed = (ucx - model_center[0], ucy + model_center[1])

    paths1, paths2 = _via_trace_paths(offsets)

    result1 = RoutingResult(
        traces=[Trace(net_id=f"via1_{i}", path=p) for i, p in enumerate(paths1)],
        pin_assignments={}, failed_nets=[],
    )
    result2 = RoutingResult(
        traces=[Trace(net_id=f"via2_{i}", path=p) for i, p in enumerate(paths2)],
        pin_assignments={}, failed_nets=[],
    )

    bitmap1 = "\n".join(generate_trace_bitmap(
        result1, TRACE_WIDTH_MM, grid=grid, model_to_bed=model_to_bed,
    ))
    bitmap2 = "\n".join(generate_trace_bitmap(
        result2, TRACE_WIDTH_MM, grid=grid, model_to_bed=model_to_bed,
    ))

    hole_paths: list[list[tuple[float, float]]] = []
    for ox, oy in offsets:
        cx = ox + BOX_SIZE_MM / 2
        cy = oy + BOX_SIZE_MM / 2
        r = HOLE_DIAMETER_MM / 2
        n_pts = 16
        import math
        ring = [
            (cx + r * math.cos(2 * math.pi * k / n_pts),
             cy + r * math.sin(2 * math.pi * k / n_pts))
            for k in range(n_pts + 1)
        ]
        hole_paths.append(ring)

    result3 = RoutingResult(
        traces=[Trace(net_id=f"via3_{i}", path=p) for i, p in enumerate(hole_paths)],
        pin_assignments={}, failed_nets=[],
    )
    bitmap3 = "\n".join(generate_trace_bitmap(
        result3, TRACE_WIDTH_MM, grid=grid, model_to_bed=model_to_bed,
    ))

    return {
        "gcode": gcode,
        "bitmap1": bitmap1,
        "bitmap2": bitmap2,
        "bitmap3": bitmap3,
    }

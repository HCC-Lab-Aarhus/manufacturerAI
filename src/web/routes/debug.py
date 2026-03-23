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

from dataclasses import dataclass

from src.catalog.loader import load_catalog, get_component
from src.catalog.models import Component
from src.pipeline.config import (
    get_printer, PrinterDef,
    SweepGrid, sweep_grid,
    TRACE_RULES,
    component_z_range, FLOOR_MM, CAVITY_START_MM, CEILING_MM, TRACE_HEIGHT_MM,
)
from src.pipeline.gcode.filaments import get_filament, FilamentDef
from src.pipeline.manifest import generate_manifest
from src.pipeline.placer.models import PlacedComponent
from src.pipeline.design.models import Outline, Enclosure, OutlineVertex
from src.pipeline.scad.resolver import (
    PINHOLE_CLEARANCE, ResolverContext, resolve_component,
)
from src.pipeline.scad.fragment import (
    ScadFragment, RectGeometry, CylinderGeometry,
    PolygonGeometry, SegmentGeometry, CapsuleGeometry,
)
from src.pipeline.scad.traces import TRACE_WIDTH as SCAD_TRACE_WIDTH

PINHOLE_TAPER_D: float = 3.5
_Z_HOP: float = 1.0

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
        lines.append(f"G1 Z{z + _Z_HOP:.2f} F720 ; z-hop")
        lines.append(f"G1 X{ox:.2f} Y{oy:.2f} F3000 ; travel")
        lines.append(f"G1 Z{z:.2f} F720 ; lower")
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

    e -= 4.0
    lines.append(f"G1 E{e:.4f} F3000 ; retract")
    lines += [
        "",
        "G91 ; relative positioning",
        "G1 Z1 F1000 ; lift head before pause",
        "G90 ; absolute positioning",
        "",
        "G1 X0 Y0 F3000 ; move to home",
        "",
        "G91 ; relative positioning",
        "G1 Z-1 F1000 ; lower head back down",
        "G90 ; absolute positioning",
        "",
        "M0 ; pause before silver ink",
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
        "G1 Z1 F1000 ; lift head before pause",
        "G90 ; absolute positioning",
        "",
        "G1 X0 Y0 F3000 ; move to home",
        "",
        "G91 ; relative positioning",
        "G1 Z-1 F1000 ; lower head back down",
        "G90 ; absolute positioning",
        "",
        "M0 ; pause before silver ink",
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
# Loads real catalog components (resistor, button, battery), arranges
# them on a common plate, and uses the pipeline's component_z_range()
# and resolve_component() to derive proper block heights, pin holes,
# body pockets, pin bridges, SCAD features (battery plate channels,
# dome contact windows, hatch), and funnel tapers — matching the
# real enclosure pipeline as closely as possible.

_BLOCK_MARGIN: float = 2.0
_PIN_MARGIN: float = 1.5
_COMP_GAP: float = 3.0
_TRACE_RUN: float = 15.0
_COMPONENT_CONFIGS: list[tuple[str, float]] = [
    ("resistor_axial", 90),
    ("tactile_button_6x6", 0),
    ("battery_holder_2xAAA", 90),
]


@dataclass
class _CompLayout:
    catalog: Component
    cx: float
    cy: float
    block_x: float
    block_y: float
    block_w: float
    block_h: float
    block_z_top: float
    pins: list[tuple[float, float, float]]
    fragments: list[ScadFragment]
    plate_x: float
    plate_y: float
    plate_w: float
    plate_h: float


def _compute_component_layout(
    pdef: PrinterDef,
    pad: float,
    z: float = 0.2,
) -> list[_CompLayout]:
    """Compute layout for catalog components, each on its own plate.

    Uses the real pipeline's component_z_range() for Z heights and
    resolve_component() for SCAD cutout fragments (body pockets, pin
    holes, pin bridges, SCAD features, hatches).

    Plates are stacked vertically and centred horizontally on the bed.
    The battery is rotated 90° so its two pins separate in Y and the
    traces extending left to the plate edge don't collide.

    Returns a list of _CompLayout, each with its own plate coordinates.
    """
    cat = load_catalog()

    nom_w = pdef.nominal_bed_width
    nom_d = pdef.nominal_bed_depth

    block_infos: list[tuple[Component, float, float, float, float, float]] = []
    for cid, rot in _COMPONENT_CONFIGS:
        comp = get_component(cat, cid)
        if comp is None:
            raise ValueError(f"Catalog component '{cid}' not found")

        body_w: float = comp.body.width_mm or 1.0
        body_l: float = comp.body.length_mm or 1.0

        pin_coords: list[tuple[float, float]] = []
        for pin in comp.pins:
            px_rel, py_rel = float(pin.position_mm[0]), float(pin.position_mm[1])
            if rot:
                rad = math.radians(rot)
                cos_a, sin_a = math.cos(rad), math.sin(rad)
                px_rel, py_rel = px_rel * cos_a - py_rel * sin_a, px_rel * sin_a + py_rel * cos_a
            pin_coords.append((px_rel, py_rel))

        if rot % 180 == 90:
            eff_w, eff_l = body_l, body_w
        else:
            eff_w, eff_l = body_w, body_l

        half_w = max(eff_w / 2,
                     max((abs(c[0]) for c in pin_coords), default=0) + _PIN_MARGIN)
        half_h = max(eff_l / 2,
                     max((abs(c[1]) for c in pin_coords), default=0) + _PIN_MARGIN)

        bw = 2 * half_w + 2 * _BLOCK_MARGIN
        bh = 2 * half_h + 2 * _BLOCK_MARGIN

        enclosure_h = CAVITY_START_MM + comp.body.height_mm + CEILING_MM
        ceil_start = enclosure_h - CEILING_MM

        _, body_top = component_z_range(
            comp.mounting.style, comp.body.height_mm,
            comp.pin_length_mm, ceil_start,
        )
        block_infos.append((comp, bw, bh, body_top, rot, enclosure_h))

    total_plate_h = sum(
        bi[2] + 2 * pad for bi in block_infos
    ) + _COMP_GAP * (len(block_infos) - 1)
    y_cursor = (nom_d - total_plate_h) / 2

    layouts: list[_CompLayout] = []
    for comp, bw, bh, body_top, rot, enclosure_h in block_infos:
        plate_w = _TRACE_RUN + bw + 2 * pad
        plate_h = bh + 2 * pad
        plate_x = (nom_w - plate_w) / 2
        plate_y = y_cursor

        outline = Outline(points=[
            OutlineVertex(plate_x, plate_y),
            OutlineVertex(plate_x + plate_w, plate_y),
            OutlineVertex(plate_x + plate_w, plate_y + plate_h),
            OutlineVertex(plate_x, plate_y + plate_h),
        ])

        ceil_start = enclosure_h - CEILING_MM
        enclosure = Enclosure(height_mm=enclosure_h)

        ctx = ResolverContext(
            outline=outline,
            enclosure=enclosure,
            base_h=enclosure_h,
            ceil_start=ceil_start,
            cavity_depth=ceil_start - CAVITY_START_MM,
            blended_height_fn=lambda _x, _y, _o, e: e.height_mm,  # type: ignore[arg-type]
        )

        block_x = plate_x + plate_w - pad - bw
        block_y = plate_y + pad
        cx = block_x + bw / 2
        cy = block_y + bh / 2

        pin_positions: dict[str, tuple[float, float]] = {}
        pins: list[tuple[float, float, float]] = []
        for pin in comp.pins:
            px_rel, py_rel = float(pin.position_mm[0]), float(pin.position_mm[1])
            if rot:
                rad = math.radians(rot)
                cos_a, sin_a = math.cos(rad), math.sin(rad)
                px_rel, py_rel = px_rel * cos_a - py_rel * sin_a, px_rel * sin_a + py_rel * cos_a
            px = cx + px_rel
            py = cy + py_rel
            pin_positions[pin.id] = (px, py)
            hole_r = (pin.hole_diameter_mm + PINHOLE_CLEARANCE) / 2
            pins.append((px, py, hole_r))

        placed = PlacedComponent(
            instance_id=comp.id,
            catalog_id=comp.id,
            x_mm=cx,
            y_mm=cy,
            rotation_deg=rot,
            pin_positions=pin_positions,
            mounting_style=comp.mounting.style,
        )

        fragments = resolve_component(placed, comp, ctx)

        frag_z_top = max(
            (f.z_base + f.depth for f in fragments if f.z_base >= 0),
            default=body_top,
        )
        block_z_top = min(max(frag_z_top, body_top), ceil_start)

        layouts.append(_CompLayout(
            catalog=comp, cx=cx, cy=cy,
            block_x=block_x, block_y=block_y,
            block_w=bw, block_h=bh,
            block_z_top=block_z_top,
            pins=pins,
            fragments=fragments,
            plate_x=plate_x, plate_y=plate_y,
            plate_w=plate_w, plate_h=plate_h,
        ))
        y_cursor += plate_h + _COMP_GAP

    return layouts


def _pinhole_gcode(
    pdef: PrinterDef,
    fdef: FilamentDef,
    pad: float,
    z: float = 0.2,
    feed: float = 1200,
) -> tuple[str, list[_CompLayout]]:
    """Generate G-code for multi-component cube-trace test.

    Each component sits on its own plate with traces extending left.

    Returns (gcode_str, layouts).
    """
    layouts = _compute_component_layout(pdef, pad, z)

    nozzle_temp = int(fdef.overrides.get("first_layer_temperature",
                      fdef.overrides.get("temperature", "215")))
    bed_temp = int(fdef.overrides.get("first_layer_bed_temperature",
                   fdef.overrides.get("bed_temperature", "40")))

    nozzle_d = 0.4
    filament_d = 1.75
    extrusion_w = nozzle_d * 1.125
    filament_area = math.pi * (filament_d / 2) ** 2
    e_per_mm = (z * extrusion_w) / filament_area
    iron_spacing = 0.1
    iron_flow = 0.05

    nom_w = pdef.nominal_bed_width
    nom_d = pdef.nominal_bed_depth

    bw_i, bd_i = int(nom_w), int(nom_d)
    comp_names = ", ".join(ly.catalog.id for ly in layouts)
    lines = [
        "; Cube-trace test - separate plates per component",
        f"; Printer: {pdef.label}  Filament: {fdef.label}",
        f"; bed {nom_w}x{nom_d}  pad {pad}",
        f"; components: {comp_names}",
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
    cur_z = 0.0

    # ── Shared G-code helpers ──

    def _rect_perimeter_infill_iron(
        x0: float, y0: float, w: float, h: float,
        do_iron: bool = False,
    ) -> None:
        nonlocal e
        x1, y1 = x0 + w, y0 + h
        e -= 0.8
        lines.append(f"G1 E{e:.4f} F2400 ; retract")
        lines.append(f"G1 Z{cur_z + _Z_HOP:.2f} F720 ; z-hop")
        lines.append(f"G1 X{x0:.3f} Y{y0:.3f} F3000 ; travel")
        lines.append(f"G1 Z{cur_z:.2f} F720 ; lower")
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
            x_pos += extrusion_w
        if do_iron:
            iron_epmm = e_per_mm * iron_flow
            lines.append(";TYPE:Ironing")
            e -= 0.8
            lines.append(f"G1 E{e:.4f} F2400 ; retract")
            lines.append(f"G1 Z{cur_z + _Z_HOP:.2f} F720 ; z-hop")
            lines.append(f"G1 X{ix1:.3f} Y{iy0:.3f} F3000")
            lines.append(f"G1 Z{cur_z:.2f} F720 ; lower")
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

    cut_hw = SCAD_TRACE_WIDTH / 2

    def _iron_trace_channels() -> None:
        nonlocal e
        iron_epmm = e_per_mm * iron_flow
        lines.append(";TYPE:Ironing - trace channels")
        for ly in layouts:
            for pin_x, pin_y, _hr in ly.pins:
                y_lo = pin_y - cut_hw
                y_hi = pin_y + cut_hw
                x_lo = ly.plate_x
                x_hi = pin_x
                if x_hi <= x_lo:
                    continue
                e -= 0.8
                lines.append(f"G1 E{e:.4f} F2400 ; retract")
                lines.append(f"G1 Z{cur_z + _Z_HOP:.2f} F720 ; z-hop")
                lines.append(f"G1 X{x_hi:.3f} Y{y_lo:.3f} F3000")
                lines.append(f"G1 Z{cur_z:.2f} F720 ; lower")
                e += 0.8
                lines.append(f"G1 E{e:.4f} F2400 ; unretract")
                x_pos = x_hi
                going_down = True
                while x_pos >= x_lo - 0.001:
                    e += (y_hi - y_lo) * iron_epmm
                    if going_down:
                        lines.append(f"G1 X{x_pos:.3f} Y{y_hi:.3f} E{e:.5f} F900")
                    else:
                        lines.append(f"G1 X{x_pos:.3f} Y{y_lo:.3f} E{e:.5f} F900")
                    going_down = not going_down
                    x_pos -= iron_spacing
                    if x_pos >= x_lo - 0.001:
                        e += iron_spacing * iron_epmm
                        lines.append(f"G1 X{x_pos:.3f} E{e:.5f} F900")

    def _frag_y_excl(
        frag: ScadFragment, x: float,
    ) -> tuple[float, float] | None:
        geom = frag.geometry
        s = max(frag.taper_scale, 1.0) if frag.taper_scale > 0 else 1.0
        if isinstance(geom, RectGeometry):
            hw = geom.width / 2 * s
            if abs(x - geom.cx) <= hw:
                hh = geom.height / 2 * s
                return (geom.cy - hh, geom.cy + hh)
        elif isinstance(geom, CylinderGeometry):
            r = geom.r * s
            dx = x - geom.cx
            if abs(dx) <= r:
                hc = math.sqrt(r ** 2 - dx ** 2)
                return (geom.cy - hc, geom.cy + hc)
        elif isinstance(geom, PolygonGeometry):
            xs = [p[0] for p in geom.points]
            if min(xs) <= x <= max(xs):
                ys = [p[1] for p in geom.points]
                return (min(ys), max(ys))
        elif isinstance(geom, (SegmentGeometry, CapsuleGeometry)):
            if isinstance(geom, SegmentGeometry):
                poly = geom.to_polygon()
                xs = [p[0] for p in poly]
                if min(xs) <= x <= max(xs):
                    ys = [p[1] for p in poly]
                    return (min(ys), max(ys))
            else:
                xmin = min(geom.x1 - geom.r1, geom.x2 - geom.r2)
                xmax = max(geom.x1 + geom.r1, geom.x2 + geom.r2)
                if xmin <= x <= xmax:
                    ymin = min(geom.y1 - geom.r1, geom.y2 - geom.r2)
                    ymax = max(geom.y1 + geom.r1, geom.y2 + geom.r2)
                return (ymin, ymax)
        return None

    def _trace_excl_at(
        ly: _CompLayout, x: float,
    ) -> list[tuple[float, float]]:
        raw: list[tuple[float, float]] = []
        for pin_x, pin_y, _hr in ly.pins:
            if x < ly.plate_x - 0.001 or x > pin_x + 0.001:
                continue
            raw.append((pin_y - cut_hw, pin_y + cut_hw))
        return raw

    trace_z_top = FLOOR_MM + TRACE_HEIGHT_MM

    def _fragment_excl_at(
        ly: _CompLayout, x: float, z_layer: float,
    ) -> list[tuple[float, float]]:
        raw: list[tuple[float, float]] = []
        if z_layer <= trace_z_top + 0.001:
            raw.extend(_trace_excl_at(ly, x))
        for frag in ly.fragments:
            if frag.z_base > z_layer or z_layer > frag.z_base + frag.depth:
                continue
            excl = _frag_y_excl(frag, x)
            if excl is not None:
                raw.append(excl)
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

    def _wall_edge(x_a: float, y_a: float, x_b: float, y_b: float,
                   ly: _CompLayout, z_layer: float) -> None:
        nonlocal e
        is_vertical = abs(x_a - x_b) < 0.001
        if is_vertical and z_layer <= trace_z_top + 0.001:
            excls = _trace_excl_at(ly, x_a)
            if excls:
                lo, hi = min(y_a, y_b), max(y_a, y_b)
                segs = _split_y(lo, hi, excls)
                going_up = y_b > y_a
                ordered = segs if going_up else list(reversed(segs))
                for s in ordered:
                    sa, sb = (s[0], s[1]) if going_up else (s[1], s[0])
                    e -= 0.8
                    lines.append(f"G1 E{e:.4f} F2400 ; retract")
                    lines.append(f"G1 Z{z_layer + _Z_HOP:.2f} F720 ; z-hop")
                    lines.append(f"G1 X{x_a:.3f} Y{sa:.3f} F3000")
                    lines.append(f"G1 Z{z_layer:.2f} F720 ; lower")
                    e += 0.8
                    lines.append(f"G1 E{e:.4f} F2400 ; unretract")
                    e += abs(sb - sa) * e_per_mm
                    lines.append(f"G1 X{x_a:.3f} Y{sb:.3f} E{e:.4f} F{feed}")
                return
        dist = math.hypot(x_b - x_a, y_b - y_a)
        e += dist * e_per_mm
        lines.append(f"G1 X{x_b:.3f} Y{y_b:.3f} E{e:.4f} F{feed}")

    n_walls = 3

    def _block_with_fragments(ly: _CompLayout, z_layer: float) -> None:
        nonlocal e
        x0, y0 = ly.block_x, ly.block_y
        w, h = ly.block_w, ly.block_h
        x1, y1 = x0 + w, y0 + h

        for wall in range(n_walls):
            offset = extrusion_w * wall
            wx0 = x0 + offset
            wy0 = y0 + offset
            wx1 = x1 - offset
            wy1 = y1 - offset

            e -= 0.8
            lines.append(f"G1 E{e:.4f} F2400 ; retract")
            lines.append(f"G1 Z{z_layer + _Z_HOP:.2f} F720 ; z-hop")
            lines.append(f"G1 X{wx0:.3f} Y{wy0:.3f} F3000 ; travel")
            lines.append(f"G1 Z{z_layer:.2f} F720 ; lower")
            e += 0.8
            lines.append(f"G1 E{e:.4f} F2400 ; unretract")
            corners = [(wx1, wy0), (wx1, wy1), (wx0, wy1), (wx0, wy0)]
            prev = (wx0, wy0)
            for nx, ny in corners:
                _wall_edge(prev[0], prev[1], nx, ny, ly, z_layer)
                prev = (nx, ny)

        inset = n_walls * extrusion_w - extrusion_w / 2
        ix0, iy0 = x0 + inset, y0 + inset
        ix1, iy1 = x1 - inset, y1 - inset
        x_pos = ix0
        going_up = True
        while x_pos <= ix1 + 0.001:
            excls = _fragment_excl_at(ly, x_pos, z_layer)
            segs = _split_y(iy0, iy1, excls)
            ordered = segs if going_up else list(reversed(segs))
            for s in ordered:
                sy_a, sy_b = (s[0], s[1]) if going_up else (s[1], s[0])
                e -= 0.8
                lines.append(f"G1 E{e:.4f} F2400 ; retract")
                lines.append(f"G1 Z{z_layer + _Z_HOP:.2f} F720 ; z-hop")
                lines.append(f"G1 X{x_pos:.3f} Y{sy_a:.3f} F3000")
                lines.append(f"G1 Z{z_layer:.2f} F720 ; lower")
                e += 0.8
                lines.append(f"G1 E{e:.4f} F2400 ; unretract")
                e += abs(sy_b - sy_a) * e_per_mm
                lines.append(f"G1 X{x_pos:.3f} Y{sy_b:.3f} E{e:.4f} F{feed}")
            going_up = not going_up
            x_pos += extrusion_w

    # ── Print the plate bases (one per component) ──
    plate_layers = max(1, round(FLOOR_MM / z))
    for pl in range(1, plate_layers + 1):
        lz = z * pl
        cur_z = lz
        lines.append(";LAYER_CHANGE")
        lines.append(f";Z:{lz:.1f}")
        lines.append(f"G1 Z{lz:.2f} F600")
        for ly in layouts:
            _rect_perimeter_infill_iron(ly.plate_x, ly.plate_y, ly.plate_w, ly.plate_h)
    _iron_trace_channels()

    # ── Print raised blocks ──
    max_block_z_top = max(ly.block_z_top for ly in layouts)
    max_block_layers = max(1, round((max_block_z_top - FLOOR_MM) / z))
    for layer in range(1, max_block_layers + 1):
        lz = z * (plate_layers + layer)
        cur_z = lz
        lines.append(";LAYER_CHANGE")
        lines.append(f";Z:{lz:.1f}")
        lines.append(f"G1 Z{lz:.2f} F600")
        for ly in layouts:
            if lz > ly.block_z_top + 0.001:
                continue
            _block_with_fragments(ly, lz)

    e -= 4.0
    lines.append(f"G1 E{e:.4f} F3000 ; retract")
    lines += [
        "",
        "G91 ; relative positioning",
        "G1 Z1 F1000 ; lift head before pause",
        "G90 ; absolute positioning",
        "",
        "G1 X0 Y0 F3000 ; move to home",
        "",
        "G91 ; relative positioning",
        "G1 Z-1 F1000 ; lower head back down",
        "G90 ; absolute positioning",
        "",
        "M0 ; pause before silver ink",
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

    return "\n".join(lines), layouts


def _pinhole_bitmap(
    pdef: PrinterDef,
    grid: SweepGrid,
    layouts: list[_CompLayout],
) -> str:
    """Generate bitmap with traces for all component pins."""
    px = grid.pixel_size_mm
    cols = grid.data_cols
    rows = grid.data_rows
    trace_width_nozzles = max(1, int(round(SCAD_TRACE_WIDTH / px)))
    half_trace = trace_width_nozzles // 2

    ink_cells: set[tuple[int, int]] = set()

    for ly in layouts:
        for pin_x, pin_y, _hr in ly.pins:
            bx0, by = grid.bed_to_bitmap(ly.plate_x, pin_y)
            bx1, _ = grid.bed_to_bitmap(pin_x, pin_y)

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
async def generate_pinhole(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
    padding: float = Query(5),
) -> dict[str, Any]:
    """Generate G-code + bitmap for multi-component cube-trace test."""
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)

    gcode, layouts = _pinhole_gcode(pdef, fdef, padding)
    bitmap = _pinhole_bitmap(pdef, grid, layouts)

    bb_x = min(ly.plate_x for ly in layouts)
    bb_y = min(ly.plate_y for ly in layouts)
    bb_x2 = max(ly.plate_x + ly.plate_w for ly in layouts)
    bb_y2 = max(ly.plate_y + ly.plate_h for ly in layouts)

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=bb_x,
        part_origin_y_mm=bb_y,
        part_width_mm=bb_x2 - bb_x,
        part_depth_mm=bb_y2 - bb_y,
        gcode_file="pinhole.gcode",
        bitmap_file="pinhole_bitmap.txt",
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

    reps = 3
    gap = pad
    total_h = 3 * rect_h + 2 * gap
    total_w = reps * rect_w + (reps - 1) * gap
    y_base = (nom_d - total_h) / 2
    x_left = abs(pdef.inkjet_offset_x) + pad

    corners = []
    for rep in range(reps):
        x_off = x_left + rep * (rect_w + gap)
        for row in range(3):
            corners.append((x_off, y_base + row * (rect_h + gap)))

    bw_i, bd_i = int(nom_w), int(nom_d)
    lines = [
        f"; Progressive-trace test - 3x{reps} rectangles",
        f"; Printer: {pdef.label}  Filament: {fdef.label}",
        f"; bed {nom_w}x{nom_d}  pad {pad}",
        f"; rect {rect_w}x{rect_h} mm x {reps} reps, {layers} layers, ironing on top",
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
            lines.append(f"G1 Z{lz + _Z_HOP:.2f} F720 ; z-hop")
            lines.append(f"G1 X{x0:.3f} Y{y0:.3f} F3000")
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
                lines.append(f"G1 Z{lz + _Z_HOP:.2f} F720 ; z-hop")
                lines.append(f"G1 X{ix1:.3f} Y{iy0:.3f} F3000")
                lines.append(f"G1 Z{lz:.2f} F720 ; lower")
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

    e -= 4.0
    lines.append(f"G1 E{e:.4f} F3000 ; retract")
    lines += [
        "",
        "G91 ; relative positioning",
        "G1 Z1 F1000 ; lift head before pause",
        "G90 ; absolute positioning",
        "",
        "G1 X0 Y0 F3000 ; move to home",
        "",
        "G91 ; relative positioning",
        "G1 Z-1 F1000 ; lower head back down",
        "G90 ; absolute positioning",
        "",
        "M0 ; pause before silver ink",
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

    reps = 3
    nom_d = pdef.nominal_bed_depth
    gap = pad
    total_h = 3 * rect_h + 2 * gap
    y_base = (nom_d - total_h) / 2
    x_left = abs(pdef.inkjet_offset_x) + pad

    corners_bed: list[tuple[float, float, int]] = []
    for rep in range(reps):
        x_off = x_left + rep * (rect_w + gap)
        for row in range(3):
            corners_bed.append((x_off, y_base + row * (rect_h + gap), row))

    ink_cells: set[tuple[int, int]] = set()

    for bed_x, bed_y, row in corners_bed:
        if row >= trace_count:
            continue
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

    reps = 3
    gap = padding
    total_h = 3 * rect_height + 2 * gap
    total_w = reps * rect_width + (reps - 1) * gap
    part_origin_x = abs(pdef.inkjet_offset_x) + padding
    part_origin_y = (pdef.nominal_bed_depth - total_h) / 2

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=part_origin_x,
        part_origin_y_mm=part_origin_y,
        part_width_mm=total_w,
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
            lines.append(f"G1 Z{lz + _Z_HOP:.2f} F720 ; z-hop")
            lines.append(f"G1 X{x0:.3f} Y{y0:.3f} F3000")
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
                lines.append(f"G1 Z{lz + _Z_HOP:.2f} F720 ; z-hop")
                lines.append(f"G1 X{ix1:.3f} Y{iy0:.3f} F3000")
                lines.append(f"G1 Z{lz:.2f} F720 ; lower")
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

    e -= 4.0
    lines.append(f"G1 E{e:.4f} F3000 ; retract")
    lines += [
        "",
        "G91 ; relative positioning",
        "G1 Z1 F1000 ; lift head before pause",
        "G90 ; absolute positioning",
        "",
        "G1 X0 Y0 F3000 ; move to home",
        "",
        "G91 ; relative positioning",
        "G1 Z-1 F1000 ; lower head back down",
        "G90 ; absolute positioning",
        "",
        "M0 ; pause before silver ink",
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
        lines.append(f"G1 Z{lz + _Z_HOP:.2f} F720 ; z-hop")
        lines.append(f"G1 X{x0:.3f} Y{y0:.3f} F3000")
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
            lines.append(f"G1 Z{lz + _Z_HOP:.2f} F720 ; z-hop")
            lines.append(f"G1 X{ix1:.3f} Y{iy0:.3f} F3000")
            lines.append(f"G1 Z{lz:.2f} F720 ; lower")
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

    e -= 4.0
    lines.append(f"G1 E{e:.4f} F3000 ; retract")
    lines += [
        "",
        "G91 ; relative positioning",
        "G1 Z1 F1000 ; lift head before pause",
        "G90 ; absolute positioning",
        "",
        "G1 X0 Y0 F3000 ; move to home",
        "",
        "G91 ; relative positioning",
        "G1 Z-1 F1000 ; lower head back down",
        "G90 ; absolute positioning",
        "",
        "M0 ; pause before silver ink",
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
    for width in [5, 10, 15, 20, 25, 30]:
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

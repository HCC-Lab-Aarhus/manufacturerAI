from __future__ import annotations

import math
import re
import tempfile
from pathlib import Path
from typing import Any
from dataclasses import dataclass

from fastapi import APIRouter, Query

from src.catalog.loader import load_catalog, get_component
from src.catalog.models import Component
from src.pipeline.config import (
    get_printer, PrinterDef, SweepGrid, sweep_grid,
    component_z_range, FLOOR_MM, CAVITY_START_MM, CEILING_MM, TRACE_HEIGHT_MM,
)
from src.pipeline.gcode.filaments import get_filament
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
from src.pipeline.scad.compiler import compile_scad
from src.pipeline.gcode.slicer import slice_stl
from src.pipeline.gcode.postprocessor import (
    postprocess_gcode, _segment_near_traces, _MOVE_RE,
)

from ._common import (
    DEBUG_CONFIG, load_slicer_params, render_bitmap,
    _PROFILES_DIR, DEBUG_OVERRIDE,
)

PINHOLE_TAPER_D: float = 3.5

router = APIRouter()

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
class CompLayout:
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


def compute_component_layout(
    pdef: PrinterDef,
    pad: float,
    z: float = 0.2,
    y_start: float | None = None,
    x_start: float | None = None,
) -> list[CompLayout]:
    """Compute layout for catalog components, each on its own plate.

    Uses the real pipeline's component_z_range() for Z heights and
    resolve_component() for SCAD cutout fragments (body pockets, pin
    holes, pin bridges, SCAD features, hatches).

    Plates are stacked vertically and centred horizontally on the bed.
    The battery is rotated 90° so its two pins separate in Y and the
    traces extending left to the plate edge don't collide.

    Returns a list of CompLayout, each with its own plate coordinates.
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
    y_cursor = y_start if y_start is not None else (nom_d - total_plate_h) / 2

    layouts: list[CompLayout] = []
    for comp, bw, bh, body_top, rot, enclosure_h in block_infos:
        plate_w = _TRACE_RUN + bw + 2 * pad
        plate_h = bh + 2 * pad
        plate_x = x_start if x_start is not None else (nom_w - plate_w) / 2
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

        layouts.append(CompLayout(
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


def frag_scad_lines(frag: ScadFragment) -> list[str]:
    """Convert a single ScadFragment to OpenSCAD code lines.

    Handles all geometry types including tapered (pin funnels) and
    tilted (3-D rotated) variants — mirrors the dispatch logic in the
    pipeline's emit.py but without the Y-mirror wrapper.
    """
    _EPS = 0.001
    g = frag.geometry

    if frag.taper_scale > 0:
        scale = frag.taper_scale
        if isinstance(g, CylinderGeometry):
            r_top = g.r * scale
            return [
                f"translate([{g.cx:.3f}, {g.cy:.3f}, {frag.z_base - _EPS:.3f}])",
                f"  cylinder(h = {frag.depth + 2 * _EPS:.3f}, "
                f"r1 = {g.r + _EPS:.3f}, r2 = {r_top + _EPS:.3f});",
            ]
        if isinstance(g, RectGeometry):
            return [
                f"translate([{g.cx:.3f}, {g.cy:.3f}, {frag.z_base - _EPS:.3f}])",
                f"  linear_extrude(height = {frag.depth + 2 * _EPS:.3f}, "
                f"scale = [{scale:.4f}, {scale:.4f}])",
                f"    square([{g.width + 2 * _EPS:.3f}, {g.height + 2 * _EPS:.3f}], "
                f"center = true);",
            ]

    if frag.tilt_deg or frag.rotate_3d:
        if frag.rotate_3d:
            rx, ry, rz = frag.rotate_3d
            z_center = frag.z_base
            length = frag.depth
        else:
            z_center = frag.z_base + frag.depth / 2
            length = frag.tilt_length
            rx, ry, rz = 0, frag.tilt_deg, frag.rotation_deg
        rot_str = f"rotate([{rx:.1f}, {ry:.1f}, {rz:.1f}])"

        if isinstance(g, CylinderGeometry):
            base_lines = [
                f"translate([{g.cx:.3f}, {g.cy:.3f}, {z_center:.3f}])",
                f"  {rot_str}",
                f"    cylinder(h = {length + 2 * _EPS:.3f}, "
                f"r = {g.r + _EPS:.3f}, center = true);",
            ]
        elif isinstance(g, RectGeometry):
            base_lines = [
                f"translate([{g.cx:.3f}, {g.cy:.3f}, {z_center:.3f}])",
                f"  {rot_str}",
                f"    linear_extrude(height = {length + 2 * _EPS:.3f}, center = true)",
                f"      square([{g.width + 2 * _EPS:.3f}, "
                f"{g.height + 2 * _EPS:.3f}], center = true);",
            ]
        else:
            return [f"// unsupported tilted geometry: {frag.label}"]

        if frag.clip_half:
            big = max(
                length,
                g.r if isinstance(g, CylinderGeometry) else max(g.width, g.height),
            ) + 10
            clip_z = z_center if frag.clip_half == "top" else z_center - big
            cx = g.cx if isinstance(g, (CylinderGeometry, RectGeometry)) else 0
            cy = g.cy if isinstance(g, (CylinderGeometry, RectGeometry)) else 0
            return [
                "intersection() {",
                *[f"  {l}" for l in base_lines],
                f"  translate([{cx - big:.3f}, {cy - big:.3f}, {clip_z:.3f}])",
                f"    cube([{2 * big:.3f}, {2 * big:.3f}, {big:.3f}]);",
                "}",
            ]
        return base_lines

    if isinstance(g, CylinderGeometry):
        return [
            f"translate([{g.cx:.3f}, {g.cy:.3f}, {frag.z_base - _EPS:.3f}])",
            f"  cylinder(h = {frag.depth + 2 * _EPS:.3f}, r = {g.r + _EPS:.3f});",
        ]

    if isinstance(g, RectGeometry):
        pts = g.to_polygon()
    elif isinstance(g, SegmentGeometry):
        pts = g.to_polygon()
    elif isinstance(g, PolygonGeometry):
        pts = g.points
    elif isinstance(g, CapsuleGeometry):
        return [
            f"translate([0, 0, {frag.z_base - _EPS:.3f}])",
            f"  linear_extrude(height = {frag.depth + 2 * _EPS:.3f})",
            f"    hull() {{",
            f"      translate([{g.x1:.3f}, {g.y1:.3f}]) circle(r = {g.r1:.3f});",
            f"      translate([{g.x2:.3f}, {g.y2:.3f}]) circle(r = {g.r2:.3f});",
            f"    }}",
        ]
    else:
        return [f"// unsupported geometry: {frag.label}"]

    if pts and len(pts) >= 3:
        pts_str = ", ".join(f"[{p[0]:.3f}, {p[1]:.3f}]" for p in pts)
        return [
            f"translate([0, 0, {frag.z_base - _EPS:.3f}])",
            f"  linear_extrude(height = {frag.depth + 2 * _EPS:.3f})",
            f"    polygon(points = [{pts_str}]);",
        ]
    return [f"// empty fragment: {frag.label}"]


def _build_components_scad(
    layouts: list[CompLayout],
    fragments: list[ScadFragment],
    plate_z: float,
) -> str:
    """Build OpenSCAD source for the component debug test.

    Creates rectangular plates and blocks as the body, then subtracts
    all fragment cutouts (pin holes, body pockets, trace channels, etc.)
    using a ``difference()`` CSG operation.  No Y-mirror is applied so
    that the resulting STL coordinates match bed / bitmap coordinates.
    """
    lines = [
        "// manufacturerAI — debug component test (auto-generated)",
        "$fn = 32;",
        "",
    ]

    cutouts = [f for f in fragments if f.type == "cutout"]
    additions = [f for f in fragments if f.type == "addition"]

    if cutouts:
        lines.append("difference() {")
        lines.append("  union() {")
        indent = "    "
    elif additions:
        lines.append("union() {")
        indent = "  "
    else:
        indent = ""

    for ly in layouts:
        lines.append(f"{indent}translate([{ly.plate_x:.3f}, {ly.plate_y:.3f}, 0])")
        lines.append(f"{indent}  cube([{ly.plate_w:.3f}, {ly.plate_h:.3f}, {plate_z:.3f}]);")
        lines.append(f"{indent}translate([{ly.block_x:.3f}, {ly.block_y:.3f}, 0])")
        lines.append(f"{indent}  cube([{ly.block_w:.3f}, {ly.block_h:.3f}, {ly.block_z_top:.3f}]);")

    for a in additions:
        frag_lines = frag_scad_lines(a)
        lines.extend(f"{indent}{l}" for l in frag_lines)

    if cutouts or additions:
        lines.append("  }" if cutouts else "}")

    if cutouts:
        lines.append("")
        for c in cutouts:
            if c.label:
                lines.append(f"  // {c.label}")
            frag_lines = frag_scad_lines(c)
            lines.extend(f"  {l}" for l in frag_lines)
        lines.append("}")

    lines.append("")
    return "\n".join(lines)


def component_ink_cells(
    grid: SweepGrid,
    layouts: list[CompLayout],
) -> set[tuple[int, int]]:
    """Ink cells for component trace lines."""
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

    return ink_cells


def _pinhole_bitmap(
    pdef: PrinterDef,
    grid: SweepGrid,
    layouts: list[CompLayout],
) -> str:
    """Generate bitmap with traces for all component pins."""
    return render_bitmap(grid.data_rows, grid.data_cols, component_ink_cells(grid, layouts))


_Z_COMMENT = re.compile(r"^;Z:([\d.]+)")


def _keep_ironing_near_traces(
    gcode: str,
    trace_segs: list[tuple[float, float, float, float]],
    ink_z: float,
) -> str:
    """Keep ironing extrusion only near trace segments, suppress elsewhere.

    Inverse of the pipeline's ``_filter_ironing_at_ink_layer``: converts
    ironing extrusion moves that are NOT near any trace into travel
    moves (G0), so only the trace paths get ironed.
    """
    lines = gcode.splitlines()
    out: list[str] = []
    current_z = 0.0
    track_x, track_y = 0.0, 0.0
    i = 0

    while i < len(lines):
        line = lines[i]

        z_m = _Z_COMMENT.match(line)
        if z_m:
            current_z = float(z_m.group(1))

        m_pos = _MOVE_RE.match(line)
        if m_pos:
            if m_pos.group("x"):
                track_x = float(m_pos.group("x"))
            if m_pos.group("y"):
                track_y = float(m_pos.group("y"))

        if line.strip() == ";TYPE:Ironing" and abs(current_z - ink_z) < 0.05:
            out.append(line)
            i += 1
            cur_x, cur_y = track_x, track_y

            while i < len(lines):
                ln = lines[i]
                if ln.startswith(";TYPE:") or ln.startswith(";LAYER_CHANGE"):
                    break

                m = _MOVE_RE.match(ln)
                if m and (m.group("x") or m.group("y")):
                    nx = float(m.group("x")) if m.group("x") else cur_x
                    ny = float(m.group("y")) if m.group("y") else cur_y

                    if _segment_near_traces(cur_x, cur_y, nx, ny, trace_segs):
                        out.append(ln)
                    else:
                        coords = ""
                        if m.group("x"):
                            coords += f" X{m.group('x')}"
                        if m.group("y"):
                            coords += f" Y{m.group('y')}"
                        out.append(f"G0{coords} ; ironing suppressed (no trace)")

                    cur_x, cur_y = nx, ny
                else:
                    out.append(ln)
                    if m:
                        if m.group("x"):
                            cur_x = float(m.group("x"))
                        if m.group("y"):
                            cur_y = float(m.group("y"))
                i += 1
            continue

        out.append(line)
        i += 1

    return "\n".join(out)


@router.post("/components")
async def generate_components(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
) -> dict[str, Any]:
    """Generate G-code + bitmap for multi-component test.

    Uses the real pipeline flow: resolve_component() for cutout fragments,
    OpenSCAD for CSG compilation (pin holes, body pockets, trace channels),
    and PrusaSlicer for G-code generation.
    """
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)
    sp = load_slicer_params(printer)

    layouts = compute_component_layout(pdef, DEBUG_CONFIG.padding, sp.layer_height)
    bitmap = _pinhole_bitmap(pdef, grid, layouts)

    plate_z = FLOOR_MM

    all_frags: list[ScadFragment] = []
    trace_segs: list[tuple[float, float, float, float]] = []
    for ly in layouts:
        all_frags.extend(ly.fragments)
        for pin_x, pin_y, _hr in ly.pins:
            all_frags.append(ScadFragment(
                type="cutout",
                geometry=SegmentGeometry(
                    ly.plate_x, pin_y, pin_x, pin_y, SCAD_TRACE_WIDTH,
                ),
                z_base=FLOOR_MM,
                depth=TRACE_HEIGHT_MM,
                label=f"trace {ly.catalog.id}",
            ))
            trace_segs.append((ly.plate_x, pin_y, pin_x, pin_y))

    scad_src = _build_components_scad(layouts, all_frags, plate_z)

    bb_x = min(ly.plate_x for ly in layouts)
    bb_y = min(ly.plate_y for ly in layouts)
    bb_x2 = max(ly.plate_x + ly.plate_w for ly in layouts)
    bb_y2 = max(ly.plate_y + ly.plate_h for ly in layouts)
    center = ((bb_x + bb_x2) / 2, (bb_y + bb_y2) / 2)

    with tempfile.TemporaryDirectory(prefix="debug_comp_") as tmpdir:
        tmp = Path(tmpdir)
        scad_path = tmp / "components.scad"
        scad_path.write_text(scad_src, encoding="utf-8")

        ok, msg, stl_path = compile_scad(scad_path)
        if not ok:
            raise RuntimeError(f"OpenSCAD compilation failed: {msg}")

        comp_override = tmp / "components_ironing.ini"
        comp_override.write_text(
            "top_solid_layers = 3\n",
            encoding="utf-8",
        )
        overrides: list[Path] = []
        if DEBUG_OVERRIDE.exists():
            overrides.append(DEBUG_OVERRIDE)
        overrides.append(comp_override)

        slicer_gcode = tmp / "slicer_output.gcode"
        ok, msg, _ = slice_stl(
            stl_path,
            output_gcode=slicer_gcode,
            printer=printer,
            filament=fdef.id,
            center=center,
            extra_overrides=overrides,
        )
        if not ok:
            raise RuntimeError(f"PrusaSlicer failed: {msg}")

        final_gcode = tmp / "components.gcode"
        postprocess_gcode(
            gcode_path=slicer_gcode,
            output_path=final_gcode,
            ink_z=FLOOR_MM,
            trace_segments=trace_segs,
        )

        gcode = _keep_ironing_near_traces(
            final_gcode.read_text(encoding="utf-8"),
            trace_segs, FLOOR_MM,
        )

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=bb_x,
        part_origin_y_mm=bb_y,
        part_width_mm=bb_x2 - bb_x,
        part_depth_mm=bb_y2 - bb_y,
        gcode_file="components.gcode",
        bitmap_file="components.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        "bitmap": bitmap,
        "contract": manifest.to_dict(),
    }

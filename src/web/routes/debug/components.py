from __future__ import annotations

import math
from pathlib import Path
from typing import Any
from dataclasses import dataclass

from fastapi import APIRouter, Query

from src.catalog.loader import load_catalog, get_component
from src.catalog.models import Component
from src.pipeline.config import (
    get_printer, PrinterDef, bed_bitmap,
    component_z_range, FLOOR_MM, CAVITY_START_MM, CEILING_MM, TRACE_HEIGHT_MM,
)
from src.pipeline.placer.models import PlacedComponent
from src.pipeline.design.models import Outline, Enclosure, OutlineVertex
from src.pipeline.scad.resolver import (
    ResolverContext, resolve_component,
)
from src.pipeline.scad.fragment import (
    ScadFragment, RectGeometry, CylinderGeometry,
    PolygonGeometry, SegmentGeometry, CapsuleGeometry,
)
from src.pipeline.config import TRACE_RULES
from src.pipeline.pin_geometry import pin_shaft_dimensions

from ._common import DEBUG_CONFIG, load_slicer_params, run_debug_pipeline

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
    pins: list[tuple[float, float, float, str]]
    fragments: list[ScadFragment]
    plate_x: float
    plate_y: float
    plate_w: float
    plate_h: float
    used_pin_ids: set[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.used_pin_ids is None:
            self.used_pin_ids = {pid for *_, pid in self.pins}


def _select_used_pins(
    comp: Component,
    pin_positions: dict[str, tuple[float, float]],
    plate_x: float,
) -> set[str]:
    grouped_pins: set[str] = set()
    used: set[str] = set()

    for pg in comp.pin_groups or []:
        if not pg.allocatable:
            continue
        grouped_pins.update(pg.pin_ids)
        best_pin = min(
            pg.pin_ids,
            key=lambda pid: abs(pin_positions[pid][0] - plate_x),
        )
        used.add(best_pin)

    for pin in comp.pins:
        if pin.id not in grouped_pins:
            used.add(pin.id)

    return used


def compute_component_layout(
    pdef: PrinterDef,
    pad: float,
    z: float = 0.2,
    y_start: float | None = None,
    x_start: float | None = None,
) -> list[CompLayout]:
    cat = load_catalog()

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

    y_cursor = y_start if y_start is not None else 0.0

    layouts: list[CompLayout] = []
    for comp, bw, bh, body_top, rot, enclosure_h in block_infos:
        plate_w = _TRACE_RUN + bw + 2 * pad
        plate_h = bh + 2 * pad
        plate_x = x_start if x_start is not None else 0.0
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
        pins: list[tuple[float, float, float, str]] = []
        for pin in comp.pins:
            px_rel, py_rel = float(pin.position_mm[0]), float(pin.position_mm[1])
            if rot:
                rad = math.radians(rot)
                cos_a, sin_a = math.cos(rad), math.sin(rad)
                px_rel, py_rel = px_rel * cos_a - py_rel * sin_a, px_rel * sin_a + py_rel * cos_a
            px = cx + px_rel
            py = cy + py_rel
            pin_positions[pin.id] = (px, py)
            shaft_w, shaft_h = pin_shaft_dimensions(pin)
            hole_r = max(shaft_w, shaft_h) / 2
            pins.append((px, py, hole_r, pin.id))

        used_pin_ids = _select_used_pins(comp, pin_positions, plate_x)

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
            used_pin_ids=used_pin_ids,
        ))
        y_cursor += plate_h + _COMP_GAP

    return layouts


def frag_scad_lines(frag: ScadFragment) -> list[str]:
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
    lines = [
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


@router.post("/components")
async def generate_components(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
) -> dict[str, Any]:
    pdef = get_printer(printer)
    sp = load_slicer_params(printer)

    layouts = compute_component_layout(pdef, DEBUG_CONFIG.padding, sp.layer_height)

    plate_z = FLOOR_MM

    all_frags: list[ScadFragment] = []
    trace_paths: list[list[tuple[float, float]]] = []
    for ly in layouts:
        all_frags.extend(ly.fragments)
        for pin_x, pin_y, _hr, pin_id in ly.pins:
            if pin_id not in ly.used_pin_ids:
                continue
            all_frags.append(ScadFragment(
                type="cutout",
                geometry=SegmentGeometry(
                    ly.plate_x, pin_y, pin_x, pin_y, TRACE_RULES.trace_width_mm,
                ),
                z_base=FLOOR_MM,
                depth=TRACE_HEIGHT_MM,
                label=f"trace {ly.catalog.id}",
            ))
            trace_paths.append([(ly.plate_x, pin_y), (pin_x, pin_y)])

    scad_src = _build_components_scad(layouts, all_frags, plate_z)

    bb_x = min(ly.plate_x for ly in layouts)
    bb_y = min(ly.plate_y for ly in layouts)
    bb_x2 = max(ly.plate_x + ly.plate_w for ly in layouts)
    bb_y2 = max(ly.plate_y + ly.plate_h for ly in layouts)
    model_center = ((bb_x + bb_x2) / 2, (bb_y + bb_y2) / 2)

    max_z = max(ly.block_z_top for ly in layouts)
    shell_height = max(max_z, FLOOR_MM + CEILING_MM)

    return run_debug_pipeline(
        scad_src, trace_paths, model_center,
        printer, filament,
        shell_height=shell_height,
        extra_overrides=["top_solid_layers = 3\n"],
    )

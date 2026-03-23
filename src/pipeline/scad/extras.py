"""extras.py — generate extra printable parts placed on the build plate.

Components can declare extra parts (via mounting.extras) that are printed
alongside the enclosure on the same build plate.  Each extra is a separate
SCAD body translated to a free area next to the enclosure.

Parts are described by shape + dimensions and rendered generically.
The only special case is shape="button", which delegates to the complex
button generator (socket + stem + cap with surface curvature).
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass

from src.catalog.models import Component, ExtraPart
from src.pipeline.design.models import Outline, Enclosure
from src.pipeline.placer.models import PlacedComponent

from .buttons import (
    build_button_configs,
    generate_button_scad,
    ButtonConfig,
)

log = logging.getLogger(__name__)

PLATE_SPACING: float = 5.0
PART_GAP: float = 3.0


@dataclass
class PlacedExtra:
    """An extra part ready to emit, with its SCAD lines and footprint size."""
    label: str
    scad_lines: list[str]
    footprint_x: float
    footprint_y: float
    preamble: list[str] | None = None


def _resolve_dimensions(extra: ExtraPart, cat: Component) -> ExtraPart:
    """Fill in any missing dimensions from the component's body."""
    w = extra.width_mm or cat.body.width_mm
    l = extra.length_mm or cat.body.length_mm
    d = extra.diameter_mm or cat.body.diameter_mm
    t = extra.thickness_mm
    return ExtraPart(
        label=extra.label,
        shape=extra.shape,
        width_mm=w,
        length_mm=l,
        thickness_mm=t or 1.5,
        diameter_mm=d,
    )


def _generate_shape_scad(extra: ExtraPart) -> PlacedExtra:
    """Generate SCAD for a simple extruded shape (rect or circle)."""
    t = extra.thickness_mm or 1.5
    lines: list[str] = []

    if extra.shape == "circle":
        d = extra.diameter_mm or 10.0
        lines.append(f"linear_extrude(height = {t:.3f})")
        lines.append(f"  circle(d = {d:.3f}, $fn = 64);")
        return PlacedExtra(
            label=extra.label,
            scad_lines=lines,
            footprint_x=d,
            footprint_y=d,
        )

    w = extra.width_mm or 10.0
    l = extra.length_mm or 10.0
    lines.append(f"linear_extrude(height = {t:.3f})")
    lines.append(f"  square([{w:.3f}, {l:.3f}], center = true);")
    return PlacedExtra(
        label=extra.label,
        scad_lines=lines,
        footprint_x=w,
        footprint_y=l,
    )


LOOP_WIDTH: float = 3.0
LOOP_HEIGHT: float = 8.0
LOOP_THICKNESS: float = 1.2
HOOK_HEIGHT: float = 1.5
HOOK_DEPTH: float = 1.5
TAB_WIDTH: float = 8.0
TAB_DEPTH: float = 2.0
TAB_HEIGHT: float = 1.5


def _generate_hatch_scad(extra: ExtraPart) -> PlacedExtra:
    """Generate SCAD for a snap-fit hatch panel with spring latch and ledge tab."""
    w = extra.width_mm or 24.4
    l = extra.length_mm or 47.4
    t = extra.thickness_mm or 1.5

    slit_w = LOOP_WIDTH + 1.0
    arm_gap = LOOP_THICKNESS * 2
    spring_depth = LOOP_THICKNESS * 2 + arm_gap
    bend_r = arm_gap / 2 + LOOP_THICKNESS / 2

    preamble = [
        f"hatch_w = {w:.3f};",
        f"hatch_l = {l:.3f};",
        f"hatch_t = {t:.3f};",
        f"loop_w = {LOOP_WIDTH:.3f};",
        f"loop_h = {LOOP_HEIGHT:.3f};",
        f"loop_t = {LOOP_THICKNESS:.3f};",
        f"hook_h = {HOOK_HEIGHT:.3f};",
        f"hook_d = {HOOK_DEPTH:.3f};",
        f"slit_w = {slit_w:.3f};",
        f"arm_gap = {arm_gap:.3f};",
        f"bend_r = {bend_r:.3f};",
        "",
        "module spring_latch() {",
        "  cube([loop_w, loop_t, loop_h]);",
        "  translate([0, -hook_d, 2])",
        "    cube([loop_w, hook_d + loop_t, hook_h]);",
        "  translate([loop_w/2, loop_t + arm_gap/2, loop_h])",
        "    rotate([90, 0, 90])",
        "      rotate_extrude(angle=180, $fn=32)",
        "        translate([bend_r, 0, 0])",
        "          square([loop_t, loop_w], center=true);",
        "  translate([0, loop_t + arm_gap, 0])",
        "    cube([loop_w, loop_t, loop_h]);",
        "}",
    ]

    lines = [
        "difference() {",
        "  cube([hatch_w, hatch_l, hatch_t]);",
        f"  translate([(hatch_w - slit_w) / 2, -hook_d - 1 + 2, -1])",
        f"    cube([slit_w, {spring_depth:.3f} + hook_d, hatch_t + 2]);",
        "}",
        "",
        "translate([(hatch_w - loop_w) / 2, 2, 0])",
        "  spring_latch();",
        "",
        f"translate([(hatch_w - {TAB_WIDTH:.3f}) / 2, hatch_l - 1, hatch_t])",
        f"  cube([{TAB_WIDTH:.3f}, {TAB_DEPTH:.3f}, {TAB_HEIGHT:.3f}]);",
    ]

    return PlacedExtra(
        label=extra.label,
        scad_lines=lines,
        footprint_x=w,
        footprint_y=l + LOOP_HEIGHT,
        preamble=preamble,
    )


def collect_and_generate_extras(
    components: list[PlacedComponent],
    catalog_index: dict[str, Component],
    outline: Outline,
    enclosure: Enclosure,
    ceil_start: float,
    flat_pts: list[list[float]],
) -> str:
    """Collect all extra parts and generate positioned SCAD.

    Extra parts are placed in a row to the right of the enclosure.
    Returns a SCAD string to append to the main enclosure output.
    """
    extras: list[PlacedExtra] = []

    button_configs = build_button_configs(
        components, catalog_index, outline, enclosure, ceil_start,
    )
    btn_cfg_map: dict[str, ButtonConfig] = {
        cfg.instance_id: cfg for cfg in button_configs
    }

    for comp in components:
        cat = catalog_index.get(comp.catalog_id)
        if cat is None:
            continue

        for extra in cat.mounting.extras:
            if extra.shape == "button":
                cfg = btn_cfg_map.get(comp.instance_id)
                if cfg is None:
                    continue
                btn_lines = generate_button_scad(cfg)
                if cfg.outline:
                    radius = max(math.hypot(p[0], p[1]) for p in cfg.outline)
                else:
                    radius = 5.0
                diameter = radius * 2
                extras.append(PlacedExtra(
                    label=extra.label,
                    scad_lines=btn_lines,
                    footprint_x=diameter,
                    footprint_y=diameter,
                ))
            elif extra.shape == "hatch":
                resolved = _resolve_dimensions(extra, cat)
                extras.append(_generate_hatch_scad(resolved))
            else:
                resolved = _resolve_dimensions(extra, cat)
                extras.append(_generate_shape_scad(resolved))

    if not extras:
        return ""

    enc_max_x = max(p[0] for p in flat_pts)
    enc_min_y = min(p[1] for p in flat_pts)

    parts: list[str] = []
    parts.append("")
    parts.append("// ============================================================")
    parts.append("// Extra parts — printed on the build plate next to the enclosure")
    parts.append("// ============================================================")
    parts.append("")

    for extra in extras:
        if extra.preamble:
            parts.extend(extra.preamble)
            parts.append("")

    current_x = enc_max_x + PLATE_SPACING

    for extra in extras:
        place_x = current_x + extra.footprint_x / 2
        place_y = enc_min_y

        parts.append(f"// {extra.label}")
        parts.append(f"translate([{place_x:.3f}, {place_y:.3f}, 0]) {{")
        for line in extra.scad_lines:
            parts.append(f"  {line}")
        parts.append("}")
        parts.append("")

        current_x = place_x + extra.footprint_x / 2 + PART_GAP

    log.info("Extra parts: %d generated", len(extras))
    return "\n".join(parts)

"""
Shell SCAD generation — solid extrusion of the outline polygon with
optional polygon cutouts subtracted from the body.

The cutout mechanism is intentionally generic: hand it a list of
``Cutout`` objects (each a 2-D polygon, a depth, and a z-base) and
they are subtracted from the solid via a single ``difference()`` block.
This keeps the OpenSCAD output fast (only ``polygon`` + ``linear_extrude``,
no ``hull()``, no high-``$fn`` cylinders) and trivially extensible —
any part of the pipeline can append cutouts without touching SCAD logic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from src.config.hardware import hw

# Default extrusion height (mm): floor + cavity + ceiling.
DEFAULT_HEIGHT_MM = hw.shell_height + hw.floor_thickness + hw.ceil_thickness


# ── Cutout data type ────────────────────────────────────────────────


@dataclass
class Cutout:
    """A 2-D polygon extruded and subtracted from the shell.

    Attributes
    ----------
    polygon : list of [x, y] vertices in mm (CCW winding preferred).
    depth   : extrusion height of the cut, in mm.
    z_base  : z-coordinate where the cut starts (0 = bottom of shell).
              Set *z_base = height - depth* to carve from the top.
    label   : optional comment emitted in the SCAD source.
    """

    polygon: list[list[float]]
    depth: float
    z_base: float = 0.0
    label: str = ""


# ── helpers ─────────────────────────────────────────────────────────


def _fmt_poly(pts: list[list[float]]) -> str:
    """Format polygon vertices for an OpenSCAD ``polygon()`` call."""
    return ", ".join(f"[{x:.3f}, {y:.3f}]" for x, y in pts)


# ── SCAD generators ────────────────────────────────────────────────


# ── rounded-top helpers ─────────────────────────────────────────────

_CURVE_STEPS = 10  # number of stacked layers for the fillet profile


def _body_lines(
    pts_var: str,
    h: float,
    curve_length: float,
    curve_height: float,
    indent: str = "",
    bottom_curve_length: float = 0.0,
    bottom_curve_height: float = 0.0,
) -> list[str]:
    """Return SCAD lines for the shell body.

    When *curve_length* and *curve_height* are both > 0 the top edge is
    rounded with a quarter-circle fillet profile (``1 − cos``).  The
    fillet is built from stacked ``linear_extrude`` layers — each layer
    is the outline polygon shrunk by ``offset(r = -inset)`` and extruded
    to the height of that profile step.  This preserves concave features
    in the outline (unlike ``hull()``, which computes a *convex* hull
    and fills in concavities).

    Similarly, *bottom_curve_length* / *bottom_curve_height* round the
    bottom edge using the same stacked-layer approach, mirrored.

    With 10 layers over a typical 8 mm curve height each step is
    ~0.8 mm — below typical 0.4 mm FDM nozzle width, and keeps
    OpenSCAD compile time well under the 180 s timeout.
    """
    has_top = curve_length > 0 and curve_height > 0
    has_bottom = bottom_curve_length > 0 and bottom_curve_height > 0

    if not has_top and not has_bottom:
        return [
            f"{indent}linear_extrude(height = {h:.3f})",
            f"{indent}    polygon(points = {pts_var});",
        ]

    # Use fewer steps when both curves are active to keep compile
    # time under the 300 s timeout (each layer adds CSG complexity).
    if has_top and has_bottom:
        bottom_steps = 5
        top_steps = 5
    else:
        bottom_steps = _CURVE_STEPS
        top_steps = _CURVE_STEPS

    # ── Bottom fillet profile ──────────────────────────────────────
    # Same elliptical quarter-arc as the top, mirrored vertically.
    # θ sweeps from π/2 → 0 so z increases from 0 → bottom_curve_height.
    #   inset(θ) = bottom_curve_length · (1 − cos θ)
    #   z(θ)     = bottom_curve_height · (1 − sin θ)
    # At z=0  (bottom): inset = bcl, horizontal tangent (flush with bottom face)
    # At z=bch (top):   inset = 0,   vertical tangent  (flush with wall)
    bottom_profile: list[tuple[float, float]] = []
    if has_bottom:
        for i in range(bottom_steps + 1):
            theta = (1.0 - i / bottom_steps) * (math.pi / 2)
            inset = bottom_curve_length * (1.0 - math.cos(theta))
            z = bottom_curve_height * (1.0 - math.sin(theta))
            bottom_profile.append((z, inset))

    # ── Top fillet profile ─────────────────────────────────────────
    # Quarter-arc from z=h-curve_height (no inset) up to z=h (max inset).
    #   inset(θ) = curve_length · (1 − cos θ)
    #   z(θ)     = h_below + curve_height · sin θ
    h_below = h - curve_height if has_top else h
    top_profile: list[tuple[float, float]] = []
    if has_top:
        for i in range(top_steps + 1):
            theta = (i / top_steps) * (math.pi / 2)
            inset = curve_length * (1.0 - math.cos(theta))
            z = h_below + curve_height * math.sin(theta)
            top_profile.append((z, inset))

    # ── Straight wall zone ─────────────────────────────────────────
    wall_z0 = bottom_curve_height if has_bottom else 0.0
    wall_z1 = h_below if has_top else h
    wall_h = wall_z1 - wall_z0

    # ── Build SCAD lines ───────────────────────────────────────────
    zone_desc = []
    if has_bottom:
        zone_desc.append(f"bottom({bottom_curve_length:.2f}×{bottom_curve_height:.2f})")
    if has_top:
        zone_desc.append(f"top({curve_length:.2f}×{curve_height:.2f})")

    lines: list[str] = [
        f"{indent}// Shell body with rounded edges (stacked-layer fillet)",
        f"{indent}// {', '.join(zone_desc)}, bottom_steps={bottom_steps}, top_steps={top_steps}",
        f"{indent}union() {{",
    ]

    # Bottom fillet layers (delta offset — faster than r offset,
    # no rounded corners since the fillet is already discrete layers)
    if has_bottom:
        for i in range(bottom_steps):
            z0, inset0 = bottom_profile[i]
            z1, _inset1 = bottom_profile[i + 1]
            dz = z1 - z0
            lines += [
                f"{indent}    // bottom fillet layer {i}",
                f"{indent}    translate([0, 0, {z0:.3f}])",
                f"{indent}        linear_extrude(height = {dz:.3f})",
                f"{indent}            offset(delta = {-inset0:.4f})",
                f"{indent}                polygon(points = {pts_var});",
            ]

    # Straight wall between curves
    if wall_h > 0:
        lines += [
            f"{indent}    // Straight wall",
            f"{indent}    translate([0, 0, {wall_z0:.3f}])",
            f"{indent}        linear_extrude(height = {wall_h:.3f})",
            f"{indent}            polygon(points = {pts_var});",
        ]

    # Top fillet layers
    if has_top:
        for i in range(top_steps):
            z0, inset0 = top_profile[i]
            z1, _inset1 = top_profile[i + 1]
            dz = z1 - z0
            lines += [
                f"{indent}    // top fillet layer {i}",
                f"{indent}    translate([0, 0, {z0:.3f}])",
                f"{indent}        linear_extrude(height = {dz:.3f})",
                f"{indent}            offset(delta = {-inset0:.4f})",
                f"{indent}                polygon(points = {pts_var});",
            ]

    lines.append(f"{indent}}}")
    return lines


def generate_enclosure_scad(
    outline: list[list[float]],
    height: float | None = None,
    cutouts: list[Cutout] | None = None,
    top_curve_length: float = 0.0,
    top_curve_height: float = 0.0,
    bottom_curve_length: float = 0.0,
    bottom_curve_height: float = 0.0,
    **_kwargs,
) -> str:
    """Generate OpenSCAD for the solid enclosure shell.

    When *cutouts* is empty or ``None`` the output is a plain
    ``linear_extrude`` of the outline.  Otherwise a ``difference()``
    block subtracts every cutout polygon at its respective z / depth.

    Parameters
    ----------
    top_curve_length : float
        How far inward (mm) the rounded edge extends from the outer
        perimeter at the very top.  0 disables rounding.
    top_curve_height : float
        Vertical extent (mm) of the rounded zone measured down from
        the top of the shell.  0 disables rounding.
    bottom_curve_length : float
        How far inward (mm) the rounded edge extends from the outer
        perimeter at the very bottom.  0 disables rounding.
    bottom_curve_height : float
        Vertical extent (mm) of the rounded zone measured up from
        the bottom of the shell.  0 disables rounding.

    Extra keyword arguments are accepted (and ignored) for
    forward-compatibility.
    """
    h = height or DEFAULT_HEIGHT_MM
    pts_str = f"[{_fmt_poly(outline)}]"

    if not cutouts:
        lines: list[str] = [
            "// Auto-generated solid enclosure",
            f"// Height: {h:.1f} mm",
            "$fn = 16;",
            "",
            f"outline_pts = {pts_str};",
            "",
        ]
        lines += _body_lines(
            "outline_pts", h, top_curve_length, top_curve_height,
            bottom_curve_length=bottom_curve_length,
            bottom_curve_height=bottom_curve_height,
        )
        return "\n".join(lines) + "\n"

    lines = [
        "// Auto-generated enclosure with cutouts",
        f"// Height: {h:.1f} mm  —  {len(cutouts)} cutout(s)",
        "$fn = 16;",
        "",
        f"outline_pts = {pts_str};",
        "",
        "difference() {",
    ]
    lines += _body_lines(
        "outline_pts", h, top_curve_length, top_curve_height,
        indent="    ",
        bottom_curve_length=bottom_curve_length,
        bottom_curve_height=bottom_curve_height,
    )
    lines.append("")

    for i, c in enumerate(cutouts):
        tag = c.label or f"cutout_{i}"
        lines.append(f"    // [{i}] {tag}")
        lines.append(f"    translate([0, 0, {c.z_base:.3f}])")
        lines.append(f"        linear_extrude(height = {c.depth:.3f})")
        lines.append(f"            polygon(points = [{_fmt_poly(c.polygon)}]);")
        lines.append("")

    lines.append("}")
    return "\n".join(lines) + "\n"


def generate_battery_hatch_scad(**_kwargs) -> str:
    """Battery hatch cover with spring latch — matches the standard design.

    Accepts (and ignores) any keyword arguments for forward-compat.
    """
    enc = hw.enclosure
    bw = hw.battery["compartment_width_mm"]
    bh = hw.battery["compartment_height_mm"]
    clearance = enc["battery_hatch_clearance_mm"]
    thickness = enc["battery_hatch_thickness_mm"]
    hatch_w = bw - 2 * clearance
    hatch_h = bh - 2 * clearance

    loop_w = enc["spring_loop_width_mm"]
    loop_h = enc["spring_loop_height_mm"]
    loop_t = enc["spring_loop_thickness_mm"]

    return f"""\
// Battery Hatch with Spring Latch — standard design
// Generated by ManufacturerAI

// Parameters
hatch_width = {hatch_w:.3f};
hatch_height = {hatch_h:.3f};
hatch_thickness = {thickness:.3f};
lip_width = 1.5;
loop_width = {loop_w:.3f};
loop_height = {loop_h:.3f};
loop_thickness = {loop_t:.3f};
hook_height = 1.5;
hook_depth = 1.5;
slit_width = loop_width + 1.0;
slit_length = loop_height + 2.0;

$fn = 32;

module spring_latch() {{
    arm_gap = loop_thickness * 2;
    bend_radius = arm_gap / 2 + loop_thickness / 2;

    // Outward arm (goes up/away from hatch)
    translate([0, 0, 0])
        cube([loop_width, loop_thickness, loop_height]);

    // Hook base at tip of outward arm
    translate([0, -hook_depth, 2])
        cube([loop_width, hook_depth + loop_thickness, hook_height]);

    // Curved top connecting two arms
    translate([loop_width/2, loop_thickness + arm_gap/2, loop_height])
        rotate([90, 0, 90])
            rotate_extrude(angle=180, $fn=32)
                translate([bend_radius, 0, 0])
                    square([loop_thickness, loop_width], center=true);

    // Return arm (comes back toward plate)
    translate([0, loop_thickness + arm_gap, 0])
        cube([loop_width, loop_thickness, loop_height]);
}}

module battery_hatch() {{
    arm_gap = loop_thickness * 2;
    spring_total_depth = loop_thickness * 2 + arm_gap;

    difference() {{
        // Main hatch body
        cube([hatch_width, hatch_height, hatch_thickness]);

        // Slit cutout for spring to flex through
        translate([(hatch_width - slit_width) / 2, -hook_depth - 1 + 2, -1])
            cube([slit_width, spring_total_depth + hook_depth, hatch_thickness + 2]);
    }}

    // Spring latch — 2mm back from edge
    translate([(hatch_width - loop_width) / 2, 2, 0])
        spring_latch();

    // Ledge notch on opposite end
    translate([(hatch_width - 8) / 2, hatch_height - 1, hatch_thickness])
        cube([8, 2, 1.5]);
}}

battery_hatch();
"""


def generate_print_plate_scad(**_kwargs) -> str:
    """Print plate that references the enclosure and hatch STLs side-by-side.

    Accepts (and ignores) any keyword arguments for forward-compat.
    """
    return """\
// Print plate — all parts laid out for printing
import("enclosure.stl");
translate([80, 0, 0]) import("battery_hatch.stl");
"""


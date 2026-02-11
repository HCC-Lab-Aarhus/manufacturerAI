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

_CURVE_STEPS = 10  # number of hull segments for the smooth fillet


def _body_lines(
    pts_var: str,
    h: float,
    curve_length: float,
    curve_height: float,
    indent: str = "",
) -> list[str]:
    """Return SCAD lines for the shell body.

    When *curve_length* and *curve_height* are both > 0 the top edge is
    rounded with a quarter-circle fillet profile (``1 − cos``).  Adjacent
    profile slices are connected with ``hull()`` so the surface is
    perfectly smooth — no stair-stepping.  The profile is tangent to the
    vertical wall at its base and tangent to the horizontal top surface
    at its peak, giving C1 continuity at both ends.
    """
    if curve_length <= 0 or curve_height <= 0:
        return [
            f"{indent}linear_extrude(height = {h:.3f})",
            f"{indent}    polygon(points = {pts_var});",
        ]

    h_below = h - curve_height
    steps = _CURVE_STEPS

    # Build profile control points using an elliptical quarter-arc
    # parametrized by sweep angle θ ∈ [0, π/2].
    #
    #   inset(θ) = curve_length · (1 − cos θ)
    #   z(θ)     = h_below + curve_height · sin θ
    #
    # At θ=0:   inset=0, dz/dθ>0, d(inset)/dz=0  → vertical  (flush with wall)
    # At θ=π/2: inset=curve_length, d(inset)/dz→∞ → horizontal (flush with top)
    #
    # This guarantees C1 (tangent) continuity at both ends of the fillet
    # regardless of height, curve_length, or curve_height values.
    profile: list[tuple[float, float]] = []
    for i in range(steps + 1):
        theta = (i / steps) * (math.pi / 2)
        inset = curve_length * (1.0 - math.cos(theta))
        z = h_below + curve_height * math.sin(theta)
        profile.append((z, inset))

    lines: list[str] = [
        f"{indent}// Shell body with rounded top edge (smooth hull-based fillet)",
        f"{indent}// curve_length = {curve_length:.2f} mm, "
        f"curve_height = {curve_height:.2f} mm",
        f"{indent}union() {{",
        f"{indent}    // Straight wall below curve zone",
        f"{indent}    linear_extrude(height = {h_below:.3f})",
        f"{indent}        polygon(points = {pts_var});",
    ]

    for i in range(steps):
        z0, inset0 = profile[i]
        z1, inset1 = profile[i + 1]
        lines += [
            f"{indent}    // fillet segment {i}",
            f"{indent}    hull() {{",
            f"{indent}        translate([0, 0, {z0:.3f}])",
            f"{indent}            linear_extrude(height = 0.001)",
            f"{indent}                offset(r = {-inset0:.4f})",
            f"{indent}                    polygon(points = {pts_var});",
            f"{indent}        translate([0, 0, {z1:.3f}])",
            f"{indent}            linear_extrude(height = 0.001)",
            f"{indent}                offset(r = {-inset1:.4f})",
            f"{indent}                    polygon(points = {pts_var});",
            f"{indent}    }}",
        ]

    lines.append(f"{indent}}}")
    return lines


def generate_enclosure_scad(
    outline: list[list[float]],
    height: float | None = None,
    cutouts: list[Cutout] | None = None,
    top_curve_length: float = 0.0,
    top_curve_height: float = 0.0,
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

    Extra keyword arguments are accepted (and ignored) for
    forward-compatibility.
    """
    h = height or DEFAULT_HEIGHT_MM
    pts_str = f"[{_fmt_poly(outline)}]"

    has_curve = top_curve_length > 0 and top_curve_height > 0

    if not cutouts:
        lines: list[str] = [
            "// Auto-generated solid enclosure",
            f"// Height: {h:.1f} mm",
            "$fn = 32;",
            "",
            f"outline_pts = {pts_str};",
            "",
        ]
        lines += _body_lines("outline_pts", h, top_curve_length, top_curve_height)
        return "\n".join(lines) + "\n"

    lines = [
        "// Auto-generated enclosure with cutouts",
        f"// Height: {h:.1f} mm  —  {len(cutouts)} cutout(s)",
        "$fn = 32;",
        "",
        f"outline_pts = {pts_str};",
        "",
        "difference() {",
    ]
    lines += _body_lines("outline_pts", h, top_curve_length, top_curve_height, indent="    ")
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


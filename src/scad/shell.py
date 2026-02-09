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


def generate_enclosure_scad(
    outline: list[list[float]],
    height: float | None = None,
    cutouts: list[Cutout] | None = None,
    **_kwargs,
) -> str:
    """Generate OpenSCAD for the solid enclosure shell.

    When *cutouts* is empty or ``None`` the output is a plain
    ``linear_extrude`` of the outline.  Otherwise a ``difference()``
    block subtracts every cutout polygon at its respective z / depth.

    Extra keyword arguments are accepted (and ignored) for
    forward-compatibility.
    """
    h = height or DEFAULT_HEIGHT_MM

    if not cutouts:
        pts = _fmt_poly(outline)
        return (
            f"// Auto-generated solid enclosure\n"
            f"// Height: {h:.1f} mm\n"
            f"$fn = 32;\n\n"
            f"linear_extrude(height = {h:.3f})\n"
            f"    polygon(points = [{pts}]);\n"
        )

    lines: list[str] = [
        "// Auto-generated enclosure with cutouts",
        f"// Height: {h:.1f} mm  —  {len(cutouts)} cutout(s)",
        "$fn = 32;",
        "",
        "difference() {",
        f"    linear_extrude(height = {h:.3f})",
        f"        polygon(points = [{_fmt_poly(outline)}]);",
        "",
    ]

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
    """Battery hatch cover — placeholder solid rectangle.

    Accepts (and ignores) any keyword arguments for forward-compat.
    """
    enc = hw.enclosure
    bw = hw.battery["compartment_width_mm"]
    bh = hw.battery["compartment_height_mm"]
    clearance = enc["battery_hatch_clearance_mm"]
    thickness = enc["battery_hatch_thickness_mm"]
    hatch_w = bw - 2 * clearance
    hatch_h = bh - 2 * clearance

    return f"""\
// Battery hatch cover (placeholder)
$fn = 32;
cube([{hatch_w:.3f}, {hatch_h:.3f}, {thickness:.3f}]);
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


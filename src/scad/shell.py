"""
Shell SCAD generation — produces a solid extrusion of the 2D outline polygon.

For now this generates a single solid body from the designer's outline.
Future iterations will subtract negatives for pins, components, trace
channels, and the battery compartment.
"""

from __future__ import annotations
from src.config.hardware import hw

# Default extrusion height (mm).  This is the full enclosure height:
# bottom floor + internal cavity + top ceiling.
DEFAULT_HEIGHT_MM = hw.shell_height + hw.floor_thickness + hw.ceil_thickness


def generate_enclosure_scad(
    outline: list[list[float]],
    height: float | None = None,
    **_kwargs,
) -> str:
    """
    Generate OpenSCAD for a solid extrusion of *outline* at a fixed height.

    Extra keyword arguments are accepted (and ignored) so that callers
    written for the future detailed version don't break.
    """
    h = height or DEFAULT_HEIGHT_MM
    pts = ", ".join(f"[{x:.3f}, {y:.3f}]" for x, y in outline)

    return f"""\
// Auto-generated solid enclosure from outline polygon
// Height: {h:.1f} mm
$fn = 32;

linear_extrude(height = {h:.3f})
    polygon(points = [{pts}]);
"""


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


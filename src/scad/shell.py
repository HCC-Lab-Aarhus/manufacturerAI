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

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from shapely.geometry import MultiPolygon, Polygon as ShapelyPolygon
from shapely.ops import unary_union
from src.config.hardware import hw

log = logging.getLogger(__name__)

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

_CURVE_STEPS = 15  # number of stacked layers for the fillet profile


def _inset_polygon(outline: list[list[float]], inset: float) -> str | None:
    """Shrink *outline* inward by *inset* mm using Shapely.

    Returns the formatted points string for an OpenSCAD ``polygon()``,
    or ``None`` if the inset collapses the polygon entirely.
    """
    if inset <= 0:
        return _fmt_poly(outline)
    poly = ShapelyPolygon(outline)
    shrunk = poly.buffer(-inset, join_style="mitre", mitre_limit=5.0)
    if shrunk.is_empty:
        return None
    # buffer() can return a MultiPolygon; take the largest piece
    if shrunk.geom_type == "MultiPolygon":
        shrunk = max(shrunk.geoms, key=lambda g: g.area)
    coords = list(shrunk.exterior.coords)[:-1]  # drop closing duplicate
    return ", ".join(f"[{x:.3f}, {y:.3f}]" for x, y in coords)


def _body_lines(
    pts_var: str,
    h: float,
    curve_length: float,
    curve_height: float,
    indent: str = "",
    bottom_curve_length: float = 0.0,
    bottom_curve_height: float = 0.0,
    outline: list[list[float]] | None = None,
) -> list[str]:
    """Return SCAD lines for the shell body.

    When *curve_length* and *curve_height* are both > 0 the top edge is
    rounded with a quarter-circle fillet profile (``1 − cos``).  The
    fillet is built from stacked ``linear_extrude`` layers — each layer
    is a pre-computed inset polygon (computed in Python via Shapely)
    extruded to the height of that profile step.

    Pre-computing polygon insets in Python (instead of using OpenSCAD's
    ``offset()``) eliminates the most expensive CGAL operation and lets
    us use many more layers for a smoother profile without meaningful
    compile-time cost.

    When *outline* is provided the inset polygons are pre-computed in
    Python; otherwise falls back to ``offset(delta=...)`` in SCAD.
    """
    has_top = curve_length > 0 and curve_height > 0
    has_bottom = bottom_curve_length > 0 and bottom_curve_height > 0

    if not has_top and not has_bottom:
        return [
            f"{indent}linear_extrude(height = {h:.3f})",
            f"{indent}    polygon(points = {pts_var});",
        ]

    # With pre-computed insets we can afford many more steps
    use_precomputed = outline is not None
    if has_top and has_bottom:
        bottom_steps = _CURVE_STEPS if use_precomputed else 5
        top_steps = _CURVE_STEPS if use_precomputed else 5
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

    # Bottom fillet layers (pre-computed inset polygons when available,
    # otherwise fallback to offset(delta=...) in SCAD)
    if has_bottom:
        for i in range(bottom_steps):
            z0, inset0 = bottom_profile[i]
            z1, _inset1 = bottom_profile[i + 1]
            dz = z1 - z0
            if use_precomputed:
                pts_str = _inset_polygon(outline, inset0)
                if pts_str is None:
                    continue  # polygon collapsed at this inset
                lines += [
                    f"{indent}    // bottom fillet layer {i}",
                    f"{indent}    translate([0, 0, {z0:.3f}])",
                    f"{indent}        linear_extrude(height = {dz:.3f})",
                    f"{indent}            polygon(points = [{pts_str}]);",
                ]
            else:
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
            if use_precomputed:
                pts_str = _inset_polygon(outline, inset0)
                if pts_str is None:
                    continue
                lines += [
                    f"{indent}    // top fillet layer {i}",
                    f"{indent}    translate([0, 0, {z0:.3f}])",
                    f"{indent}        linear_extrude(height = {dz:.3f})",
                    f"{indent}            polygon(points = [{pts_str}]);",
                ]
            else:
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
            outline=outline,
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
        outline=outline,
    )
    lines.append("")

    # ── Merge cutouts that share the same (z_base, depth) ─────────
    # Instead of emitting 150+ individual ``difference()`` children
    # (which makes OpenSCAD's CGAL backend extremely slow), we group
    # cutouts by their extrusion parameters and merge the 2-D
    # polygons with Shapely's ``unary_union``.  This typically
    # collapses ~160 operations down to ~5-10.
    #
    # After merging, cutouts are clipped to the enclosure outline to
    # prevent component pockets from punching through the outer walls.
    groups: dict[tuple[float, float], list[Cutout]] = defaultdict(list)
    for c in cutouts:
        groups[(round(c.z_base, 4), round(c.depth, 4))].append(c)

    # Build outline polygon for clipping (once, outside the loop)
    outline_clip_poly: ShapelyPolygon | None = None
    if len(outline) >= 3:
        try:
            outline_clip_poly = ShapelyPolygon(outline)
            if not outline_clip_poly.is_valid or outline_clip_poly.is_empty:
                outline_clip_poly = None
        except Exception:
            outline_clip_poly = None

    group_idx = 0
    for (z_base, depth), members in groups.items():
        labels = ", ".join(m.label for m in members if m.label)
        count = len(members)

        # Build Shapely polygons for all members in this group
        shapely_polys = []
        for m in members:
            if len(m.polygon) >= 3:
                try:
                    sp = ShapelyPolygon(m.polygon)
                    if sp.is_valid and not sp.is_empty:
                        shapely_polys.append(sp)
                except Exception:
                    pass

        if not shapely_polys:
            continue

        merged = unary_union(shapely_polys)

        # Clip merged cutouts to the outline boundary to prevent
        # component pockets from punching through enclosure walls.
        if outline_clip_poly is not None and not merged.is_empty:
            try:
                merged = merged.intersection(outline_clip_poly)
            except Exception:
                pass  # Keep unclipped if intersection fails

        # Extract polygon(s) from the merged result
        if merged.is_empty:
            continue

        polys: list[ShapelyPolygon] = []
        if isinstance(merged, ShapelyPolygon):
            polys = [merged]
        elif isinstance(merged, MultiPolygon):
            polys = list(merged.geoms)
        else:
            # GeometryCollection or unexpected — skip
            log.warning("Unexpected Shapely result type %s for group z=%.2f d=%.2f",
                        type(merged).__name__, z_base, depth)
            continue

        # Emit ONE polygon() per group using OpenSCAD's multi-path
        # syntax.  This collapses all cutouts in this z/depth group
        # into a single difference() child — the key CSG speedup.
        all_pts: list[tuple[float, float]] = []
        paths: list[list[int]] = []
        for poly in polys:
            exterior = list(poly.exterior.coords)[:-1]
            start = len(all_pts)
            all_pts.extend(exterior)
            paths.append(list(range(start, start + len(exterior))))
            for hole in poly.interiors:
                hole_coords = list(hole.coords)[:-1]
                h_start = len(all_pts)
                all_pts.extend(hole_coords)
                paths.append(list(range(h_start, h_start + len(hole_coords))))

        tag = f"group {group_idx}: {count} cutouts at z={z_base:.1f} d={depth:.1f}"
        if labels:
            tag += f" ({labels[:80]})"
        lines.append(f"    // {tag}")
        lines.append(f"    translate([0, 0, {z_base:.3f}])")
        lines.append(f"        linear_extrude(height = {depth:.3f})")
        pts_str = _fmt_poly(all_pts)
        paths_str = ", ".join(
            "[" + ", ".join(str(i) for i in p) + "]" for p in paths
        )
        lines.append(f"            polygon(points = [{pts_str}], paths = [{paths_str}]);")
        lines.append("")
        group_idx += 1

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


def generate_button_cap_scad(
    button_id: str,
    shape_outline: list[list[float]],
) -> str:
    """Generate a standalone SCAD file for a custom button cap.

    The button is printed **upside-down** (flat cap face on the build plate,
    snap socket pointing up).  It has:

    * A flat cap body whose outline matches *shape_outline*.
    * A rectangular stem connecting the cap to the snap socket.
    * A 4-wall snap-fit socket that grips the tactile switch head.

    The switch head is a two-step box (wider top, narrower bottom).
    The socket has a **seat** section (wider, for the head top) near the
    stem and a **grip** section (narrower) near the entry.  Diagonal
    corner flex-slots let each wall flex independently during assembly,
    creating a reliable snap-fit that the head top cannot escape through.
    A short entry chamfer eases insertion.

    Parameters
    ----------
    button_id : str
        Component ID (e.g. ``"btn_1"``), used in SCAD comments.
    shape_outline : list of [x, y]
        Polygon vertices in mm, centered at (0, 0), CCW winding.
    """
    bc = hw.button_cap
    cap_t   = bc.get("cap_thickness_mm", 2.0)
    stem_w  = bc.get("stem_width_mm", 3.0)
    stem_h  = bc.get("stem_height_mm", 5.3)
    wall_t  = bc.get("clip_thickness_mm", 0.8)
    head_top    = bc.get("switch_head_top_mm", 1.5)
    head_bot    = bc.get("switch_head_bottom_mm", 1.2)
    head_top_h  = bc.get("switch_head_top_height_mm", 1.0)
    head_bot_h  = bc.get("switch_head_bottom_height_mm", 1.0)

    # ── Snap-socket derived dimensions ────────────────────────────
    # Seat: wider cavity where head_top rests (near stem, low Z in print)
    seat_clr = 0.15  # per-side clearance in the seat
    # Grip: narrow band that catches behind head_top (near entry, high Z)
    grip_clr = 0.05  # per-side clearance — snug fit on head_bottom
    seat_inner = head_top + 2 * seat_clr   # 1.80 mm
    grip_inner = head_bot + 2 * grip_clr   # 1.30 mm
    socket_outer = seat_inner + 2 * wall_t  # 3.40 mm
    socket_h = head_top_h + head_bot_h      # 2.00 mm
    slot_w  = 0.3   # diagonal corner flex-slot width
    chamfer_h = 0.4  # entry chamfer height

    # Effective retention per wall: (seat_inner - grip_inner)/2 = 0.25 mm
    # Deflection for assembly: (head_top - grip_inner)/2 = 0.10 mm per wall

    # Format polygon for OpenSCAD
    pts = ", ".join(f"[{x:.3f}, {y:.3f}]" for x, y in shape_outline)

    return f"""\
// Custom Button Cap — {button_id}
// Generated by ManufacturerAI
// Printed upside-down: cap face on build plate, snap socket pointing UP.
//
// Socket layout (in print Z, bottom → top):
//   z = base_z .. base_z + head_top_h      → SEAT  (wider, receives head top)
//   z = base_z + head_top_h .. + socket_h  → GRIP  (narrower, retains head)
//   z = base_z + socket_h .. + chamfer_h   → CHAMFER (flared entry)
//
// When assembled (flipped), the entry is the lower opening.
// The switch head top (wider) snaps past the grip into the seat.

$fn = 32;

cap_thickness  = {cap_t:.3f};
stem_width     = {stem_w:.3f};
stem_height    = {stem_h:.3f};
wall_thickness = {wall_t:.3f};
socket_outer   = {socket_outer:.3f};
seat_inner     = {seat_inner:.3f};
grip_inner     = {grip_inner:.3f};
head_top_h     = {head_top_h:.3f};
head_bot_h     = {head_bot_h:.3f};
socket_h       = {socket_h:.3f};
slot_w         = {slot_w:.3f};
chamfer_h      = {chamfer_h:.3f};

module cap_body() {{
    // Flat cap — the visible surface the user presses.
    linear_extrude(height = cap_thickness)
        polygon(points = [{pts}]);
}}

module stem() {{
    // Rectangular stem connecting cap body to snap socket.
    translate([-stem_width/2, -stem_width/2, cap_thickness])
        cube([stem_width, stem_width, stem_height]);
}}

module snap_socket() {{
    // 4-wall snap-fit socket with stepped cavity + entry chamfer.
    // Corner flex-slots let walls deflect during snap-on assembly.
    base_z  = cap_thickness + stem_height;
    total_h = socket_h + chamfer_h;

    difference() {{
        // ── Outer block ──
        translate([-socket_outer/2, -socket_outer/2, base_z])
            cube([socket_outer, socket_outer, total_h]);

        // ── Seat cavity (wider, for head_top) ──
        translate([-seat_inner/2, -seat_inner/2, base_z - 0.01])
            cube([seat_inner, seat_inner, head_top_h + 0.01]);

        // ── Grip cavity (narrower, retains head_top) ──
        translate([-grip_inner/2, -grip_inner/2, base_z + head_top_h])
            cube([grip_inner, grip_inner, head_bot_h]);

        // ── Entry chamfer — tapers from grip_inner to seat_inner ──
        translate([0, 0, base_z + socket_h])
            linear_extrude(height = chamfer_h + 0.01,
                           scale  = seat_inner / grip_inner)
                square([grip_inner, grip_inner], center = true);

        // ── Corner flex-slots (4 diagonal cuts at 45°) ──
        // Each slot lets one wall segment flex independently
        // during assembly, reducing the snap-on insertion force.
        for (a = [45, 135, 225, 315])
            rotate([0, 0, a])
                translate([-slot_w/2, 0, base_z - 0.01])
                    cube([slot_w, socket_outer, total_h + 0.02]);
    }}
}}

module button_cap() {{
    cap_body();
    stem();
    snap_socket();
}}

button_cap();
"""


def generate_print_plate_scad(button_caps: list[str] | None = None, **_kwargs) -> str:
    """Print plate that references the enclosure and hatch STLs side-by-side.

    Parameters
    ----------
    button_caps : list of str, optional
        Filenames of additional button cap STL files to include on the
        plate (e.g. ``["button_btn_1.stl", "button_btn_2.stl"]``).
    """
    lines = [
        "// Print plate — all parts laid out for printing",
        'import("enclosure.stl");',
        'translate([80, 0, 0]) import("battery_hatch.stl");',
    ]
    if button_caps:
        x_offset = 120.0  # start button caps at X=120
        for i, cap_file in enumerate(button_caps):
            x = x_offset + i * 25.0
            lines.append(f'translate([{x:.1f}, 0, 0]) import("{cap_file}");')
    lines.append("")
    return "\n".join(lines)


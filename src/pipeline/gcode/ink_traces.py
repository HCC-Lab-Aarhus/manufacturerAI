"""Generate G-code toolpaths for conductive ink deposition.

Converts trace routing data (paths already in world-mm from the Python
router) into G-code move commands that lay down conductive ink along
each net.  The ink is deposited on the freshly ironed floor surface
at a fixed Z.

The output is a list of G-code lines that can be injected into the
main print G-code by the post-processor.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Sequence

from shapely.geometry import LineString, MultiLineString, Polygon

from src.pipeline.config import FDM_EXTRUSION_W, TRACE_RULES
from src.pipeline.scad.resolver import PINHOLE_CLEARANCE

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PinHoleRect:
    """Axis-aligned rectangular pin hole exclusion zone (world coords)."""
    cx: float
    cy: float
    half_w: float
    half_h: float

# ── Ink deposition defaults ────────────────────────────────────────

INK_TRAVEL_SPEED = 3000    # mm/min — rapid move to trace start
INK_DRAW_SPEED = 300       # mm/min — slow linear move while dispensing
INK_Z_HOP = 1.0            # mm — lift between traces to avoid dragging


def generate_ink_gcode(
    routing_result: dict,
    ink_z: float,
    *,
    draw_speed: float = INK_DRAW_SPEED,
    travel_speed: float = INK_TRAVEL_SPEED,
    z_hop: float = INK_Z_HOP,
) -> list[str]:
    """Build G-code lines for conductive ink along each routed trace.

    Parameters
    ----------
    routing_result : dict
        The ``routing.json`` data.  Each trace has ``net_id`` (str)
        and ``path`` (list of ``[x_mm, y_mm]`` waypoints already in mm).
    ink_z : float
        Z-height (mm) at which to deposit ink (top of ironed floor).
    draw_speed : float
        Feed rate (mm/min) while dispensing ink.
    travel_speed : float
        Feed rate (mm/min) for rapid non-dispensing moves.
    z_hop : float
        Z lift (mm) between traces.

    Returns
    -------
    list[str]
        G-code lines (without trailing newlines).
    """
    traces = routing_result.get("traces", [])
    if not traces:
        return ["; INK: no traces to deposit"]

    lines: list[str] = []
    lines.append("")
    lines.append("; " + "=" * 50)
    lines.append("; CONDUCTIVE INK DEPOSITION")
    lines.append(f"; Z = {ink_z:.2f} mm — {len(traces)} traces")
    lines.append("; " + "=" * 50)
    lines.append("")

    # Lift to safe height before starting ink pass
    # Retract filament first to prevent ooze during long travels
    lines.append("G1 E-0.80000 F2700 ; retract before ink travels")
    lines.append(f"G0 Z{ink_z + z_hop:.3f} F{travel_speed}")

    for trace in traces:
        net = trace.get("net_id", "unknown")
        path = trace.get("path", [])
        if len(path) < 2:
            continue

        simplified = _simplify_path(path)
        if len(simplified) < 2:
            continue

        start_x, start_y = simplified[0][0], simplified[0][1]

        lines.append(f"")
        lines.append(f"; --- trace: {net} ({len(simplified)} points) ---")

        # Rapid to start position (lifted)
        lines.append(f"G0 Z{ink_z + z_hop:.3f} F{travel_speed}")
        lines.append(f"G0 X{start_x:.3f} Y{start_y:.3f} F{travel_speed}")

        # Lower to ink Z
        lines.append(f"G0 Z{ink_z:.3f} F1000")

        # Trace the path
        for pt in simplified[1:]:
            x, y = pt[0], pt[1]
            lines.append(f"G1 X{x:.3f} Y{y:.3f} F{draw_speed}")

        # Lift after trace
        lines.append(f"G0 Z{ink_z + z_hop:.3f} F1000")

    lines.append("")
    lines.append("; Unretract — restore filament state before next M601 pause")
    lines.append("G1 E0.80000 F1500 ; unretract after ink travels")
    lines.append("")
    lines.append("; " + "=" * 50)
    lines.append("; END CONDUCTIVE INK")
    lines.append("; " + "=" * 50)
    lines.append("")

    log.info("Generated ink G-code: %d traces, %d lines", len(traces), len(lines))
    return lines


# ── Trace segment extraction (for postprocessor) ──────────────────

def extract_trace_segments(
    routing_result: dict,
) -> list[tuple[float, float, float, float]]:
    """Return trace paths as ``(x1, y1, x2, y2)`` mm line segments.

    This is used by the post-processor to know *where* the traces run
    so it can skip ironing over them and add a highlight extrusion pass.
    """
    if not routing_result:
        return []

    traces = routing_result.get("traces", [])
    if not traces:
        return []

    segments: list[tuple[float, float, float, float]] = []
    for trace in traces:
        path = trace.get("path", [])
        simplified = _simplify_path(path)
        if len(simplified) < 2:
            continue
        for j in range(len(simplified) - 1):
            x1, y1 = simplified[j][0], simplified[j][1]
            x2, y2 = simplified[j + 1][0], simplified[j + 1][1]
            segments.append((x1, y1, x2, y2))

    log.info("Extracted %d trace segments from %d traces", len(segments), len(traces))
    return segments


def extract_pad_centers(
    placement_result: dict | None,
) -> list[tuple[float, float]]:
    """Return unique component pin positions from placement data.

    Each placed component's ``pin_positions`` dict maps pin IDs to
    ``[x, y]`` world coordinates.  All pin positions are collected
    and deduplicated (within 0.01 mm).
    """
    if not placement_result:
        return []

    components = placement_result.get("components", [])
    seen: set[tuple[int, int]] = set()
    centers: list[tuple[float, float]] = []

    for comp in components:
        pin_positions = comp.get("pin_positions", {})
        for pos in pin_positions.values():
            if not pos or len(pos) < 2:
                continue
            key = (round(pos[0] * 100), round(pos[1] * 100))
            if key not in seen:
                seen.add(key)
                centers.append((float(pos[0]), float(pos[1])))

    log.info("Extracted %d pad centres from %d components", len(centers), len(components))
    return centers


def extract_pin_holes(
    placement_result: dict | None,
    catalog_index: dict[str, object] | None = None,
) -> list[PinHoleRect]:
    """Build pin hole exclusion rectangles matching the SCAD cutouts.

    Uses catalog pin shapes + PINHOLE_CLEARANCE so the ironing
    clipping matches the actual hole geometry in the printed model.
    Falls back to hole_diameter_mm + clearance as a square when no
    catalog entry is found.
    """
    if not placement_result:
        return []

    from src.catalog.models import Component

    components = placement_result.get("components", [])
    holes: list[PinHoleRect] = []

    for comp in components:
        pin_positions = comp.get("pin_positions", {})
        catalog_id = comp.get("catalog_id", "")
        cat: Component | None = catalog_index.get(catalog_id) if catalog_index else None  # type: ignore[union-attr]

        pin_lookup: dict[str, object] = {}
        if cat is not None:
            pin_lookup = {p.id: p for p in cat.pins}

        for pin_id, pos in pin_positions.items():
            if not pos or len(pos) < 2:
                continue
            cx, cy = float(pos[0]), float(pos[1])
            cat_pin = pin_lookup.get(pin_id)

            if cat_pin is not None:
                d = cat_pin.hole_diameter_mm + PINHOLE_CLEARANCE  # type: ignore[union-attr]
                shape = getattr(cat_pin, "shape", None)
                if shape and shape.type in ("rect", "slot"):
                    hw = ((shape.width_mm or d) + PINHOLE_CLEARANCE) / 2
                    hh = ((shape.length_mm or d) + PINHOLE_CLEARANCE) / 2
                else:
                    hw = d / 2
                    hh = d / 2
            else:
                hw = hh = PAD_RADIUS

            holes.append(PinHoleRect(cx, cy, hw, hh))

    log.info("Extracted %d pin holes from %d components", len(holes), len(components))
    return holes


# ── Trace-following ironing G-code ────────────────────────────────

IRONING_SPEED = 1200       # mm/min
IRONING_FLOW_PCT = 0.15    # fraction of normal flow
IRONING_Z_LIFT = 0.1       # mm — lift ironing above nominal surface
PAD_RADIUS = 1.0           # mm — half-size of pad area
PAD_LINE_SPACING = 0.4     # mm — spacing between pad ironing lines
IRONING_LINE_SPACING = FDM_EXTRUSION_W  # spacing between parallel passes


def _clip_segment_around_pads(
    x1: float, y1: float, x2: float, y2: float,
    pin_holes: list[PinHoleRect],
) -> tuple[float, float, float, float] | None:
    """Shorten a segment so it stops at the edge of each pin hole rectangle.

    Uses slab intersection against each axis-aligned rectangle.
    Returns *None* if the clipped segment has zero or negative length.
    """
    dx = x2 - x1
    dy = y2 - y1
    seg_len = math.hypot(dx, dy)
    if seg_len < 1e-6:
        return None

    ux, uy = dx / seg_len, dy / seg_len
    t_min = 0.0
    t_max = seg_len

    for hole in pin_holes:
        rx_min = hole.cx - hole.half_w
        rx_max = hole.cx + hole.half_w
        ry_min = hole.cy - hole.half_h
        ry_max = hole.cy + hole.half_h

        t_enter = 0.0
        t_exit = seg_len

        for origin, direction, lo, hi in (
            (x1, ux, rx_min, rx_max),
            (y1, uy, ry_min, ry_max),
        ):
            if abs(direction) < 1e-12:
                if origin < lo or origin > hi:
                    t_enter = seg_len
                    t_exit = 0.0
                    break
            else:
                t1 = (lo - origin) / direction
                t2 = (hi - origin) / direction
                if t1 > t2:
                    t1, t2 = t2, t1
                t_enter = max(t_enter, t1)
                t_exit = min(t_exit, t2)

        if t_enter >= t_exit:
            continue

        if t_enter <= t_min and t_exit > t_min:
            t_min = t_exit
        if t_exit >= t_max and t_enter < t_max:
            t_max = t_enter

    if t_max - t_min < 0.01:
        return None

    return (
        x1 + ux * t_min,
        y1 + uy * t_min,
        x1 + ux * t_max,
        y1 + uy * t_max,
    )


def generate_trace_ironing_gcode(
    trace_segs: list[tuple[float, float, float, float]],
    pad_centers: list[tuple[float, float]],
    *,
    ink_z: float,
    pad_z_drop: float = 0.0,
    layer_height: float = 0.2,
    extrusion_w: float = FDM_EXTRUSION_W,
    ironing_speed: float = IRONING_SPEED,
    flow_pct: float = IRONING_FLOW_PCT,
    pad_radius: float = PAD_RADIUS,
    pad_line_spacing: float = PAD_LINE_SPACING,
    trace_width: float = TRACE_RULES.trace_width_mm,
    line_spacing: float = IRONING_LINE_SPACING,
    pin_holes: list[PinHoleRect] | None = None,
    inflated_polygons: list[Polygon] | None = None,
) -> list[str]:
    """Generate ironing G-code that covers the full inflated trace area.

    When *inflated_polygons* are provided, ironing fills the entire
    inflated polygon footprint with serpentine scanlines.  Falls back
    to the old parallel-offset-along-centreline approach when no
    inflated polygons are available.

    Parameters
    ----------
    trace_segs : list
        ``(x1, y1, x2, y2)`` trace segments in bed coords (fallback).
    pad_centers : list
        ``(x, y)`` positions of pin pads in bed coords.
    ink_z : float
        Z height of the trace ironing surface (mm).
    pin_holes : list of PinHoleRect, optional
        Pin hole exclusion rectangles.
    inflated_polygons : list of Polygon, optional
        Shapely polygons representing the full inflated trace
        footprints in bed coords.  When provided, ironing fills
        these areas instead of following trace centrelines.
    """
    if pin_holes is None:
        pin_holes = [
            PinHoleRect(cx, cy, pad_radius, pad_radius)
            for cx, cy in pad_centers
        ]

    filament_area = math.pi * (1.75 / 2) ** 2
    e_per_mm = (layer_height * extrusion_w * flow_pct) / filament_area

    lines: list[str] = [
        ";TYPE:Ironing",
        "; custom trace-following ironing",
        f"G0 Z{ink_z + IRONING_Z_LIFT:.3f} F720 ; ensure trace ironing Z",
    ]
    cumulative_e = 0.0

    if inflated_polygons:
        cumulative_e = _ironing_fill_polygons(
            inflated_polygons, pin_holes, line_spacing,
            e_per_mm, ironing_speed, lines, cumulative_e,
        )
        log.info("Generated inflated-polygon ironing: %d polygons, %d lines",
                 len(inflated_polygons), len(lines))
    else:
        num_passes = max(1, round(trace_width / line_spacing))
        offsets = [
            (i - (num_passes - 1) / 2) * line_spacing
            for i in range(num_passes)
        ]

        for x1, y1, x2, y2 in trace_segs:
            dx = x2 - x1
            dy = y2 - y1
            length = math.hypot(dx, dy)
            if length < 0.01:
                continue

            nx = -dy / length
            ny = dx / length

            for offset in offsets:
                ox1 = x1 + nx * offset
                oy1 = y1 + ny * offset
                ox2 = x2 + nx * offset
                oy2 = y2 + ny * offset

                clipped = _clip_segment_around_pads(
                    ox1, oy1, ox2, oy2, pin_holes,
                )
                if clipped is None:
                    continue
                cx1, cy1, cx2, cy2 = clipped

                seg_len = math.hypot(cx2 - cx1, cy2 - cy1)
                de = e_per_mm * seg_len
                lines.append(f"G0 X{cx1:.3f} Y{cy1:.3f} F21000")
                cumulative_e += de
                lines.append(f"G1 X{cx2:.3f} Y{cy2:.3f} E{cumulative_e:.5f} F{ironing_speed:.0f}")

        log.info("Generated trace ironing: %d trace segments × %d passes, %d lines",
                 len(trace_segs), num_passes, len(lines))
    return lines


def _ironing_fill_polygons(
    polygons: list[Polygon],
    pin_holes: list[PinHoleRect],
    line_spacing: float,
    e_per_mm: float,
    ironing_speed: float,
    lines: list[str],
    cumulative_e: float,
) -> float:
    """Fill inflated polygons with serpentine scanlines, clipping around pin holes."""
    from shapely.geometry import box as shapely_box
    from shapely.ops import unary_union

    hole_polys = [
        shapely_box(
            h.cx - h.half_w, h.cy - h.half_h,
            h.cx + h.half_w, h.cy + h.half_h,
        )
        for h in pin_holes
    ]
    hole_union = unary_union(hole_polys) if hole_polys else Polygon()

    for poly in polygons:
        if poly.is_empty or not poly.is_valid:
            continue

        clipped = poly.difference(hole_union) if not hole_union.is_empty else poly
        if clipped.is_empty:
            continue

        min_x, min_y, max_x, max_y = clipped.bounds
        y = min_y + line_spacing / 2
        forward = True

        while y <= max_y:
            scanline = LineString([(min_x - 1, y), (max_x + 1, y)])
            intersection = clipped.intersection(scanline)

            segments: list[tuple[float, float, float, float]] = []
            if intersection.is_empty:
                y += line_spacing
                continue
            elif isinstance(intersection, LineString):
                coords = list(intersection.coords)
                if len(coords) >= 2:
                    segments.append((coords[0][0], coords[0][1],
                                     coords[-1][0], coords[-1][1]))
            elif isinstance(intersection, MultiLineString):
                for ls in intersection.geoms:
                    coords = list(ls.coords)
                    if len(coords) >= 2:
                        segments.append((coords[0][0], coords[0][1],
                                         coords[-1][0], coords[-1][1]))
            else:
                for geom in getattr(intersection, 'geoms', []):
                    if isinstance(geom, LineString):
                        coords = list(geom.coords)
                        if len(coords) >= 2:
                            segments.append((coords[0][0], coords[0][1],
                                             coords[-1][0], coords[-1][1]))

            segments.sort(key=lambda s: s[0])
            if not forward:
                segments.reverse()
                segments = [(x2, y2, x1, y1) for x1, y1, x2, y2 in segments]

            for sx1, sy1, sx2, sy2 in segments:
                seg_len = math.hypot(sx2 - sx1, sy2 - sy1)
                if seg_len < 0.01:
                    continue
                de = e_per_mm * seg_len
                lines.append(f"G0 X{sx1:.3f} Y{sy1:.3f} F21000")
                cumulative_e += de
                lines.append(f"G1 X{sx2:.3f} Y{sy2:.3f} E{cumulative_e:.5f} F{ironing_speed:.0f}")

            forward = not forward
            y += line_spacing

    return cumulative_e


def generate_pad_ironing_gcode(
    pad_centers: list[tuple[float, float]],
    *,
    layer_height: float = 0.2,
    extrusion_w: float = FDM_EXTRUSION_W,
    ironing_speed: float = IRONING_SPEED,
    flow_pct: float = IRONING_FLOW_PCT,
    pad_radius: float = PAD_RADIUS,
    pad_line_spacing: float = PAD_LINE_SPACING,
) -> list[str]:
    """Generate ironing G-code for pin pads on an earlier layer.

    Each pad is a small filled rectangle ironed with serpentine
    lines.  This is injected on the layer at the bottom of the
    pin holes so the ironed surface is below the trace layer.
    """
    if not pad_centers:
        return []

    filament_area = math.pi * (1.75 / 2) ** 2
    e_per_mm = (layer_height * extrusion_w * flow_pct) / filament_area

    lines: list[str] = [
        ";TYPE:Ironing",
        "; custom pad ironing (pin holes)",
    ]
    cumulative_e = 0.0

    for cx, cy in pad_centers:
        y_lo = cy - pad_radius
        y_hi = cy + pad_radius
        y_pos = y_lo
        forward = True
        while y_pos <= y_hi + 0.001:
            x_lo = cx - pad_radius
            x_hi = cx + pad_radius
            sx, ex = (x_lo, x_hi) if forward else (x_hi, x_lo)
            lines.append(f"G0 X{sx:.3f} Y{y_pos:.3f} F21000")
            seg_len = abs(x_hi - x_lo)
            de = e_per_mm * seg_len
            cumulative_e += de
            lines.append(f"G1 X{ex:.3f} Y{y_pos:.3f} E{cumulative_e:.5f} F{ironing_speed:.0f}")
            forward = not forward
            y_pos += pad_line_spacing

    log.info("Generated pad ironing: %d pads, %d lines",
             len(pad_centers), len(lines))
    return lines


# ── Path simplification ───────────────────────────────────────────

def _simplify_path(path: list[Sequence[float]]) -> list[Sequence[float]]:
    """Remove collinear intermediate points, keeping corners only."""
    if len(path) <= 2:
        return list(path)

    result = [path[0]]
    for i in range(1, len(path) - 1):
        prev = path[i - 1]
        curr = path[i]
        nxt = path[i + 1]

        dx1 = curr[0] - prev[0]
        dy1 = curr[1] - prev[1]
        dx2 = nxt[0] - curr[0]
        dy2 = nxt[1] - curr[1]

        if (dx1, dy1) != (dx2, dy2):
            result.append(curr)

    result.append(path[-1])
    return result

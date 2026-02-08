"""
Pure-Python polygon geometry utilities.

All coordinates in mm, origin bottom-left, X = width, Y = length.
"""

from __future__ import annotations
import math
from typing import Sequence

Vertex = list[float]  # [x, y]
Outline = list[Vertex]


# ── core primitives ─────────────────────────────────────────────────


def polygon_area(outline: Outline) -> float:
    """Signed area via shoelace formula (positive = CCW)."""
    n = len(outline)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        x0, y0 = outline[i]
        x1, y1 = outline[(i + 1) % n]
        area += x0 * y1 - x1 * y0
    return area / 2.0


def ensure_ccw(outline: Outline) -> Outline:
    """Return a copy with counter-clockwise winding."""
    if polygon_area(outline) < 0:
        return list(reversed(outline))
    return list(outline)


def point_in_polygon(x: float, y: float, outline: Outline) -> bool:
    """Ray-casting point-in-polygon test."""
    n = len(outline)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = outline[i]
        xj, yj = outline[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def polygon_bounds(outline: Outline) -> tuple[float, float, float, float]:
    """Return (min_x, min_y, max_x, max_y)."""
    xs = [v[0] for v in outline]
    ys = [v[1] for v in outline]
    return min(xs), min(ys), max(xs), max(ys)


# ── segment intersection ───────────────────────────────────────────


def _cross(o: Vertex, a: Vertex, b: Vertex) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _on_segment(p: Vertex, q: Vertex, r: Vertex) -> bool:
    return (min(p[0], r[0]) <= q[0] <= max(p[0], r[0]) and
            min(p[1], r[1]) <= q[1] <= max(p[1], r[1]))


def segments_intersect(
    a1: Vertex, a2: Vertex, b1: Vertex, b2: Vertex
) -> bool:
    """Check if segments (a1-a2) and (b1-b2) properly intersect."""
    d1 = _cross(b1, b2, a1)
    d2 = _cross(b1, b2, a2)
    d3 = _cross(a1, a2, b1)
    d4 = _cross(a1, a2, b2)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
       ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True
    if d1 == 0 and _on_segment(b1, a1, b2):
        return True
    if d2 == 0 and _on_segment(b1, a2, b2):
        return True
    if d3 == 0 and _on_segment(a1, b1, a2):
        return True
    if d4 == 0 and _on_segment(a1, b2, a2):
        return True
    return False


def _is_self_intersecting(outline: Outline) -> bool:
    """O(n²) edge-crossing check."""
    n = len(outline)
    for i in range(n):
        a1, a2 = outline[i], outline[(i + 1) % n]
        for j in range(i + 2, n):
            if j == (i - 1) % n or (i == 0 and j == n - 1):
                continue  # adjacent edges
            b1, b2 = outline[j], outline[(j + 1) % n]
            if segments_intersect(a1, a2, b1, b2):
                return True
    return False


# ── outline validation ──────────────────────────────────────────────


def validate_outline(
    outline: Outline,
    width: float,
    length: float,
    min_area: float = 400.0,
    button_positions: list[dict] | None = None,
    edge_clearance: float = 3.0,
) -> list[str]:
    """
    Validate a polygon outline for use as a remote shell profile.

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    if len(outline) < 3:
        errors.append(f"Outline has only {len(outline)} vertices — need at least 3.")
        return errors

    # bounds check
    for i, (x, y) in enumerate(outline):
        if x < -0.5 or x > width + 0.5 or y < -0.5 or y > length + 0.5:
            errors.append(
                f"Vertex {i} at ({x:.1f}, {y:.1f}) is outside the "
                f"{width}×{length}mm bounding box."
            )

    # area
    area = abs(polygon_area(outline))
    if area < min_area:
        errors.append(
            f"Polygon area is {area:.1f}mm² — need at least {min_area:.0f}mm² "
            f"to fit battery + controller."
        )

    # self-intersection
    if _is_self_intersecting(outline):
        errors.append("Polygon has self-intersecting edges.")

    # button containment
    if button_positions:
        ccw = ensure_ccw(outline)
        for btn in button_positions:
            bx, by = btn["x"], btn["y"]
            if not point_in_polygon(bx, by, ccw):
                errors.append(
                    f"Button {btn['id']} at ({bx:.1f}, {by:.1f}) is outside the polygon."
                )
            else:
                # check edge clearance
                min_dist, nearest_seg = _min_dist_to_boundary_detailed(bx, by, ccw)
                if min_dist < edge_clearance:
                    v1, v2 = nearest_seg
                    errors.append(
                        f"Button {btn['id']} at ({bx:.1f}, {by:.1f}) is only "
                        f"{min_dist:.1f}mm from the polygon edge between "
                        f"({v1[0]:.1f}, {v1[1]:.1f}) and ({v2[0]:.1f}, {v2[1]:.1f}) "
                        f"— need ≥{edge_clearance:.1f}mm clearance. "
                        f"Move the button inward or make the shape wider in this area."
                    )

    return errors


def _min_dist_to_boundary(px: float, py: float, outline: Outline) -> float:
    """Minimum distance from point to polygon boundary."""
    dist, _ = _min_dist_to_boundary_detailed(px, py, outline)
    return dist


def _min_dist_to_boundary_detailed(
    px: float, py: float, outline: Outline
) -> tuple[float, tuple[Vertex, Vertex]]:
    """Minimum distance from point to polygon boundary, plus nearest segment."""
    min_d = float("inf")
    nearest = (outline[0], outline[1]) if len(outline) >= 2 else (outline[0], outline[0])
    n = len(outline)
    for i in range(n):
        v1 = outline[i]
        v2 = outline[(i + 1) % n]
        d = _point_segment_dist(px, py, v1[0], v1[1], v2[0], v2[1])
        if d < min_d:
            min_d = d
            nearest = (v1, v2)
    return min_d, nearest


def _point_segment_dist(
    px: float, py: float,
    x1: float, y1: float,
    x2: float, y2: float,
) -> float:
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.hypot(px - proj_x, py - proj_y)


# ── polygon inset (pure python, no pyclipper) ──────────────────────


def inset_polygon(outline: Outline, margin: float) -> Outline:
    """
    Approximate inward offset by moving each edge inward by *margin* mm.

    Works well for convex and mildly concave shapes typical of remote outlines.
    For extreme concavities pyclipper should be used instead.
    """
    ccw = ensure_ccw(outline)
    n = len(ccw)
    if n < 3:
        return ccw

    inset: Outline = []
    for i in range(n):
        p0 = ccw[(i - 1) % n]
        p1 = ccw[i]
        p2 = ccw[(i + 1) % n]

        # normals of the two incident edges (pointing inward for CCW)
        n1 = _inward_normal(p0, p1)
        n2 = _inward_normal(p1, p2)

        # offset lines
        # Line 1: p0 + margin*n1 → p1 + margin*n1
        # Line 2: p1 + margin*n2 → p2 + margin*n2
        a1 = [p0[0] + margin * n1[0], p0[1] + margin * n1[1]]
        b1 = [p1[0] + margin * n1[0], p1[1] + margin * n1[1]]
        a2 = [p1[0] + margin * n2[0], p1[1] + margin * n2[1]]
        b2 = [p2[0] + margin * n2[0], p2[1] + margin * n2[1]]

        pt = _line_intersection(a1, b1, a2, b2)
        if pt is not None:
            inset.append(pt)
        else:
            # parallel edges — just offset the vertex
            inset.append([p1[0] + margin * n1[0], p1[1] + margin * n1[1]])

    return inset


def _inward_normal(a: Vertex, b: Vertex) -> Vertex:
    """Unit inward normal for edge a→b (CCW polygon → inward is right-hand)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1e-12:
        return [0.0, 0.0]
    # left-hand normal (inward for CCW) = (-dy, dx) / length
    return [-dy / length, dx / length]


def _line_intersection(
    a1: Vertex, a2: Vertex, b1: Vertex, b2: Vertex
) -> Vertex | None:
    """Intersection of lines (a1→a2) and (b1→b2), or None if parallel."""
    dx1, dy1 = a2[0] - a1[0], a2[1] - a1[1]
    dx2, dy2 = b2[0] - b1[0], b2[1] - b1[1]
    denom = dx1 * dy2 - dy1 * dx2
    if abs(denom) < 1e-12:
        return None
    t = ((b1[0] - a1[0]) * dy2 - (b1[1] - a1[1]) * dx2) / denom
    return [a1[0] + t * dx1, a1[1] + t * dy1]

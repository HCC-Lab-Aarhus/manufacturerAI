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


def generate_ellipse(width: float, length: float, n: int = 32) -> Outline:
    """Generate a *n*-vertex ellipse inscribed in *width* × *length* box.

    Origin at bottom-left (0, 0).  Counter-clockwise winding starting at
    the rightmost point (mid-right).
    """
    cx, cy = width / 2, length / 2
    rx, ry = width / 2, length / 2
    pts: Outline = []
    for i in range(n):
        angle = 2 * math.pi * i / n
        pts.append([
            round(cx + rx * math.cos(angle), 4),
            round(cy + ry * math.sin(angle), 4),
        ])
    return pts


def generate_racetrack(width: float, length: float, n_cap: int = 16) -> Outline:
    """Generate a stadium/racetrack shape inscribed in *width* × *length*.

    Two semicircles joined by straight sides.  The semicircles are on the
    shorter dimension.  Origin at bottom-left.  CCW winding.
    """
    if width > length:
        # Wide: semicircles on left / right
        r = length / 2
        sx = r  # start of straight segment x
        ex = width - r  # end of straight segment x
        pts: Outline = []
        # Right semicircle (top to bottom)
        for i in range(n_cap + 1):
            angle = math.pi / 2 - math.pi * i / n_cap
            pts.append([round(ex + r * math.cos(angle), 4),
                        round(r + r * math.sin(angle), 4)])
        # Left semicircle (bottom to top)
        for i in range(n_cap + 1):
            angle = -math.pi / 2 - math.pi * i / n_cap
            pts.append([round(sx + r * math.cos(angle), 4),
                        round(r + r * math.sin(angle), 4)])
        return pts
    else:
        # Tall (normal remote): semicircles on top / bottom
        r = width / 2
        sy = r  # start of straight segment y
        ey = length - r  # end of straight segment y
        pts = []
        # Bottom semicircle (left to right)
        for i in range(n_cap + 1):
            angle = math.pi + math.pi * i / n_cap
            pts.append([round(r + r * math.cos(angle), 4),
                        round(sy + r * math.sin(angle), 4)])
        # Right side up
        # Top semicircle (right to left)
        for i in range(n_cap + 1):
            angle = 0 + math.pi * i / n_cap
            pts.append([round(r + r * math.cos(angle), 4),
                        round(ey + r * math.sin(angle), 4)])
        return pts


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


# ── polygon smoothing ──────────────────────────────────────────────


def _edge_lengths(outline: Outline) -> list[float]:
    """Return the length of each edge [i] → [i+1] (wrapping)."""
    n = len(outline)
    return [
        math.hypot(
            outline[(i + 1) % n][0] - outline[i][0],
            outline[(i + 1) % n][1] - outline[i][1],
        )
        for i in range(n)
    ]


def _interior_angle(a: Vertex, b: Vertex, c: Vertex) -> float:
    """Interior angle at vertex *b* for a CCW polygon, in degrees.

    Returns the angle "inside" the polygon at b, where a is the
    previous vertex and c is the next vertex (CCW order).
    Convex corners: 0–180°.  Reflex corners: 180–360°.
    """
    # Vectors from b toward neighbours
    ax, ay = a[0] - b[0], a[1] - b[1]
    cx, cy = c[0] - b[0], c[1] - b[1]
    dot = ax * cx + ay * cy
    # Cross product sign tells us which side of ba the vector bc is on.
    # For CCW polygons, negate the cross so the interior (left) side
    # maps to the 0–π range.
    cross = ay * cx - ax * cy
    angle = math.atan2(cross, dot)
    if angle < 0:
        angle += 2 * math.pi
    return math.degrees(angle)


def _chaikin_cut(outline: Outline) -> Outline:
    """One iteration of Chaikin's corner-cutting algorithm.

    Each edge is split at 25% and 75%, producing a polygon with 2n
    vertices that converges toward a smooth B-spline curve.
    """
    n = len(outline)
    result: Outline = []
    for i in range(n):
        p0 = outline[i]
        p1 = outline[(i + 1) % n]
        result.append([0.75 * p0[0] + 0.25 * p1[0],
                       0.75 * p0[1] + 0.25 * p1[1]])
        result.append([0.25 * p0[0] + 0.75 * p1[0],
                       0.25 * p0[1] + 0.75 * p1[1]])
    return result


def smooth_polygon(
    outline: Outline,
    *,
    iterations: int = 3,
    max_vertices: int = 128,
    angle_threshold: float = 130.0,
) -> Outline:
    """Smooth a polygon using Chaikin's corner-cutting subdivision.

    Designed for LLM-generated outlines that attempt rounded/oval
    shapes but only produce 6–20 vertices (resulting in visible
    faceting).  The algorithm only activates when the polygon looks
    like it's *trying* to be curved — i.e. most interior angles are
    ≥ *angle_threshold* degrees (nearly straight, typical of a coarse
    circle approximation).

    Parameters
    ----------
    outline : list of [x, y]
        The polygon vertices (CCW or CW — works either way).
    iterations : int
        Number of Chaikin subdivision passes (each doubles vertex count).
        3 passes: 8 verts → 64 verts → smooth.
    max_vertices : int
        Stop subdividing if vertex count would exceed this.
    angle_threshold : float
        Interior angle (degrees) above which a corner is considered
        "gentle".  If ≥ 70% of corners exceed this threshold, the
        polygon is treated as an attempted curve and gets smoothed.
        Polygons with sharp corners (rectangles, diamonds, T-shapes,
        rounded rectangles, hexagons) are left alone.

    Returns
    -------
    Smoothed polygon, or the original if smoothing is not appropriate.
    """
    if len(outline) < 5:
        return outline

    ccw = ensure_ccw(outline)
    n = len(ccw)

    # Compute interior angles to decide if this looks like an
    # attempted curve or a shape with intentional sharp corners.
    smooth_count = 0
    for i in range(n):
        a = ccw[(i - 1) % n]
        b = ccw[i]
        c = ccw[(i + 1) % n]
        angle = _interior_angle(a, b, c)
        if angle >= angle_threshold:
            smooth_count += 1

    smooth_ratio = smooth_count / n
    if smooth_ratio < 0.70:
        # Polygon has too many sharp corners — probably intentional
        # (rectangle, diamond, T-shape, etc.).  Don't smooth.
        return outline

    # Apply Chaikin subdivision
    result = list(ccw)
    for _ in range(iterations):
        if len(result) * 2 > max_vertices:
            break
        result = _chaikin_cut(result)

    return result

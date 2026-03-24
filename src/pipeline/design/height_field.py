"""Height field utilities for 3D enclosure shapes.

Public API
----------
blended_height(x, y, outline, enclosure) -> float
    Returns the ceiling Z (always enclosure.height_mm).

sample_height_grid(outline, enclosure, resolution_mm) -> dict
    Samples height on a regular grid covering the outline bounding box,
    masked to the interior of the polygon.  Returns a JSON-safe dict.

surface_normal_at(x, y, grid) -> tuple[float, float, float]
    Returns the outward surface normal at (x, y) using central differences
    on a pre-sampled grid dict.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Outline, Enclosure


# ── Bézier expansion ──────────────────────────────────────────────


def _bezier_expand_outline(
    outline: "Outline",
    segments: int = 6,
) -> list[tuple[float, float]]:
    """Expand bezier-eased corners into sub-points for polygon operations.

    Mirrors the JS ``expandOutlineVertices`` logic so the Shapely masking
    polygon matches the rounded shape visible in the 3-D viewport.
    """
    points = outline.points
    n = len(points)
    result: list[tuple[float, float]] = []

    for i in range(n):
        prev = (i - 1) % n
        next_ = (i + 1) % n
        Cx, Cy = points[i].x, points[i].y
        Px, Py = points[prev].x, points[prev].y
        Nx, Ny = points[next_].x, points[next_].y

        e_in  = points[i].ease_in  or 0.0
        e_out = points[i].ease_out or 0.0

        if e_in == 0 and e_out == 0:
            result.append((Cx, Cy))
            continue

        dPx, dPy = Px - Cx, Py - Cy
        dNx, dNy = Nx - Cx, Ny - Cy
        lenP = math.hypot(dPx, dPy)
        lenN = math.hypot(dNx, dNy)

        if lenP < 1e-9 or lenN < 1e-9:
            result.append((Cx, Cy))
            continue

        safe_in  = min(e_in,  lenP * 0.45)
        safe_out = min(e_out, lenN * 0.45)
        t1 = (Cx + dPx * (safe_in  / lenP), Cy + dPy * (safe_in  / lenP))
        t2 = (Cx + dNx * (safe_out / lenN), Cy + dNy * (safe_out / lenN))

        for s in range(segments + 1):
            u  = s / segments
            ku = 1.0 - u
            bx = ku*ku*t1[0] + 2*ku*u*Cx + u*u*t2[0]
            by = ku*ku*t1[1] + 2*ku*u*Cy + u*u*t2[1]
            result.append((bx, by))

    return result


# ── Height queries ─────────────────────────────────────────────────


def blended_height(
    x: float,
    y: float,
    outline: "Outline",
    enclosure: "Enclosure",
) -> float:
    """Return the ceiling Z at world position (x, y)."""
    return enclosure.height_mm


def sample_height_grid(
    outline: "Outline",
    enclosure: "Enclosure",
    resolution_mm: float = 2.0,
) -> dict:
    """Sample height on a regular grid over the outline bounding box.

    Returns a JSON-safe dict:
    {
        "origin_x": float,
        "origin_y": float,
        "step_mm":  float,
        "cols":     int,
        "rows":     int,
        "grid":     [[z, ...], ...]   # rows x cols; None outside the polygon
    }
    """
    verts = outline.vertices
    if len(verts) < 3:
        return {"origin_x": 0, "origin_y": 0, "step_mm": resolution_mm,
                "cols": 0, "rows": 0, "grid": []}

    expanded_verts = _bezier_expand_outline(outline) or verts

    xs = [v[0] for v in expanded_verts]
    ys = [v[1] for v in expanded_verts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    step = resolution_mm
    cols = max(1, int(math.ceil((max_x - min_x) / step)) + 1)
    rows = max(1, int(math.ceil((max_y - min_y) / step)) + 1)

    poly_expanded = None
    use_shapely = False
    try:
        from shapely.geometry import Polygon, Point
        poly = Polygon(expanded_verts)
        if poly.is_valid:
            poly_expanded = poly
            use_shapely = True
    except ImportError:
        pass

    z = round(enclosure.height_mm, 3)
    grid: list[list[float | None]] = []
    for r in range(rows):
        row: list[float | None] = []
        y = min_y + r * step
        for c in range(cols):
            x = min_x + c * step
            if use_shapely:
                inside = poly_expanded.contains(Point(x, y))
            else:
                inside = _point_in_polygon(x, y, verts)
            row.append(z if inside else None)
        grid.append(row)

    return {
        "origin_x": round(min_x, 3),
        "origin_y": round(min_y, 3),
        "step_mm": step,
        "cols": cols,
        "rows": rows,
        "grid": grid,
    }


def surface_normal_at(
    x: float,
    y: float,
    grid: dict,
) -> tuple[float, float, float]:
    """Compute the outward surface normal at (x, y) from a pre-sampled grid.

    Uses central differences on the sampled height grid.
    Returns (nx, ny, nz) as a normalised vector.
    """
    origin_x: float = grid["origin_x"]
    origin_y: float = grid["origin_y"]
    step: float = grid["step_mm"]
    grid_data: list[list[float | None]] = grid["grid"]
    rows: int = grid["rows"]
    cols: int = grid["cols"]

    def _sample(c: int, r: int) -> float | None:
        if 0 <= r < rows and 0 <= c < cols:
            return grid_data[r][c]
        return None

    c = (x - origin_x) / step
    r = (y - origin_y) / step
    ci = int(round(c))
    ri = int(round(r))

    def _z(dc: int, dr: int) -> float:
        v = _sample(ci + dc, ri + dr)
        if v is None:
            v = _sample(ci, ri)
        return v if v is not None else 25.0

    dzdx = (_z(1, 0) - _z(-1, 0)) / (2 * step)
    dzdy = (_z(0, 1) - _z(0, -1)) / (2 * step)

    nx, ny, nz = -dzdx, -dzdy, 1.0
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-9:
        return (0.0, 0.0, 1.0)
    return (nx / length, ny / length, nz / length)


def _point_in_polygon(x: float, y: float, verts: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test (fallback when Shapely is unavailable)."""
    n = len(verts)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = verts[i]
        xj, yj = verts[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-15) + xi):
            inside = not inside
        j = i
    return inside

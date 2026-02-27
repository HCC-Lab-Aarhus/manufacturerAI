"""Low-level geometry helpers for the placer."""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import Polygon, box as shapely_box

from src.catalog.models import Component


def footprint_halfdims(
    cat: Component, rotation_deg: int,
) -> tuple[float, float]:
    """Return (half_width, half_height) of the body at a given rotation.

    For rect bodies, width/length swap at 90° and 270°.
    For circle bodies, the half-dims are equal regardless of rotation.
    """
    if cat.body.shape == "circle":
        r = (cat.body.diameter_mm or 5.0) / 2
        return (r, r)
    # rect
    w = (cat.body.width_mm or 1.0) / 2
    h = (cat.body.length_mm or 1.0) / 2
    if rotation_deg in (90, 270):
        return (h, w)
    return (w, h)


def footprint_area(cat: Component) -> float:
    """Footprint area in mm², used for placement ordering."""
    if cat.body.shape == "circle":
        r = (cat.body.diameter_mm or 5.0) / 2
        return math.pi * r * r
    return (cat.body.width_mm or 1.0) * (cat.body.length_mm or 1.0)


def pin_world_xy(
    pin_local: tuple[float, float],
    cx: float, cy: float,
    rotation_deg: int,
) -> tuple[float, float]:
    """Transform a component-local pin position to world coordinates."""
    px, py = pin_local
    rad = math.radians(rotation_deg)
    cos_r = math.cos(rad)
    sin_r = math.sin(rad)
    return (
        cx + px * cos_r - py * sin_r,
        cy + px * sin_r + py * cos_r,
    )


def _point_seg_dist(
    px: float, py: float,
    x1: float, y1: float,
    x2: float, y2: float,
) -> float:
    """Distance from point (px, py) to line segment (x1,y1)-(x2,y2)."""
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _min_dist_to_boundary(
    px: float, py: float,
    verts: list[tuple[float, float]],
) -> float:
    """Minimum distance from a point to a polygon boundary."""
    n = len(verts)
    return min(
        _point_seg_dist(
            px, py,
            verts[i][0], verts[i][1],
            verts[(i + 1) % n][0], verts[(i + 1) % n][1],
        )
        for i in range(n)
    )


def _rect_perimeter_samples(
    cx: float, cy: float,
    hw: float, hh: float,
    spacing: float = 4.0,
) -> list[tuple[float, float]]:
    """Dense perimeter samples of an axis-aligned rectangle.

    Returns corner points, edge midpoints, and additional points so
    no two adjacent samples are more than *spacing* mm apart.  This
    catches concavities that a 4-corner check would miss.
    """
    w, h = hw * 2, hh * 2
    nx = max(2, int(math.ceil(w / spacing)) + 1)
    ny = max(2, int(math.ceil(h / spacing)) + 1)
    pts: list[tuple[float, float]] = []
    for i in range(nx):
        t = i / (nx - 1) if nx > 1 else 0.5
        x = cx - hw + w * t
        pts.append((x, cy - hh))
        pts.append((x, cy + hh))
    for j in range(1, ny - 1):
        t = j / (ny - 1)
        y = cy - hh + h * t
        pts.append((cx - hw, y))
        pts.append((cx + hw, y))
    return pts


def rect_edge_clearance(
    cx: float, cy: float,
    hw: float, hh: float,
    verts: list[tuple[float, float]],
) -> float:
    """Min distance from rectangle perimeter samples to polygon boundary."""
    return min(
        _min_dist_to_boundary(px, py, verts)
        for px, py in _rect_perimeter_samples(cx, cy, hw, hh)
    )


def rect_inside_polygon(
    cx: float, cy: float,
    hw: float, hh: float,
    poly: Polygon,
) -> bool:
    """Check if an AABB is fully inside a Shapely polygon."""
    rect = shapely_box(cx - hw, cy - hh, cx + hw, cy + hh)
    return poly.contains(rect)


def aabb_gap(
    cx1: float, cy1: float, hw1: float, hh1: float,
    cx2: float, cy2: float, hw2: float, hh2: float,
) -> float:
    """Chebyshev gap between two AABBs.

    Returns the minimum separation between the two rectangles' edges.
    Negative values mean overlap.
    """
    gap_x = abs(cx1 - cx2) - hw1 - hw2
    gap_y = abs(cy1 - cy2) - hh1 - hh2
    # If both gaps are positive the AABBs are separated diagonally;
    # the true gap is the Euclidean distance of the corner gap.
    # But for placement scoring Chebyshev (max) is a good conservative
    # approximation and much cheaper.
    return max(gap_x, gap_y)

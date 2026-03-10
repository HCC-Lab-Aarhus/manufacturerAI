"""Candidate position scoring for the placement engine."""

from __future__ import annotations

import math

from src.catalog.models import Component

from .geometry import rect_edge_clearance, aabb_gap, pin_world_xy
from .models import Placed
from .nets import NetEdge, resolve_pin_positions


def score_candidate(
    cx: float, cy: float, rotation: int,
    ehw: float, ehh: float, keepout: float,
    instance_id: str,
    cat: Component,
    placed: list[Placed],
    placed_map: dict[str, Placed],
    catalog_map: dict[str, Component],
    net_graph: dict[str, list[NetEdge]],
    outline_verts: list[tuple[float, float]],
    outline_bounds: tuple[float, float, float, float],
    mounting_style: str,
) -> float:
    """Lightweight scoring: net proximity + edge/bottom preference + spacing."""
    score = 0.0

    # 1. Net proximity (dominant term)
    for edge in net_graph.get(instance_id, []):
        other = placed_map.get(edge.other_iid)
        if other is None:
            continue
        my_positions = resolve_pin_positions(edge.my_pins, cat)
        other_cat = catalog_map.get(other.catalog_id)
        if other_cat is None:
            continue
        other_positions = resolve_pin_positions(edge.other_pins, other_cat)
        best_dist = float("inf")
        for mp in my_positions:
            wx, wy = pin_world_xy(mp, cx, cy, rotation)
            for op in other_positions:
                owx, owy = pin_world_xy(op, other.x, other.y, other.rotation)
                d = math.hypot(wx - owx, wy - owy)
                if d < best_dist:
                    best_dist = d
        if best_dist < float("inf"):
            fanout_boost = 1.0 + math.log2(max(edge.fanout, 2)) - 1.0
            score -= best_dist * 5.0 * fanout_boost

    # 2. Edge clearance (small reward for safe distance)
    edge_dist = rect_edge_clearance(cx, cy, ehw, ehh, outline_verts)
    score += min(edge_dist, 5.0) * 0.5

    # 3. Bottom preference
    if mounting_style == "bottom":
        _, ymin_b, _, _ = outline_bounds
        score -= (cy - ymin_b) * 0.08

    # 4. Large component → prefer edges
    outline_area = (outline_bounds[2] - outline_bounds[0]) * (outline_bounds[3] - outline_bounds[1])
    if outline_area > 0:
        comp_area = ehw * 2 * ehh * 2
        area_ratio = comp_area / outline_area
        if area_ratio > 0.05:
            strength = min(area_ratio / 0.05, 3.0)
            score -= edge_dist * 1.0 * strength

    # 5. Spacing reward — prefer staying spread from neighbours
    if placed:
        min_gap = float("inf")
        for p in placed:
            g = aabb_gap(cx, cy, ehw, ehh, p.x, p.y, p.env_hw, p.env_hh)
            if g < min_gap:
                min_gap = g
        if min_gap < float("inf"):
            score += min(min_gap, 15.0) * 0.4

    # 6. Compactness (mild)
    if placed:
        centroid_x = sum(p.x for p in placed) / len(placed)
        centroid_y = sum(p.y for p in placed) / len(placed)
        score -= math.hypot(cx - centroid_x, cy - centroid_y) * 0.2

    return score

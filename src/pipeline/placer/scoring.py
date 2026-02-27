"""Candidate position scoring for the placer."""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.catalog.models import Component

from .geometry import pin_world_xy, rect_edge_clearance, aabb_gap
from .models import (
    W_NET_PROXIMITY, W_EDGE_CLEARANCE, W_COMPACTNESS,
    W_CLEARANCE_UNIFORM, W_BOTTOM_PREFERENCE,
)
from .nets import NetEdge, resolve_pin_positions


@dataclass
class Placed:
    """Tracking info for a placed component during the algorithm."""

    instance_id: str
    catalog_id: str
    x: float
    y: float
    rotation: int
    hw: float       # half width (rotated body)
    hh: float       # half height (rotated body)
    keepout: float   # keepout_margin_mm


def score_candidate(
    cx: float, cy: float, rotation: int,
    hw: float, hh: float, keepout: float,
    instance_id: str,
    cat: Component,
    placed: list[Placed],
    catalog_map: dict[str, Component],
    net_graph: dict[str, list[NetEdge]],
    outline_verts: list[tuple[float, float]],
    outline_bounds: tuple[float, float, float, float],
    mounting_style: str,
) -> float:
    """Score a candidate position.  Higher = better.

    Combines net proximity (dominant), edge clearance, uniform clearance
    among neighbors, compactness, and mounting-style preferences.
    """
    score = 0.0

    # ── 1. Net proximity (MAIN driver) ──────────────────────────────
    for edge in net_graph.get(instance_id, []):
        other = next((p for p in placed if p.instance_id == edge.other_iid), None)
        if other is None:
            continue

        my_positions = resolve_pin_positions(edge.my_pins, cat)
        other_cat = catalog_map[other.catalog_id]
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
            score -= best_dist * W_NET_PROXIMITY

    # ── 2. Edge clearance ───────────────────────────────────────────
    edge_dist = rect_edge_clearance(cx, cy, hw, hh, outline_verts)
    score += min(edge_dist, 5.0) * W_EDGE_CLEARANCE

    # ── 3. Uniform clearance to neighbors ───────────────────────────
    if placed:
        for p in placed:
            gap = aabb_gap(cx, cy, hw, hh, p.x, p.y, p.hw, p.hh)
            target = max(keepout, p.keepout)
            if gap > 0:
                deviation = abs(gap - target)
                score -= deviation * W_CLEARANCE_UNIFORM / len(placed)

    # ── 4. Compactness ──────────────────────────────────────────────
    if placed:
        centroid_x = sum(p.x for p in placed) / len(placed)
        centroid_y = sum(p.y for p in placed) / len(placed)
        score -= math.hypot(cx - centroid_x, cy - centroid_y) * W_COMPACTNESS

    # ── 5. Bottom preference for bottom-mount components ────────────
    if mounting_style == "bottom":
        _, ymin, _, _ = outline_bounds
        score -= (cy - ymin) * W_BOTTOM_PREFERENCE

    return score

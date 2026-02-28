"""Candidate position scoring for the placer."""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.catalog.models import Component

from .geometry import pin_world_xy, rect_edge_clearance, aabb_gap, segments_cross
from .models import (
    W_NET_PROXIMITY, W_EDGE_CLEARANCE, W_COMPACTNESS,
    W_CLEARANCE_UNIFORM, W_BOTTOM_PREFERENCE, W_CROSSING,
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


# ── Net-segment types for crossing detection ──────────────────────

# A virtual wire: (net_id, endpoint_1, endpoint_2)
WireSegment = tuple[str, tuple[float, float], tuple[float, float]]


def compute_placed_segments(
    placed: list[Placed],
    catalog_map: dict[str, Component],
    net_graph: dict[str, list[NetEdge]],
) -> list[WireSegment]:
    """Compute virtual wire segments between already-placed components.

    For each pair of placed instances connected by a net, find the
    closest connected pin pair and record the straight-line segment.
    These segments are used to detect crossings when scoring new
    candidate positions — crossings imply single-layer routing is
    impossible.

    Returns list of (net_id, world_point_1, world_point_2).
    """
    placed_ids = {p.instance_id for p in placed}
    placed_map = {p.instance_id: p for p in placed}
    # De-duplicate: one segment per (net, inst_a, inst_b)
    seen: set[tuple[str, str, str]] = set()
    segments: list[WireSegment] = []

    for p in placed:
        cat_a = catalog_map.get(p.catalog_id)
        if cat_a is None:
            continue
        for edge in net_graph.get(p.instance_id, []):
            if edge.other_iid not in placed_ids:
                continue
            key = (
                edge.net_id,
                min(p.instance_id, edge.other_iid),
                max(p.instance_id, edge.other_iid),
            )
            if key in seen:
                continue
            seen.add(key)

            other_p = placed_map[edge.other_iid]
            cat_b = catalog_map.get(other_p.catalog_id)
            if cat_b is None:
                continue

            my_positions = resolve_pin_positions(edge.my_pins, cat_a)
            other_positions = resolve_pin_positions(edge.other_pins, cat_b)

            best_dist = float("inf")
            best_pair: tuple[tuple[float, float], tuple[float, float]] | None = None
            for mp in my_positions:
                wx1, wy1 = pin_world_xy(mp, p.x, p.y, p.rotation)
                for op in other_positions:
                    wx2, wy2 = pin_world_xy(op, other_p.x, other_p.y, other_p.rotation)
                    d = (wx1 - wx2) ** 2 + (wy1 - wy2) ** 2
                    if d < best_dist:
                        best_dist = d
                        best_pair = ((wx1, wy1), (wx2, wy2))

            if best_pair is not None:
                segments.append((edge.net_id, best_pair[0], best_pair[1]))

    return segments


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
    existing_segments: list[WireSegment] | None = None,
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

    # ── 6. Crossing penalty (planarity) ─────────────────────────────
    #
    # For each net edge from this candidate to an already-placed
    # instance, compute the straight-line pin-to-pin segment and
    # count how many existing (inter-instance) segments it crosses.
    # Crossings between segments of the SAME net are harmless (the
    # router handles those as a single tree).  Only different-net
    # crossings count.
    if existing_segments:
        crossings = 0
        for edge in net_graph.get(instance_id, []):
            other = next(
                (p for p in placed if p.instance_id == edge.other_iid), None,
            )
            if other is None:
                continue

            my_positions = resolve_pin_positions(edge.my_pins, cat)
            other_cat = catalog_map.get(other.catalog_id)
            if other_cat is None:
                continue
            other_positions = resolve_pin_positions(edge.other_pins, other_cat)

            # Closest pin pair for this edge
            best_d = float("inf")
            best_seg: tuple[tuple[float, float], tuple[float, float]] | None = None
            for mp in my_positions:
                wx, wy = pin_world_xy(mp, cx, cy, rotation)
                for op in other_positions:
                    owx, owy = pin_world_xy(
                        op, other.x, other.y, other.rotation,
                    )
                    d = (wx - owx) ** 2 + (wy - owy) ** 2
                    if d < best_d:
                        best_d = d
                        best_seg = ((wx, wy), (owx, owy))

            if best_seg is None:
                continue

            for seg_net_id, sp1, sp2 in existing_segments:
                if seg_net_id == edge.net_id:
                    continue  # same net — crossing is fine
                if segments_cross(best_seg[0], best_seg[1], sp1, sp2):
                    crossings += 1

        score -= crossings * W_CROSSING

    return score

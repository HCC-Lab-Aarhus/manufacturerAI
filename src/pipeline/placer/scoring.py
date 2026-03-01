"""Candidate position scoring for the placer."""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.catalog.models import Component

from .geometry import pin_world_xy, rect_edge_clearance, aabb_gap, segments_cross
from .models import (
    W_NET_PROXIMITY, W_EDGE_CLEARANCE, W_COMPACTNESS,
    W_CLEARANCE_UNIFORM, W_BOTTOM_PREFERENCE, W_CROSSING,
    W_PIN_COLLOCATION, MIN_PIN_CLEARANCE_MM,
    ROUTING_CHANNEL_MM, W_SPREAD,
    W_LARGE_EDGE_PULL, W_PIN_SIDE, W_GROUP_COHESION,
)
from .nets import NetEdge, count_shared_nets, resolve_pin_positions


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
    env_hw: float = 0.0   # half width of pin-inclusive envelope
    env_hh: float = 0.0   # half height of pin-inclusive envelope

    def __post_init__(self) -> None:
        # Default envelope to body dims if not explicitly set
        if self.env_hw == 0.0:
            self.env_hw = self.hw
        if self.env_hh == 0.0:
            self.env_hh = self.hh


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
    env_hw: float = 0.0,
    env_hh: float = 0.0,
    outline_area: float = 0.0,
    group_mates: set[str] | None = None,
) -> float:
    """Score a candidate position.  Higher = better.

    Combines net proximity (dominant), edge clearance, uniform clearance
    among neighbors, compactness, and mounting-style preferences.
    """
    # Fall back to body dims if envelope not supplied
    if env_hw == 0.0:
        env_hw = hw
    if env_hh == 0.0:
        env_hh = hh
    score = 0.0

    # ── 1. Net proximity (MAIN driver) ──────────────────────────────
    #
    # High-fanout nets (3+ instances, e.g. GND, VCC) get a boosted
    # proximity weight so their components cluster tighter.  The
    # boost is log-based to avoid over-dominating with very large
    # nets: fanout 2 → 1×, fanout 3 → ~1.6×, fanout 6 → ~2.6×.
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
            fanout_boost = 1.0 + math.log2(max(edge.fanout, 2)) - 1.0
            score -= best_dist * W_NET_PROXIMITY * fanout_boost

    # ── 2. Edge clearance ───────────────────────────────────────────
    edge_dist = rect_edge_clearance(cx, cy, hw, hh, outline_verts)
    score += min(edge_dist, 5.0) * W_EDGE_CLEARANCE

    # ── 3. Uniform clearance to neighbors ───────────────────────────
    if placed:
        for p in placed:
            gap = aabb_gap(cx, cy, env_hw, env_hh,
                           p.x, p.y, p.env_hw, p.env_hh)
            n_channels = count_shared_nets(
                instance_id, p.instance_id, net_graph,
            )
            channel_gap = n_channels * ROUTING_CHANNEL_MM
            target = max(keepout, p.keepout, channel_gap)
            if gap > 0:
                deviation = abs(gap - target)
                score -= deviation * W_CLEARANCE_UNIFORM / len(placed)

    # ── 4. Compactness (weakened when there is ample space) ────────
    if placed:
        centroid_x = sum(p.x for p in placed) / len(placed)
        centroid_y = sum(p.y for p in placed) / len(placed)
        score -= math.hypot(cx - centroid_x, cy - centroid_y) * W_COMPACTNESS

    # ── 4b. Spread preference ─────────────────────────────────
    # When the outline is much larger than the component footprints,
    # reward positions that keep a healthy minimum gap to all
    # neighbours.  The reward scales with how much slack exists.
    if placed and outline_area > 0:
        total_comp_area = env_hw * 2 * env_hh * 2
        for p in placed:
            total_comp_area += p.env_hw * 2 * p.env_hh * 2
        slack = max(0.0, 1.0 - total_comp_area / outline_area)
        if slack > 0.15:  # only kick in when >15% free
            min_gap = float("inf")
            for p in placed:
                g = aabb_gap(cx, cy, env_hw, env_hh,
                             p.x, p.y, p.env_hw, p.env_hh)
                if g < min_gap:
                    min_gap = g
            if min_gap < float("inf"):
                score += min(min_gap, 15.0) * W_SPREAD * slack

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

    # ── 7. Pin-collocation penalty ────────────────────────────────
    #
    # Penalise candidate positions where any of this component’s
    # pins land too close to a pin of an already-placed component.
    # This catches cases the envelope AABB check misses (e.g.
    # diagonal pin proximity).
    if placed:
        my_pin_world = [
            pin_world_xy(p.position_mm, cx, cy, rotation)
            for p in cat.pins
        ]
        near_pins = 0
        for p in placed:
            other_cat = catalog_map.get(p.catalog_id)
            if other_cat is None:
                continue
            for opin in other_cat.pins:
                opx, opy = pin_world_xy(
                    opin.position_mm, p.x, p.y, p.rotation,
                )
                for mpx, mpy in my_pin_world:
                    if math.hypot(mpx - opx, mpy - opy) < MIN_PIN_CLEARANCE_MM:
                        near_pins += 1
        score -= near_pins * W_PIN_COLLOCATION

    # ── 8. Large-component edge preference ────────────────────────
    #
    # Large components benefit from edge positions because routes
    # don't have to circumnavigate them.  The pull scales with the
    # component's share of the outline area; it only activates for
    # components occupying > 5 % of the board.
    if outline_area > 0:
        comp_area = env_hw * 2 * env_hh * 2
        area_ratio = comp_area / outline_area
        if area_ratio > 0.05:
            # Strength ramps linearly, capped at 3× base weight.
            strength = min(area_ratio / 0.05, 3.0)
            score -= edge_dist * W_LARGE_EDGE_PULL * strength

    # ── 9. Pin-side awareness ─────────────────────────────────────
    #
    # When this candidate connects to an already-placed component,
    # check whether the candidate approaches from the *same* side as
    # the connecting pins.  Wrong-side approach forces the route to
    # go around the placed component — the penalty is proportional
    # to the placed component's size (half-perimeter).
    for edge in net_graph.get(instance_id, []):
        other = next((p for p in placed if p.instance_id == edge.other_iid), None)
        if other is None:
            continue
        other_cat = catalog_map.get(other.catalog_id)
        if other_cat is None:
            continue
        other_positions = resolve_pin_positions(edge.other_pins, other_cat)
        if not other_positions:
            continue

        # Average pin position on the other component (local coords)
        avg_px = sum(p[0] for p in other_positions) / len(other_positions)
        avg_py = sum(p[1] for p in other_positions) / len(other_positions)
        # Pin direction in world space (where the pin faces)
        pin_wx, pin_wy = pin_world_xy(
            (avg_px, avg_py), other.x, other.y, other.rotation,
        )
        pin_dx = pin_wx - other.x
        pin_dy = pin_wy - other.y
        pin_len = math.hypot(pin_dx, pin_dy)
        if pin_len < 0.1:
            continue  # pin at centre — no directional preference

        # Direction from placed component to candidate
        cand_dx = cx - other.x
        cand_dy = cy - other.y
        # Dot product: negative ⇒ candidate is on opposite side
        dot = (pin_dx * cand_dx + pin_dy * cand_dy) / pin_len
        if dot < 0:
            other_size = other.env_hw + other.env_hh
            score += dot / max(math.hypot(cand_dx, cand_dy), 1.0) * other_size * W_PIN_SIDE

    # ── 10. Group cohesion ────────────────────────────────────────
    #
    # If the current component belongs to a connectivity group,
    # reward positions near the centroid of already-placed group
    # mates.  This keeps clusters spatially compact even when the
    # overall centroid (term 4) drifts toward other groups.
    if group_mates and placed:
        mates_placed = [p for p in placed if p.instance_id in group_mates]
        if mates_placed:
            gx = sum(p.x for p in mates_placed) / len(mates_placed)
            gy = sum(p.y for p in mates_placed) / len(mates_placed)
            score -= math.hypot(cx - gx, cy - gy) * W_GROUP_COHESION

    return score

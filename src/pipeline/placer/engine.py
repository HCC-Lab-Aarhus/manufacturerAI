"""Main placement engine — grid-search placer with hard/soft constraints."""

from __future__ import annotations

import logging
import math

from shapely.geometry import Polygon, box as shapely_box
from shapely.prepared import prep as shapely_prep

from src.catalog.models import CatalogResult
from src.pipeline.design.models import DesignSpec, Outline

from .geometry import (
    footprint_halfdims, footprint_envelope_halfdims, footprint_area,
    rect_inside_polygon, rect_edge_clearance, aabb_gap,
    pin_world_xy,
)
from .models import (
    PlacedComponent, FullPlacement, PlacementError,
    GRID_STEP_MM, VALID_ROTATIONS, MIN_EDGE_CLEARANCE_MM,
    ROUTING_CHANNEL_MM, MIN_PIN_CLEARANCE_MM,
)
from .nets import build_net_graph, count_shared_nets, build_placement_groups
from .scoring import Placed, score_candidate, compute_placed_segments


log = logging.getLogger(__name__)


# ── Side-mount helpers ─────────────────────────────────────────────


def _edge_direction(
    outline: Outline, edge_index: int,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return (start_vertex, end_vertex) for an outline edge."""
    pts = outline.vertices
    n = len(pts)
    return pts[edge_index % n], pts[(edge_index + 1) % n]


def _edge_rotation(
    p1: tuple[float, float], p2: tuple[float, float],
) -> int:
    """Compute the nearest 90° rotation for a component on an edge.

    The component's "forward" direction should point outward through
    the wall.  For clockwise winding, the outward normal is to the
    right of the edge direction.
    """
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    angle = math.degrees(math.atan2(dy, dx))
    normal_angle = angle - 90
    snapped = round(normal_angle / 90) * 90
    return int(snapped) % 360


def _snap_to_edge(
    x_mm: float, y_mm: float,
    outline: Outline, edge_index: int,
) -> tuple[float, float, int]:
    """Snap a point to the nearest position on an outline edge.

    Returns (snapped_x, snapped_y, rotation_deg).
    """
    p1, p2 = _edge_direction(outline, edge_index)
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-12:
        return (p1[0], p1[1], 0)

    t = max(0.0, min(1.0, ((x_mm - p1[0]) * dx + (y_mm - p1[1]) * dy) / length_sq))
    snap_x = p1[0] + t * dx
    snap_y = p1[1] + t * dy
    rotation = _edge_rotation(p1, p2)
    return (snap_x, snap_y, rotation)


# ── Main placement function ───────────────────────────────────────


def place_components(
    design: DesignSpec,
    catalog: CatalogResult,
    *,
    grid_step: float = GRID_STEP_MM,
) -> FullPlacement:
    """Place all components inside the outline.

    UI components are fixed at their agent-specified positions.
    Non-UI components are auto-placed via exhaustive grid search,
    optimising for net proximity, uniform clearance, and compactness.

    Parameters
    ----------
    design : DesignSpec
        The agent's design specification.
    catalog : CatalogResult
        The loaded component catalog.
    grid_step : float
        Grid scan resolution in mm (default 1.0).

    Returns
    -------
    FullPlacement
        All components positioned with (x, y, rotation).

    Raises
    ------
    PlacementError
        If a component cannot be legally placed.
    """
    catalog_map = {c.id: c for c in catalog.components}
    outline_poly = Polygon(design.outline.vertices)
    outline_verts = design.outline.vertices
    xmin, ymin, xmax, ymax = outline_poly.bounds
    outline_bounds = (xmin, ymin, xmax, ymax)

    if not outline_poly.is_valid or outline_poly.area <= 0:
        raise PlacementError("_outline", "_outline",
                             "Outline polygon is invalid or has zero area")

    # Build net connectivity graph
    net_graph = build_net_graph(design.nets)
    outline_area = outline_poly.area

    # Resolve effective mounting style for each instance
    effective_style: dict[str, str] = {}
    for ci in design.components:
        cat = catalog_map.get(ci.catalog_id)
        if cat:
            effective_style[ci.instance_id] = ci.mounting_style or cat.mounting.style

    # ── 1. Place UI components (fixed positions) ───────────────────

    placed: list[Placed] = []
    ui_ids: set[str] = set()

    for up in design.ui_placements:
        ci = next(c for c in design.components if c.instance_id == up.instance_id)
        cat = catalog_map[ci.catalog_id]
        style = effective_style.get(ci.instance_id, cat.mounting.style)

        if style == "side" and up.edge_index is not None:
            x, y, rot = _snap_to_edge(up.x_mm, up.y_mm, design.outline, up.edge_index)
        else:
            x, y, rot = up.x_mm, up.y_mm, 0

        hw, hh = footprint_halfdims(cat, rot)
        ehw, ehh = footprint_envelope_halfdims(cat, rot)
        placed.append(Placed(
            instance_id=ci.instance_id,
            catalog_id=ci.catalog_id,
            x=x, y=y, rotation=rot,
            hw=hw, hh=hh,
            keepout=cat.mounting.keepout_margin_mm,
            env_hw=ehw, env_hh=ehh,
        ))
        ui_ids.add(ci.instance_id)
        log.info("UI-placed %s at (%.1f, %.1f) rot=%d°",
                 ci.instance_id, x, y, rot)

    # ── 2. Sort remaining by connectivity group, then area ─────────

    to_place_ids = [
        ci.instance_id for ci in design.components
        if ci.instance_id not in ui_ids
    ]
    area_map = {
        ci.instance_id: footprint_area(catalog_map[ci.catalog_id])
        for ci in design.components
        if ci.instance_id not in ui_ids
    }
    groups = build_placement_groups(to_place_ids, net_graph, area_map)

    # Build a lookup: instance_id -> set of group-mates (excluding self)
    group_mates_map: dict[str, set[str]] = {}
    for group in groups:
        group_set = set(group)
        for iid in group:
            group_mates_map[iid] = group_set - {iid}

    # Flatten groups into a single ordered list, preserving group
    # contiguity and hub-first ordering within each group.
    ordered_ids = [iid for group in groups for iid in group]
    ci_map = {ci.instance_id: ci for ci in design.components}
    to_place = [ci_map[iid] for iid in ordered_ids]

    # ── 3. Auto-place each component via grid search ───────────────
    # Precompute for speed: prepared polygon for O(1) containment,
    # shared-nets cache (persists across components), and squared
    # pin clearance threshold to avoid sqrt in inner loop.
    prep_poly = shapely_prep(outline_poly)
    shared_nets_cache: dict[tuple[str, str], int] = {}
    _min_pin_sq = MIN_PIN_CLEARANCE_MM * MIN_PIN_CLEARANCE_MM
    for ci in to_place:
        cat = catalog_map[ci.catalog_id]
        style = effective_style.get(ci.instance_id, cat.mounting.style)
        keepout = cat.mounting.keepout_margin_mm

        # Precompute existing virtual wire segments between all
        # already-placed components (for crossing detection).
        existing_segments = compute_placed_segments(
            placed, catalog_map, net_graph,
        )

        # Precompute placed-component pin world positions — constant
        # during this component's grid scan (saves trig per cell).
        placed_pin_positions: dict[str, list[tuple[float, float]]] = {}
        for _p in placed:
            _p_cat = catalog_map.get(_p.catalog_id)
            if _p_cat is not None:
                placed_pin_positions[_p.instance_id] = [
                    pin_world_xy(pin.position_mm, _p.x, _p.y, _p.rotation)
                    for pin in _p_cat.pins
                ]

        best_pos: tuple[float, float] | None = None
        best_rot = 0
        best_score = -float("inf")

        for rotation in VALID_ROTATIONS:
            hw, hh = footprint_halfdims(cat, rotation)
            ehw, ehh = footprint_envelope_halfdims(cat, rotation)

            # Inflated half-dims: the envelope (body + pins) + edge
            # clearance must fit inside the outline.
            ihw = ehw + MIN_EDGE_CLEARANCE_MM
            ihh = ehh + MIN_EDGE_CLEARANCE_MM

            # Scan range: outline bounding box shrunk by inflated
            # half-dims.
            scan_xmin = xmin + ihw
            scan_xmax = xmax - ihw
            scan_ymin = ymin + ihh
            scan_ymax = ymax - ihh

            if scan_xmin > scan_xmax or scan_ymin > scan_ymax:
                continue

            # Precompute rotated pin offsets (rotation-dependent,
            # position-independent — just add cx, cy in inner loop).
            _rad = math.radians(rotation)
            _cos_r, _sin_r = math.cos(_rad), math.sin(_rad)
            my_pin_offsets = [
                (pin.position_mm[0] * _cos_r - pin.position_mm[1] * _sin_r,
                 pin.position_mm[0] * _sin_r + pin.position_mm[1] * _cos_r)
                for pin in cat.pins
            ]

            cx = scan_xmin
            while cx <= scan_xmax + 1e-6:
                cy = scan_ymin
                while cy <= scan_ymax + 1e-6:
                    # Hard constraint 1: inflated footprint inside outline
                    # Uses prepared polygon for fast repeated containment.
                    if not prep_poly.contains(
                        shapely_box(cx - ihw, cy - ihh, cx + ihw, cy + ihh)
                    ):
                        cy += grid_step
                        continue

                    # Hard constraint 2: no overlap (using pin envelopes)
                    # The required gap accounts for both keepout and
                    # the number of trace channels that must pass
                    # between the two components.
                    overlap = False
                    for p in placed:
                        _sn_key = (min(ci.instance_id, p.instance_id),
                                   max(ci.instance_id, p.instance_id))
                        if _sn_key not in shared_nets_cache:
                            shared_nets_cache[_sn_key] = count_shared_nets(
                                ci.instance_id, p.instance_id, net_graph,
                            )
                        n_channels = shared_nets_cache[_sn_key]
                        channel_gap = n_channels * ROUTING_CHANNEL_MM
                        required_gap = max(keepout, p.keepout, channel_gap)
                        actual_gap = aabb_gap(
                            cx, cy, ehw, ehh,
                            p.x, p.y, p.env_hw, p.env_hh,
                        )
                        if actual_gap < required_gap:
                            overlap = True
                            break
                    if overlap:
                        cy += grid_step
                        continue

                    # Hard constraint 3: minimum edge clearance
                    # Check against envelope so pins don't land
                    # outside or too close to the outline wall.
                    edge_dist = rect_edge_clearance(
                        cx, cy, ehw, ehh, outline_verts)
                    if edge_dist < MIN_EDGE_CLEARANCE_MM:
                        cy += grid_step
                        continue
                    # Hard constraint 4: pin-to-pin clearance
                    # Uses precomputed pin offsets and placed pin
                    # world positions; squared distance avoids sqrt.
                    pin_clash = False
                    my_pins_world = [(cx + ox, cy + oy)
                                     for ox, oy in my_pin_offsets]
                    for p in placed:
                        if pin_clash:
                            break
                        _other_pins = placed_pin_positions.get(
                            p.instance_id, ())
                        for opx, opy in _other_pins:
                            if pin_clash:
                                break
                            for mpx, mpy in my_pins_world:
                                dx, dy = mpx - opx, mpy - opy
                                if dx * dx + dy * dy < _min_pin_sq:
                                    pin_clash = True
                                    break
                    if pin_clash:
                        cy += grid_step
                        continue
                    # Soft constraints: score position
                    score = score_candidate(
                        cx, cy, rotation, hw, hh, keepout,
                        ci.instance_id, cat,
                        placed, catalog_map, net_graph,
                        outline_verts, outline_bounds,
                        style,
                        existing_segments,
                        env_hw=ehw, env_hh=ehh,
                        outline_area=outline_area,
                        group_mates=group_mates_map.get(ci.instance_id),
                    )

                    if score > best_score:
                        best_score = score
                        best_pos = (cx, cy)
                        best_rot = rotation

                    cy += grid_step
                cx += grid_step

        if best_pos is None:
            body_w = cat.body.width_mm or cat.body.diameter_mm or 0
            body_h = cat.body.length_mm or cat.body.diameter_mm or 0
            raise PlacementError(
                ci.instance_id, ci.catalog_id,
                f"No valid position found inside the "
                f"{xmax - xmin:.0f}×{ymax - ymin:.0f}mm outline.  "
                f"Body is {body_w:.1f}×{body_h:.1f}mm with "
                f"{keepout:.1f}mm keepout.  "
                f"Try widening the outline or repositioning other "
                f"components.",
            )

        hw_final, hh_final = footprint_halfdims(cat, best_rot)
        ehw_final, ehh_final = footprint_envelope_halfdims(cat, best_rot)
        placed.append(Placed(
            instance_id=ci.instance_id,
            catalog_id=ci.catalog_id,
            x=best_pos[0], y=best_pos[1],
            rotation=best_rot,
            hw=hw_final, hh=hh_final,
            keepout=keepout,
            env_hw=ehw_final, env_hh=ehh_final,
        ))
        log.info(
            "Auto-placed %s at (%.1f, %.1f) rot=%d° score=%.2f",
            ci.instance_id, best_pos[0], best_pos[1], best_rot, best_score,
        )

    # ── 4. Build output ────────────────────────────────────────────

    result_components = [
        PlacedComponent(
            instance_id=p.instance_id,
            catalog_id=p.catalog_id,
            x_mm=round(p.x, 2),
            y_mm=round(p.y, 2),
            rotation_deg=p.rotation,
        )
        for p in placed
    ]

    return FullPlacement(
        components=result_components,
        outline=design.outline,
        nets=design.nets,
    )

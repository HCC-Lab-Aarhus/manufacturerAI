"""Main placement engine — grid-search placer with hard/soft constraints."""

from __future__ import annotations

import logging
import math

from shapely.geometry import Polygon

from src.catalog.models import CatalogResult
from src.pipeline.design.models import DesignSpec, Outline

from .geometry import (
    footprint_halfdims, footprint_area,
    rect_inside_polygon, rect_edge_clearance, aabb_gap,
)
from .models import (
    PlacedComponent, FullPlacement, PlacementError,
    GRID_STEP_MM, VALID_ROTATIONS, MIN_EDGE_CLEARANCE_MM,
)
from .nets import build_net_graph
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
        placed.append(Placed(
            instance_id=ci.instance_id,
            catalog_id=ci.catalog_id,
            x=x, y=y, rotation=rot,
            hw=hw, hh=hh,
            keepout=cat.mounting.keepout_margin_mm,
        ))
        ui_ids.add(ci.instance_id)
        log.info("UI-placed %s at (%.1f, %.1f) rot=%d°",
                 ci.instance_id, x, y, rot)

    # ── 2. Sort remaining by footprint area (largest first) ────────

    to_place = [
        ci for ci in design.components
        if ci.instance_id not in ui_ids
    ]
    to_place.sort(
        key=lambda ci: footprint_area(catalog_map[ci.catalog_id]),
        reverse=True,
    )

    # ── 3. Auto-place each component via grid search ───────────────

    for ci in to_place:
        cat = catalog_map[ci.catalog_id]
        style = effective_style.get(ci.instance_id, cat.mounting.style)
        keepout = cat.mounting.keepout_margin_mm

        # Precompute existing virtual wire segments between all
        # already-placed components (for crossing detection).
        existing_segments = compute_placed_segments(
            placed, catalog_map, net_graph,
        )

        best_pos: tuple[float, float] | None = None
        best_rot = 0
        best_score = -float("inf")

        for rotation in VALID_ROTATIONS:
            hw, hh = footprint_halfdims(cat, rotation)

            # Inflated half-dims: the body + edge clearance must
            # fit inside the outline.
            ihw = hw + MIN_EDGE_CLEARANCE_MM
            ihh = hh + MIN_EDGE_CLEARANCE_MM

            # Scan range: outline bounding box shrunk by inflated
            # half-dims.
            scan_xmin = xmin + ihw
            scan_xmax = xmax - ihw
            scan_ymin = ymin + ihh
            scan_ymax = ymax - ihh

            if scan_xmin > scan_xmax or scan_ymin > scan_ymax:
                continue

            cx = scan_xmin
            while cx <= scan_xmax + 1e-6:
                cy = scan_ymin
                while cy <= scan_ymax + 1e-6:
                    # Hard constraint 1: inflated footprint inside outline
                    if not rect_inside_polygon(cx, cy, ihw, ihh, outline_poly):
                        cy += grid_step
                        continue

                    # Hard constraint 2: no overlap
                    overlap = False
                    for p in placed:
                        required_gap = max(keepout, p.keepout)
                        actual_gap = aabb_gap(
                            cx, cy, hw, hh,
                            p.x, p.y, p.hw, p.hh,
                        )
                        if actual_gap < required_gap:
                            overlap = True
                            break
                    if overlap:
                        cy += grid_step
                        continue

                    # Hard constraint 3: minimum edge clearance
                    edge_dist = rect_edge_clearance(
                        cx, cy, hw, hh, outline_verts)
                    if edge_dist < MIN_EDGE_CLEARANCE_MM:
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
        placed.append(Placed(
            instance_id=ci.instance_id,
            catalog_id=ci.catalog_id,
            x=best_pos[0], y=best_pos[1],
            rotation=best_rot,
            hw=hw_final, hh=hh_final,
            keepout=keepout,
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

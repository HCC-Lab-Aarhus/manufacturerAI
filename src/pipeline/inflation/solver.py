"""Polygon inflation solver — grows polygons using Shapely buffer operations.

Each iteration uniformly buffers every polygon, then clips to:
  - the board outline (minus edge clearance, applied by caller)
  - obstacle keepout zones
  - foreign pin clearance circles
  - foreign polygon clearance zones
  - a maximum expansion cap from the initial shape

All constraints produce clean geometric boundaries because they use
Shapely boolean operations rather than per-vertex manipulation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.ops import unary_union

log = logging.getLogger(__name__)


@dataclass
class NetPolygon:
    net_id: str
    polygon: Polygon
    inside_pins: set[str] = field(default_factory=set)
    outside_pins: set[str] = field(default_factory=set)


MAX_ITERATIONS = 200
STEP_MM = 0.3
MIN_GROWTH_MM2 = 0.1
ITER_SIMPLIFY_TOL = 0.05
SMOOTH_MM = 0.5
FINAL_SIMPLIFY_TOL = 0.15


def inflate(
    net_polygons: list[NetPolygon],
    outline: Polygon,
    obstacle_union: Polygon,
    *,
    pin_positions: dict[str, tuple[float, float]],
    pin_pads: dict[str, Polygon],
    trace_clearance: float,
    pin_clearance: float,
    max_half: float,
    min_half: float,
) -> None:
    if not net_polygons:
        return

    max_expand = max_half - min_half

    frozen_inside: list[set[str]] = [set(np_.inside_pins) for np_ in net_polygons]
    frozen_outside: list[set[str]] = [set(np_.outside_pins) for np_ in net_polygons]

    available: list[Polygon | MultiPolygon] = []
    for i, np_ in enumerate(net_polygons):
        zone = np_.polygon.buffer(max_expand).intersection(outline)
        if not obstacle_union.is_empty:
            zone = zone.difference(obstacle_union)
        keepouts = []
        for pid in frozen_outside[i]:
            if pid in pin_pads:
                keepouts.append(pin_pads[pid].buffer(pin_clearance, quad_segs=8))
            elif pid in pin_positions:
                keepouts.append(
                    Point(pin_positions[pid]).buffer(pin_clearance, quad_segs=8)
                )
        if keepouts:
            zone = zone.difference(unary_union(keepouts))
        available.append(zone)

    for _it in range(MAX_ITERATIONS):
        total_growth = 0.0
        buffered_clear = [np_.polygon.buffer(trace_clearance) for np_ in net_polygons]

        for i, np_ in enumerate(net_polygons):
            grown = np_.polygon.buffer(STEP_MM, quad_segs=8)
            clipped = grown.intersection(available[i])

            foreign = [b for j, b in enumerate(buffered_clear) if j != i]
            if foreign:
                clipped = clipped.difference(unary_union(foreign))

            if isinstance(clipped, MultiPolygon):
                clipped = max(clipped.geoms, key=lambda g: g.area)
            if not isinstance(clipped, Polygon) or clipped.is_empty:
                continue

            clipped = clipped.simplify(ITER_SIMPLIFY_TOL, preserve_topology=True)
            if not isinstance(clipped, Polygon) or clipped.is_empty:
                continue

            if _pin_invariant_violated(clipped, pin_positions,
                                       frozen_inside[i], frozen_outside[i]):
                continue

            growth = clipped.area - np_.polygon.area
            if growth > 0:
                total_growth += growth
                np_.polygon = clipped

        if total_growth < MIN_GROWTH_MM2:
            log.info("Inflation converged at iteration %d", _it)
            break

    for np_ in net_polygons:
        s = np_.polygon.buffer(SMOOTH_MM, quad_segs=16).buffer(-SMOOTH_MM, quad_segs=16)
        s = s.buffer(-SMOOTH_MM, quad_segs=16).buffer(SMOOTH_MM, quad_segs=16)
        s = s.simplify(FINAL_SIMPLIFY_TOL, preserve_topology=True)
        if isinstance(s, MultiPolygon):
            s = max(s.geoms, key=lambda g: g.area)
        if isinstance(s, Polygon) and not s.is_empty:
            idx = net_polygons.index(np_)
            if not _pin_invariant_violated(
                s, pin_positions, frozen_inside[idx], frozen_outside[idx],
            ):
                np_.polygon = s

    snapshot = [np_.polygon for np_ in net_polygons]
    for i, np_ in enumerate(net_polygons):
        foreign = [
            snapshot[j].buffer(trace_clearance / 2)
            for j in range(len(net_polygons)) if j != i
        ]
        if not foreign:
            continue
        trimmed = np_.polygon.difference(unary_union(foreign))
        if isinstance(trimmed, MultiPolygon):
            trimmed = max(trimmed.geoms, key=lambda g: g.area)
        if isinstance(trimmed, Polygon) and not trimmed.is_empty:
            trimmed = trimmed.simplify(FINAL_SIMPLIFY_TOL, preserve_topology=True)
            if isinstance(trimmed, MultiPolygon):
                trimmed = max(trimmed.geoms, key=lambda g: g.area)
            if isinstance(trimmed, Polygon) and not trimmed.is_empty:
                if not _pin_invariant_violated(
                    trimmed, pin_positions, frozen_inside[i], frozen_outside[i],
                ):
                    np_.polygon = trimmed


def _pin_invariant_violated(
    poly: Polygon,
    pin_positions: dict[str, tuple[float, float]],
    frozen_inside: set[str],
    frozen_outside: set[str],
) -> bool:
    for pid in frozen_inside:
        pos = pin_positions.get(pid)
        if pos is None:
            continue
        pt = Point(pos)
        if not poly.contains(pt) and poly.boundary.distance(pt) > 0.2:
            return True
    for pid in frozen_outside:
        pos = pin_positions.get(pid)
        if pos is None:
            continue
        if poly.contains(Point(pos)):
            return True
    return False

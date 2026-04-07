"""Trace inflation entry point."""

from __future__ import annotations

import logging
from collections import defaultdict

from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from src.pipeline.config import TRACE_RULES
from src.pipeline.router.models import InflatedTrace, RoutingResult

from .solver import NetPolygon, inflate

log = logging.getLogger(__name__)


def _build_net_polygons(
    result: RoutingResult,
    min_half: float,
    pin_positions: dict[str, tuple[float, float]],
    net_pin_ids: dict[str, set[str]],
) -> list[NetPolygon]:
    net_paths: dict[str, list[list[tuple[float, float]]]] = defaultdict(list)
    for trace in result.traces:
        if trace.path:
            net_paths[trace.net_id].append(list(trace.path))

    all_pin_ids = set(pin_positions.keys())
    net_polygons: list[NetPolygon] = []

    for net_id, paths in net_paths.items():
        own_pins = net_pin_ids.get(net_id, set())

        buffers: list[Polygon] = []
        for path in paths:
            if len(path) >= 2:
                buffers.append(LineString(path).buffer(min_half, cap_style="round", quad_segs=6))
            else:
                buffers.append(Point(path[0]).buffer(min_half, quad_segs=6))

        for pid in own_pins:
            pos = pin_positions.get(pid)
            if pos is not None:
                buffers.append(Point(pos).buffer(min_half, quad_segs=6))

        merged = unary_union(buffers)
        if isinstance(merged, MultiPolygon):
            bridged = merged.buffer(0.3).buffer(-0.1)
            if isinstance(bridged, Polygon) and not bridged.is_empty:
                merged = bridged
            else:
                merged = max(merged.geoms, key=lambda g: g.area)

        if not isinstance(merged, Polygon) or merged.is_empty:
            continue

        inside: set[str] = set()
        outside: set[str] = set()
        for pid in all_pin_ids:
            pos = pin_positions.get(pid)
            if pos is None:
                continue
            pt = Point(pos)
            if merged.contains(pt) or merged.boundary.distance(pt) < 0.1:
                inside.add(pid)
            elif pid not in own_pins:
                outside.add(pid)

        net_polygons.append(NetPolygon(
            net_id=net_id,
            polygon=merged,
            inside_pins=inside,
            outside_pins=outside,
        ))

    return net_polygons


def _resolve_routed_pins(
    result: RoutingResult,
    pin_positions: dict[str, tuple[float, float]],
    net_pin_ids: dict[str, set[str]],
    snap_mm: float = 0.5,
) -> dict[str, set[str]]:
    """Add physical pins that traces actually connect to into each net's own-pin set."""
    import numpy as np

    if not pin_positions:
        return {nid: set(pins) for nid, pins in net_pin_ids.items()}

    pids = list(pin_positions.keys())
    positions = np.array([pin_positions[pid] for pid in pids], dtype=np.float64)

    resolved = {nid: set(pins) for nid, pins in net_pin_ids.items()}
    for trace in result.traces:
        if not trace.path:
            continue
        own = resolved.setdefault(trace.net_id, set())
        for endpoint in (trace.path[0], trace.path[-1]):
            ep = np.array(endpoint, dtype=np.float64)
            dists = np.hypot(positions[:, 0] - ep[0], positions[:, 1] - ep[1])
            idx = int(np.argmin(dists))
            if dists[idx] < snap_mm:
                own.add(pids[idx])
    return resolved


def inflate_traces(
    result: RoutingResult,
    outline: Polygon,
    obstacles: list[Polygon] | None = None,
    *,
    max_width_mm: float = TRACE_RULES.max_trace_width_mm,
    edge_clearance_mm: float = TRACE_RULES.edge_clearance_mm,
    min_wall_gap_mm: float = TRACE_RULES.trace_clearance_mm,
    pin_clearance_mm: float = TRACE_RULES.pin_clearance_mm,
    pin_positions: dict[str, tuple[float, float]] | None = None,
    pin_pads: dict[str, Polygon] | None = None,
    net_pin_ids: dict[str, set[str]] | None = None,
) -> list[InflatedTrace]:
    if not result.traces:
        return []

    obstacles = obstacles or []
    min_half = TRACE_RULES.trace_width_mm / 2.0
    max_half = max_width_mm / 2.0

    inset_outline = outline.buffer(-edge_clearance_mm)
    if inset_outline.is_empty or not inset_outline.is_valid:
        inset_outline = outline.buffer(-edge_clearance_mm / 2)
    if inset_outline.is_empty:
        inset_outline = outline

    obstacle_union = unary_union(obstacles) if obstacles else Polygon()
    pin_positions = pin_positions or {}
    pin_pads = pin_pads or {}
    net_pin_ids = dict(net_pin_ids or {})

    net_pin_ids = _resolve_routed_pins(result, pin_positions, net_pin_ids)

    net_polys = _build_net_polygons(result, min_half, pin_positions, net_pin_ids)

    inflate(
        net_polys,
        inset_outline,
        obstacle_union,
        pin_positions=pin_positions,
        pin_pads=pin_pads or {},
        trace_clearance=min_wall_gap_mm,
        pin_clearance=pin_clearance_mm,
        max_half=max_half,
        min_half=min_half,
    )

    net_paths: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for trace in result.traces:
        net_paths[trace.net_id].extend(trace.path)

    inflated: list[InflatedTrace] = []

    for np_ in net_polys:
        poly = np_.polygon
        if poly.is_empty:
            continue

        own_pins = net_pin_ids.get(np_.net_id, set())
        pads = []
        for pid in own_pins:
            if pid in pin_pads:
                pads.append(pin_pads[pid])
            elif pid in pin_positions:
                pads.append(Point(pin_positions[pid]).buffer(min_half, quad_segs=6))
        if pads:
            poly = unary_union([poly] + pads)
            if isinstance(poly, MultiPolygon):
                poly = max(poly.geoms, key=lambda g: g.area)

        if not isinstance(poly, Polygon) or poly.is_empty:
            continue

        inflated.append(InflatedTrace(
            net_id=np_.net_id,
            centreline=net_paths.get(np_.net_id, []),
            polygon=poly,
        ))

    log.info("Inflated %d traces", len(inflated))
    return inflated

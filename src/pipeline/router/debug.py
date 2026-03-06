"""Self-contained debug grid generation for the routing viewport.

Builds per-net obstacle maps from scratch using only the routing
inputs and outputs — completely independent of the live routing grid.

Cell categories in the output:
    0  FREE              (transparent)
    1  TRACE_CLEARANCE   other nets' clearance zones
    2  PERMANENT         component bodies / board edges / outside outline
    3  TRACE_PATH        other nets' actual trace cells
    4  VORONOI           foreign-pin Voronoi clearance
"""

from __future__ import annotations

import base64
import math

from shapely.geometry import Polygon

from src.catalog.models import CatalogResult
from src.pipeline.placer.models import FullPlacement
from src.pipeline.placer.geometry import footprint_halfdims

from .grid import RoutingGrid, FREE, BLOCKED, PERMANENTLY_BLOCKED, TRACE_PATH
from .models import RouterConfig
from .pins import pin_world_xy

# Debug-only cell values (not used by the real grid)
_VORONOI = 4


def build_debug_grids(
    placement: FullPlacement,
    catalog: CatalogResult,
    routed_paths: dict[str, list[list[tuple[int, int]]]],
    routed_pads: dict[str, list],
    *,
    config: RouterConfig | None = None,
) -> list[dict]:
    """Build a per-net debug snapshot showing every obstacle except that
    net's own traces.

    Each snapshot is an independent reconstruction — no reliance on the
    engine's internal grid state.
    """
    if config is None:
        config = RouterConfig()

    catalog_map = {c.id: c for c in catalog.components}
    outline_poly = Polygon(placement.outline.vertices)

    if not outline_poly.is_valid or outline_poly.area <= 0:
        return []

    # -- Build a fresh grid with component blocking ----------------
    grid = RoutingGrid(
        outline_poly,
        resolution=config.grid_resolution_mm,
        edge_clearance=config.edge_clearance_mm,
        trace_width_mm=config.trace_width_mm,
        trace_clearance_mm=config.trace_clearance_mm,
    )
    pad_radius = max(1, math.ceil(
        (config.trace_width_mm / 2 + config.trace_clearance_mm)
        / config.grid_resolution_mm
    ))
    _block_components(grid, placement, catalog_map, pad_radius)

    # -- Pin cell map + Voronoi ------------------------------------
    all_pin_cells = _collect_pin_cells(placement, catalog, grid)
    pin_clearance_cells = max(1, math.ceil(
        (config.trace_width_mm / 2 + config.pin_clearance_mm)
        / config.grid_resolution_mm
    ))
    pin_voronoi = _build_voronoi(all_pin_cells, grid, pin_clearance_cells)

    # -- Commit every routed net's traces to the fresh grid --------
    for nid, paths in routed_paths.items():
        for path in paths:
            grid.block_trace(path, net_id=nid)

    # -- Snapshot each net -----------------------------------------
    W = grid.width
    H = grid.height
    base_cells = bytes(grid._cells)
    clearance_cells = max(1, math.ceil(
        (config.trace_width_mm / 2 + config.trace_clearance_mm)
        / config.grid_resolution_mm
    ))

    results: list[dict] = []
    for nid, pads in routed_pads.items():
        cells = bytearray(base_cells)

        # Erase this net's own trace + clearance
        path_set: set[tuple[int, int]] = set()
        for p in routed_paths.get(nid, []):
            path_set.update(p)

        for gx, gy in path_set:
            for dy in range(-clearance_cells, clearance_cells + 1):
                for dx in range(-clearance_cells, clearance_cells + 1):
                    nx, ny = gx + dx, gy + dy
                    if 0 <= nx < W and 0 <= ny < H:
                        v = cells[ny * W + nx]
                        if v == BLOCKED or v == TRACE_PATH:
                            cells[ny * W + nx] = FREE

        # Stamp Voronoi clearance for foreign pins
        if pin_voronoi:
            net_pin_keys = {
                f"{pad.instance_id}:{pad.pin_id}" for pad in pads
            }
            for flat, pin_key in pin_voronoi.items():
                if pin_key not in net_pin_keys and cells[flat] == FREE:
                    cells[flat] = _VORONOI

        results.append({
            "net_id": nid,
            "layer": "keepout",
            "width": W,
            "height": H,
            "origin_x": grid.origin_x,
            "origin_y": grid.origin_y,
            "resolution": grid.resolution,
            "cells": base64.b64encode(bytes(cells)).decode("ascii"),
        })

    # -- Ownership layers (global, not per-net) --------------------
    net_ids = sorted(routed_paths.keys())
    net_index = {nid: i + 1 for i, nid in enumerate(net_ids)}

    combined_map = bytearray(W * H)
    for flat, owner in grid._trace_owner.items():
        idx = net_index.get(owner, 0)
        if idx:
            combined_map[flat] = idx
    for flat, owners in grid._clearance_owner.items():
        if combined_map[flat]:
            continue
        for owner in owners:
            idx = net_index.get(owner, 0)
            if idx:
                combined_map[flat] = idx
                break

    palette = {nid: i + 1 for i, nid in enumerate(net_ids)}

    results.append({
        "layer": "combined_owner",
        "width": W,
        "height": H,
        "origin_x": grid.origin_x,
        "origin_y": grid.origin_y,
        "resolution": grid.resolution,
        "cells": base64.b64encode(bytes(combined_map)).decode("ascii"),
        "palette": palette,
    })

    return results


# ── Internal helpers (mirror of engine logic, kept minimal) ────────


def _block_components(
    grid: RoutingGrid,
    placement: FullPlacement,
    catalog_map: dict,
    pad_radius: int,
) -> None:
    for pc in placement.components:
        cat = catalog_map.get(pc.catalog_id)
        if cat is None or not cat.mounting.blocks_routing:
            continue
        hw, hh = footprint_halfdims(cat, pc.rotation_deg)
        keepout = cat.mounting.keepout_margin_mm
        grid.block_rect_world(
            pc.x_mm, pc.y_mm,
            hw + keepout, hh + keepout,
            permanent=True,
        )

    for pc in placement.components:
        cat = catalog_map.get(pc.catalog_id)
        if cat is None:
            continue
        for pin in cat.pins:
            wx, wy = pin_world_xy(
                pin.position_mm, pc.x_mm, pc.y_mm, pc.rotation_deg,
            )
            gx, gy = grid.world_to_grid(wx, wy)
            for dx in range(-pad_radius, pad_radius + 1):
                for dy in range(-pad_radius, pad_radius + 1):
                    grid.force_free_cell(gx + dx, gy + dy)
                    grid.protect_cell(gx + dx, gy + dy)

    for pc in placement.components:
        cat = catalog_map.get(pc.catalog_id)
        if cat is None or not cat.mounting.blocks_routing:
            continue
        hw, hh = footprint_halfdims(cat, pc.rotation_deg)
        grid.block_rect_world(pc.x_mm, pc.y_mm, hw, hh, permanent=True)

    for pc in placement.components:
        cat = catalog_map.get(pc.catalog_id)
        if cat is None or not cat.mounting.blocks_routing:
            continue
        for pin in cat.pins:
            wx, wy = pin_world_xy(
                pin.position_mm, pc.x_mm, pc.y_mm, pc.rotation_deg,
            )
            gx, gy = grid.world_to_grid(wx, wy)
            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    grid.force_free_cell(gx + dx, gy + dy)
                    grid.protect_cell(gx + dx, gy + dy)


def _collect_pin_cells(
    placement: FullPlacement,
    catalog: CatalogResult,
    grid: RoutingGrid,
) -> dict[str, set[tuple[int, int]]]:
    catalog_map = {c.id: c for c in catalog.components}
    result: dict[str, set[tuple[int, int]]] = {}
    for pc in placement.components:
        cat = catalog_map.get(pc.catalog_id)
        if cat is None:
            continue
        for pin in cat.pins:
            wx, wy = pin_world_xy(
                pin.position_mm, pc.x_mm, pc.y_mm, pc.rotation_deg,
            )
            gx, gy = grid.world_to_grid(wx, wy)
            result[f"{pc.instance_id}:{pin.id}"] = {(gx, gy)}
    return result


def _build_voronoi(
    all_pin_cells: dict[str, set[tuple[int, int]]],
    grid: RoutingGrid,
    pin_clearance_cells: int,
) -> dict[int, str]:
    W = grid.width
    H = grid.height
    r = pin_clearance_cells
    r2 = r * r
    nearest: dict[int, tuple[int, str]] = {}

    for pin_key, cells in all_pin_cells.items():
        for (px, py) in cells:
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    d2 = dx * dx + dy * dy
                    if d2 > r2:
                        continue
                    nx, ny = px + dx, py + dy
                    if not (0 <= nx < W and 0 <= ny < H):
                        continue
                    flat = ny * W + nx
                    if flat not in nearest or d2 < nearest[flat][0]:
                        nearest[flat] = (d2, pin_key)

    return {flat: key for flat, (_, key) in nearest.items()}

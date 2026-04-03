"""Main routing engine — greedy Manhattan trace routing with iterative improvement.

Algorithm:
  1. Build routing grid, block component bodies, protect pin cells.
  2. Resolve pin positions for all nets (with dynamic MCU allocation).
  3. Sort nets by priority (pin count, then HPWL).
  4. Route all nets via the Solution class (clean A* with crossing rip-up).
  5. Iteratively improve: rip up worst nets + neighbors, re-route in
     perturbed order. Keep best result seen. Stop when perfect or stalled.
"""

from __future__ import annotations

import logging
import math
import random

from shapely.geometry import Polygon

from src.catalog.models import CatalogResult
from src.pipeline.placer.models import FullPlacement
from src.pipeline.placer.geometry import footprint_halfdims

from .grid import RoutingGrid
from .models import RoutingResult, RouterConfig
from .pins import (
    PinPool,
    pin_world_xy, build_pin_pools,
    resolve_pin_ref, get_pin_world_pos,
    allocate_best_pin,
)
from .solution import Solution, NetPad, _PinRef


log = logging.getLogger(__name__)


# ── Main entry point ───────────────────────────────────────────────


def route_traces(
    placement: FullPlacement,
    catalog: CatalogResult,
    *,
    config: RouterConfig | None = None,
) -> RoutingResult:
    """Route all nets using iterative improvement."""
    if config is None:
        config = RouterConfig()

    catalog_map = {c.id: c for c in catalog.components}
    outline_poly = Polygon(
        placement.outline.vertices,
        placement.outline.hole_vertices or None,
    )

    log.info("Router: %d components, %d nets, area=%.1f mm²",
             len(placement.components), len(placement.nets), outline_poly.area)

    if not outline_poly.is_valid or outline_poly.area <= 0:
        return RoutingResult(
            traces=[], pin_assignments={},
            failed_nets=[n.id for n in placement.nets],
        )

    # 1. Build grid & block component bodies
    grid = RoutingGrid(
        outline_poly,
        resolution=config.grid_resolution_mm,
        edge_clearance=config.edge_clearance_mm,
        trace_width_mm=config.trace_width_mm,
        trace_clearance_mm=config.trace_clearance_mm,
    )

    raised_blocked = grid.block_raised_floor(placement.outline, placement.enclosure)
    if raised_blocked:
        log.info("Router: blocked %d cells in raised-floor zone", raised_blocked)

    pad_radius = _compute_pad_radius(config)
    _block_components(grid, placement, catalog_map, pad_radius)

    # 2. Prepare pin cell map, Voronoi pin proximity
    all_pin_cells = _build_all_pin_cells(placement, catalog, grid)
    pin_clearance_cells = _compute_pin_clearance_cells(config)
    pin_voronoi = _build_pin_voronoi(all_pin_cells, grid, pin_clearance_cells)

    # 3. Parse net pin references
    net_pad_map = _parse_net_refs(placement, catalog, catalog_map)

    # 4. Collect routable net IDs and compute priority ordering
    net_ids = [
        n.id for n in placement.nets
        if len(net_pad_map.get(n.id, [])) >= 2
    ]

    pads_map, pin_assignments = _resolve_all_pads(
        net_ids, net_pad_map, placement, catalog, grid,
    )
    ordering = _priority_order(net_ids, net_pad_map, pads_map)

    # 5. Build solution and route initial pass
    solution = Solution(
        grid, config, placement, catalog,
        net_pad_map, pin_voronoi, all_pin_cells,
    )
    solution.expected_nets = set(net_ids)
    solution.pin_assignments = pin_assignments

    solution.route_nets(ordering, pads_map)
    log.info("Initial solution: score=%s", solution.score())

    if solution.is_perfect():
        log.info("Router: all %d nets routed", len(net_ids))
        return solution.to_result()

    # 6. Iterative improvement
    best = solution.snapshot()
    best_score = solution.score()
    stall = 0
    iteration = 0

    while True:
        all_routed = best_score[0] == 0
        if all_routed and iteration >= config.max_improve_iterations:
            break

        targets = solution.worst_nets(k=3)
        if not targets:
            break

        neighborhood = solution.neighborhood(targets)
        before = solution.score()

        solution.rip_up(neighborhood)
        new_order = _perturb(neighborhood, targets, iteration)
        solution.route_nets(new_order, pads_map)
        after = solution.score()

        if after < before:
            best = solution.snapshot()
            best_score = after
            stall = 0
            log.info("Iter %d: improved %s → %s", iteration + 1, before, after)
            if solution.is_perfect():
                break
        else:
            solution.restore(best)
            stall += 1
            if all_routed and stall >= config.stall_limit:
                log.info(
                    "Stalled for %d iterations at iteration %d, stopping",
                    stall, iteration + 1,
                )
                break

        iteration += 1

    solution.restore(best)

    routed = len(solution.routes)
    missing = len(net_ids) - routed
    if missing > 0:
        log.warning("Router: %d/%d nets routed (%d missing)",
                    routed, len(net_ids), missing)
    else:
        log.info("Router: all %d nets routed", len(net_ids))

    return solution.to_result()


# ── Net reference parsing ──────────────────────────────────────────

def _parse_net_refs(
    placement: FullPlacement,
    catalog: CatalogResult,
    catalog_map: dict,
) -> dict[str, list[_PinRef]]:
    catalog_map_groups: dict[str, dict[str, list[str]]] = {}
    for cat_comp in catalog.components:
        if cat_comp.pin_groups:
            fixed: dict[str, list[str]] = {}
            for pg in cat_comp.pin_groups:
                if pg.fixed_net:
                    fixed[pg.id] = list(pg.pin_ids)
            if fixed:
                catalog_map_groups[cat_comp.id] = fixed

    net_pad_map: dict[str, list[_PinRef]] = {}
    for net in placement.nets:
        refs: list[_PinRef] = []
        for pin_ref_str in net.pins:
            iid, pid, is_group = resolve_pin_ref(pin_ref_str, placement, catalog)
            if is_group:
                pc = next((p for p in placement.components if p.instance_id == iid), None)
                cat_id = pc.catalog_id if pc else None
                fixed_pins = catalog_map_groups.get(cat_id, {}).get(pid)
                if fixed_pins:
                    for fpin in fixed_pins:
                        refs.append(_PinRef(
                            raw=f"{iid}:{fpin}",
                            instance_id=iid,
                            pin_or_group=fpin,
                            is_group=False,
                        ))
                    continue
            refs.append(_PinRef(
                raw=pin_ref_str, instance_id=iid,
                pin_or_group=pid, is_group=is_group,
            ))
        net_pad_map[net.id] = refs
    return net_pad_map


# ── Pad resolution ─────────────────────────────────────────────────

def _resolve_all_pads(
    net_ids: list[str],
    net_pad_map: dict[str, list[_PinRef]],
    placement: FullPlacement,
    catalog: CatalogResult,
    grid: RoutingGrid,
) -> tuple[dict[str, list[NetPad]], dict[str, str]]:
    pin_pools = build_pin_pools(placement, catalog)
    pin_assignments: dict[str, str] = {}
    pads_map: dict[str, list[NetPad]] = {}

    for nid in net_ids:
        refs = net_pad_map[nid]
        pads = _resolve_pads(
            refs, nid, placement, catalog, pin_pools, grid, pin_assignments,
        )
        if pads is not None and len(pads) >= 2:
            pads_map[nid] = pads

    return pads_map, pin_assignments


def _resolve_pads(
    refs: list[_PinRef],
    net_id: str,
    placement: FullPlacement,
    catalog: CatalogResult,
    pin_pools: dict[str, PinPool],
    grid: RoutingGrid,
    pin_assignments: dict[str, str],
) -> list[NetPad] | None:
    pads: list[NetPad | None] = [None] * len(refs)
    unresolved_indices: list[int] = []

    for i, ref in enumerate(refs):
        if not ref.is_group:
            pos = get_pin_world_pos(
                ref.instance_id, ref.pin_or_group, placement, catalog,
            )
            if pos is None:
                log.warning("Net %s: cannot resolve pin %s", net_id, ref.raw)
                return None
            gx, gy = grid.world_to_grid(pos[0], pos[1])
            pads[i] = NetPad(
                instance_id=ref.instance_id,
                pin_id=ref.pin_or_group,
                group_id=None,
                gx=gx, gy=gy,
                world_x=pos[0], world_y=pos[1],
            )
        else:
            assignment_key = f"{net_id}|{ref.raw}"
            if assignment_key in pin_assignments:
                assigned_pin = pin_assignments[assignment_key].split(":", 1)[1]
                pos = get_pin_world_pos(
                    ref.instance_id, assigned_pin, placement, catalog,
                )
                if pos is not None:
                    gx, gy = grid.world_to_grid(pos[0], pos[1])
                    pads[i] = NetPad(
                        instance_id=ref.instance_id,
                        pin_id=assigned_pin,
                        group_id=ref.pin_or_group,
                        gx=gx, gy=gy,
                        world_x=pos[0], world_y=pos[1],
                    )
                    continue
            unresolved_indices.append(i)

    resolved_pads = [p for p in pads if p is not None]
    if resolved_pads:
        centroid_x = sum(p.world_x for p in resolved_pads) / len(resolved_pads)
        centroid_y = sum(p.world_y for p in resolved_pads) / len(resolved_pads)
    else:
        centroid_x = grid.origin_x + grid.width * grid.resolution / 2
        centroid_y = grid.origin_y + grid.height * grid.resolution / 2

    for i in unresolved_indices:
        ref = refs[i]
        pool = pin_pools.get(ref.instance_id)
        if pool is None:
            log.warning("Net %s: no pin pool for %s", net_id, ref.raw)
            return None

        other_pads = [p for p in pads if p is not None]
        if other_pads:
            target_x = sum(p.world_x for p in other_pads) / len(other_pads)
            target_y = sum(p.world_y for p in other_pads) / len(other_pads)
        else:
            target_x, target_y = centroid_x, centroid_y

        chosen_pin = allocate_best_pin(
            ref.instance_id, ref.pin_or_group,
            target_x, target_y,
            pool, placement, catalog,
        )
        if chosen_pin is None:
            log.warning("Net %s: pool exhausted for %s:%s",
                        net_id, ref.instance_id, ref.pin_or_group)
            return None

        pos = get_pin_world_pos(ref.instance_id, chosen_pin, placement, catalog)
        if pos is None:
            return None

        gx, gy = grid.world_to_grid(pos[0], pos[1])
        pads[i] = NetPad(
            instance_id=ref.instance_id,
            pin_id=chosen_pin,
            group_id=ref.pin_or_group,
            gx=gx, gy=gy,
            world_x=pos[0], world_y=pos[1],
        )
        pin_assignments[f"{net_id}|{ref.raw}"] = f"{ref.instance_id}:{chosen_pin}"

    result = [p for p in pads if p is not None]
    return result if len(result) == len(refs) else None


# ── Priority ordering ──────────────────────────────────────────────


def _priority_order(
    net_ids: list[str],
    net_pad_map: dict[str, list[_PinRef]],
    pads_map: dict[str, list[NetPad]],
) -> list[str]:
    hpwl: dict[str, int] = {}
    for nid in net_ids:
        pads = pads_map.get(nid)
        if pads is None or len(pads) < 2:
            hpwl[nid] = 0
            continue
        xs = [p.gx for p in pads]
        ys = [p.gy for p in pads]
        hpwl[nid] = (max(xs) - min(xs)) + (max(ys) - min(ys))

    def net_priority(nid: str) -> tuple[int, int]:
        pin_count = len(net_pad_map.get(nid, []))
        return (-pin_count, -hpwl.get(nid, 0))

    ordered = sorted(net_ids, key=net_priority)
    log.debug("Initial ordering (HPWL): %s",
             ", ".join(f"{nid}({len(net_pad_map[nid])}p/{hpwl[nid]}hpwl)"
                       for nid in ordered))
    return ordered


# ── Ordering perturbation ──────────────────────────────────────────


def _perturb(
    neighborhood: list[str],
    targets: list[str],
    iteration: int,
) -> list[str]:
    ordering = list(neighborhood)
    if not targets:
        random.shuffle(ordering)
        return ordering

    n = len(ordering)
    half = max(1, (1 + n) // 2)

    if iteration < half:
        for nid in targets:
            if nid not in ordering:
                continue
            idx = ordering.index(nid)
            new_idx = max(0, idx - (iteration + 1))
            ordering.pop(idx)
            ordering.insert(new_idx, nid)
    else:
        non_targets = [nid for nid in ordering if nid not in targets]
        random.shuffle(non_targets)
        target_copy = list(targets)
        random.shuffle(target_copy)
        ordering = list(non_targets)
        for nid in target_copy:
            pos = random.randint(0, max(0, n // 2))
            ordering.insert(pos, nid)

    return ordering


# ── Component blocking ─────────────────────────────────────────────


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


# ── Helpers ────────────────────────────────────────────────────────


def _compute_pad_radius(cfg: RouterConfig) -> int:
    return max(1, math.ceil(
        (cfg.trace_width_mm / 2 + cfg.trace_clearance_mm) / cfg.grid_resolution_mm
    ))


def _compute_pin_clearance_cells(cfg: RouterConfig) -> int:
    return max(1, math.ceil(
        (cfg.trace_width_mm / 2 + cfg.pin_clearance_mm) / cfg.grid_resolution_mm
    ))


def _build_pin_voronoi(
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


def _build_all_pin_cells(
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

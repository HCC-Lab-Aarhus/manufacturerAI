"""Main routing engine — connects all net pins via Manhattan traces.

Algorithm overview:
  1. Resolve pin positions (world coords) for all nets.
  2. Build the routing grid (blocked: outside outline, routing-blocked
     component bodies, and component keepout zones).
  3. Decompose multi-pin nets into 2-pin segments via MST.
  4. Order nets: shorter/simpler first, power nets last.
  5. Route each net via A* (greedy spanning tree for 3+ pin nets).
  6. If routing fails, use rip-up and reroute with random orderings.

Dynamic pin allocation:
  When a net references a group ID (e.g. "mcu_1:gpio"), the router
  picks the best physical pin from that group to minimise trace length.
"""

from __future__ import annotations

import copy
import logging
import math
import random
import time
from dataclasses import dataclass

from shapely.geometry import Polygon

from src.catalog.models import CatalogResult
from src.pipeline.placer.models import FullPlacement, PlacedComponent
from src.pipeline.placer.geometry import footprint_halfdims

from .grid import RoutingGrid
from .models import (
    Trace, RoutingResult, RouterConfig,
    GRID_RESOLUTION_MM, TRACE_WIDTH_MM, TRACE_CLEARANCE_MM,
    TURN_PENALTY,
    MAX_RIP_UP_ATTEMPTS, INNER_RIP_UP_LIMIT, TIME_BUDGET_S,
)
from .pathfinder import find_path, find_path_to_tree
from .pins import (
    ResolvedPin, PinPool,
    pin_world_xy, build_pin_pools,
    resolve_pin_ref, get_pin_world_pos,
    get_group_pin_positions, allocate_best_pin,
)


log = logging.getLogger(__name__)


# ── Data structures used during routing ────────────────────────────


@dataclass
class NetPad:
    """A pad (pin position) participating in a net, in grid coordinates."""

    instance_id: str
    pin_id: str             # resolved physical pin ID
    group_id: str | None    # original group ID if dynamic allocation
    gx: int
    gy: int
    world_x: float
    world_y: float


@dataclass
class NetSegment:
    """A 2-pin segment to route, derived from MST decomposition."""

    net_id: str
    pad_a: NetPad
    pad_b: NetPad
    manhattan_dist: int


# ── Main entry point ───────────────────────────────────────────────


def route_traces(
    placement: FullPlacement,
    catalog: CatalogResult,
    *,
    config: RouterConfig | None = None,
) -> RoutingResult:
    """Route all nets in the placement.

    Parameters
    ----------
    placement : FullPlacement
        Output from the placer (all components positioned).
    catalog : CatalogResult
        Loaded component catalog.
    config : RouterConfig | None
        Tuneable parameters.  Uses defaults when *None*.

    Returns
    -------
    RoutingResult
        Traces (in world mm), dynamic pin assignments, and failed nets.
    """
    if config is None:
        config = RouterConfig()

    catalog_map = {c.id: c for c in catalog.components}
    placed_map = {p.instance_id: p for p in placement.components}
    outline_poly = Polygon(placement.outline.vertices)

    if not outline_poly.is_valid or outline_poly.area <= 0:
        return RoutingResult(traces=[], pin_assignments={}, failed_nets=[
            n.id for n in placement.nets
        ])

    # ── 1. Build pin pools for dynamic allocation ──────────────────
    pin_pools = build_pin_pools(placement, catalog)

    # ── 2. Resolve net pads ────────────────────────────────────────
    #
    # For each net, resolve all pin references to NetPads.
    # Group references are resolved *lazily* during routing — the
    # exact pin is chosen to minimise trace length.
    #
    # At this stage we collect the pads with enough info to resolve
    # them during routing.

    net_pad_map: dict[str, list[_PinRef]] = {}
    for net in placement.nets:
        refs: list[_PinRef] = []
        for pin_ref_str in net.pins:
            iid, pid, is_group = resolve_pin_ref(
                pin_ref_str, placement, catalog,
            )
            refs.append(_PinRef(
                raw=pin_ref_str,
                instance_id=iid,
                pin_or_group=pid,
                is_group=is_group,
            ))
        net_pad_map[net.id] = refs

    # ── 3. Build grid + block components ───────────────────────────
    base_grid = RoutingGrid(
        outline_poly,
        resolution=config.grid_resolution_mm,
        edge_clearance=config.edge_clearance_mm,
    )
    pad_radius = _compute_pad_radius(config)
    _block_components(base_grid, placement, catalog_map, config.grid_resolution_mm, pad_radius)

    # ── 4. Route with rip-up ──────────────────────────────────────
    result = _route_with_ripup(
        net_pad_map,
        base_grid,
        placement,
        catalog,
        pin_pools,
        outline_poly,
        config,
        pad_radius,
    )

    return result


# ── Internal types ─────────────────────────────────────────────────


@dataclass
class _PinRef:
    """Unresolved pin reference from the net list."""

    raw: str
    instance_id: str
    pin_or_group: str
    is_group: bool


# ── Component blocking ─────────────────────────────────────────────


def _block_components(
    grid: RoutingGrid,
    placement: FullPlacement,
    catalog_map: dict,
    resolution: float,
    pad_radius: int,
) -> None:
    """Block grid cells under component bodies that block routing.

    After blocking, force-frees all pin positions so traces can still
    reach them (pins poke through the floor even under routing-blocked
    components like battery holders).
    """
    for pc in placement.components:
        cat = catalog_map.get(pc.catalog_id)
        if cat is None:
            continue

        if not cat.mounting.blocks_routing:
            continue

        hw, hh = footprint_halfdims(cat, pc.rotation_deg)
        keepout = cat.mounting.keepout_margin_mm
        grid.block_rect_world(
            pc.x_mm, pc.y_mm,
            hw + keepout, hh + keepout,
            permanent=True,
        )

    # For routing-blocked components, carve escape channels from each
    # pin BEFORE freeing the 3x3 neighborhoods, so the scan correctly
    # identifies the boundary of the blocked zone.
    #
    # We also carve escape channels for ANY pin on ANY component that
    # sits in a permanently-blocked zone — this covers:
    #   - Pins of non-blocking components whose position falls inside
    #     another component's blocked body (e.g. resistor pin inside
    #     battery footprint).
    #   - Wall-mounted components whose pins are in the outline edge
    #     clearance zone or just outside the outline boundary.
    for pc in placement.components:
        cat = catalog_map.get(pc.catalog_id)
        if cat is None:
            continue
        for pin in cat.pins:
            wx, wy = pin_world_xy(pin.position_mm, pc.x_mm, pc.y_mm, pc.rotation_deg)
            gx, gy = grid.world_to_grid(wx, wy)
            if grid.is_permanently_blocked(gx, gy):
                _carve_escape_channel(grid, gx, gy)

    # Force-free all pin positions (on ALL components) so they're
    # always routable, and mark them as protected so trace clearance
    # doesn't block them.
    for pc in placement.components:
        cat = catalog_map.get(pc.catalog_id)
        if cat is None:
            continue
        for pin in cat.pins:
            wx, wy = pin_world_xy(pin.position_mm, pc.x_mm, pc.y_mm, pc.rotation_deg)
            gx, gy = grid.world_to_grid(wx, wy)
            for dx in range(-pad_radius, pad_radius + 1):
                for dy in range(-pad_radius, pad_radius + 1):
                    grid.force_free_cell(gx + dx, gy + dy)
                    grid.protect_cell(gx + dx, gy + dy)


def _carve_escape_channel(
    grid: RoutingGrid,
    pin_gx: int,
    pin_gy: int,
) -> None:
    """Carve escape channels from a pin through permanently blocked cells.

    Scans outward from the pin in all 4 cardinal directions through
    permanently-blocked cells until reaching a non-permanently-blocked
    cell.  Frees all cells along the two shortest directions to ensure
    the pin has a clear path out of the blocked zone.
    """
    directions = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    dir_dists: list[tuple[int, tuple[int, int]]] = []

    for dx, dy in directions:
        dist = 0
        gx, gy = pin_gx, pin_gy
        found = False
        while dist < 300:  # safety limit
            gx += dx
            gy += dy
            dist += 1
            if not grid.in_bounds(gx, gy):
                break
            if not grid.is_permanently_blocked(gx, gy):
                dir_dists.append((dist, (dx, dy)))
                found = True
                break
        # If we didn't find a free cell, skip this direction

    if not dir_dists:
        return

    # Sort by distance, carve the two shortest directions for flexibility
    dir_dists.sort()
    for _, (dx, dy) in dir_dists[:2]:
        gx, gy = pin_gx, pin_gy
        while True:
            gx += dx
            gy += dy
            if not grid.in_bounds(gx, gy):
                break
            if not grid.is_permanently_blocked(gx, gy):
                # Reached open space — done with this direction
                break
            grid.force_free_cell(gx, gy)
            # Free one cell on each side perpendicular for clearance
            perp_dx, perp_dy = dy, dx
            grid.force_free_cell(gx + perp_dx, gy + perp_dy)
            grid.force_free_cell(gx - perp_dx, gy - perp_dy)


# ── Pad resolution (deferred for group pins) ──────────────────────


def _resolve_pads(
    refs: list[_PinRef],
    net_id: str,
    placement: FullPlacement,
    catalog: CatalogResult,
    pin_pools: dict[str, PinPool],
    grid: RoutingGrid,
    pin_assignments: dict[str, str],
) -> list[NetPad] | None:
    """Resolve all pin references in a net to NetPads with grid coords.

    For group references, allocates the best physical pin from the pool
    based on proximity to other pads in the net.

    Returns None if any pin cannot be resolved.
    """
    catalog_map = {c.id: c for c in catalog.components}

    # First pass: resolve all direct pins
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
            # Check if this group ref was already assigned (from a
            # previous routing attempt)
            assignment_key = ref.raw
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

    # Second pass: resolve group references by proximity to known pads
    # Compute centroid of all already-resolved pads as fallback target
    resolved_pads = [p for p in pads if p is not None]
    if resolved_pads:
        centroid_x = sum(p.world_x for p in resolved_pads) / len(resolved_pads)
        centroid_y = sum(p.world_y for p in resolved_pads) / len(resolved_pads)
    else:
        # Fallback: center of outline
        bounds = grid.origin_x, grid.origin_y
        centroid_x = grid.origin_x + grid.width * grid.resolution / 2
        centroid_y = grid.origin_y + grid.height * grid.resolution / 2

    for i in unresolved_indices:
        ref = refs[i]
        pool = pin_pools.get(ref.instance_id)
        if pool is None:
            log.warning("Net %s: no pin pool for %s", net_id, ref.raw)
            return None

        # Use centroid of all other pads in this net as target
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
            log.warning("Net %s: resolved pin %s:%s has no position",
                        net_id, ref.instance_id, chosen_pin)
            return None

        gx, gy = grid.world_to_grid(pos[0], pos[1])
        pads[i] = NetPad(
            instance_id=ref.instance_id,
            pin_id=chosen_pin,
            group_id=ref.pin_or_group,
            gx=gx, gy=gy,
            world_x=pos[0], world_y=pos[1],
        )
        pin_assignments[ref.raw] = f"{ref.instance_id}:{chosen_pin}"

    # All should be resolved
    result = [p for p in pads if p is not None]
    if len(result) != len(refs):
        return None
    return result


# ── MST decomposition ─────────────────────────────────────────────


def _compute_mst(pads: list[NetPad]) -> list[tuple[int, int]]:
    """Kruskal's MST on pads by Manhattan distance.

    Returns list of (pad_index_a, pad_index_b) edges.
    """
    n = len(pads)
    if n < 2:
        return []

    edges: list[tuple[int, int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            d = abs(pads[i].gx - pads[j].gx) + abs(pads[i].gy - pads[j].gy)
            edges.append((d, i, j))
    edges.sort()

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> bool:
        ra, rb = find(a), find(b)
        if ra == rb:
            return False
        parent[ra] = rb
        return True

    result: list[tuple[int, int]] = []
    for d, i, j in edges:
        if union(i, j):
            result.append((i, j))
            if len(result) == n - 1:
                break

    return result


# ── Single-net routing ─────────────────────────────────────────────


# ── Pad neighbourhood helpers ──────────────────────────────────────


def _compute_pad_radius(cfg: RouterConfig) -> int:
    """Compute the pad protection/freeing radius from config."""
    return max(1, math.ceil(
        (cfg.trace_width_mm / 2 + cfg.trace_clearance_mm) / cfg.grid_resolution_mm
    ))


# Module-level fallback (used by tests that call helpers directly)
_PAD_RADIUS = _compute_pad_radius(RouterConfig())


def _free_pad_neighborhood(
    grid: RoutingGrid,
    gx: int, gy: int,
    pad_radius: int = _PAD_RADIUS,
) -> list[tuple[int, int]]:
    """Temporarily free cells around a pad.

    Returns a list of cells that were changed (for later restore).
    Only frees temporarily-blocked cells, never permanently-blocked.
    """
    freed: list[tuple[int, int]] = []
    for dx in range(-pad_radius, pad_radius + 1):
        for dy in range(-pad_radius, pad_radius + 1):
            cx, cy = gx + dx, gy + dy
            if grid.is_blocked(cx, cy) and not grid.is_permanently_blocked(cx, cy):
                grid.free_cell(cx, cy)
                freed.append((cx, cy))
    return freed


def _restore_cells(grid: RoutingGrid, cells: list[tuple[int, int]]) -> None:
    """Re-block cells that were temporarily freed."""
    for cx, cy in cells:
        grid.block_cell(cx, cy)


# ── Foreign-pin blocking ──────────────────────────────────────────


def _build_all_pin_cells(
    placement: FullPlacement,
    catalog: CatalogResult,
    grid: RoutingGrid,
) -> dict[str, set[tuple[int, int]]]:
    """Build a map of instance_id:pin_id → grid cell for every component pin.

    Returns { "inst:pin": (gx, gy), ... } — one entry per physical pin.
    """
    catalog_map = {c.id: c for c in catalog.components}
    result: dict[str, set[tuple[int, int]]] = {}
    for pc in placement.components:
        cat = catalog_map.get(pc.catalog_id)
        if cat is None:
            continue
        for pin in cat.pins:
            wx, wy = pin_world_xy(pin.position_mm, pc.x_mm, pc.y_mm, pc.rotation_deg)
            gx, gy = grid.world_to_grid(wx, wy)
            key = f"{pc.instance_id}:{pin.id}"
            result[key] = {(gx, gy)}
    return result


def _block_foreign_pins(
    grid: RoutingGrid,
    all_pin_cells: dict[str, set[tuple[int, int]]],
    net_pads: list[NetPad],
) -> list[tuple[int, int]]:
    """Temporarily block pin cells not belonging to the current net.

    Returns the list of cells that were blocked (for later restore).
    """
    # Collect cells belonging to this net's pins
    net_cells: set[tuple[int, int]] = set()
    for pad in net_pads:
        net_cells.add((pad.gx, pad.gy))

    blocked: list[tuple[int, int]] = []
    for _key, cells in all_pin_cells.items():
        for cell in cells:
            if cell not in net_cells and grid.is_free(*cell):
                grid.block_cell(*cell)
                blocked.append(cell)
    return blocked


def _unblock_foreign_pins(
    grid: RoutingGrid,
    blocked: list[tuple[int, int]],
) -> None:
    """Restore previously blocked foreign pin cells."""
    for cx, cy in blocked:
        grid.free_cell(cx, cy)


# ── Single-net routing ─────────────────────────────────────────────


def _route_single_net(
    net_id: str,
    pads: list[NetPad],
    grid: RoutingGrid,
    pad_radius: int = _PAD_RADIUS,
    turn_penalty: int = TURN_PENALTY,
) -> tuple[list[list[tuple[int, int]]], bool]:
    """Route a single net by connecting pads via greedy spanning tree.

    Returns (list_of_grid_paths, success).
    Each path is a list of (gx, gy) cells.
    """
    if len(pads) < 2:
        return ([], True)

    if len(pads) == 2:
        # Simple 2-pin net: direct A*
        # Temporarily free pad neighbourhoods so the pathfinder can
        # escape through trace-clearance zones that cover the pad area.
        src = (pads[0].gx, pads[0].gy)
        snk = (pads[1].gx, pads[1].gy)

        freed_src = _free_pad_neighborhood(grid, *src, pad_radius)
        freed_snk = _free_pad_neighborhood(grid, *snk, pad_radius)

        path = find_path(grid, src, snk, turn_penalty=turn_penalty)

        _restore_cells(grid, freed_src)
        _restore_cells(grid, freed_snk)

        if path is None:
            return ([], False)
        return ([path], True)

    # Multi-pin net: greedy Steiner tree approximation
    # Start from pad 0, iteratively connect nearest unconnected pad
    tree_cells: set[tuple[int, int]] = {(pads[0].gx, pads[0].gy)}
    connected: set[int] = {0}
    all_paths: list[list[tuple[int, int]]] = []

    while len(connected) < len(pads):
        best_path: list[tuple[int, int]] | None = None
        best_idx = -1
        best_len = float("inf")

        for i in range(len(pads)):
            if i in connected:
                continue

            src = (pads[i].gx, pads[i].gy)

            # Free tree cells so pathfinder can reach them
            freed: list[tuple[int, int]] = []
            for cell in tree_cells:
                if grid.is_blocked(*cell) and not grid.is_permanently_blocked(*cell):
                    grid.free_cell(*cell)
                    freed.append(cell)

            # Free pad neighbourhood for source
            freed_src = _free_pad_neighborhood(grid, *src, pad_radius)

            path = find_path_to_tree(grid, src, tree_cells, turn_penalty=turn_penalty)

            # Restore freed cells
            _restore_cells(grid, freed)
            _restore_cells(grid, freed_src)

            if path is not None and len(path) < best_len:
                best_path = path
                best_idx = i
                best_len = len(path)

        if best_path is None or best_idx == -1:
            return (all_paths, False)

        connected.add(best_idx)
        for cell in best_path:
            tree_cells.add(cell)
        all_paths.append(best_path)

    return (all_paths, True)


# ── Routing orchestrator with rip-up ──────────────────────────────


def _route_with_ripup(
    net_pad_map: dict[str, list[_PinRef]],
    base_grid: RoutingGrid,
    placement: FullPlacement,
    catalog: CatalogResult,
    pin_pools: dict[str, PinPool],
    outline_poly: Polygon,
    config: RouterConfig,
    pad_radius: int,
) -> RoutingResult:
    """Route all nets with rip-up and reroute on failure.

    Tries multiple random net orderings.  For each ordering:
      Phase 1: route all nets in order, skip failures.
      Phase 2: rip-up and reroute failed nets.
    Returns the best result found.
    """
    net_ids = [n.id for n in placement.nets if len(net_pad_map.get(n.id, [])) >= 2]

    if not net_ids:
        return RoutingResult(traces=[], pin_assignments={}, failed_nets=[])

    start_time = time.monotonic()
    best_traces: list[Trace] = []
    best_assignments: dict[str, str] = {}
    best_failed: list[str] = list(net_ids)

    def _time_left() -> bool:
        return (time.monotonic() - start_time) < config.time_budget_s

    # Sort nets: signal first, then by their total Manhattan span
    # (shorter nets are easier to route and less likely to block others)
    def net_priority(nid: str) -> tuple[int, int]:
        refs = net_pad_map.get(nid, [])
        is_power = nid in ("VCC", "GND")
        # Estimate span from pin refs (use centroid distances)
        return (1 if is_power else 0, len(refs))

    base_order = sorted(net_ids, key=net_priority)

    for attempt in range(config.max_rip_up_attempts):
        if not _time_left():
            log.info("Router: time budget exhausted after %d attempts", attempt)
            break

        # First attempt uses the sorted order; subsequent use random shuffles
        if attempt == 0:
            order = list(base_order)
        else:
            order = list(base_order)
            random.shuffle(order)

        # Fresh pin pools for this attempt (deep copy)
        attempt_pools = _copy_pools(pin_pools)
        attempt_assignments: dict[str, str] = {}

        # Fresh grid (restore to base state)
        grid = RoutingGrid.__new__(RoutingGrid)
        grid.resolution = base_grid.resolution
        grid.edge_clearance = base_grid.edge_clearance
        grid.origin_x = base_grid.origin_x
        grid.origin_y = base_grid.origin_y
        grid.width = base_grid.width
        grid.height = base_grid.height
        grid._cells = bytearray(base_grid._cells)
        grid._protected = set(base_grid._protected)

        # Build pin-cell map for foreign-pin blocking
        all_pin_cells = _build_all_pin_cells(placement, catalog, grid)

        # ── Phase 1: Route all nets in order ───────────────────────
        routed_paths: dict[str, list[list[tuple[int, int]]]] = {}
        failed_set: set[str] = set()

        for nid in order:
            refs = net_pad_map[nid]
            pads = _resolve_pads(
                refs, nid, placement, catalog,
                attempt_pools, grid, attempt_assignments,
            )
            if pads is None or len(pads) < 2:
                failed_set.add(nid)
                continue

            foreign_blocked = _block_foreign_pins(grid, all_pin_cells, pads)
            paths, ok = _route_single_net(nid, pads, grid, pad_radius, config.turn_penalty)
            _unblock_foreign_pins(grid, foreign_blocked)
            if ok and paths:
                routed_paths[nid] = paths
                # Block trace cells
                for path in paths:
                    grid.block_trace(path)
            else:
                failed_set.add(nid)

        log.info("Router attempt %d: %d/%d nets routed (phase 1)",
                 attempt + 1, len(order) - len(failed_set), len(order))

        if not failed_set:
            # All routed on first pass!
            traces = _grid_paths_to_traces(routed_paths, grid)
            return RoutingResult(
                traces=traces,
                pin_assignments=attempt_assignments,
                failed_nets=[],
            )

        # ── Phase 2: Inner rip-up loop ─────────────────────────────
        for inner_iter in range(config.inner_rip_up_limit):
            if not failed_set or not _time_left():
                break

            progress = False
            failed_list = list(failed_set)
            random.shuffle(failed_list)

            for failed_net in failed_list:
                if failed_net not in failed_set:
                    continue

                refs = net_pad_map[failed_net]
                pads = _resolve_pads(
                    refs, failed_net, placement, catalog,
                    attempt_pools, grid, attempt_assignments,
                )
                if pads is None or len(pads) < 2:
                    continue

                # Try routing with crossing allowed
                # First, free pad neighbourhoods for the failed net's pads
                freed_pads: list[tuple[int, int]] = []
                for pad in pads:
                    freed_pads.extend(_free_pad_neighborhood(grid, pad.gx, pad.gy, pad_radius))

                # Block foreign pins to prevent routing through other pins
                foreign_blocked = _block_foreign_pins(grid, all_pin_cells, pads)

                # Try simple route first
                paths, ok = _route_single_net(failed_net, pads, grid, pad_radius, config.turn_penalty)
                if ok and paths:
                    _unblock_foreign_pins(grid, foreign_blocked)
                    _restore_cells(grid, freed_pads)
                    routed_paths[failed_net] = paths
                    for path in paths:
                        grid.block_trace(path)
                    failed_set.discard(failed_net)
                    progress = True
                    continue

                # Try crossing-aware route
                tree_cells: set[tuple[int, int]] = {(pads[0].gx, pads[0].gy)}
                connected: set[int] = {0}
                crossing_paths: list[list[tuple[int, int]]] = []
                crossed_cells: set[tuple[int, int]] = set()
                route_ok = True

                for pad_idx in range(1, len(pads)):
                    if pad_idx in connected:
                        continue

                    # Free tree cells
                    freed: list[tuple[int, int]] = []
                    for cell in tree_cells:
                        if grid.is_blocked(*cell) and not grid.is_permanently_blocked(*cell):
                            grid.free_cell(*cell)
                            freed.append(cell)

                    src = (pads[pad_idx].gx, pads[pad_idx].gy)
                    freed_src = _free_pad_neighborhood(grid, *src, pad_radius)

                    path = find_path_to_tree(
                        grid, src, tree_cells,
                        turn_penalty=config.turn_penalty,
                        allow_crossings=True,
                    )

                    # Restore
                    _restore_cells(grid, freed)
                    _restore_cells(grid, freed_src)

                    if path is None:
                        route_ok = False
                        break

                    connected.add(pad_idx)
                    for cell in path:
                        tree_cells.add(cell)
                        if grid.is_blocked(*cell) and not grid.is_permanently_blocked(*cell):
                            crossed_cells.add(cell)
                    crossing_paths.append(path)

                if not route_ok or not crossed_cells:
                    _unblock_foreign_pins(grid, foreign_blocked)
                    _restore_cells(grid, freed_pads)
                    continue

                # Find which nets were crossed
                ripped_nets: set[str] = set()
                for nid, npaths in routed_paths.items():
                    if nid == failed_net:
                        continue
                    for npath in npaths:
                        for cell in npath:
                            if cell in crossed_cells:
                                ripped_nets.add(nid)
                                break
                        if nid in ripped_nets:
                            break

                if not ripped_nets:
                    _unblock_foreign_pins(grid, foreign_blocked)
                    _restore_cells(grid, freed_pads)
                    continue

                log.debug("  Rip-up %s: crosses %d nets", failed_net, len(ripped_nets))

                # Restore foreign pins and freed pad cells before modifying traces
                _unblock_foreign_pins(grid, foreign_blocked)
                _restore_cells(grid, freed_pads)

                # Rip up crossed nets
                for ripped in ripped_nets:
                    if ripped in routed_paths:
                        for rpath in routed_paths[ripped]:
                            grid.free_trace(rpath)
                        del routed_paths[ripped]
                    failed_set.add(ripped)

                # Place the crossing net
                routed_paths[failed_net] = crossing_paths
                for cpath in crossing_paths:
                    grid.block_trace(cpath)
                failed_set.discard(failed_net)

                # Try to re-route the ripped nets
                for ripped in ripped_nets:
                    if ripped not in failed_set:
                        continue
                    rrefs = net_pad_map[ripped]
                    rpads = _resolve_pads(
                        rrefs, ripped, placement, catalog,
                        attempt_pools, grid, attempt_assignments,
                    )
                    if rpads is None or len(rpads) < 2:
                        continue
                    rforeign = _block_foreign_pins(grid, all_pin_cells, rpads)
                    rpaths, rok = _route_single_net(ripped, rpads, grid, pad_radius, config.turn_penalty)
                    _unblock_foreign_pins(grid, rforeign)
                    if rok and rpaths:
                        routed_paths[ripped] = rpaths
                        for rp in rpaths:
                            grid.block_trace(rp)
                        failed_set.discard(ripped)

                progress = True
                break  # restart inner loop

            if not progress:
                break

        # Check if this attempt is best so far
        if len(failed_set) < len(best_failed):
            best_traces = _grid_paths_to_traces(routed_paths, grid)
            best_assignments = dict(attempt_assignments)
            best_failed = list(failed_set)

        if not failed_set:
            log.info("Router: all nets routed on attempt %d", attempt + 1)
            return RoutingResult(
                traces=best_traces,
                pin_assignments=best_assignments,
                failed_nets=[],
            )

    log.info("Router: finished with %d failed nets", len(best_failed))
    return RoutingResult(
        traces=best_traces,
        pin_assignments=best_assignments,
        failed_nets=best_failed,
    )


# ── Helpers ────────────────────────────────────────────────────────


def _grid_paths_to_traces(
    routed_paths: dict[str, list[list[tuple[int, int]]]],
    grid: RoutingGrid,
) -> list[Trace]:
    """Convert grid-coordinate paths to world-coordinate Traces.

    Also simplifies paths: removes intermediate collinear points
    (keeps only waypoints where direction changes).
    """
    traces: list[Trace] = []
    for net_id, paths in routed_paths.items():
        for grid_path in paths:
            if len(grid_path) < 2:
                continue
            world_path = _simplify_path(grid_path, grid)
            traces.append(Trace(net_id=net_id, path=world_path))
    return traces


def _simplify_path(
    grid_path: list[tuple[int, int]],
    grid: RoutingGrid,
) -> list[tuple[float, float]]:
    """Remove collinear intermediate points and convert to world coords.

    Keeps the start, end, and every point where the direction changes.
    """
    if len(grid_path) <= 2:
        return [grid.grid_to_world(gx, gy) for gx, gy in grid_path]

    waypoints: list[tuple[int, int]] = [grid_path[0]]

    for i in range(1, len(grid_path) - 1):
        prev = grid_path[i - 1]
        curr = grid_path[i]
        nxt = grid_path[i + 1]
        # Direction from prev to curr
        d1 = (curr[0] - prev[0], curr[1] - prev[1])
        # Direction from curr to next
        d2 = (nxt[0] - curr[0], nxt[1] - curr[1])
        if d1 != d2:
            waypoints.append(curr)

    waypoints.append(grid_path[-1])

    return [grid.grid_to_world(gx, gy) for gx, gy in waypoints]


def _copy_pools(pools: dict[str, PinPool]) -> dict[str, PinPool]:
    """Deep-copy pin pools for a fresh routing attempt."""
    return {
        iid: PinPool(
            instance_id=pool.instance_id,
            pools={gid: list(pins) for gid, pins in pool.pools.items()},
        )
        for iid, pool in pools.items()
    }

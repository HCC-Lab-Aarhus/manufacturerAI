"""Main routing engine — greedy Manhattan trace routing with retry.

Algorithm:
  1. Build routing grid, block component bodies, protect pin cells.
  2. Resolve pin positions for all nets (with dynamic MCU allocation).
  3. Sort nets by initial priority (power/ground first, then pin count).
  4. Route each net via A* with foreign-pin clearance enforcement.
  5. Commit each trace + clearance zone to the grid.
  6. If any nets fail, clear all traces and retry with a different
     random net ordering.  Previously tried orderings are tracked to
     avoid duplicates.  The best result (fewest failures) is kept.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass

from shapely.geometry import Polygon, Point

from src.catalog.models import CatalogResult
from src.pipeline.placer.models import FullPlacement
from src.pipeline.placer.geometry import footprint_halfdims

from .debug import build_debug_grids
from .grid import RoutingGrid, FREE, BLOCKED, PERMANENTLY_BLOCKED, TRACE_PATH
from .models import (
    Trace, RoutingResult, RouterConfig,
    TURN_PENALTY,
)
from .pathfinder import find_path, find_path_to_tree
from .pins import (
    PinPool,
    pin_world_xy, build_pin_pools,
    resolve_pin_ref, get_pin_world_pos,
    allocate_best_pin,
)


log = logging.getLogger(__name__)


# ── Data structures ────────────────────────────────────────────────


@dataclass
class NetPad:
    """A pad (pin position) participating in a net, in grid coordinates."""

    instance_id: str
    pin_id: str
    group_id: str | None
    gx: int
    gy: int
    world_x: float
    world_y: float


@dataclass
class _PinRef:
    """Unresolved pin reference from the net list."""

    raw: str
    instance_id: str
    pin_or_group: str
    is_group: bool


# ── Main entry point ───────────────────────────────────────────────


def route_traces(
    placement: FullPlacement,
    catalog: CatalogResult,
    *,
    config: RouterConfig | None = None,
) -> RoutingResult:
    """Route all nets in the placement (single-pass, greedy)."""
    if config is None:
        config = RouterConfig()

    catalog_map = {c.id: c for c in catalog.components}
    outline_poly = Polygon(placement.outline.vertices)

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

    # Block cells in raised-floor zones (z_bottom >= FLOOR_MM)
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
    net_pad_map: dict[str, list[_PinRef]] = {}
    for net in placement.nets:
        refs: list[_PinRef] = []
        for pin_ref_str in net.pins:
            iid, pid, is_group = resolve_pin_ref(
                pin_ref_str, placement, catalog,
            )
            refs.append(_PinRef(
                raw=pin_ref_str, instance_id=iid,
                pin_or_group=pid, is_group=is_group,
            ))
        net_pad_map[net.id] = refs

    # 4. Collect routable net IDs and compute initial ordering
    net_ids = [
        n.id for n in placement.nets
        if len(net_pad_map.get(n.id, [])) >= 2
    ]

    # Route each net in isolation to measure its path length
    log.debug("Isolation routing: measuring path lengths for %d nets", len(net_ids))
    iso_pools = build_pin_pools(placement, catalog)
    iso_lengths: dict[str, int] = {}
    for nid in net_ids:
        refs = net_pad_map[nid]
        pads = _resolve_pads(
            refs, nid, placement, catalog,
            iso_pools, grid, {},
        )
        if pads is None or len(pads) < 2:
            iso_lengths[nid] = 0
            log.debug("  %-20s isolation: no pads", nid)
            continue
        paths, ok, _ = _route_single_net(
            nid, pads, grid, pad_radius, config.turn_penalty,
            pin_voronoi=pin_voronoi,
        )
        length = sum(len(p) for p in paths) if ok and paths else 0
        iso_lengths[nid] = length
        log.debug("  %-20s isolation: %d cells, %d pins",
                  nid, length, len(refs))

    def net_priority(nid: str) -> tuple[int, int]:
        pin_count = len(net_pad_map.get(nid, []))
        return (-pin_count, -iso_lengths.get(nid, 0))

    net_ids.sort(key=net_priority)
    log.debug("Initial ordering: %s",
             ", ".join(f"{nid}({len(net_pad_map[nid])}p/{iso_lengths[nid]}c)"
                       for nid in net_ids))

    # 5. Negotiated congestion routing (attempt before retry fallback)
    neg_result = _negotiate_routes(
        net_ids, net_pad_map, grid, pad_radius, config,
        placement, catalog, pin_voronoi,
    )

    best: dict | None = None
    if neg_result is not None:
        # Try committing negotiated paths with real clearance.
        # Different commit orderings cause different clearance conflicts,
        # so we try several and keep the best.
        neg_paths = neg_result["routed_paths"]
        neg_pads = neg_result["routed_pads"]
        neg_pins = neg_result["pin_assignments"]
        neg_nids = [nid for nid in net_ids if nid in neg_paths]

        # Generate candidate commit orderings
        orderings_to_try: list[list[str]] = [
            # 1. Largest first (most path cells → hardest to reroute)
            sorted(neg_nids,
                   key=lambda nid: sum(len(p) for p in neg_paths[nid]),
                   reverse=True),
            # 2. Most pins first (most constrained)
            sorted(neg_nids,
                   key=lambda nid: len(neg_pads.get(nid, [])),
                   reverse=True),
            # 3. Smallest first (quick wins, then big ones reroute)
            sorted(neg_nids,
                   key=lambda nid: sum(len(p) for p in neg_paths[nid])),
        ]
        # 4-7. Random shuffles
        for _ in range(4):
            shuf = list(neg_nids)
            random.shuffle(shuf)
            orderings_to_try.append(shuf)

        for oi, commit_order in enumerate(orderings_to_try):
            committed_paths: dict[str, list[list[tuple[int, int]]]] = {}
            committed_pads: dict[str, list[NetPad]] = {}
            deferred: list[str] = []

            for nid in commit_order:
                can_commit = True
                for path in neg_paths[nid]:
                    for gx, gy in path:
                        if not grid.is_free(gx, gy) and not grid.is_protected(gx, gy):
                            can_commit = False
                            break
                    if not can_commit:
                        break
                if can_commit:
                    for path in neg_paths[nid]:
                        grid.block_trace(path, net_id=nid)
                    committed_paths[nid] = neg_paths[nid]
                    committed_pads[nid] = neg_pads[nid]
                else:
                    deferred.append(nid)

            # Reroute deferred nets on the partially-filled grid
            for nid in deferred:
                pads = neg_pads.get(nid)
                if pads is None or len(pads) < 2:
                    continue
                paths, ok, _ = _route_single_net(
                    nid, pads, grid, pad_radius, config.turn_penalty,
                    pin_voronoi=pin_voronoi,
                )
                if ok and paths:
                    for path in paths:
                        grid.block_trace(path, net_id=nid)
                    committed_paths[nid] = paths
                    committed_pads[nid] = pads
                else:
                    ok2 = _try_crossing_ripup(
                        nid, pads, grid, pad_radius, config,
                        committed_paths, committed_pads, pin_voronoi,
                    )
                    if not ok2:
                        pass  # stays unrouted

            cur_failed = [nid for nid in neg_nids
                          if nid not in committed_paths]
            log.info("Negotiation commit order %d: %d/%d routed, "
                     "%d deferred, %d failed",
                     oi + 1, len(committed_paths), len(neg_nids),
                     len(deferred), len(cur_failed))

            cur_result = {
                "routed_paths": committed_paths,
                "routed_pads": committed_pads,
                "pin_assignments": neg_pins,
                "failed_nets": cur_failed,
                "failed_pads": {},
            }

            if not cur_failed:
                best = cur_result
                log.info("Negotiation commit order %d: all nets routed!",
                         oi + 1)
                break

            if best is None or len(cur_failed) < len(best["failed_nets"]):
                best = cur_result

            # Clear grid for next ordering attempt
            for cn in committed_paths:
                for path in committed_paths[cn]:
                    grid.free_trace(path, net_id=cn)

        if best is not None and best["failed_nets"]:
            log.info("Negotiation best: %d failures, trying retry loop",
                     len(best["failed_nets"]))

    # 5b. Retry-based routing (fallback if negotiation had no result,
    #     or negotiation committed with some failures)
    baseline_order = list(net_ids)
    tried_orderings: set[tuple[str, ...]] = set()
    last_attempt: dict | None = None
    ordering = list(baseline_order)
    prev_failed: list[str] = []
    neg_perfect = (best is not None and not best["failed_nets"])
    _retry_attempts = 0 if neg_perfect else (1 + config.max_retries)

    for attempt in range(_retry_attempts):
        ordering_key = tuple(ordering)
        if ordering_key in tried_orderings:
            ordering = _perturb_ordering(baseline_order, prev_failed, attempt)
            log.debug("Attempt %d: skipped duplicate ordering", attempt + 1)
            continue
        tried_orderings.add(ordering_key)
        log.debug("Attempt %d: order = [%s]",
                  attempt + 1, ", ".join(ordering))

        pin_pools = build_pin_pools(placement, catalog)
        routed_paths: dict[str, list[list[tuple[int, int]]]] = {}
        routed_pads: dict[str, list[NetPad]] = {}
        pin_assignments: dict[str, str] = {}
        failed_nets: list[str] = []
        failed_pads_map: dict[str, list[NetPad]] = {}

        for nid in ordering:
            refs = net_pad_map[nid]
            pads = _resolve_pads(
                refs, nid, placement, catalog,
                pin_pools, grid, pin_assignments,
            )
            if pads is None or len(pads) < 2:
                failed_nets.append(nid)
                log.info("  %-20s FAIL — pad resolution", nid)
                continue

            paths, ok, _ = _route_single_net(
                nid, pads, grid, pad_radius, config.turn_penalty,
                pin_voronoi=pin_voronoi,
            )

            if ok and paths:
                routed_paths[nid] = paths
                routed_pads[nid] = pads
                for path in paths:
                    grid.block_trace(path, net_id=nid)
                log.info("  %-20s OK — %d segments", nid, len(paths))
            else:
                ok2 = _try_crossing_ripup(
                    nid, pads, grid, pad_radius, config,
                    routed_paths, routed_pads, pin_voronoi,
                )
                if ok2:
                    log.info("  %-20s OK — rip-up resolved", nid)
                else:
                    failed_nets.append(nid)
                    failed_pads_map[nid] = pads
                    log.info("  %-20s FAIL — no route", nid)

        last_attempt = {
            "routed_paths": routed_paths,
            "routed_pads": routed_pads,
            "pin_assignments": pin_assignments,
            "failed_nets": failed_nets,
            "failed_pads": failed_pads_map,
        }

        if not failed_nets:
            log.info("Router: all nets routed on attempt %d", attempt + 1)
            best = last_attempt
            break

        if best is None or len(failed_nets) < len(best["failed_nets"]):
            best = last_attempt
            log.debug("Attempt %d: new best (%d failed)",
                      attempt + 1, len(failed_nets))

        log.info("Router attempt %d: %d/%d failed [%s]",
                 attempt + 1, len(failed_nets), len(ordering),
                 ", ".join(failed_nets))

        for nid, net_paths in routed_paths.items():
            for path in net_paths:
                grid.free_trace(path, net_id=nid)

        prev_failed = list(failed_nets)
        ordering = _perturb_ordering(baseline_order, failed_nets, attempt)

    assert best is not None

    if last_attempt is not None and best is not last_attempt:
        for nid, net_paths in best["routed_paths"].items():
            for path in net_paths:
                grid.block_trace(path, net_id=nid)

    routed_paths = best["routed_paths"]
    routed_pads = best["routed_pads"]
    pin_assignments = best["pin_assignments"]
    failed_nets = best["failed_nets"]

    # 5b. Capture debug snapshots (self-contained, does not use the live grid)
    debug_grids = build_debug_grids(
        placement, catalog, routed_paths, routed_pads, config=config,
    )

    # 6. Convert grid paths to world-coordinate traces
    traces = _grid_paths_to_traces(routed_paths, grid)

    if failed_nets:
        log.warning("Router: %d/%d nets failed: %s",
                    len(failed_nets), len(net_ids), failed_nets)
    else:
        log.info("Router: all %d nets routed", len(net_ids))

    return RoutingResult(
        traces=traces,
        pin_assignments=pin_assignments,
        failed_nets=failed_nets,
        debug_grids=debug_grids,
    )


# ── Ordering perturbation ─────────────────────────────────────────


def _perturb_ordering(
    baseline: list[str],
    failed_nets: list[str],
    attempt: int,
) -> list[str]:
    """Generate a new net ordering informed by previous failures.

    Early attempts promote failed nets towards the front of the
    baseline order (they failed because earlier nets blocked them).
    Later attempts make increasingly random perturbations.
    """
    ordering = list(baseline)
    if not failed_nets:
        random.shuffle(ordering)
        log.debug("Perturb attempt %d: no failures, random shuffle", attempt)
        return ordering

    n = len(ordering)
    half = max(1, (1 + len(baseline)) // 2)

    if attempt < half:
        # Promote each failed net by `attempt` positions in the baseline
        for nid in failed_nets:
            if nid not in ordering:
                continue
            idx = ordering.index(nid)
            new_idx = max(0, idx - (attempt + 1))
            ordering.pop(idx)
            ordering.insert(new_idx, nid)
        log.debug("Perturb attempt %d: promoted %s by %d positions",
                  attempt, failed_nets, attempt + 1)
    else:
        # Random shuffle with failed nets biased towards the front
        non_failed = [nid for nid in ordering if nid not in failed_nets]
        random.shuffle(non_failed)
        failed_copy = list(failed_nets)
        random.shuffle(failed_copy)
        # Insert failed nets into random positions in the first half
        ordering = list(non_failed)
        for nid in failed_copy:
            pos = random.randint(0, max(0, n // 2))
            ordering.insert(pos, nid)
        log.debug("Perturb attempt %d: random with %s biased to front",
                  attempt, failed_nets)

    return ordering


# ── Negotiated congestion routing ──────────────────────────────────

# PathFinder / McMurchie-Ebeling (1995) style.  All nets are routed on
# a clean grid (no inter-net blocking) guided by a per-cell cost map.
# After each iteration the history cost at cells used by more than one
# net is increased, progressively pushing conflicting traces apart
# until they stop overlapping.

_NEG_HISTORY_FACTOR = 2.0   # multiplier for accumulated history cost
_NEG_PRESENT_FACTOR = 20.0  # per-extra-user penalty for current overlap
_NEG_MAX_ITERATIONS = 30    # hard cap on negotiation iterations


def _negotiate_routes(
    net_ids: list[str],
    net_pad_map: dict[str, list[_PinRef]],
    grid: RoutingGrid,
    pad_radius: int,
    config: RouterConfig,
    placement: FullPlacement,
    catalog: CatalogResult,
    pin_voronoi: dict[int, str] | None,
) -> dict | None:
    """Negotiated congestion routing.

    Returns a result dict (same shape as the retry loop's ``best``)
    when negotiation converges to a conflict-free solution, or *None*
    if it fails to converge within ``_NEG_MAX_ITERATIONS`` iterations.
    Paths are **not** committed to the grid — the caller must do that.
    """
    W = grid.width

    # ── Resolve pads once (pin assignments stay fixed) ─────────
    pin_pools = build_pin_pools(placement, catalog)
    pin_assignments: dict[str, str] = {}
    resolved_pads: dict[str, list[NetPad]] = {}
    unroutable: list[str] = []

    for nid in net_ids:
        refs = net_pad_map[nid]
        pads = _resolve_pads(
            refs, nid, placement, catalog,
            pin_pools, grid, pin_assignments,
        )
        if pads is not None and len(pads) >= 2:
            resolved_pads[nid] = pads
        else:
            unroutable.append(nid)

    routable = [nid for nid in net_ids if nid in resolved_pads]
    if len(routable) < 2:
        return None

    log.info("Negotiation: %d routable nets, %d unroutable",
             len(routable), len(unroutable))

    # ── Iteration state ────────────────────────────────────────
    history_cost: dict[int, float] = {}
    net_cells: dict[str, set[int]] = {}   # flat-index sets per net
    cell_usage: dict[int, int] = {}       # total users per cell
    net_paths: dict[str, list[list[tuple[int, int]]]] = {}

    prev_congested = None
    stall_count = 0

    # Track the best (lowest-congestion) snapshot across iterations so we
    # can fall back to it when negotiation stalls near convergence.
    best_congested = float("inf")
    best_snapshot: dict | None = None

    for iteration in range(_NEG_MAX_ITERATIONS):
        # Shuffle net order after the first iteration to avoid bias
        order = list(routable)
        if iteration > 0:
            random.shuffle(order)

        for nid in order:
            pads = resolved_pads[nid]

            # Remove this net's old contribution to cell_usage
            old = net_cells.pop(nid, None)
            if old:
                for key in old:
                    cell_usage[key] -= 1
                    if cell_usage[key] == 0:
                        del cell_usage[key]

            # Build cost map: present congestion + history
            cost_map: dict[int, float] = {}
            for key, count in cell_usage.items():
                cost_map[key] = (
                    count * _NEG_PRESENT_FACTOR
                    + history_cost.get(key, 0.0) * _NEG_HISTORY_FACTOR
                )
            for key, h in history_cost.items():
                if key not in cost_map and h > 0:
                    cost_map[key] = h * _NEG_HISTORY_FACTOR

            # Route on the clean grid (only component blocks, no traces)
            paths, ok, _ = _route_single_net(
                nid, pads, grid, pad_radius, config.turn_penalty,
                pin_voronoi=pin_voronoi,
                cost_map=cost_map if cost_map else None,
            )

            if ok and paths:
                net_paths[nid] = paths
                cells: set[int] = set()
                for path in paths:
                    for gx, gy in path:
                        cells.add(gy * W + gx)
                net_cells[nid] = cells
                for key in cells:
                    cell_usage[key] = cell_usage.get(key, 0) + 1
            else:
                net_paths.pop(nid, None)

        # ── Measure congestion ─────────────────────────────────
        congested = sum(1 for c in cell_usage.values() if c > 1)
        overflow = sum(max(0, c - 1) for c in cell_usage.values())

        log.info("Negotiation iter %d: %d congested cells, overflow=%d, "
                 "%d/%d nets routed",
                 iteration + 1, congested, overflow,
                 len(net_paths), len(routable))

        # ── Snapshot best iteration ────────────────────────────
        if (len(net_paths) == len(routable) and
                congested < best_congested):
            best_congested = congested
            # Deep-copy paths so later iterations don't mutate them
            best_snapshot = {
                nid: [list(seg) for seg in segs]
                for nid, segs in net_paths.items()
            }

        # ── Update history ─────────────────────────────────────
        for key, count in list(cell_usage.items()):
            if count > 1:
                history_cost[key] = history_cost.get(key, 0.0) + (count - 1)

        # ── Check convergence ──────────────────────────────────
        if congested == 0 and len(net_paths) == len(routable):
            log.info("Negotiation converged in %d iterations", iteration + 1)
            failed = unroutable + [
                nid for nid in routable if nid not in net_paths
            ]
            return {
                "routed_paths": net_paths,
                "routed_pads": {nid: resolved_pads[nid]
                                for nid in net_paths},
                "pin_assignments": pin_assignments,
                "failed_nets": failed,
                "failed_pads": {},
            }

        # ── Stall detection ────────────────────────────────────
        if prev_congested is not None and congested >= prev_congested:
            stall_count += 1
            if stall_count >= 8:
                log.warning("Negotiation stalled for %d iterations, "
                            "giving up", stall_count)
                break
        else:
            stall_count = 0
        prev_congested = congested

    # ── Near-convergence: return best snapshot if overlap is small ────
    # Even if path-cell overlaps remain, the path shapes are good
    # guidance.  The caller verifies clearance at commit time.
    if best_snapshot is not None and best_congested <= 5:
        log.info("Negotiation nearly converged (best=%d path overlaps), "
                 "returning best snapshot", best_congested)
        failed = unroutable + [
            nid for nid in routable if nid not in best_snapshot
        ]
        return {
            "routed_paths": best_snapshot,
            "routed_pads": {nid: resolved_pads[nid]
                            for nid in best_snapshot},
            "pin_assignments": pin_assignments,
            "failed_nets": failed,
            "failed_pads": {},
        }

    log.warning("Negotiation did not converge after %d iterations "
                "(%d congested cells remain)",
                min(iteration + 1, _NEG_MAX_ITERATIONS),
                congested if 'congested' in dir() else -1)
    return None


# ── Crossing rip-up ────────────────────────────────────────────────


def _try_crossing_ripup(
    net_id: str,
    pads: list[NetPad],
    grid: RoutingGrid,
    pad_radius: int,
    config: RouterConfig,
    routed_paths: dict[str, list[list[tuple[int, int]]]],
    routed_pads: dict[str, list[NetPad]],
    pin_voronoi: dict[int, str] | None,
) -> bool:
    """Route a net using crossing-cost A*, then rip up crossed nets.

    1. Run A* with crossing_cost — the path may walk through existing
       traces, paying a high penalty.
    2. Identify which nets the path crossed (via grid trace ownership).
    3. Rip those nets, commit the new path, reroute ripped nets.
    4. If any ripped net cannot reroute, revert everything.
    """
    paths_cross, ok, _ = _route_single_net(
        net_id, pads, grid, pad_radius, config.turn_penalty,
        pin_voronoi=pin_voronoi,
        crossing_cost=config.crossing_cost,
    )
    if not ok or not paths_cross:
        return False

    crossed_nets: set[str] = set()
    for path in paths_cross:
        for gx, gy in path:
            owners = grid.cell_owner_at(gx, gy)
            crossed_nets.update(owners - {net_id})

    # Only rip nets that are actually routed — stale ownership or
    # pin-protection cells may reference nets not in routed_paths.
    crossed_nets = {cn for cn in crossed_nets if cn in routed_paths}

    if not crossed_nets:
        for path in paths_cross:
            grid.block_trace(path, net_id=net_id)
        routed_paths[net_id] = paths_cross
        routed_pads[net_id] = pads
        return True

    log.debug("  rip-up: %s crosses %s", net_id, list(crossed_nets))

    saved: dict[str, list[list[tuple[int, int]]]] = {}
    for cn in crossed_nets:
        saved[cn] = routed_paths[cn]
        for path in routed_paths[cn]:
            grid.free_trace(path, net_id=cn)

    for path in paths_cross:
        grid.block_trace(path, net_id=net_id)
    routed_paths[net_id] = paths_cross
    routed_pads[net_id] = pads

    all_rerouted = True
    for cn in crossed_nets:
        cn_pads = routed_pads[cn]
        cn_paths, cn_ok, _ = _route_single_net(
            cn, cn_pads, grid, pad_radius, config.turn_penalty,
            pin_voronoi=pin_voronoi,
        )
        if cn_ok and cn_paths:
            for path in cn_paths:
                grid.block_trace(path, net_id=cn)
            routed_paths[cn] = cn_paths
            log.debug("  rip-up: rerouted %s OK", cn)
        else:
            log.debug("  rip-up: rerouted %s FAIL, reverting", cn)
            all_rerouted = False
            break

    if all_rerouted:
        return True

    for path in paths_cross:
        grid.free_trace(path, net_id=net_id)
    del routed_paths[net_id]

    for rn in crossed_nets:
        if rn in routed_paths and routed_paths[rn] is not saved.get(rn):
            for path in routed_paths[rn]:
                grid.free_trace(path, net_id=rn)
        if rn in saved:
            routed_paths[rn] = saved[rn]
            for path in saved[rn]:
                grid.block_trace(path, net_id=rn)

    return False


# ── Component blocking ─────────────────────────────────────────────


def _block_components(
    grid: RoutingGrid,
    placement: FullPlacement,
    catalog_map: dict,
    pad_radius: int,
) -> None:
    """Block component bodies, then ensure all pin cells are reachable."""

    # Block routing-blocking component bodies (with keepout margin)
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

    # Force-free pin cells + pad_radius neighbourhood, mark as protected
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

    # Re-block body interiors so traces never cross through a component
    for pc in placement.components:
        cat = catalog_map.get(pc.catalog_id)
        if cat is None or not cat.mounting.blocks_routing:
            continue
        hw, hh = footprint_halfdims(cat, pc.rotation_deg)
        grid.block_rect_world(pc.x_mm, pc.y_mm, hw, hh, permanent=True)

    # Re-free pin cells (1-cell ring) that the body re-block may have covered
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


# ── Pad resolution ─────────────────────────────────────────────────


def _resolve_pads(
    refs: list[_PinRef],
    net_id: str,
    placement: FullPlacement,
    catalog: CatalogResult,
    pin_pools: dict[str, PinPool],
    grid: RoutingGrid,
    pin_assignments: dict[str, str],
) -> list[NetPad] | None:
    """Resolve all pin references in a net to NetPads with grid coords."""
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


# ── MST decomposition ─────────────────────────────────────────────


def _compute_mst(pads: list[NetPad]) -> list[tuple[int, int]]:
    """Kruskal's MST on pads by Manhattan distance."""
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


# ── Helpers ────────────────────────────────────────────────────────


def _compute_pad_radius(cfg: RouterConfig) -> int:
    return max(1, math.ceil(
        (cfg.trace_width_mm / 2 + cfg.trace_clearance_mm) / cfg.grid_resolution_mm
    ))


_PAD_RADIUS = _compute_pad_radius(RouterConfig())


def _compute_pin_clearance_cells(cfg: RouterConfig) -> int:
    return max(1, math.ceil(
        (cfg.trace_width_mm / 2 + cfg.pin_clearance_mm) / cfg.grid_resolution_mm
    ))


def _build_pin_voronoi(
    all_pin_cells: dict[str, set[tuple[int, int]]],
    grid: RoutingGrid,
    pin_clearance_cells: int,
) -> dict[int, str]:
    """Pre-compute a Voronoi map: for each cell within pin_clearance of
    any pin, record which pin is nearest.

    Returns flat_index -> "instance_id:pin_id" of the nearest pin.

    When routing a net, cells whose nearest pin is foreign get blocked.
    Cells whose nearest pin belongs to the net stay free — the trace
    can approach its own pin through its own Voronoi territory.
    """
    W = grid.width
    H = grid.height
    r = pin_clearance_cells
    r2 = r * r
    nearest: dict[int, tuple[int, str]] = {}  # flat -> (dist_sq, pin_key)

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


def _block_voronoi(
    grid: RoutingGrid,
    pin_voronoi: dict[int, str],
    net_pads: list[NetPad],
) -> list[tuple[int, int]]:
    """Block cells in the Voronoi territory of foreign pins.

    For each cell within pin_clearance of any pin, check if its nearest
    pin belongs to the current net.  If not, block it.  This creates a
    natural Voronoi boundary: traces approach their own net's pins
    freely but are fenced away from foreign pins.
    """
    net_pin_keys = {f"{pad.instance_id}:{pad.pin_id}" for pad in net_pads}
    W = grid.width
    blocked: list[tuple[int, int]] = []
    for flat, pin_key in pin_voronoi.items():
        if pin_key in net_pin_keys:
            continue
        gx = flat % W
        gy = flat // W
        if grid.is_free(gx, gy):
            grid.block_cell(gx, gy)
            blocked.append((gx, gy))
    return blocked


def _unblock_voronoi(
    grid: RoutingGrid, blocked: list[tuple[int, int]],
) -> None:
    for cx, cy in blocked:
        grid.free_cell(cx, cy)


def _build_all_pin_cells(
    placement: FullPlacement,
    catalog: CatalogResult,
    grid: RoutingGrid,
) -> dict[str, set[tuple[int, int]]]:
    """Map every component pin to its grid cell."""
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


# ── Single-net routing ─────────────────────────────────────────────


def _route_single_net(
    net_id: str,
    pads: list[NetPad],
    grid: RoutingGrid,
    pad_radius: int = _PAD_RADIUS,
    turn_penalty: int = TURN_PENALTY,
    *,
    pin_voronoi: dict[int, str] | None = None,
    crossing_cost: int = 0,
    cost_map: dict[int, float] | None = None,
) -> tuple[list[list[tuple[int, int]]], bool, list[dict]]:
    """Route a single net. Returns (grid_paths, success, debug_snapshots)."""
    if len(pads) < 2:
        return ([], True, [])

    if len(pads) == 2:
        return _route_two_pin(
            net_id, pads, grid, pad_radius, turn_penalty,
            pin_voronoi, crossing_cost=crossing_cost,
            cost_map=cost_map,
        )

    return _route_multi_pin(
        net_id, pads, grid, pad_radius, turn_penalty,
        pin_voronoi, crossing_cost=crossing_cost,
        cost_map=cost_map,
    )


def _route_two_pin(
    net_id: str,
    pads: list[NetPad],
    grid: RoutingGrid,
    pad_radius: int,
    turn_penalty: int,
    pin_voronoi: dict[int, str] | None,
    *,
    crossing_cost: int = 0,
    cost_map: dict[int, float] | None = None,
) -> tuple[list[list[tuple[int, int]]], bool, list[dict]]:
    src = (pads[0].gx, pads[0].gy)
    snk = (pads[1].gx, pads[1].gy)

    blocked_v: list[tuple[int, int]] = []
    if pin_voronoi is not None:
        blocked_v = _block_voronoi(grid, pin_voronoi, pads)

    path = find_path(grid, src, snk, turn_penalty=turn_penalty, crossing_cost=crossing_cost, cost_map=cost_map)

    _unblock_voronoi(grid, blocked_v)

    if path is None:
        return ([], False, [])
    return ([path], True, [])


def _route_multi_pin(
    net_id: str,
    pads: list[NetPad],
    grid: RoutingGrid,
    pad_radius: int,
    turn_penalty: int,
    pin_voronoi: dict[int, str] | None,
    *,
    crossing_cost: int = 0,
    cost_map: dict[int, float] | None = None,
) -> tuple[list[list[tuple[int, int]]], bool, list[dict]]:
    """MST-guided Steiner tree routing for multi-pin nets."""
    mst_edges = _compute_mst(pads)
    all_paths: list[list[tuple[int, int]]] = []

    uf_parent = list(range(len(pads)))
    uf_rank = [0] * len(pads)

    def _find(x: int) -> int:
        while uf_parent[x] != x:
            uf_parent[x] = uf_parent[uf_parent[x]]
            x = uf_parent[x]
        return x

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra == rb:
            return
        if uf_rank[ra] < uf_rank[rb]:
            ra, rb = rb, ra
        uf_parent[rb] = ra
        if uf_rank[ra] == uf_rank[rb]:
            uf_rank[ra] += 1

    comp_trees: dict[int, set[tuple[int, int]]] = {
        i: {(pads[i].gx, pads[i].gy)} for i in range(len(pads))
    }

    def _get_tree(idx: int) -> set[tuple[int, int]]:
        return comp_trees[_find(idx)]

    def _merge(a: int, b: int, path_cells: list[tuple[int, int]]) -> None:
        ra, rb = _find(a), _find(b)
        if ra == rb:
            comp_trees[ra].update(path_cells)
            return
        tree_a = comp_trees.pop(ra)
        tree_b = comp_trees.pop(rb)
        _union(a, b)
        new_root = _find(a)
        if len(tree_a) >= len(tree_b):
            tree_a.update(tree_b)
            tree_a.update(path_cells)
            comp_trees[new_root] = tree_a
        else:
            tree_b.update(tree_a)
            tree_b.update(path_cells)
            comp_trees[new_root] = tree_b

    for pa, pb in mst_edges:
        if _find(pa) == _find(pb):
            continue

        tree_a = _get_tree(pa)
        tree_b = _get_tree(pb)
        if len(tree_a) >= len(tree_b):
            src_tree, target_tree = tree_b, tree_a
        else:
            src_tree, target_tree = tree_a, tree_b

        blocked_v: list[tuple[int, int]] = []
        if pin_voronoi is not None:
            blocked_v = _block_voronoi(grid, pin_voronoi, pads)

        path = find_path_to_tree(
            grid, src_tree, target_tree,
            turn_penalty=turn_penalty,
            crossing_cost=crossing_cost,
            cost_map=cost_map,
        )

        _unblock_voronoi(grid, blocked_v)

        if path is not None:
            all_paths.append(path)
            _merge(pa, pb, path)
        else:
            return (all_paths, False, [])

    roots = {_find(i) for i in range(len(pads))}
    return (all_paths, len(roots) == 1, [])


# ── Output conversion ─────────────────────────────────────────────


def _grid_paths_to_traces(
    routed_paths: dict[str, list[list[tuple[int, int]]]],
    grid: RoutingGrid,
) -> list[Trace]:
    """Convert grid paths to world-coordinate Traces with simplification."""
    outline = grid.outline_poly
    traces: list[Trace] = []
    for net_id, paths in routed_paths.items():
        for grid_path in paths:
            if len(grid_path) < 2:
                continue
            world_path = _simplify_path(grid_path, grid)
            clamped: list[tuple[float, float]] = []
            for wx, wy in world_path:
                pt = Point(wx, wy)
                if not outline.contains(pt):
                    nearest = outline.exterior.interpolate(
                        outline.exterior.project(pt),
                    )
                    clamped.append((nearest.x, nearest.y))
                else:
                    clamped.append((wx, wy))
            traces.append(Trace(net_id=net_id, path=clamped))
    return traces


def _simplify_path(
    grid_path: list[tuple[int, int]],
    grid: RoutingGrid,
) -> list[tuple[float, float]]:
    """Remove collinear intermediate points, convert to world coords."""
    if len(grid_path) <= 2:
        return [grid.grid_to_world(gx, gy) for gx, gy in grid_path]

    waypoints: list[tuple[int, int]] = [grid_path[0]]
    for i in range(1, len(grid_path) - 1):
        prev, curr, nxt = grid_path[i - 1], grid_path[i], grid_path[i + 1]
        d1 = (curr[0] - prev[0], curr[1] - prev[1])
        d2 = (nxt[0] - curr[0], nxt[1] - curr[1])
        if d1 != d2:
            waypoints.append(curr)
    waypoints.append(grid_path[-1])

    return [grid.grid_to_world(gx, gy) for gx, gy in waypoints]

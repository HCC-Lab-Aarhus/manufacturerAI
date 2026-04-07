"""Mutable routing solution — owns grid state and all per-net routes.

The engine creates one Solution, seeds it with an initial routing pass,
then iteratively improves it by ripping up problematic nets and
re-routing them.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
from shapely import contains_xy as _contains_xy
from shapely.geometry import Point

from src.catalog.models import CatalogResult
from src.pipeline.placer.models import FullPlacement

from .grid import RoutingGrid, FREE, BLOCKED, TRACE_PATH, PERMANENTLY_BLOCKED
from .models import Trace, RoutingResult, RouterConfig
from .pathfinder import find_path, find_path_to_tree, _octile_dt, _update_octile_dt
from .pins import pin_world_xy

log = logging.getLogger(__name__)


# ── Data structures ────────────────────────────────────────────────

@dataclass
class NetPad:
    instance_id: str
    pin_id: str
    group_id: str | None
    gx: int
    gy: int
    world_x: float
    world_y: float


@dataclass
class _PinRef:
    raw: str
    instance_id: str
    pin_or_group: str
    is_group: bool


@dataclass
class NetRoute:
    paths: list[list[tuple[int, int]]]
    pads: list[NetPad]

    @property
    def trace_cells(self) -> int:
        return sum(len(p) for p in self.paths)


@dataclass
class Snapshot:
    routes: dict[str, NetRoute]
    cells: bytearray
    trace_owner: dict[int, str]
    clearance_owner: dict[int, set[str]]


# ── Solution ───────────────────────────────────────────────────────

class Solution:
    """A mutable routing state with snapshot/restore."""

    def __init__(
        self,
        grid: RoutingGrid,
        config: RouterConfig,
        placement: FullPlacement,
        catalog: CatalogResult,
        net_pad_map: dict[str, list[_PinRef]],
        pin_voronoi: dict[int, str] | None,
        all_pin_cells: dict[str, set[tuple[int, int]]],
    ) -> None:
        self.grid = grid
        self.config = config
        self.placement = placement
        self.catalog = catalog
        self.net_pad_map = net_pad_map
        self.pin_voronoi = pin_voronoi
        self.all_pin_cells = all_pin_cells

        self.routes: dict[str, NetRoute] = {}
        self.expected_nets: set[str] = set()
        self.pin_assignments: dict[str, str] = {}
        self._pad_radius = max(1, math.ceil(
            (config.trace_width_mm / 2 + config.trace_clearance_mm)
            / config.grid_resolution_mm
        ))

        self._voronoi_by_pin: dict[str, list[tuple[int, int]]] = {}
        self._voronoi_flat_by_pin: dict[str, np.ndarray] = {}
        if pin_voronoi is not None:
            W = grid.width
            groups: dict[str, list[int]] = {}
            for flat, pin_key in pin_voronoi.items():
                groups.setdefault(pin_key, []).append(flat)
            for pin_key, flats in groups.items():
                self._voronoi_flat_by_pin[pin_key] = np.array(flats, dtype=np.intp)
                self._voronoi_by_pin[pin_key] = [
                    (f % W, f // W) for f in flats
                ]

    # ── Scoring ────────────────────────────────────────────────

    def score(self) -> tuple[int, int]:
        """(missing_nets, total_trace_cells). Lower is better."""
        missing = len(self.expected_nets - set(self.routes)) if self.expected_nets else 0
        total_cells = sum(r.trace_cells for r in self.routes.values())
        return (missing, total_cells)

    def is_perfect(self) -> bool:
        return self.score()[0] == 0

    def trace_lengths_mm(self) -> dict[str, float]:
        """Per-net trace length in mm, computed from grid paths."""
        res = self.grid.resolution
        lengths: dict[str, float] = {}
        for net_id, route in self.routes.items():
            cells = sum(max(0, len(p) - 1) for p in route.paths)
            lengths[net_id] = round(cells * res, 2)
        return lengths

    # ── Snapshot / Restore ─────────────────────────────────────

    def snapshot(self) -> Snapshot:
        routes_copy: dict[str, NetRoute] = {}
        for nid, route in self.routes.items():
            routes_copy[nid] = NetRoute(
                paths=[list(p) for p in route.paths],
                pads=list(route.pads),
            )
        return Snapshot(
            routes=routes_copy,
            cells=bytearray(self.grid._cells),
            trace_owner=dict(self.grid._trace_owner),
            clearance_owner={k: set(v) for k, v in self.grid._clearance_owner.items()},
        )

    def restore(self, snap: Snapshot) -> None:
        self.routes = {
            nid: NetRoute(
                paths=[list(p) for p in route.paths],
                pads=list(route.pads),
            )
            for nid, route in snap.routes.items()
        }
        self.grid._cells[:] = snap.cells
        self.grid._trace_owner = dict(snap.trace_owner)
        self.grid._clearance_owner = {k: set(v) for k, v in snap.clearance_owner.items()}

    # ── Rip-up ─────────────────────────────────────────────────

    def rip_up(self, net_ids: list[str]) -> None:
        for nid in net_ids:
            route = self.routes.pop(nid, None)
            if route is None:
                continue
            for path in route.paths:
                self.grid.free_trace(path, net_id=nid)

    # ── Route nets ─────────────────────────────────────────────

    def route_net(self, net_id: str, pads: list[NetPad]) -> None:
        """Route one net. Tries clean route, then crossing rip-up."""
        if net_id in self.routes:
            self.rip_up([net_id])

        # 1. Try clean route
        paths, ok = self._find_paths(net_id, pads)
        if ok and paths and not self._has_foreign_cells(paths, net_id):
            self._commit(net_id, paths, pads)
            return

        # 2. Try crossing-cost route → surgical rip-up of crossed nets
        paths_cross, ok = self._find_paths(
            net_id, pads, crossing_cost=self.config.crossing_cost,
        )
        if ok and paths_cross:
            crossed = self._find_crossed_nets(paths_cross, net_id)
            if not crossed:
                self._commit(net_id, paths_cross, pads)
                return
            if self._try_rip_reroute(net_id, paths_cross, pads, crossed):
                return

        log.info("  %-20s FAIL — no route", net_id)

    def route_nets(self, ordering: list[str], pads_map: dict[str, list[NetPad]]) -> None:
        for nid in ordering:
            pads = pads_map.get(nid)
            if pads is None or len(pads) < 2:
                continue
            self.route_net(nid, pads)

    # ── Identify worst nets and neighborhoods ──────────────────

    def worst_nets(self, k: int = 3) -> list[str]:
        """Return net IDs of the k longest-trace nets (candidates for rip-up)."""
        by_length = sorted(
            ((nid, r.trace_cells) for nid, r in self.routes.items()),
            key=lambda x: -x[1],
        )
        return [nid for nid, _ in by_length[:k]]

    def random_nets(self, k: int = 3) -> list[str]:
        """Return k random routed net IDs (for diversified refinement)."""
        import random as _rnd
        ids = list(self.routes.keys())
        if len(ids) <= k:
            return ids
        return _rnd.sample(ids, k)

    def find_blockers(
        self, missing: list[str], pads_map: dict[str, list[NetPad]],
    ) -> set[str]:
        """Identify routed nets that would be crossed when routing *missing* nets."""
        blockers: set[str] = set()
        for nid in missing:
            pads = pads_map.get(nid)
            if not pads or len(pads) < 2:
                continue
            paths, ok = self._find_paths(
                nid, pads, crossing_cost=self.config.crossing_cost,
            )
            if ok and paths:
                blockers.update(self._find_crossed_nets(paths, nid))
        return blockers

    def refine_single_net(
        self, net_id: str, pads: list[NetPad],
    ) -> bool:
        """Rip up one net and re-route it; keep only if trace is shorter."""
        route = self.routes.get(net_id)
        if route is None:
            return False
        old_cells = route.trace_cells
        snap = self.snapshot()

        self.rip_up([net_id])
        self.route_net(net_id, pads)

        new_route = self.routes.get(net_id)
        if new_route is None or new_route.trace_cells >= old_cells:
            self.restore(snap)
            return False
        return True

    def neighborhood(self, seeds: list[str]) -> list[str]:
        """Given seed net IDs, find all nets that share grid cells with
        them (adjacent or overlapping clearance zones)."""
        seed_cells: set[int] = set()
        W = self.grid.width
        for nid in seeds:
            route = self.routes.get(nid)
            if route is None:
                continue
            for path in route.paths:
                for gx, gy in path:
                    seed_cells.add(gy * W + gx)

        neighbors: set[str] = set(seeds)
        for flat in seed_cells:
            owner = self.grid._trace_owner.get(flat)
            if owner:
                neighbors.add(owner)
            cl_owners = self.grid._clearance_owner.get(flat)
            if cl_owners:
                neighbors.update(cl_owners)

        for nid in list(neighbors):
            route = self.routes.get(nid)
            if route is None:
                continue
            for path in route.paths:
                for gx, gy in path:
                    flat = gy * W + gx
                    owner = self.grid._trace_owner.get(flat)
                    if owner:
                        neighbors.add(owner)
                    cl_owners = self.grid._clearance_owner.get(flat)
                    if cl_owners:
                        neighbors.update(cl_owners)

        return [nid for nid in neighbors if nid in self.routes]

    # ── Output ─────────────────────────────────────────────────

    def to_result(self, *, include_debug: bool = True) -> RoutingResult:
        routed_paths = {nid: r.paths for nid, r in self.routes.items()}
        routed_pads = {nid: r.pads for nid, r in self.routes.items()}

        debug_grids: list[dict] = []
        if include_debug:
            from .debug import build_debug_grids
            debug_grids = build_debug_grids(
                self.placement, self.catalog, routed_paths, routed_pads,
                config=self.config, grid=self.grid,
            )

        traces = self._grid_paths_to_traces(routed_paths, routed_pads)
        failed_nets = sorted(self.expected_nets - set(self.routes)) if self.expected_nets else []

        return RoutingResult(
            traces=traces,
            pin_assignments=dict(self.pin_assignments),
            failed_nets=failed_nets,
            debug_grids=debug_grids,
        )

    # ── Internal: pathfinding ──────────────────────────────────

    def _find_paths(
        self,
        net_id: str,
        pads: list[NetPad],
        *,
        crossing_cost: int = 0,
        cost_map: dict[int, float] | None = None,
    ) -> tuple[list[list[tuple[int, int]]], bool]:
        if len(pads) < 2:
            return ([], False)

        blocked_v = self._block_voronoi(pads)

        if len(pads) == 2:
            src = (pads[0].gx, pads[0].gy)
            snk = (pads[1].gx, pads[1].gy)
            path = find_path(
                self.grid, src, snk,
                turn_penalty=self.config.turn_penalty,
                crossing_cost=crossing_cost,
                cost_map=cost_map,
            )
            self._unblock_voronoi(blocked_v)
            if path is None:
                return ([], False)
            return ([path], True)

        result = self._route_multi_pin(pads, crossing_cost, cost_map)
        self._unblock_voronoi(blocked_v)
        return result

    def _route_multi_pin(
        self,
        pads: list[NetPad],
        crossing_cost: int = 0,
        cost_map: dict[int, float] | None = None,
    ) -> tuple[list[list[tuple[int, int]]], bool]:
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
                if ra in hmap_cache:
                    _update_octile_dt(W, H, hmap_cache[ra], path_cells)
                return
            tree_a = comp_trees.pop(ra)
            tree_b = comp_trees.pop(rb)
            _union(a, b)
            new_root = _find(a)
            combined = tree_a | tree_b | set(path_cells)
            comp_trees[new_root] = combined

            hm_a = hmap_cache.pop(ra, None)
            hm_b = hmap_cache.pop(rb, None)
            if hm_a is not None and hm_b is None:
                _update_octile_dt(W, H, hm_a, list(tree_b) + path_cells)
                hmap_cache[new_root] = hm_a
            elif hm_b is not None and hm_a is None:
                _update_octile_dt(W, H, hm_b, list(tree_a) + path_cells)
                hmap_cache[new_root] = hm_b
            elif hm_a is not None and hm_b is not None:
                bigger, smaller_cells = (hm_a, list(tree_b)) if len(tree_a) >= len(tree_b) else (hm_b, list(tree_a))
                _update_octile_dt(W, H, bigger, smaller_cells + path_cells)
                hmap_cache[new_root] = bigger

        W = self.grid.width
        H = self.grid.height
        hmap_cache: dict = {}

        for pa, pb in mst_edges:
            if _find(pa) == _find(pb):
                continue

            tree_a = _get_tree(pa)
            tree_b = _get_tree(pb)
            ra = _find(pa)
            rb = _find(pb)
            if len(tree_a) >= len(tree_b):
                src_tree, target_tree = tree_b, tree_a
                tgt_root = ra
            else:
                src_tree, target_tree = tree_a, tree_b
                tgt_root = rb

            cached_hmap = hmap_cache.get(tgt_root)
            if cached_hmap is None:
                cached_hmap = _octile_dt(W, H, list(target_tree))
                hmap_cache[tgt_root] = cached_hmap

            path = find_path_to_tree(
                self.grid, src_tree, target_tree,
                turn_penalty=self.config.turn_penalty,
                crossing_cost=crossing_cost,
                cost_map=cost_map,
                h_map=cached_hmap,
            )

            if path is not None:
                all_paths.append(path)
                _merge(pa, pb, path)
            else:
                return (all_paths, False)

        roots = {_find(i) for i in range(len(pads))}
        return (all_paths, len(roots) == 1)

    # ── Internal: commit / rip-up helpers ──────────────────────

    def _has_foreign_cells(
        self,
        paths: list[list[tuple[int, int]]],
        net_id: str,
    ) -> bool:
        W = self.grid.width
        for path in paths:
            for gx, gy in path:
                flat = gy * W + gx
                existing = self.grid._trace_owner.get(flat)
                if existing and existing != net_id:
                    return True
        return False

    def _commit(
        self,
        net_id: str,
        paths: list[list[tuple[int, int]]],
        pads: list[NetPad],
    ) -> None:
        for path in paths:
            self.grid.block_trace(path, net_id=net_id)
        self.routes[net_id] = NetRoute(paths=paths, pads=pads)

    def _find_crossed_nets(
        self,
        paths: list[list[tuple[int, int]]],
        net_id: str,
    ) -> set[str]:
        crossed: set[str] = set()
        for path in paths:
            for gx, gy in path:
                owners = self.grid.cell_owner_at(gx, gy)
                crossed.update(owners - {net_id})
        return {cn for cn in crossed if cn in self.routes}

    def _try_rip_reroute(
        self,
        net_id: str,
        paths_cross: list[list[tuple[int, int]]],
        pads: list[NetPad],
        crossed: set[str],
        *,
        _depth: int = 0,
        _exempt: frozenset[str] | None = None,
    ) -> bool:
        _MAX_RIP_DEPTH = 2
        exempt = (_exempt or frozenset()) | frozenset(crossed) | frozenset({net_id})

        snap = self.snapshot()

        saved_pads: dict[str, list[NetPad]] = {}
        for cn in crossed:
            route = self.routes.get(cn)
            if route:
                saved_pads[cn] = route.pads

        for cn in crossed:
            route = self.routes.pop(cn, None)
            if route:
                for path in route.paths:
                    self.grid.free_trace(path, net_id=cn)

        self._commit(net_id, paths_cross, pads)

        for cn in crossed:
            cn_pads = saved_pads.get(cn)
            if cn_pads is None:
                self.restore(snap)
                return False

            cn_paths, cn_ok = self._find_paths(cn, cn_pads)
            if cn_ok and cn_paths and not self._has_foreign_cells(cn_paths, cn):
                self._commit(cn, cn_paths, cn_pads)
                continue

            if _depth < _MAX_RIP_DEPTH:
                cn_cross_paths, cn_ok = self._find_paths(
                    cn, cn_pads, crossing_cost=self.config.crossing_cost,
                )
                if cn_ok and cn_cross_paths:
                    cn_crossed = self._find_crossed_nets(cn_cross_paths, cn)
                    if not cn_crossed:
                        self._commit(cn, cn_cross_paths, cn_pads)
                        continue
                    if not (cn_crossed & exempt) and cn_crossed:
                        if self._try_rip_reroute(
                            cn, cn_cross_paths, cn_pads, cn_crossed,
                            _depth=_depth + 1, _exempt=exempt,
                        ):
                            continue

            self.restore(snap)
            return False

        return True

    # ── Internal: Voronoi pin blocking ─────────────────────────

    def _block_voronoi(self, net_pads: list[NetPad]) -> np.ndarray:
        if not self._voronoi_flat_by_pin:
            return np.array([], dtype=np.intp)
        net_pin_keys = {f"{pad.instance_id}:{pad.pin_id}" for pad in net_pads}
        foreign_arrays = [
            arr for key, arr in self._voronoi_flat_by_pin.items()
            if key not in net_pin_keys
        ]
        if not foreign_arrays:
            return np.array([], dtype=np.intp)
        all_foreign = np.concatenate(foreign_arrays)
        cells_np = np.frombuffer(self.grid._cells, dtype=np.uint8)
        mask = cells_np[all_foreign] == FREE
        to_block = all_foreign[mask]
        cells_np[to_block] = BLOCKED
        return to_block

    def _unblock_voronoi(self, blocked: np.ndarray) -> None:
        if len(blocked) == 0:
            return
        cells_np = np.frombuffer(self.grid._cells, dtype=np.uint8)
        cells_np[blocked] = FREE

    # ── Internal: output conversion ────────────────────────────

    def _grid_paths_to_traces(
        self,
        routed_paths: dict[str, list[list[tuple[int, int]]]],
        routed_pads: dict[str, list[NetPad]],
    ) -> list[Trace]:
        outline = self.grid.outline_poly
        traces: list[Trace] = []
        for net_id, paths in routed_paths.items():
            pads = routed_pads.get(net_id, [])
            pad_by_grid: dict[tuple[int, int], NetPad] = {
                (p.gx, p.gy): p for p in pads
            }
            for grid_path in paths:
                if len(grid_path) < 2:
                    continue
                world_path = _simplify_path(grid_path, self.grid)

                start_pad = pad_by_grid.get(grid_path[0])
                if start_pad is not None:
                    sx, sy = start_pad.world_x, start_pad.world_y
                    gx0, gy0 = world_path[0]
                    if (sx, sy) != (gx0, gy0):
                        world_path[0:1] = [(sx, sy), (sx, gy0), (gx0, gy0)]

                end_pad = pad_by_grid.get(grid_path[-1])
                if end_pad is not None:
                    ex, ey = end_pad.world_x, end_pad.world_y
                    gxn, gyn = world_path[-1]
                    if (ex, ey) != (gxn, gyn):
                        world_path[-1:] = [(gxn, gyn), (ex, gyn), (ex, ey)]

                xs = np.array([wx for wx, _ in world_path])
                ys = np.array([wy for _, wy in world_path])
                inside = _contains_xy(outline, xs, ys)
                clamped: list[tuple[float, float]] = []
                for i, (wx, wy) in enumerate(world_path):
                    if inside[i]:
                        clamped.append((wx, wy))
                    else:
                        nearest = outline.exterior.interpolate(
                            outline.exterior.project(Point(wx, wy)),
                        )
                        clamped.append((nearest.x, nearest.y))
                traces.append(Trace(net_id=net_id, path=clamped))
        return traces


# ── Module-level helpers ───────────────────────────────────────────

def _compute_mst(pads: list[NetPad]) -> list[tuple[int, int]]:
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


def _simplify_path(
    grid_path: list[tuple[int, int]],
    grid: RoutingGrid,
) -> list[tuple[float, float]]:
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

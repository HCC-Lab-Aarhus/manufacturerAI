"""Mutable routing solution — owns grid state and all per-net routes.

The engine creates one Solution, seeds it with an initial routing pass,
then iteratively improves it by ripping up problematic nets and
re-routing them.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from shapely.geometry import Point

from src.catalog.models import CatalogResult
from src.pipeline.placer.models import FullPlacement

from .grid import RoutingGrid, FREE, BLOCKED, TRACE_PATH, PERMANENTLY_BLOCKED
from .models import Trace, RoutingResult, RouterConfig
from .pathfinder import find_path, find_path_to_tree
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
        self.grid._cells = bytearray(snap.cells)
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

        traces = self._grid_paths_to_traces(routed_paths)
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
                return
            tree_a = comp_trees.pop(ra)
            tree_b = comp_trees.pop(rb)
            _union(a, b)
            new_root = _find(a)
            combined = tree_a | tree_b | set(path_cells)
            comp_trees[new_root] = combined

        for pa, pb in mst_edges:
            if _find(pa) == _find(pb):
                continue

            tree_a = _get_tree(pa)
            tree_b = _get_tree(pb)
            if len(tree_a) >= len(tree_b):
                src_tree, target_tree = tree_b, tree_a
            else:
                src_tree, target_tree = tree_a, tree_b

            path = find_path_to_tree(
                self.grid, src_tree, target_tree,
                turn_penalty=self.config.turn_penalty,
                crossing_cost=crossing_cost,
                cost_map=cost_map,
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

    def _block_voronoi(self, net_pads: list[NetPad]) -> list[tuple[int, int]]:
        if self.pin_voronoi is None:
            return []
        net_pin_keys = {f"{pad.instance_id}:{pad.pin_id}" for pad in net_pads}
        W = self.grid.width
        blocked: list[tuple[int, int]] = []
        for flat, pin_key in self.pin_voronoi.items():
            if pin_key in net_pin_keys:
                continue
            gx = flat % W
            gy = flat // W
            if self.grid.is_free(gx, gy):
                self.grid.block_cell(gx, gy)
                blocked.append((gx, gy))
        return blocked

    def _unblock_voronoi(self, blocked: list[tuple[int, int]]) -> None:
        for cx, cy in blocked:
            self.grid.free_cell(cx, cy)

    # ── Internal: output conversion ────────────────────────────

    def _grid_paths_to_traces(
        self, routed_paths: dict[str, list[list[tuple[int, int]]]],
    ) -> list[Trace]:
        outline = self.grid.outline_poly
        traces: list[Trace] = []
        for net_id, paths in routed_paths.items():
            for grid_path in paths:
                if len(grid_path) < 2:
                    continue
                world_path = _simplify_path(grid_path, self.grid)
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

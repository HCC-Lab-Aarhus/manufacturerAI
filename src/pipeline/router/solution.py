"""Mutable routing solution — owns grid state and all per-net routes.

A Solution always holds a *complete* result: every net is either
cleanly routed, routed with jumper wires, or bridged by a full-span
jumper.  Zero failed nets by construction.

The engine creates one Solution, seeds it with an initial routing pass,
then iteratively improves it by ripping up problematic nets and
re-routing them.
"""

from __future__ import annotations

import copy
import logging
import math
import random
from dataclasses import dataclass, field

from src.catalog.models import CatalogResult
from src.pipeline.placer.models import FullPlacement

from .grid import RoutingGrid, FREE, BLOCKED, TRACE_PATH, PERMANENTLY_BLOCKED
from .models import (
    Trace, JumperWire, JumperEndpoint, RoutingResult, RouterConfig,
)
from .pathfinder import find_path, find_path_to_tree
from .pins import pin_world_xy, build_pin_pools, allocate_best_pin, get_pin_world_pos, resolve_pin_ref, PinPool

log = logging.getLogger(__name__)

JUMPER_PAD_RADIUS: float = 0.5


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
    jumpers: list[dict] = field(default_factory=list)

    @property
    def trace_cells(self) -> int:
        return sum(len(p) for p in self.paths)


@dataclass
class Snapshot:
    routes: dict[str, NetRoute]
    cells: bytearray
    trace_owner: dict[int, str]
    clearance_owner: dict[int, set[str]]
    jumper_committed: list[tuple[float, float]]


# ── Jumper collision checking ──────────────────────────────────────

@dataclass
class JumperEndpointChecker:
    pin_points: list[tuple[float, float]] = field(default_factory=list)
    keepout_mm: float = 1.5
    _committed: list[tuple[float, float]] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        placement: FullPlacement,
        catalog: CatalogResult,
        keepout_mm: float = 1.5,
    ) -> "JumperEndpointChecker":
        catalog_map = {c.id: c for c in catalog.components}
        pin_points: list[tuple[float, float]] = []
        for pc in placement.components:
            cat = catalog_map.get(pc.catalog_id)
            if cat is None:
                continue
            for pin in cat.pins:
                wx, wy = pin_world_xy(
                    pin.position_mm, pc.x_mm, pc.y_mm, pc.rotation_deg,
                )
                pin_points.append((wx, wy))
        return cls(pin_points=pin_points, keepout_mm=keepout_mm)

    @staticmethod
    def _point_seg_dist_sq(
        px: float, py: float,
        ax: float, ay: float,
        bx: float, by: float,
    ) -> float:
        dx, dy = bx - ax, by - ay
        len_sq = dx * dx + dy * dy
        if len_sq < 1e-12:
            return (px - ax) ** 2 + (py - ay) ** 2
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len_sq))
        proj_x = ax + t * dx
        proj_y = ay + t * dy
        return (px - proj_x) ** 2 + (py - proj_y) ** 2

    def is_wire_clear(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> bool:
        ko_sq = self.keepout_mm ** 2
        ax, ay = start
        bx, by = end
        for px, py in self.pin_points:
            if self._point_seg_dist_sq(px, py, ax, ay, bx, by) < ko_sq:
                return False
        for ex, ey in self._committed:
            if self._point_seg_dist_sq(ex, ey, ax, ay, bx, by) < ko_sq:
                return False
        return True

    def adjust_along_path(
        self,
        path: list[tuple[int, int]],
        start_idx: int,
        end_idx: int,
        grid: RoutingGrid,
    ) -> tuple[int, int] | None:
        js, je = start_idx, end_idx
        max_expand = max(len(path), 200)

        for _ in range(max_expand):
            s_wx, s_wy = grid.grid_to_world(*path[js])
            e_wx, e_wy = grid.grid_to_world(*path[je])

            if self.is_wire_clear((s_wx, s_wy), (e_wx, e_wy)):
                if path[js] == path[je]:
                    return None
                return js, je

            can_left = js > 0
            can_right = je < len(path) - 1
            if not can_left and not can_right:
                return None
            if can_left:
                js -= 1
            if can_right:
                je += 1

        return None

    def commit(self, start: tuple[float, float], end: tuple[float, float]) -> None:
        self._committed.append(start)
        self._committed.append(end)

    def reset(self) -> None:
        self._committed.clear()


# ── Solution ───────────────────────────────────────────────────────

class Solution:
    """A complete, always-valid routing state."""

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
        self.jumper_checker = JumperEndpointChecker.build(
            placement, catalog, keepout_mm=config.pin_clearance_mm,
        )
        self._pad_radius = max(1, math.ceil(
            (config.trace_width_mm / 2 + config.trace_clearance_mm)
            / config.grid_resolution_mm
        ))

    # ── Scoring ────────────────────────────────────────────────

    def score(self) -> tuple[int, int, float, int]:
        """(missing_nets, jumper_count, jumper_length_mm, total_trace_cells).
        Lower is better, lexicographic."""
        missing = len(self.expected_nets - set(self.routes)) if self.expected_nets else 0
        jumper_count = 0
        jumper_length = 0.0
        total_cells = 0
        for route in self.routes.values():
            jumper_count += len(route.jumpers)
            jumper_length += sum(j.get("length_mm", 0.0) for j in route.jumpers)
            total_cells += route.trace_cells
        return (missing, jumper_count, jumper_length, total_cells)

    def is_perfect(self) -> bool:
        s = self.score()
        return s[0] == 0 and s[1] == 0

    def jumper_count(self) -> int:
        return sum(len(r.jumpers) for r in self.routes.values())

    def crossed_net_for_jumper(self, jumper: dict) -> str | None:
        """Identify which net's trace a jumper wire crosses over."""
        sx, sy = jumper["start"]
        ex, ey = jumper["end"]
        sgx, sgy = self.grid.world_to_grid(sx, sy)
        egx, egy = self.grid.world_to_grid(ex, ey)
        own_net = jumper["net_id"]
        crossed: dict[str, int] = {}
        dx = egx - sgx
        dy = egy - sgy
        steps = max(abs(dx), abs(dy), 1)

        for i in range(steps + 1):
            t = i / steps
            gx = round(sgx + t * dx)
            gy = round(sgy + t * dy)
            if 0 <= gx < self.grid.width and 0 <= gy < self.grid.height:
                flat = gy * self.grid.width + gx
                owner = self.grid._trace_owner.get(flat)
                if owner and owner != own_net:
                    crossed[owner] = crossed.get(owner, 0) + 1

        if not crossed:
            return None
        return max(crossed, key=crossed.get)

    # ── Snapshot / Restore ─────────────────────────────────────

    def snapshot(self) -> Snapshot:
        routes_copy: dict[str, NetRoute] = {}
        for nid, route in self.routes.items():
            routes_copy[nid] = NetRoute(
                paths=[list(p) for p in route.paths],
                pads=list(route.pads),
                jumpers=[dict(j) for j in route.jumpers],
            )
        return Snapshot(
            routes=routes_copy,
            cells=bytearray(self.grid._cells),
            trace_owner=dict(self.grid._trace_owner),
            clearance_owner={k: set(v) for k, v in self.grid._clearance_owner.items()},
            jumper_committed=list(self.jumper_checker._committed),
        )

    def restore(self, snap: Snapshot) -> None:
        self.routes = {
            nid: NetRoute(
                paths=[list(p) for p in route.paths],
                pads=list(route.pads),
                jumpers=[dict(j) for j in route.jumpers],
            )
            for nid, route in snap.routes.items()
        }
        self.grid._cells = bytearray(snap.cells)
        self.grid._trace_owner = dict(snap.trace_owner)
        self.grid._clearance_owner = {k: set(v) for k, v in snap.clearance_owner.items()}
        self.jumper_checker._committed = list(snap.jumper_committed)

    # ── Rip-up ─────────────────────────────────────────────────

    def rip_up(self, net_ids: list[str]) -> None:
        for nid in net_ids:
            route = self.routes.pop(nid, None)
            if route is None:
                continue
            for path in route.paths:
                self.grid.free_trace(path, net_id=nid)

    # ── Route a single net (always succeeds) ───────────────────

    def route_net(self, net_id: str, pads: list[NetPad]) -> None:
        """Route one net. Tries progressively more aggressive strategies
        until one succeeds. Full-span jumper is the absolute last resort
        and always works, so this never fails."""
        if net_id in self.routes:
            self.rip_up([net_id])

        config = self.config

        # 1. Try clean route
        paths, ok = self._find_paths(net_id, pads)
        if ok and paths and not self._has_foreign_cells(paths, net_id):
            self._commit(net_id, paths, pads)
            return

        # 2. Try crossing-cost route → surgical rip-up of crossed nets
        paths_cross, ok = self._find_paths(
            net_id, pads, crossing_cost=config.crossing_cost,
        )
        if ok and paths_cross:
            crossed = self._find_crossed_nets(paths_cross, net_id)
            if not crossed:
                self._commit(net_id, paths_cross, pads)
                return
            if self._try_rip_reroute(net_id, paths_cross, pads, crossed):
                return

        # 3. Place jumper at shortest conflict segment
        if ok and paths_cross:
            if self._try_jumper(net_id, pads, paths_cross):
                return

        # 4. Full-span jumper(s) — always works
        self._commit_full_jumper(net_id, pads)

    def route_nets(self, ordering: list[str], pads_map: dict[str, list[NetPad]]) -> None:
        for nid in ordering:
            pads = pads_map.get(nid)
            if pads is None or len(pads) < 2:
                continue
            self.route_net(nid, pads)

    # ── Identify worst nets and their neighborhoods ────────────

    def worst_nets(self, k: int = 3) -> list[str]:
        """Nets contributing most to the score (those with jumpers first,
        then longest trace nets)."""
        with_jumpers = [
            (nid, sum(j.get("length_mm", 0.0) for j in r.jumpers))
            for nid, r in self.routes.items() if r.jumpers
        ]
        with_jumpers.sort(key=lambda x: -x[1])
        result = [nid for nid, _ in with_jumpers[:k]]
        if len(result) < k:
            by_length = sorted(
                ((nid, r.trace_cells) for nid, r in self.routes.items()
                 if nid not in result),
                key=lambda x: -x[1],
            )
            for nid, _ in by_length:
                if len(result) >= k:
                    break
                result.append(nid)
        return result

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

    def to_result(self) -> RoutingResult:
        from .debug import build_debug_grids
        from shapely.geometry import Point

        routed_paths = {nid: r.paths for nid, r in self.routes.items()}
        routed_pads = {nid: r.pads for nid, r in self.routes.items()}

        debug_grids = build_debug_grids(
            self.placement, self.catalog, routed_paths, routed_pads,
            config=self.config,
        )

        traces = self._grid_paths_to_traces(routed_paths)

        raw_jumpers: list[dict] = []
        for route in self.routes.values():
            raw_jumpers.extend(route.jumpers)
        jumper_wires = self._finalize_jumper_endpoints(raw_jumpers)

        failed_nets = sorted(self.expected_nets - set(self.routes)) if self.expected_nets else []

        return RoutingResult(
            traces=traces,
            pin_assignments=dict(self.pin_assignments),
            failed_nets=failed_nets,
            jumpers=jumper_wires,
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
        """Route a net without committing to the grid."""
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

        # Multi-pin: MST-guided Steiner tree
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
        """Check if any path cell is owned by a different net."""
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
        jumpers: list[dict] | None = None,
    ) -> None:
        for path in paths:
            self.grid.block_trace(path, net_id=net_id)
        self.routes[net_id] = NetRoute(
            paths=paths,
            pads=pads,
            jumpers=jumpers or [],
        )

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
        """Rip up *crossed* nets, commit *net_id*, then try to reroute each
        displaced net.  Displaced nets may themselves displace others up to
        ``_MAX_RIP_DEPTH`` levels deep."""
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
                    if cn_crossed & exempt:
                        pass
                    elif cn_crossed:
                        if self._try_rip_reroute(
                            cn, cn_cross_paths, cn_pads, cn_crossed,
                            _depth=_depth + 1, _exempt=exempt,
                        ):
                            continue

            self.restore(snap)
            return False

        return True

    # ── Internal: jumper placement ─────────────────────────────

    def _try_jumper(
        self,
        net_id: str,
        pads: list[NetPad],
        paths_cross: list[list[tuple[int, int]]],
    ) -> bool:
        if len(pads) > 2:
            return self._try_jumper_multi(net_id, pads)

        cross_path = paths_cross[0]
        if len(cross_path) < 3:
            return False

        crossing_segments = _find_crossing_segments(cross_path, self.grid)
        if not crossing_segments:
            self._commit(net_id, paths_cross, pads)
            return True

        crossing_segments.sort(key=lambda seg: seg[1] - seg[0])

        for seg_start, seg_end in crossing_segments:
            jstart_idx = max(0, seg_start)
            jend_idx = min(len(cross_path) - 1, seg_end)

            adjusted = self.jumper_checker.adjust_along_path(
                cross_path, jstart_idx, jend_idx, self.grid,
            )
            if adjusted is None:
                continue
            jstart_idx, jend_idx = adjusted

            jstart_cell = cross_path[jstart_idx]
            jend_cell = cross_path[jend_idx]
            if jstart_cell == jend_cell:
                continue

            sub_ok, sub_paths = self._route_jumper_subsegments(
                net_id, pads, jstart_cell, jend_cell,
            )
            if sub_ok:
                jstart_wx, jstart_wy = self.grid.grid_to_world(*jstart_cell)
                jend_wx, jend_wy = self.grid.grid_to_world(*jend_cell)
                length_mm = math.hypot(jend_wx - jstart_wx, jend_wy - jstart_wy)
                jumper = {
                    "net_id": net_id,
                    "start": (jstart_wx, jstart_wy),
                    "end": (jend_wx, jend_wy),
                    "length_mm": length_mm,
                }
                self._commit(net_id, sub_paths, pads, [jumper])
                self.jumper_checker.commit(jumper["start"], jumper["end"])
                log.info("  %-20s OK — jumper wire (%.1f mm)", net_id, length_mm)
                return True

        return False

    def _try_jumper_multi(self, net_id: str, pads: list[NetPad]) -> bool:
        """Jumper fallback for multi-pin nets using MST decomposition."""
        mst_edges = _compute_mst(pads)
        all_paths: list[list[tuple[int, int]]] = []
        edge_jumpers: list[dict] = []

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

        blocked_v = self._block_voronoi(pads)

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
            )

            if path is not None:
                all_paths.append(path)
                for p in [path]:
                    self.grid.block_trace(p, net_id=net_id)
                _merge(pa, pb, path)
                continue

            cross_path = find_path_to_tree(
                self.grid, src_tree, target_tree,
                turn_penalty=self.config.turn_penalty,
                crossing_cost=self.config.crossing_cost,
            )

            if cross_path is None or len(cross_path) < 3:
                j = _make_edge_jumper(net_id, pads[pa], pads[pb], self.grid)
                if not self.jumper_checker.is_wire_clear(j["start"], j["end"]):
                    log.warning(
                        "  %-20s edge jumper crosses pin keepout",
                        net_id,
                    )
                self.jumper_checker.commit(j["start"], j["end"])
                edge_jumpers.append(j)
                _merge(pa, pb, [])
                continue

            crossing_segments = _find_crossing_segments(cross_path, self.grid)
            if not crossing_segments:
                all_paths.append(cross_path)
                self.grid.block_trace(cross_path, net_id=net_id)
                _merge(pa, pb, cross_path)
                continue

            crossing_segments.sort(key=lambda seg: seg[1] - seg[0])
            jumper_placed = False

            for seg_start, seg_end in crossing_segments:
                jstart_idx = max(0, seg_start)
                jend_idx = min(len(cross_path) - 1, seg_end)

                adjusted = self.jumper_checker.adjust_along_path(
                    cross_path, jstart_idx, jend_idx, self.grid,
                )
                if adjusted is None:
                    continue
                jstart_idx, jend_idx = adjusted

                jstart_cell = cross_path[jstart_idx]
                jend_cell = cross_path[jend_idx]
                if jstart_cell == jend_cell:
                    continue

                sub_paths = self._route_jumper_subsegments_multi(
                    net_id, src_tree, target_tree,
                    jstart_cell, jend_cell, pads,
                )
                if sub_paths is not None:
                    for sp in sub_paths:
                        all_paths.append(sp)
                        self.grid.block_trace(sp, net_id=net_id)

                    jstart_wx, jstart_wy = self.grid.grid_to_world(*jstart_cell)
                    jend_wx, jend_wy = self.grid.grid_to_world(*jend_cell)
                    length_mm = math.hypot(jend_wx - jstart_wx, jend_wy - jstart_wy)
                    jumper = {
                        "net_id": net_id,
                        "start": (jstart_wx, jstart_wy),
                        "end": (jend_wx, jend_wy),
                        "length_mm": length_mm,
                    }
                    edge_jumpers.append(jumper)
                    self.jumper_checker.commit(jumper["start"], jumper["end"])

                    merged_cells: list[tuple[int, int]] = []
                    for sp in sub_paths:
                        merged_cells.extend(sp)
                    _merge(pa, pb, merged_cells)
                    jumper_placed = True
                    break

            if not jumper_placed:
                j = _make_edge_jumper(net_id, pads[pa], pads[pb], self.grid)
                if not self.jumper_checker.is_wire_clear(j["start"], j["end"]):
                    log.warning(
                        "  %-20s edge jumper crosses pin keepout",
                        net_id,
                    )
                self.jumper_checker.commit(j["start"], j["end"])
                edge_jumpers.append(j)
                _merge(pa, pb, [])

        self._unblock_voronoi(blocked_v)

        self.routes[net_id] = NetRoute(
            paths=all_paths,
            pads=pads,
            jumpers=edge_jumpers,
        )
        return True

    def _route_jumper_subsegments(
        self,
        net_id: str,
        pads: list[NetPad],
        jstart: tuple[int, int],
        jend: tuple[int, int],
    ) -> tuple[bool, list[list[tuple[int, int]]]]:
        src = (pads[0].gx, pads[0].gy)
        snk = (pads[1].gx, pads[1].gy)

        self.grid.force_free_cell(jstart[0], jstart[1])
        self.grid.protect_cell(jstart[0], jstart[1])
        self.grid.force_free_cell(jend[0], jend[1])
        self.grid.protect_cell(jend[0], jend[1])

        blocked_v = self._block_voronoi(pads)

        path_a = find_path(self.grid, src, jstart, turn_penalty=self.config.turn_penalty)
        path_b = find_path(self.grid, snk, jend, turn_penalty=self.config.turn_penalty)

        self._unblock_voronoi(blocked_v)

        if path_a is None or path_b is None:
            blocked_v2 = self._block_voronoi(pads)
            path_a2 = find_path(self.grid, src, jend, turn_penalty=self.config.turn_penalty)
            path_b2 = find_path(self.grid, snk, jstart, turn_penalty=self.config.turn_penalty)
            self._unblock_voronoi(blocked_v2)
            if path_a2 is not None and path_b2 is not None:
                path_a, path_b = path_a2, path_b2
            else:
                return (False, [])

        return (True, [path_a, path_b])

    def _route_jumper_subsegments_multi(
        self,
        net_id: str,
        src_tree: set[tuple[int, int]],
        target_tree: set[tuple[int, int]],
        jstart: tuple[int, int],
        jend: tuple[int, int],
        pads: list[NetPad],
    ) -> list[list[tuple[int, int]]] | None:
        self.grid.force_free_cell(jstart[0], jstart[1])
        self.grid.protect_cell(jstart[0], jstart[1])
        self.grid.force_free_cell(jend[0], jend[1])
        self.grid.protect_cell(jend[0], jend[1])

        path_a = find_path_to_tree(
            self.grid, src_tree, {jstart}, turn_penalty=self.config.turn_penalty,
        )
        path_b = find_path_to_tree(
            self.grid, target_tree, {jend}, turn_penalty=self.config.turn_penalty,
        )

        if path_a is None or path_b is None:
            path_a2 = find_path_to_tree(
                self.grid, src_tree, {jend}, turn_penalty=self.config.turn_penalty,
            )
            path_b2 = find_path_to_tree(
                self.grid, target_tree, {jstart}, turn_penalty=self.config.turn_penalty,
            )
            if path_a2 is not None and path_b2 is not None:
                path_a, path_b = path_a2, path_b2
            else:
                return None

        return [path_a, path_b]

    def _commit_full_jumper(self, net_id: str, pads: list[NetPad]) -> None:
        if len(pads) == 2:
            src_wx, src_wy = self.grid.grid_to_world(pads[0].gx, pads[0].gy)
            snk_wx, snk_wy = self.grid.grid_to_world(pads[1].gx, pads[1].gy)
            start = (src_wx, src_wy)
            end = (snk_wx, snk_wy)
            if not self.jumper_checker.is_wire_clear(start, end):
                log.warning(
                    "  %-20s full-span jumper crosses pin keepout",
                    net_id,
                )
            jumper = {
                "net_id": net_id,
                "start": start,
                "end": end,
                "length_mm": math.hypot(snk_wx - src_wx, snk_wy - src_wy),
            }
            self.jumper_checker.commit(start, end)
            self.routes[net_id] = NetRoute(paths=[], pads=pads, jumpers=[jumper])
            log.info("  %-20s OK — full-span jumper (%.1f mm)", net_id, jumper["length_mm"])
        else:
            if not self._try_jumper_multi(net_id, pads):
                mst_edges = _compute_mst(pads)
                jumpers = []
                for a, b in mst_edges:
                    j = _make_edge_jumper(net_id, pads[a], pads[b], self.grid)
                    if not self.jumper_checker.is_wire_clear(j["start"], j["end"]):
                        log.warning(
                            "  %-20s MST-edge jumper crosses pin keepout",
                            net_id,
                        )
                    self.jumper_checker.commit(j["start"], j["end"])
                    jumpers.append(j)
                self.routes[net_id] = NetRoute(paths=[], pads=pads, jumpers=jumpers)

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
        from shapely.geometry import Point

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

    def _finalize_jumper_endpoints(
        self, raw_jumpers: list[dict],
    ) -> list[JumperWire]:
        catalog_map = {c.id: c for c in self.catalog.components}
        pin_map: list[tuple[float, float, float]] = []
        for pc in self.placement.components:
            cat = catalog_map.get(pc.catalog_id)
            if cat is None:
                continue
            for pin in cat.pins:
                wx, wy = pin_world_xy(
                    pin.position_mm, pc.x_mm, pc.y_mm, pc.rotation_deg,
                )
                pin_map.append((wx, wy, pin.hole_diameter_mm / 2))

        wires: list[JumperWire] = []
        for j in raw_jumpers:
            sx, sy = j["start"]
            ex, ey = j["end"]
            start_ep = _offset_endpoint(sx, sy, ex, ey, pin_map)
            end_ep = _offset_endpoint(ex, ey, sx, sy, pin_map)
            wires.append(JumperWire(
                net_id=j["net_id"],
                start=start_ep,
                end=end_ep,
                length_mm=j["length_mm"],
            ))
        return wires


# ── Free functions ─────────────────────────────────────────────────

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


def _find_crossing_segments(
    path: list[tuple[int, int]],
    grid: RoutingGrid,
) -> list[tuple[int, int]]:
    segments: list[tuple[int, int]] = []
    in_crossing = False
    seg_start = 0
    for i, (gx, gy) in enumerate(path):
        cell_blocked = not grid.is_free(gx, gy) and not grid.is_protected(gx, gy)
        if cell_blocked and not in_crossing:
            seg_start = max(0, i - 1)
            in_crossing = True
        elif not cell_blocked and in_crossing:
            segments.append((seg_start, i))
            in_crossing = False
    if in_crossing:
        segments.append((seg_start, len(path) - 1))
    return segments


def _make_edge_jumper(
    net_id: str,
    pad_a: NetPad,
    pad_b: NetPad,
    grid: RoutingGrid,
) -> dict:
    start = grid.grid_to_world(pad_a.gx, pad_a.gy)
    end = grid.grid_to_world(pad_b.gx, pad_b.gy)
    return {
        "net_id": net_id,
        "start": start,
        "end": end,
        "length_mm": math.hypot(end[0] - start[0], end[1] - start[1]),
    }


def _offset_endpoint(
    px: float, py: float,
    other_x: float, other_y: float,
    pin_map: list[tuple[float, float, float]],
) -> JumperEndpoint:
    for pin_wx, pin_wy, pin_r in pin_map:
        dist = math.hypot(px - pin_wx, py - pin_wy)
        if dist < pin_r + 0.01:
            dx = other_x - px
            dy = other_y - py
            seg_len = math.hypot(dx, dy)
            if seg_len < 1e-9:
                return JumperEndpoint(x=px, y=py)
            ux, uy = dx / seg_len, dy / seg_len
            offset = pin_r + JUMPER_PAD_RADIUS
            return JumperEndpoint(
                x=pin_wx + ux * offset,
                y=pin_wy + uy * offset,
                pin_center=(pin_wx, pin_wy),
                pin_radius_mm=pin_r,
            )
    return JumperEndpoint(x=px, y=py)


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

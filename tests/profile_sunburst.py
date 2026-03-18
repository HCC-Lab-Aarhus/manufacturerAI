"""Hierarchical routing profiler with interactive sunburst visualization.

Instruments every significant operation in the routing pipeline with nested
timers. Produces an interactive HTML sunburst chart where you can click into
any slice to see its sub-breakdown, and "unaccounted" time is explicitly shown.

Run:  python tests/profile_sunburst.py
"""

from __future__ import annotations

import json
import sys
import time
import functools
from pathlib import Path
from contextlib import contextmanager
from dataclasses import dataclass, field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Hierarchical timer ────────────────────────────────────────────

@dataclass
class TimerNode:
    name: str
    elapsed: float = 0.0
    children: list[TimerNode] = field(default_factory=list)
    _child_map: dict[str, TimerNode] = field(default_factory=dict, repr=False)
    call_count: int = 0

    def child(self, name: str) -> TimerNode:
        if name not in self._child_map:
            node = TimerNode(name=name)
            self._child_map[name] = node
            self.children.append(node)
        return self._child_map[name]

    @property
    def self_time(self) -> float:
        return max(0.0, self.elapsed - sum(c.elapsed for c in self.children))


class HTimer:
    """Hierarchical timer with context-manager and decorator support."""

    def __init__(self):
        self.root = TimerNode(name="total")
        self._stack: list[TimerNode] = [self.root]
        self._t_stack: list[float] = []

    @contextmanager
    def section(self, name: str):
        parent = self._stack[-1]
        node = parent.child(name)
        node.call_count += 1
        self._stack.append(node)
        t0 = time.perf_counter()
        try:
            yield node
        finally:
            dt = time.perf_counter() - t0
            node.elapsed += dt
            self._stack.pop()

    def wrap(self, name: str):
        """Decorator that wraps a function in a timer section."""
        def decorator(fn):
            @functools.wraps(fn)
            def wrapper(*a, **kw):
                with self.section(name):
                    return fn(*a, **kw)
            return wrapper
        return decorator

    def current(self) -> TimerNode:
        return self._stack[-1]


def _flatten_for_sunburst(
    node: TimerNode,
    parent_id: str = "",
    ids: list | None = None,
    labels: list | None = None,
    parents: list | None = None,
    values: list | None = None,
    texts: list | None = None,
    colors: list | None = None,
    depth: int = 0,
):
    if ids is None:
        ids, labels, parents, values, texts, colors = [], [], [], [], [], []

    node_id = f"{parent_id}/{node.name}" if parent_id else node.name

    ids.append(node_id)
    labels.append(node.name)
    parents.append(parent_id)
    values.append(round(node.elapsed * 1000, 1))

    pct = ""
    if parent_id:
        # find parent elapsed
        pct_val = node.elapsed / max(1e-9, _find_node(ids, parents, values, parent_id))
        pct = f" ({pct_val*100:.1f}%)"

    calls_str = f"  [{node.call_count}x]" if node.call_count > 1 else ""
    texts.append(f"{node.elapsed*1000:.1f}ms{pct}{calls_str}")

    PALETTE = [
        "#4e79a7", "#f28e2b", "#e15759", "#76b7b2",
        "#59a14f", "#edc948", "#b07aa1", "#ff9da7",
        "#9c755f", "#bab0ac",
    ]
    colors.append(PALETTE[depth % len(PALETTE)] if parent_id else "#ffffff")

    for child in node.children:
        _flatten_for_sunburst(
            child, node_id, ids, labels, parents, values, texts, colors, depth + 1,
        )

    # Ensure children always sum exactly to parent (required by branchvalues=total)
    if node.children:
        node_idx = ids.index(node_id)
        parent_val = values[node_idx]
        children_sum = round(sum(
            values[i] for i, p in enumerate(parents) if p == node_id
        ), 1)
        remainder = round(parent_val - children_sum, 1)
        if remainder > 0:
            uid = f"{node_id}/(other)"
            ids.append(uid)
            labels.append("(other)")
            parents.append(node_id)
            values.append(remainder)
            u_pct = remainder / max(1e-9, parent_val) * 100
            texts.append(f"{remainder:.1f}ms ({u_pct:.1f}%)")
            colors.append("#dddddd")
        elif remainder < 0:
            values[node_idx] = children_sum

    return ids, labels, parents, values, texts, colors


def _find_node(ids, parents, values, target_id):
    for i, id_ in enumerate(ids):
        if id_ == target_id:
            return values[i] / 1000.0
    return 1.0


def build_sunburst_html(root: TimerNode, title: str = "Router Profile") -> str:
    ids, labels, parents, values, texts, colors = _flatten_for_sunburst(root)

    import plotly.graph_objects as go

    fig = go.Figure(go.Sunburst(
        ids=ids,
        labels=labels,
        parents=parents,
        values=values,
        text=texts,
        branchvalues="total",
        hovertemplate="<b>%{label}</b><br>%{text}<extra></extra>",
        textinfo="label+text",
        insidetextorientation="radial",
        marker=dict(colors=colors, line=dict(width=1, color="white")),
        maxdepth=3,
    ))
    fig.update_layout(
        title=dict(text=title, font=dict(size=20)),
        margin=dict(t=50, l=10, r=10, b=10),
        width=1000,
        height=800,
    )
    html = fig.to_html(include_plotlyjs="cdn", full_html=True)

    # Build sorted category table
    all_nodes: list[tuple[str, float, int]] = []
    _collect_leaf_times(root, all_nodes)
    merged: dict[str, tuple[float, int]] = {}
    for name, elapsed, calls in all_nodes:
        t, c = merged.get(name, (0.0, 0))
        merged[name] = (t + elapsed, c + calls)
    rows = sorted(merged.items(), key=lambda x: -x[1][0])
    table_rows = "".join(
        f"<tr><td>{name}</td><td>{t*1000:.1f}</td><td>{c}</td>"
        f"<td>{t*1000/c:.2f}</td></tr>"
        for name, (t, c) in rows if c > 0
    )
    table_html = (
        '<div style="max-width:800px;margin:30px auto;font-family:sans-serif">'
        '<h2>All categories sorted by total time</h2>'
        '<table style="border-collapse:collapse;width:100%">'
        '<tr style="background:#f0f0f0">'
        '<th style="text-align:left;padding:4px 8px">Category</th>'
        '<th style="text-align:right;padding:4px 8px">Total (ms)</th>'
        '<th style="text-align:right;padding:4px 8px">Calls</th>'
        '<th style="text-align:right;padding:4px 8px">Avg (ms)</th></tr>'
        + table_rows +
        '</table></div>'
    )
    html = html.replace('</body>', table_html + '</body>')
    return html


def print_tree(node: TimerNode, indent: int = 0, parent_elapsed: float = 0):
    pct = f" ({node.elapsed / parent_elapsed * 100:5.1f}%)" if parent_elapsed > 0 else ""
    calls = f" [{node.call_count}x]" if node.call_count > 1 else ""
    print(f"{'  ' * indent}{node.name}: {node.elapsed * 1000:.1f}ms{pct}{calls}")
    for child in sorted(node.children, key=lambda c: -c.elapsed):
        print_tree(child, indent + 1, node.elapsed)
    if node.children:
        st = node.self_time
        if st > 0.5e-3:
            pct2 = f" ({st / node.elapsed * 100:5.1f}%)" if node.elapsed > 0 else ""
            print(f"{'  ' * (indent + 1)}(other): {st * 1000:.1f}ms{pct2}")


def _collect_leaf_times(node: TimerNode, out: list[tuple[str, float, int]]):
    if not node.children:
        out.append((node.name, node.elapsed, max(node.call_count, 1)))
    else:
        for child in node.children:
            _collect_leaf_times(child, out)
        st = node.self_time
        if st > 0:
            out.append((f"{node.name} (self)", st, max(node.call_count, 1)))


# ── Profiled routing pipeline ────────────────────────────────────


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def run_profiled_route():
    from src.pipeline.design.parsing import parse_physical_design, parse_circuit
    from src.pipeline.placer.serialization import assemble_full_placement
    from src.catalog.loader import load_catalog
    from src.pipeline.router.grid import RoutingGrid
    from src.pipeline.router.models import RouterConfig
    from src.pipeline.router.engine import (
        _block_components, _build_all_pin_cells, _build_pin_voronoi,
        _compute_pad_radius, _compute_pin_clearance_cells,
        _parse_net_refs, _resolve_all_pads, _priority_order,
        _perturb, _try_pin_shifts,
    )
    from src.pipeline.router.solution import Solution
    from src.pipeline.router import pathfinder as pf_mod
    from src.pipeline.router.pathfinder import (
        _try_l_route, _manhattan_dt, DIRS,
        FREE, BLOCKED, TRACE_PATH, PERMANENTLY_BLOCKED,
    )
    from heapq import heappush as _heappush, heappop as _heappop
    import array as _array_mod
    import src.pipeline.router.solution as sol_mod
    from src.pipeline.router.grid import RoutingGrid as grid_cls
    from shapely.geometry import Polygon

    ht = HTimer()

    # ── Load fixture ──
    design_data = json.loads((FIXTURE_DIR / "large_design.json").read_text(encoding="utf-8"))
    circuit_data = json.loads((FIXTURE_DIR / "large_circuit.json").read_text(encoding="utf-8"))
    placement_data = json.loads((FIXTURE_DIR / "large_placement.json").read_text(encoding="utf-8"))

    physical = parse_physical_design(design_data)
    circuit = parse_circuit(circuit_data)
    catalog = load_catalog()
    placement = assemble_full_placement(
        placement_data, physical.outline, circuit.nets, physical.enclosure,
    )
    catalog_map = {c.id: c for c in catalog.components}
    config = RouterConfig()
    outline_poly = Polygon(placement.outline.vertices)

    # ── Instrument functions ──────────────────────────────────────

    # Wrap pathfinder functions
    orig_find_path = pf_mod.find_path
    orig_find_path_to_tree = pf_mod.find_path_to_tree

    from src.pipeline.router.models import TURN_PENALTY as _TURN_PENALTY

    def timed_find_path(grid, source, sink, *, turn_penalty=_TURN_PENALTY,
                        crossing_cost=0, cost_map=None):
        with ht.section("find_path"):
            if cost_map is None:
                with ht.section("l_route_attempt"):
                    l_path = _try_l_route(grid, source, sink)
                if l_path is not None:
                    return l_path
            with ht.section("astar"):
                # Pass empty dict to skip L-route retry inside orig
                effective_cost_map = cost_map if cost_map is not None else {}
                return orig_find_path(
                    grid, source, sink,
                    turn_penalty=turn_penalty,
                    crossing_cost=crossing_cost,
                    cost_map=effective_cost_map,
                )

    def timed_find_path_to_tree(grid, source, tree, *, turn_penalty=_TURN_PENALTY,
                                crossing_cost=0, cost_map=None):
        with ht.section("find_path_to_tree"):
            W, H = grid.width, grid.height
            N = W * H
            cells = grid._cells

            if isinstance(source, set):
                sources = source
            else:
                sources = {source}

            overlap = sources & tree
            if overlap:
                return [next(iter(overlap))]

            with ht.section("build_tree_mask"):
                tree_mask = bytearray(N)
                tree_list = list(tree)
                for tx, ty in tree_list:
                    tree_mask[ty * W + tx] = 1

            with ht.section("manhattan_dt"):
                h_map = _manhattan_dt(W, H, tree_list)

            with ht.section("astar"):
                INF = 0x7FFFFFFF
                g = [INF] * N
                parent = [-1] * N
                closed = bytearray(N)
                counter = 0
                heap = []
                for sx, sy in sources:
                    if not (0 <= sx < W and 0 <= sy < H):
                        continue
                    skey = sy * W + sx
                    if cells[skey] != FREE and not tree_mask[skey]:
                        continue
                    g[skey] = 0
                    _heappush(heap, (h_map[skey], counter, sx, sy, -1))
                    counter += 1
                if not heap:
                    return None
                while heap:
                    f, _cnt, cx, cy, direction = _heappop(heap)
                    key = cy * W + cx
                    if closed[key]:
                        continue
                    closed[key] = 1
                    if tree_mask[key]:
                        path = [(cx, cy)]
                        k = key
                        while True:
                            pk = parent[k]
                            if pk < 0:
                                break
                            path.append((pk % W, pk // W))
                            k = pk
                        path.reverse()
                        return path
                    cur_g = g[key]
                    for d, (dx, dy) in enumerate(DIRS):
                        nx, ny = cx + dx, cy + dy
                        if not (0 <= nx < W and 0 <= ny < H):
                            continue
                        nkey = ny * W + nx
                        if closed[nkey]:
                            continue
                        nval = cells[nkey]
                        cross_extra = 0
                        if nval != FREE and not tree_mask[nkey]:
                            if nval == PERMANENTLY_BLOCKED:
                                continue
                            if crossing_cost > 0 and (nval == TRACE_PATH or nval == BLOCKED):
                                cross_extra = crossing_cost
                            else:
                                continue
                        is_turn = direction != -1 and direction != d
                        cost = 1 + (turn_penalty if is_turn else 0) + cross_extra
                        if cost_map is not None:
                            cost += cost_map.get(nkey, 0)
                        tentative_g = cur_g + cost
                        if tentative_g < g[nkey]:
                            g[nkey] = tentative_g
                            parent[nkey] = key
                            counter += 1
                            _heappush(heap, (tentative_g + h_map[nkey], counter, nx, ny, d))
                return None

    pf_mod.find_path = timed_find_path
    sol_mod.find_path = timed_find_path
    pf_mod.find_path_to_tree = timed_find_path_to_tree
    sol_mod.find_path_to_tree = timed_find_path_to_tree

    # Wrap grid operations
    orig_block_trace = grid_cls.block_trace
    orig_free_trace = grid_cls.free_trace

    def timed_block_trace(self, *a, **kw):
        with ht.section("block_trace"):
            return orig_block_trace(self, *a, **kw)

    def timed_free_trace(self, *a, **kw):
        with ht.section("free_trace"):
            return orig_free_trace(self, *a, **kw)

    grid_cls.block_trace = timed_block_trace
    grid_cls.free_trace = timed_free_trace

    # Wrap solution methods
    orig_block_voronoi = Solution._block_voronoi
    orig_unblock_voronoi = Solution._unblock_voronoi
    orig_find_crossed = Solution._find_crossed_nets
    orig_has_foreign = Solution._has_foreign_cells
    orig_relax_tree = Solution._relax_tree
    orig_commit = Solution._commit
    orig_try_rip_reroute = Solution._try_rip_reroute
    orig_try_jumper = Solution._try_jumper
    orig_commit_full_jumper = Solution._commit_full_jumper
    orig_snapshot = Solution.snapshot
    orig_restore = Solution.restore
    orig_rip_up = Solution.rip_up

    def timed_block_voronoi(self, *a, **kw):
        with ht.section("block_voronoi"):
            return orig_block_voronoi(self, *a, **kw)

    def timed_unblock_voronoi(self, *a, **kw):
        with ht.section("unblock_voronoi"):
            return orig_unblock_voronoi(self, *a, **kw)

    def timed_find_crossed(self, *a, **kw):
        with ht.section("find_crossed_nets"):
            return orig_find_crossed(self, *a, **kw)

    def timed_has_foreign(self, *a, **kw):
        with ht.section("has_foreign_cells"):
            return orig_has_foreign(self, *a, **kw)

    def timed_relax_tree(self, *a, **kw):
        with ht.section("relax_tree"):
            return orig_relax_tree(self, *a, **kw)

    def timed_commit(self, *a, **kw):
        with ht.section("commit"):
            return orig_commit(self, *a, **kw)

    def timed_try_rip_reroute(self, *a, **kw):
        with ht.section("try_rip_reroute"):
            return orig_try_rip_reroute(self, *a, **kw)

    def timed_try_jumper(self, *a, **kw):
        with ht.section("try_jumper"):
            return orig_try_jumper(self, *a, **kw)

    def timed_commit_full_jumper(self, *a, **kw):
        with ht.section("commit_full_jumper"):
            return orig_commit_full_jumper(self, *a, **kw)

    def timed_snapshot(self, *a, **kw):
        with ht.section("snapshot"):
            return orig_snapshot(self, *a, **kw)

    def timed_restore(self, *a, **kw):
        with ht.section("restore"):
            return orig_restore(self, *a, **kw)

    def timed_rip_up(self, *a, **kw):
        with ht.section("rip_up"):
            return orig_rip_up(self, *a, **kw)

    Solution._block_voronoi = timed_block_voronoi
    Solution._unblock_voronoi = timed_unblock_voronoi
    Solution._find_crossed_nets = timed_find_crossed
    Solution._has_foreign_cells = timed_has_foreign
    Solution._relax_tree = timed_relax_tree
    Solution._commit = timed_commit
    Solution._try_rip_reroute = timed_try_rip_reroute
    Solution._try_jumper = timed_try_jumper
    Solution._commit_full_jumper = timed_commit_full_jumper
    Solution.snapshot = timed_snapshot
    Solution.restore = timed_restore
    Solution.rip_up = timed_rip_up

    try:
        with ht.section("total"):
            # ── Phase 1: Grid construction ──
            with ht.section("grid_construction"):
                with ht.section("grid_init"):
                    grid = RoutingGrid(
                        outline_poly,
                        resolution=config.grid_resolution_mm,
                        edge_clearance=config.edge_clearance_mm,
                        trace_width_mm=config.trace_width_mm,
                        trace_clearance_mm=config.trace_clearance_mm,
                    )
                with ht.section("block_raised_floor"):
                    grid.block_raised_floor(placement.outline, placement.enclosure)
                with ht.section("block_components"):
                    pad_radius = _compute_pad_radius(config)
                    _block_components(grid, placement, catalog_map, pad_radius)

            # ── Phase 2: Pin mapping & Voronoi ──
            with ht.section("pin_setup"):
                with ht.section("build_pin_cells"):
                    all_pin_cells = _build_all_pin_cells(placement, catalog, grid)
                with ht.section("pin_clearance"):
                    pin_clearance_cells = _compute_pin_clearance_cells(config)
                with ht.section("build_voronoi"):
                    pin_voronoi = _build_pin_voronoi(all_pin_cells, grid, pin_clearance_cells)

            # ── Phase 3: Net parsing & pad resolution ──
            with ht.section("net_resolution"):
                with ht.section("parse_net_refs"):
                    net_pad_map = _parse_net_refs(placement, catalog, catalog_map)
                with ht.section("filter_nets"):
                    net_ids = [
                        n.id for n in placement.nets
                        if len(net_pad_map.get(n.id, [])) >= 2
                    ]
                with ht.section("resolve_pads"):
                    pads_map, pin_assignments = _resolve_all_pads(
                        net_ids, net_pad_map, placement, catalog, grid,
                    )
                with ht.section("priority_order"):
                    ordering = _priority_order(
                        net_ids, net_pad_map, pads_map, grid, config, pin_voronoi,
                    )

            # ── Phase 4: Solution construction ──
            with ht.section("solution_init"):
                solution = Solution(
                    grid, config, placement, catalog,
                    net_pad_map, pin_voronoi, all_pin_cells,
                )
                solution.expected_nets = set(net_ids)
                solution.pin_assignments = pin_assignments

            # ── Phase 5: Initial routing ──
            with ht.section("initial_routing"):
                solution.route_nets(ordering, pads_map)

            score = solution.score()
            print(f"Initial score: {score}")

            if solution.is_perfect():
                print("Perfect solution on initial pass!")
            else:
                # ── Phase 6: Iterative improvement ──
                with ht.section("improvement_loop"):
                    best = solution.snapshot()
                    best_score = solution.score()
                    stall = 0
                    pin_shift_tried = False

                    for iteration in range(config.max_improve_iterations):
                        jc = solution.jumper_count()
                        targets = solution.worst_nets(k=max(3, jc // 2))
                        if not targets:
                            break

                        neighborhood = solution.neighborhood(targets)
                        before = solution.score()

                        solution.rip_up(neighborhood)
                        new_order = _perturb(neighborhood, targets, iteration)

                        with ht.section("re_route"):
                            solution.route_nets(new_order, pads_map)

                        after = solution.score()

                        if after < before:
                            best = solution.snapshot()
                            best_score = after
                            stall = 0
                            pin_shift_tried = False
                            print(f"  Iter {iteration+1}: improved {before} -> {after}")
                            if solution.is_perfect():
                                print("  Perfect solution found!")
                                break
                        else:
                            solution.restore(best)
                            stall += 1
                            print(f"  Iter {iteration+1}: no improvement (stall {stall})")

                            if not pin_shift_tried and stall >= 3 and jc > 0:
                                pin_shift_tried = True
                                with ht.section("pin_shifts"):
                                    shifted = _try_pin_shifts(
                                        solution, net_pad_map, pads_map,
                                        pin_assignments, ordering,
                                        placement, catalog,
                                    )
                                if shifted:
                                    best = solution.snapshot()
                                    best_score = solution.score()
                                    stall = 0
                                    if solution.is_perfect():
                                        break
                                    continue

                            if stall >= config.stall_limit:
                                print(f"  Stalled for {stall} iterations, stopping")
                                break

                    solution.restore(best)
                    final_score = solution.score()
                    print(f"Final score: {final_score}")

    finally:
        # Restore all originals
        pf_mod.find_path = orig_find_path
        sol_mod.find_path = orig_find_path
        pf_mod.find_path_to_tree = orig_find_path_to_tree
        sol_mod.find_path_to_tree = orig_find_path_to_tree
        grid_cls.block_trace = orig_block_trace
        grid_cls.free_trace = orig_free_trace
        Solution._block_voronoi = orig_block_voronoi
        Solution._unblock_voronoi = orig_unblock_voronoi
        Solution._find_crossed_nets = orig_find_crossed
        Solution._has_foreign_cells = orig_has_foreign
        Solution._relax_tree = orig_relax_tree
        Solution._commit = orig_commit
        Solution._try_rip_reroute = orig_try_rip_reroute
        Solution._try_jumper = orig_try_jumper
        Solution._commit_full_jumper = orig_commit_full_jumper
        Solution.snapshot = orig_snapshot
        Solution.restore = orig_restore
        Solution.rip_up = orig_rip_up

    return ht


def main():
    if not FIXTURE_DIR.exists():
        print(f"Fixture data not found at: {FIXTURE_DIR}")
        return

    print("Running profiled route...")
    ht = run_profiled_route()

    # Print tree to console
    print("\n" + "=" * 90)
    print("  HIERARCHICAL TIMING BREAKDOWN")
    print("=" * 90)
    root = ht.root.children[0]  # the "total" section
    print_tree(root, parent_elapsed=0)
    print("=" * 90)

    # Build sunburst HTML
    out_path = Path(__file__).resolve().parent.parent / "outputs" / "profile_sunburst.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html = build_sunburst_html(root, title="Router Hierarchical Profile")
    out_path.write_text(html, encoding="utf-8")
    print(f"\nInteractive sunburst chart saved to: {out_path}")


if __name__ == "__main__":
    main()

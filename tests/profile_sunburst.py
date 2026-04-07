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


def _tree_to_dict(node: TimerNode) -> dict:
    """Serialize a TimerNode tree to a JSON-friendly dict."""
    return {
        "name": node.name,
        "elapsed": node.elapsed,
        "call_count": node.call_count,
        "children": [_tree_to_dict(c) for c in node.children],
    }


def _collect_all_names(node: TimerNode, out: set[str] | None = None) -> set[str]:
    if out is None:
        out = set()
    out.add(node.name)
    for c in node.children:
        _collect_all_names(c, out)
    return out


def build_sunburst_html(root: TimerNode, title: str = "Router Profile") -> str:
    import json as _json
    tree_json = _json.dumps(_tree_to_dict(root))

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

    return f'''<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>{title}</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 20px; background: #fafafa; }}
.container {{ display: flex; gap: 20px; max-width: 1400px; margin: 0 auto; }}
.sidebar {{ width: 280px; flex-shrink: 0; }}
.sidebar h3 {{ margin: 0 0 8px; font-size: 14px; color: #555; }}
.node-list {{ max-height: 700px; overflow-y: auto; border: 1px solid #ddd; border-radius: 6px; background: #fff; padding: 6px; }}
.node-item {{ display: flex; align-items: center; gap: 6px; padding: 4px 6px; border-radius: 4px; cursor: pointer; font-size: 13px; user-select: none; }}
.node-item:hover {{ background: #f0f4ff; }}
.node-item.selected {{ background: #e0e8ff; font-weight: 600; }}
.node-item .time {{ color: #888; margin-left: auto; font-size: 11px; white-space: nowrap; }}
.chart {{ flex: 1; min-width: 0; }}
.controls {{ display: flex; gap: 8px; margin-bottom: 12px; align-items: center; }}
.controls button {{ padding: 5px 14px; border: 1px solid #ccc; border-radius: 4px; background: #fff; cursor: pointer; font-size: 13px; }}
.controls button:hover {{ background: #f0f0f0; }}
.controls .depth-label {{ font-size: 13px; color: #555; }}
.controls input[type=range] {{ width: 100px; }}
h1 {{ font-size: 20px; margin: 0 0 16px; text-align: center; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ padding: 4px 8px; text-align: left; border-bottom: 1px solid #eee; font-size: 13px; }}
th {{ background: #f0f0f0; }}
td:nth-child(2), td:nth-child(3), td:nth-child(4),
th:nth-child(2), th:nth-child(3), th:nth-child(4) {{ text-align: right; }}
</style>
</head><body>
<h1>{title}</h1>
<div class="container">
  <div class="sidebar">
    <h3>Select root node(s)</h3>
    <div class="controls">
      <button id="btn-reset">Reset</button>
      <button id="btn-all">Select all</button>
    </div>
    <div class="node-list" id="node-list"></div>
  </div>
  <div class="chart">
    <div class="controls">
      <span class="depth-label">Depth:</span>
      <input type="range" id="depth-slider" min="2" max="10" value="3">
      <span id="depth-val">3</span>
    </div>
    <div id="sunburst"></div>
  </div>
</div>
<div style="max-width:800px;margin:30px auto">
  <h2 style="font-size:16px">All categories sorted by total time</h2>
  <table>
    <tr><th>Category</th><th>Total (ms)</th><th>Calls</th><th>Avg (ms)</th></tr>
    {table_rows}
  </table>
</div>
<script>
const TREE = {tree_json};
const PALETTE = ["#4e79a7","#f28e2b","#e15759","#76b7b2","#59a14f","#edc948","#b07aa1","#ff9da7","#9c755f","#bab0ac"];

function collectNodes(node, path, depth, out) {{
  const id = path ? path + "/" + node.name : node.name;
  out.push({{ id, name: node.name, elapsed: node.elapsed, calls: node.call_count, depth, node }});
  for (const c of node.children) collectNodes(c, id, depth + 1, out);
}}

function findByName(node, name, results) {{
  if (node.name === name) results.push(node);
  for (const c of node.children) findByName(c, name, results);
}}

function flattenNode(node, parentId, depth, ids, labels, parents, values, texts, colors) {{
  const nodeId = parentId ? parentId + "/" + node.name : node.name;
  const ms = Math.round(node.elapsed * 1000 * 10) / 10;
  const callStr = node.call_count > 1 ? "  [" + node.call_count + "x]" : "";
  ids.push(nodeId); labels.push(node.name); parents.push(parentId);
  values.push(ms); texts.push(ms.toFixed(1) + "ms" + callStr);
  colors.push(parentId ? PALETTE[depth % PALETTE.length] : "#ffffff");
  let childSum = 0;
  for (const c of node.children) {{
    flattenNode(c, nodeId, depth + 1, ids, labels, parents, values, texts, colors);
    childSum += Math.round(c.elapsed * 1000 * 10) / 10;
  }}
  if (node.children.length > 0) {{
    const remainder = Math.round((ms - childSum) * 10) / 10;
    if (remainder > 0) {{
      const uid = nodeId + "/(other)";
      ids.push(uid); labels.push("(other)"); parents.push(nodeId);
      values.push(remainder); texts.push(remainder.toFixed(1) + "ms");
      colors.push("#dddddd");
    }} else if (remainder < 0) {{
      const idx = ids.indexOf(nodeId);
      values[idx] = Math.round(childSum * 10) / 10;
    }}
  }}
}}

function mergeNodes(nodes) {{
  const merged = {{ name: "selected", elapsed: 0, call_count: 0, children: [] }};
  const childMap = {{}};
  for (const n of nodes) {{
    merged.elapsed += n.elapsed;
    merged.call_count += n.call_count;
    for (const c of n.children) {{
      if (childMap[c.name]) {{
        childMap[c.name] = mergeTwo(childMap[c.name], c);
      }} else {{
        childMap[c.name] = deepCopy(c);
      }}
    }}
    const selfTime = n.elapsed - n.children.reduce((s, c) => s + c.elapsed, 0);
    if (selfTime > 0.0005 && n.children.length > 0) {{
      if (childMap["(self)"]) {{
        childMap["(self)"].elapsed += selfTime;
        childMap["(self)"].call_count += Math.max(n.call_count, 1);
      }} else {{
        childMap["(self)"] = {{ name: "(self)", elapsed: selfTime, call_count: Math.max(n.call_count, 1), children: [] }};
      }}
    }}
  }}
  merged.children = Object.values(childMap).sort((a, b) => b.elapsed - a.elapsed);
  return merged;
}}

function mergeTwo(a, b) {{
  const merged = {{ name: a.name, elapsed: a.elapsed + b.elapsed, call_count: a.call_count + b.call_count, children: [] }};
  const childMap = {{}};
  for (const c of a.children) childMap[c.name] = deepCopy(c);
  for (const c of b.children) {{
    if (childMap[c.name]) childMap[c.name] = mergeTwo(childMap[c.name], c);
    else childMap[c.name] = deepCopy(c);
  }}
  merged.children = Object.values(childMap).sort((a, b) => b.elapsed - a.elapsed);
  return merged;
}}

function deepCopy(n) {{
  return {{ name: n.name, elapsed: n.elapsed, call_count: n.call_count, children: n.children.map(deepCopy) }};
}}

// Build node list (unique names with aggregated time)
const allFlat = [];
collectNodes(TREE, "", 0, allFlat);
const nameMap = {{}};
for (const n of allFlat) {{
  if (n.node.children.length === 0) continue;
  if (!nameMap[n.name]) nameMap[n.name] = {{ elapsed: 0, calls: 0 }};
  nameMap[n.name].elapsed += n.elapsed;
  nameMap[n.name].calls += n.calls;
}}
const nameList = Object.entries(nameMap).sort((a, b) => b[1].elapsed - a[1].elapsed);

const listEl = document.getElementById("node-list");
const selected = new Set();

for (const [name, info] of nameList) {{
  const div = document.createElement("div");
  div.className = "node-item";
  div.dataset.name = name;
  div.innerHTML = '<span>' + name + '</span><span class="time">' + (info.elapsed * 1000).toFixed(1) + 'ms</span>';
  div.addEventListener("click", () => {{
    if (selected.has(name)) selected.delete(name); else selected.add(name);
    div.classList.toggle("selected");
    rebuild();
  }});
  listEl.appendChild(div);
}}

let maxDepth = 3;
const depthSlider = document.getElementById("depth-slider");
const depthVal = document.getElementById("depth-val");
depthSlider.addEventListener("input", () => {{
  maxDepth = parseInt(depthSlider.value);
  depthVal.textContent = maxDepth;
  rebuild();
}});

document.getElementById("btn-reset").addEventListener("click", () => {{
  selected.clear();
  listEl.querySelectorAll(".node-item").forEach(el => el.classList.remove("selected"));
  rebuild();
}});
document.getElementById("btn-all").addEventListener("click", () => {{
  listEl.querySelectorAll(".node-item").forEach(el => {{
    selected.add(el.dataset.name);
    el.classList.add("selected");
  }});
  rebuild();
}});

function rebuild() {{
  let root;
  if (selected.size === 0) {{
    root = TREE;
  }} else {{
    const matches = [];
    for (const name of selected) {{
      findByName(TREE, name, matches);
    }}
    if (matches.length === 0) {{ root = TREE; }}
    else if (matches.length === 1) {{ root = deepCopy(matches[0]); }}
    else {{ root = mergeNodes(matches); }}
  }}
  const ids = [], labels = [], parents = [], values = [], texts = [], colors = [];
  flattenNode(root, "", 0, ids, labels, parents, values, texts, colors);
  Plotly.react("sunburst", [{{
    type: "sunburst", ids, labels, parents, values, text: texts,
    branchvalues: "total",
    hovertemplate: "<b>%{{label}}</b><br>%{{text}}<extra></extra>",
    textinfo: "label+text", insidetextorientation: "radial",
    marker: {{ colors, line: {{ width: 1, color: "white" }} }},
    maxdepth: maxDepth,
  }}], {{
    margin: {{ t: 10, l: 10, r: 10, b: 10 }},
    width: 900, height: 800,
  }});
}}
rebuild();
</script>
</body></html>'''


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
        _perturb, _re_resolve_and_route,
        _copy_pin_pools, _restore_pin_pools,
    )
    from src.pipeline.router.pins import build_pin_pools
    from src.pipeline.router.solution import Solution
    from src.pipeline.router import pathfinder as pf_mod
    from src.pipeline.router.pathfinder import (
        _try_l_route, _octile_dt, _octile_h, _turn_cost, _SQRT2, DIRS,
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
                sx, sy = source
                tx, ty = sink
                W = grid.width
                H = grid.height
                cells = grid._cells
                if not (0 <= sx < W and 0 <= sy < H and 0 <= tx < W and 0 <= ty < H):
                    return None
                if cells[sy * W + sx] == TRACE_PATH or cells[ty * W + tx] == TRACE_PATH:
                    return None
                if source == sink:
                    return [source]
                N = W * H
                INF = float('inf')
                sink_key = ty * W + tx
                with ht.section("alloc"):
                    g = [INF] * N
                    parent = [-1] * N
                    closed = bytearray(N)
                    g[sy * W + sx] = 0
                    counter = 0
                    heap = [(_octile_h(sx - tx, sy - ty), counter, sx, sy, -1)]
                with ht.section("search"):
                    found_key = -1
                    found_cx = found_cy = 0
                    while heap:
                        f, _cnt, cx, cy, direction = _heappop(heap)
                        key = cy * W + cx
                        if closed[key]:
                            continue
                        closed[key] = 1
                        if key == sink_key:
                            found_key = key
                            found_cx, found_cy = cx, cy
                            break
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
                            if nval != FREE:
                                if nval == PERMANENTLY_BLOCKED:
                                    continue
                                if crossing_cost > 0 and (nval == TRACE_PATH or nval == BLOCKED):
                                    cross_extra = crossing_cost
                                else:
                                    if nval == TRACE_PATH:
                                        continue
                                    if (nx, ny) != sink and (nx, ny) != source:
                                        continue
                            move_cost = 1.0 if d < 4 else _SQRT2
                            tc = _turn_cost(direction, d, turn_penalty)
                            cost = move_cost + tc + cross_extra
                            if cost_map is not None:
                                cost += cost_map.get(nkey, 0)
                            tentative_g = cur_g + cost
                            if tentative_g < g[nkey]:
                                g[nkey] = tentative_g
                                parent[nkey] = key
                                counter += 1
                                _heappush(heap, (tentative_g + _octile_h(nx - tx, ny - ty), counter, nx, ny, d))
                if found_key < 0:
                    return None
                with ht.section("reconstruct"):
                    path = [(found_cx, found_cy)]
                    k = found_key
                    while True:
                        pk = parent[k]
                        if pk < 0:
                            break
                        path.append((pk % W, pk // W))
                        k = pk
                    path.reverse()
                    return path

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
                h_map = _octile_dt(W, H, tree_list)

            with ht.section("astar"):
                INF = 0x7FFFFFFF
                with ht.section("alloc"):
                    g = [INF] * N
                    parent = [-1] * N
                    closed = bytearray(N)
                with ht.section("seed"):
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
                with ht.section("search"):
                    found_key = -1
                    found_cx = found_cy = 0
                    while heap:
                        f, _cnt, cx, cy, direction = _heappop(heap)
                        key = cy * W + cx
                        if closed[key]:
                            continue
                        closed[key] = 1
                        if tree_mask[key]:
                            found_key = key
                            found_cx, found_cy = cx, cy
                            break
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
                if found_key < 0:
                    return None
                with ht.section("reconstruct"):
                    path = [(found_cx, found_cy)]
                    k = found_key
                    while True:
                        pk = parent[k]
                        if pk < 0:
                            break
                        path.append((pk % W, pk // W))
                        k = pk
                    path.reverse()
                    return path

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
    orig_commit = Solution._commit
    orig_try_rip_reroute = Solution._try_rip_reroute
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

    def timed_commit(self, *a, **kw):
        with ht.section("commit"):
            return orig_commit(self, *a, **kw)

    def timed_try_rip_reroute(self, *a, **kw):
        with ht.section("try_rip_reroute"):
            return orig_try_rip_reroute(self, *a, **kw)

    def timed_snapshot(self, *a, **kw):
        with ht.section("snapshot"):
            return orig_snapshot(self, *a, **kw)

    def timed_restore(self, *a, **kw):
        with ht.section("restore"):
            return orig_restore(self, *a, **kw)

    def timed_rip_up(self, *a, **kw):
        with ht.section("rip_up"):
            return orig_rip_up(self, *a, **kw)

    orig_grid_paths_to_traces = Solution._grid_paths_to_traces
    orig_to_result = Solution.to_result

    def timed_grid_paths_to_traces(self, *a, **kw):
        with ht.section("grid_paths_to_traces"):
            return orig_grid_paths_to_traces(self, *a, **kw)

    def timed_to_result(self, *a, **kw):
        with ht.section("to_result"):
            return orig_to_result(self, *a, **kw)

    Solution._block_voronoi = timed_block_voronoi
    Solution._unblock_voronoi = timed_unblock_voronoi
    Solution._find_crossed_nets = timed_find_crossed
    Solution._has_foreign_cells = timed_has_foreign
    Solution._commit = timed_commit
    Solution._try_rip_reroute = timed_try_rip_reroute
    Solution.snapshot = timed_snapshot
    Solution.restore = timed_restore
    Solution.rip_up = timed_rip_up
    Solution._grid_paths_to_traces = timed_grid_paths_to_traces
    Solution.to_result = timed_to_result

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
                with ht.section("build_pin_pools"):
                    pin_pools = build_pin_pools(placement, catalog)
                with ht.section("resolve_pads"):
                    pads_map, pin_assignments = _resolve_all_pads(
                        net_ids, net_pad_map, placement, catalog, grid, pin_pools,
                    )
                with ht.section("priority_order"):
                    ordering = _priority_order(
                        net_ids, net_pad_map, pads_map,
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
                    best_pads_map = dict(pads_map)
                    best_pin_assignments = dict(pin_assignments)
                    best_pin_pools = _copy_pin_pools(pin_pools)
                    stall = 0
                    iteration = 0
                    profiler_max_iterations = 200
                    t_loop_start = time.perf_counter()
                    profiler_time_limit = 60.0

                    while True:
                        all_routed = best_score[0] == 0
                        if all_routed and iteration >= config.max_improve_iterations:
                            break
                        if iteration >= profiler_max_iterations:
                            print(f"  Profiler cap reached ({profiler_max_iterations} iters)")
                            break
                        if time.perf_counter() - t_loop_start > profiler_time_limit:
                            print(f"  Profiler time limit reached ({profiler_time_limit}s)")
                            break

                        targets = solution.worst_nets(k=3)
                        if not targets:
                            break

                        neighborhood = solution.neighborhood(targets)
                        before = solution.score()

                        solution.rip_up(neighborhood)
                        new_order = _perturb(neighborhood, targets, iteration)

                        with ht.section("re_route"):
                            solution.route_nets(new_order, pads_map)

                        with ht.section("retry_unrouted"):
                            unrouted = [nid for nid in net_ids
                                        if nid not in solution.routes and nid in pads_map]
                            if unrouted:
                                solution.route_nets(unrouted, pads_map)

                        with ht.section("re_resolve_unrouted"):
                            unrouted_still = [nid for nid in net_ids
                                             if nid not in solution.routes]
                            if unrouted_still:
                                _re_resolve_and_route(
                                    unrouted_still, net_pad_map, placement, catalog,
                                    grid, pin_pools, pin_assignments, pads_map, solution,
                                )

                        after = solution.score()

                        if after < before:
                            best = solution.snapshot()
                            best_score = after
                            best_pads_map = dict(pads_map)
                            best_pin_assignments = dict(pin_assignments)
                            best_pin_pools = _copy_pin_pools(pin_pools)
                            stall = 0
                            print(f"  Iter {iteration+1}: improved {before} -> {after}")
                            if solution.is_perfect():
                                print("  Perfect solution found!")
                                break
                        else:
                            solution.restore(best)
                            pads_map.update(best_pads_map)
                            pin_assignments.update(best_pin_assignments)
                            _restore_pin_pools(pin_pools, best_pin_pools)
                            stall += 1
                            print(f"  Iter {iteration+1}: no improvement (stall {stall})")

                            if all_routed and stall >= config.stall_limit:
                                print(f"  Stalled for {stall} iterations, stopping")
                                break

                        iteration += 1

                    solution.restore(best)
                    pin_assignments.update(best_pin_assignments)
                    solution.pin_assignments = pin_assignments
                    final_score = solution.score()
                    print(f"Final score: {final_score}")

            # ── Phase 7: Output conversion ──
            with ht.section("output_conversion"):
                solution.to_result(include_debug=False)

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
        Solution._commit = orig_commit
        Solution._try_rip_reroute = orig_try_rip_reroute
        Solution.snapshot = orig_snapshot
        Solution.restore = orig_restore
        Solution.rip_up = orig_rip_up
        Solution._grid_paths_to_traces = orig_grid_paths_to_traces
        Solution.to_result = orig_to_result

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

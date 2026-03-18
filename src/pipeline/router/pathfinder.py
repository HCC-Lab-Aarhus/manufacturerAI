"""A* pathfinder for Manhattan routing on the routing grid.

Supports:
  - Point-to-point routing (find_path)
  - Point-to-tree routing for multi-pin nets (find_path_to_tree)
  - Turn penalty to prefer straight runs
"""

from __future__ import annotations

import array as _array_mod
from heapq import heappush as _heappush, heappop as _heappop

import numpy as np

from .grid import RoutingGrid, FREE, BLOCKED, TRACE_PATH, PERMANENTLY_BLOCKED
from .models import TURN_PENALTY


# Manhattan directions: (dx, dy)
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def find_path(
    grid: RoutingGrid,
    source: tuple[int, int],
    sink: tuple[int, int],
    *,
    turn_penalty: int = TURN_PENALTY,
    crossing_cost: int = 0,
    cost_map: dict[int, float] | None = None,
) -> list[tuple[int, int]] | None:
    """A* point-to-point Manhattan routing.

    Returns a list of (gx, gy) grid cells from source to sink,
    or None if no path exists.

    When crossing_cost > 0 the pathfinder is allowed to walk through
    TRACE_PATH and BLOCKED cells at the given extra cost per cell.
    """
    sx, sy = source
    tx, ty = sink

    W = grid.width
    H = grid.height
    cells = grid._cells

    if not (0 <= sx < W and 0 <= sy < H and 0 <= tx < W and 0 <= ty < H):
        return None
    if cells[sy * W + sx] == TRACE_PATH:
        return None
    if cells[ty * W + tx] == TRACE_PATH:
        return None
    if source == sink:
        return [source]

    if cost_map is None:
        l_path = _try_l_route(grid, source, sink)
        if l_path is not None:
            return l_path

    N = W * H
    INF = 0x7FFFFFFF
    start_key = sy * W + sx
    sink_key = ty * W + tx

    g = [INF] * N
    g[start_key] = 0
    parent = [-1] * N
    closed = bytearray(N)

    h0 = abs(sx - tx) + abs(sy - ty)
    counter = 0
    heap: list[tuple[int, int, int, int, int]] = [(h0, counter, sx, sy, -1)]

    while heap:
        f, _cnt, cx, cy, direction = _heappop(heap)
        key = cy * W + cx

        if closed[key]:
            continue
        closed[key] = 1

        if key == sink_key:
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

            is_turn = direction != -1 and direction != d
            cost = 1 + (turn_penalty if is_turn else 0) + cross_extra
            if cost_map is not None:
                cost += cost_map.get(nkey, 0)
            tentative_g = cur_g + cost

            if tentative_g < g[nkey]:
                g[nkey] = tentative_g
                parent[nkey] = key
                h = abs(nx - tx) + abs(ny - ty)
                counter += 1
                _heappush(heap, (tentative_g + h, counter, nx, ny, d))

    return None


def find_path_to_tree(
    grid: RoutingGrid,
    source: tuple[int, int] | set[tuple[int, int]],
    tree: set[tuple[int, int]],
    *,
    turn_penalty: int = TURN_PENALTY,
    crossing_cost: int = 0,
    cost_map: dict[int, float] | None = None,
) -> list[tuple[int, int]] | None:
    """A* from source point(s) to any cell in an existing routing tree.

    *source* may be a single ``(gx, gy)`` tuple **or** a set of
    candidate source cells (multi-source A*).

    Returns the path (grid cells) or None.
    """

    # Cache grid internals as locals
    W = grid.width
    H = grid.height
    N = W * H
    cells = grid._cells
    INF = 0x7FFFFFFF

    # ── Normalise source to a set ──────────────────────────────
    if isinstance(source, set):
        sources = source
    else:
        sources = {source}

    # Quick overlap check
    overlap = sources & tree
    if overlap:
        cell = next(iter(overlap))
        return [cell]

    # Build a flat bytearray mask for O(1) tree membership
    tree_mask = bytearray(N)
    tree_list = list(tree)
    for tx, ty in tree_list:
        tree_mask[ty * W + tx] = 1

    # Precomputed distance transform: O(1) heuristic lookup
    h_map = _manhattan_dt(W, H, tree_list)

    # ── Pre-allocated containers ───────────────────────────────
    g = [INF] * N
    parent = [-1] * N
    closed = bytearray(N)

    # ── Seed heap with all valid source cells ──────────────────
    counter = 0
    heap: list[tuple[int, int, int, int, int]] = []

    for sx, sy in sources:
        if not (0 <= sx < W and 0 <= sy < H):
            continue
        skey = sy * W + sx
        if cells[skey] != FREE and not tree_mask[skey]:
            continue
        g[skey] = 0
        h0 = h_map[skey]
        _heappush(heap, (h0, counter, sx, sy, -1))
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
            # Reconstruct path
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


# ── Manhattan distance transform ──────────────────────────────────

def _manhattan_dt(W: int, H: int, tree_cells: list[tuple[int, int]]) -> _array_mod.array:
    """Return flat array of Manhattan distances to nearest tree cell.

    Uses a separable 2-pass distance transform: O(W*H) total,
    then O(1) per heuristic lookup during A*.
    """
    INF32 = np.int32(W + H + 2)
    dist = np.full((H, W), INF32, dtype=np.int32)
    n = len(tree_cells)
    if n > 32:
        tc = np.array(tree_cells, dtype=np.intp)
        dist[tc[:, 1], tc[:, 0]] = 0
    else:
        for tx, ty in tree_cells:
            dist[ty, tx] = 0
    one = np.int32(1)
    for x in range(1, W):
        np.minimum(dist[:, x], dist[:, x - 1] + one, out=dist[:, x])
    for x in range(W - 2, -1, -1):
        np.minimum(dist[:, x], dist[:, x + 1] + one, out=dist[:, x])
    for y in range(1, H):
        np.minimum(dist[y], dist[y - 1] + one, out=dist[y])
    for y in range(H - 2, -1, -1):
        np.minimum(dist[y], dist[y + 1] + one, out=dist[y])
    return _array_mod.array('i', dist.tobytes())


# ── Fast L-shaped route ────────────────────────────────────────────

def _try_l_route(
    grid: RoutingGrid,
    source: tuple[int, int],
    sink: tuple[int, int],
) -> list[tuple[int, int]] | None:
    """Try a simple L-shaped (one-bend) route.  Returns path or None."""
    for h_first in (True, False):
        path = _l_route(grid, source, sink, h_first)
        if path is not None:
            return path
    return None


def _l_route(
    grid: RoutingGrid,
    source: tuple[int, int],
    sink: tuple[int, int],
    horizontal_first: bool,
) -> list[tuple[int, int]] | None:
    sx, sy = source
    tx, ty = sink

    cells = grid._cells
    W = grid.width
    H = grid.height

    def _ok(x: int, y: int) -> bool:
        if not (0 <= x < W and 0 <= y < H):
            return False
        val = cells[y * W + x]
        if val == FREE:
            return True
        if (x, y) == sink:
            return True
        return False

    path: list[tuple[int, int]] = [(sx, sy)]

    if horizontal_first:
        dx = 1 if tx > sx else -1
        x, y = sx, sy
        while x != tx:
            x += dx
            if not _ok(x, y):
                return None
            path.append((x, y))
        dy = 1 if ty > sy else -1
        while y != ty:
            y += dy
            if not _ok(x, y):
                return None
            path.append((x, y))
    else:
        dy = 1 if ty > sy else -1
        x, y = sx, sy
        while y != ty:
            y += dy
            if not _ok(x, y):
                return None
            path.append((x, y))
        dx = 1 if tx > sx else -1
        while x != tx:
            x += dx
            if not _ok(x, y):
                return None
            path.append((x, y))

    return path

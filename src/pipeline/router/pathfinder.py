"""A* pathfinder for Manhattan routing on the routing grid.

Supports:
  - Point-to-point routing (findPath)
  - Point-to-tree routing for multi-pin nets (findPathToTree)
  - Turn penalty to prefer straight runs
  - Optional cell cost function for edge-hugging power traces
  - Crossing-aware mode for rip-up (heavy penalty for blocked cells)
"""

from __future__ import annotations

import heapq

from .grid import RoutingGrid
from .models import TURN_PENALTY, CROSSING_PENALTY


# Manhattan directions: (dx, dy)
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def find_path(
    grid: RoutingGrid,
    source: tuple[int, int],
    sink: tuple[int, int],
    *,
    turn_penalty: int = TURN_PENALTY,
) -> list[tuple[int, int]] | None:
    """A* point-to-point Manhattan routing.

    Returns a list of (gx, gy) grid cells from source to sink,
    or None if no path exists.
    """
    sx, sy = source
    tx, ty = sink

    if not grid.in_bounds(sx, sy) or not grid.in_bounds(tx, ty):
        return None
    if source == sink:
        return [source]

    # Try L-shaped routes first (fast path)
    l_path = _try_l_route(grid, source, sink)
    if l_path is not None:
        return l_path

    # Full A*
    W = grid.width
    encode = lambda x, y: y * W + x

    # heap entries: (f, g, x, y, direction, parent_key)
    start_key = encode(sx, sy)
    h0 = abs(sx - tx) + abs(sy - ty)
    # use counter for tiebreaking
    counter = 0
    heap: list[tuple[int, int, int, int, int, int]] = [(h0, counter, sx, sy, -1, -1)]
    g_scores: dict[int, int] = {start_key: 0}
    parents: dict[int, tuple[int, int]] = {}  # key -> (parent_key, direction)
    closed: set[int] = set()

    while heap:
        f, _cnt, cx, cy, direction, parent_key = heapq.heappop(heap)
        key = encode(cx, cy)

        if key in closed:
            continue
        closed.add(key)
        if key != start_key:
            parents[key] = (parent_key, direction)

        if cx == tx and cy == ty:
            # Reconstruct path
            path = [(cx, cy)]
            k = key
            while k in parents:
                pk, _ = parents[k]
                if pk < 0:
                    break
                px, py = pk % W, pk // W
                path.append((px, py))
                k = pk
            path.reverse()
            return path

        cur_g = g_scores[key]

        for d, (dx, dy) in enumerate(DIRS):
            nx, ny = cx + dx, cy + dy
            if not grid.in_bounds(nx, ny):
                continue
            nkey = encode(nx, ny)
            if nkey in closed:
                continue
            # Allow stepping onto the source or sink even if blocked
            if not grid.is_free(nx, ny) and (nx, ny) != sink and (nx, ny) != source:
                continue

            is_turn = direction != -1 and direction != d
            cost = 1 + (turn_penalty if is_turn else 0)
            tentative_g = cur_g + cost

            if nkey not in g_scores or tentative_g < g_scores[nkey]:
                g_scores[nkey] = tentative_g
                h = abs(nx - tx) + abs(ny - ty)
                counter += 1
                heapq.heappush(heap, (tentative_g + h, counter, nx, ny, d, key))

    return None


def find_path_to_tree(
    grid: RoutingGrid,
    source: tuple[int, int],
    tree: set[tuple[int, int]],
    *,
    turn_penalty: int = TURN_PENALTY,
    allow_crossings: bool = False,
) -> list[tuple[int, int]] | None:
    """A* from a source point to any cell in an existing routing tree.

    Used for multi-pin nets: connect each pad to the growing tree.

    If allow_crossings=True, blocked (non-permanent) cells can be
    traversed with a heavy penalty.  This is used during rip-up to
    find minimum-crossing paths.

    Returns the path (grid cells) or None.
    """
    sx, sy = source
    if not grid.in_bounds(sx, sy):
        return None
    if (sx, sy) in tree:
        return [(sx, sy)]

    # Precompute tree coordinates for fast heuristic
    tree_list = list(tree)
    tree_xs = [t[0] for t in tree_list]
    tree_ys = [t[1] for t in tree_list]

    def min_h(x: int, y: int) -> int:
        best = abs(x - tree_xs[0]) + abs(y - tree_ys[0])
        for i in range(1, len(tree_xs)):
            d = abs(x - tree_xs[i]) + abs(y - tree_ys[i])
            if d < best:
                best = d
                if d == 0:
                    return 0
        return best

    W = grid.width
    encode = lambda x, y: y * W + x
    tree_keys = {encode(t[0], t[1]) for t in tree}

    h0 = min_h(sx, sy)
    counter = 0
    heap: list[tuple[int, int, int, int, int, int]] = [(h0, counter, sx, sy, -1, -1)]
    g_scores: dict[int, int] = {encode(sx, sy): 0}
    parents: dict[int, tuple[int, int]] = {}
    closed: set[int] = set()

    while heap:
        f, _cnt, cx, cy, direction, parent_key = heapq.heappop(heap)
        key = encode(cx, cy)

        if key in closed:
            continue
        closed.add(key)
        if key != encode(sx, sy):
            parents[key] = (parent_key, direction)

        if key in tree_keys:
            # Reconstruct path
            path = [(cx, cy)]
            k = key
            while k in parents:
                pk, _ = parents[k]
                if pk < 0:
                    break
                px, py = pk % W, pk // W
                path.append((px, py))
                k = pk
            path.reverse()
            return path

        cur_g = g_scores[key]

        for d, (dx, dy) in enumerate(DIRS):
            nx, ny = cx + dx, cy + dy
            if not grid.in_bounds(nx, ny):
                continue
            nkey = encode(nx, ny)
            if nkey in closed:
                continue

            is_tree_cell = nkey in tree_keys
            cell_free = grid.is_free(nx, ny)

            if not cell_free and not is_tree_cell:
                if not allow_crossings or grid.is_permanently_blocked(nx, ny):
                    continue

            is_turn = direction != -1 and direction != d
            cost = 1 + (turn_penalty if is_turn else 0)
            if not cell_free and not is_tree_cell:
                cost += CROSSING_PENALTY
            tentative_g = cur_g + cost

            if nkey not in g_scores or tentative_g < g_scores[nkey]:
                g_scores[nkey] = tentative_g
                h = min_h(nx, ny)
                counter += 1
                heapq.heappush(heap, (tentative_g + h, counter, nx, ny, d, key))

    return None


# ── Fast L-shaped route ────────────────────────────────────────────

def _try_l_route(
    grid: RoutingGrid,
    source: tuple[int, int],
    sink: tuple[int, int],
) -> list[tuple[int, int]] | None:
    """Try a simple L-shaped (one-bend) route.  Returns path or None."""
    # Try horizontal-first then vertical-first
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
    path: list[tuple[int, int]] = [(sx, sy)]

    if horizontal_first:
        # Horizontal leg
        dx = 1 if tx > sx else -1
        x, y = sx, sy
        while x != tx:
            x += dx
            if not grid.is_free(x, y) and (x, y) != sink:
                return None
            path.append((x, y))
        # Vertical leg
        dy = 1 if ty > sy else -1
        while y != ty:
            y += dy
            if not grid.is_free(x, y) and (x, y) != sink:
                return None
            path.append((x, y))
    else:
        # Vertical leg
        dy = 1 if ty > sy else -1
        x, y = sx, sy
        while y != ty:
            y += dy
            if not grid.is_free(x, y) and (x, y) != sink:
                return None
            path.append((x, y))
        # Horizontal leg
        dx = 1 if tx > sx else -1
        while x != tx:
            x += dx
            if not grid.is_free(x, y) and (x, y) != sink:
                return None
            path.append((x, y))

    return path

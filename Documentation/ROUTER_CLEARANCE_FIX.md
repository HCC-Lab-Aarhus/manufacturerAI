# Router Clearance Fix & Voronoi Optimization

## Problem: Protected Pin Cells Bypass Clearance Blocking

### Symptom

The router outputs traces that violate minimum clearance (1.9 mm center-to-center for 1.0 mm trace width + 0.9 mm clearance). A sample routing session produced **65 clearance violations**, with the worst being **0.50 mm** (1 grid cell) between BTN_R1C6 and LED_CTRL_4 in the MCU pin area.

### Root Cause

In `grid.py`, `block_trace` excludes all `protected_flats` from clearance:

```python
clearance_flats -= protected_flats
```

Every pin gets a `pad_radius=3` protection zone (7×7 cells = 3.5×3.5 mm) via `force_free_cell` + `protect_cell` in `engine.py`. The ATmega328P DIP-28 has 28 pins across two rows spanning ~33 mm — their overlapping 7×7 protected zones create a massive band where **no trace-to-trace clearance is enforced**.

Additionally, `score()` only measures `(missing_nets, total_trace_cells)`. There is no geometric clearance validation anywhere in the pipeline — not in `score()`, not in `to_result()`, not before final export.

### Fix Strategy

**Replace the blanket `protected_flats` exemption with a narrow, net-aware exemption.**

1. **Track pin ownership of protected cells.** Add `_protected_pin_map: dict[int, str]` mapping `flat → "instance_id:pin_id"`. Populate during `protect_cell` by accepting a `pin_key` parameter.

2. **Make `block_trace` net-aware.** Instead of subtracting all `protected_flats`, only exempt cells whose `pin_key` belongs to the net being committed. `block_trace` already receives `net_id` — pass the net's own pin keys in, and change the exemption to:
   ```python
   own_pin_flats = {f for f, pk in self._protected_pin_map.items() if pk in net_pin_keys}
   clearance_flats -= own_pin_flats
   ```

3. **Mirror in `free_trace`.** Use the same net-aware exemption so rip-up correctly restores cells.

4. **Keep `force_free_cell`** on immediate pin cells (possibly reduce from `pad_radius=3` to radius 1). Pins must remain reachable by A*.

5. **Keep `protect_cell`** on the same narrow set — protection means "don't PERMANENTLY block this", not "exempt from all clearance".

### Expected Impact

Some nets that currently route through the MCU pin field will fail instead of producing clearance violations. The router should report `failed_nets` honestly. The iterative improvement loop and pin allocator will then try different MCU pin assignments.

---

## Optimization: Restore Numpy-Vectorized Voronoi Blocking

### Background

The voronoi system temporarily blocks cells nearest to foreign pins during A* pathfinding, so traces stay in their own pin's territory. It was introduced in `76526ec` and went through three stages:

1. **Original (76526ec)** — standalone functions in `engine.py`, plain loop over all voronoi cells per net.
2. **Numpy-optimized (2496f9f)** — pre-grouped by pin key at init time, bulk numpy operations for blocking/unblocking.
3. **Current (bef13f7)** — simplified back to plain loops during jumper-wire removal refactor.

### What Was Lost

The numpy version pre-built `_voronoi_by_pin` and `_voronoi_flat_by_pin` dicts at `Solution.__init__` time, keyed by `"instance_id:pin_id"`. `_block_voronoi()` concatenated foreign-pin arrays and used vectorized masking instead of iterating the entire voronoi map:

```python
def _block_voronoi(self, net_pads: list[NetPad]) -> np.ndarray:
    if not self._voronoi_flat_by_pin:
        return np.array([], dtype=np.int32)
    net_pin_keys = {f"{pad.instance_id}:{pad.pin_id}" for pad in net_pads}
    foreign_arrs = [
        arr for key, arr in self._voronoi_flat_by_pin.items()
        if key not in net_pin_keys
    ]
    if not foreign_arrs:
        return np.array([], dtype=np.int32)
    foreign_flat = np.concatenate(foreign_arrs)
    cells_np = np.frombuffer(self.grid._cells, dtype=np.uint8)
    free_mask = cells_np[foreign_flat] == FREE
    to_block = foreign_flat[free_mask]
    cells_np[to_block] = BLOCKED
    return to_block

def _unblock_voronoi(self, blocked: np.ndarray) -> None:
    if len(blocked) == 0:
        return
    cells_np = np.frombuffer(self.grid._cells, dtype=np.uint8)
    cells_np[blocked] = FREE
```

Init-time pre-grouping:

```python
self._voronoi_by_pin: dict[str, list[tuple[int, int]]] = {}
self._voronoi_flat_by_pin: dict[str, np.ndarray] = {}
if pin_voronoi is not None:
    W = grid.width
    for flat, pin_key in pin_voronoi.items():
        gx = flat % W
        gy = flat // W
        self._voronoi_by_pin.setdefault(pin_key, []).append((gx, gy))
    for pin_key, cells in self._voronoi_by_pin.items():
        self._voronoi_flat_by_pin[pin_key] = np.array(
            [gy * W + gx for gx, gy in cells], dtype=np.int32,
        )
```

### How to Restore

Restore the pre-grouped dicts and numpy block/unblock in `solution.py`. The current plain-loop version is functionally identical but slower — `_block_voronoi` is called once per net per pathfind attempt, so the savings compound across the iterative improvement loop.

This is independent of the clearance fix and can be done separately.

---

## Lost Optimization: Tree Relaxation for Multi-Pin Nets

### What It Was

Introduced in `101a5fb`, removed in `bef13f7` (jumper-wire removal).

After routing a multi-pin net via MST-guided Steiner tree, `_relax_tree()` would iteratively re-route each tree edge through the remaining tree to find shortcuts. For each edge:

1. Identify cells unique to that edge (not shared with other edges or pads).
2. Free those cells from the grid.
3. Re-route from the edge's start to the remaining tree using `find_path_to_tree`.
4. If the new path is shorter, keep it; otherwise restore the original.
5. Repeat up to 3 rounds until no improvement.

This reduced total trace length for nets with 3+ pins (e.g. VCC, GND) by finding shared routing corridors that the initial MST-ordered routing missed.

### How to Restore

Re-implement `_relax_tree()` and `_relax_find_path()` in `solution.py`, plus the `_bfs_grid_cells` helper for 4-connected flood fill. Call it after committing a multi-pin net. The original implementation was ~80 lines.

Key detail: the original manipulated `_cells` and `_trace_owner` directly without touching clearance — that was acceptable because relaxation only shrinks paths, so existing clearance stays valid.

---

## Lost Optimization: Negotiated Congestion Routing

### What It Was

Introduced in `b478626`, removed in `5a7db02` (jumper-wire rewrite).

A pre-pass before the main greedy routing: route all nets simultaneously on a clean grid (no trace blocking) using a shared cost map. Over multiple iterations, cells used by multiple nets get increasing congestion penalties, pushing overlapping nets apart. When near-convergence was reached (≤5 overlapping path cells), the negotiated paths were used as guidance for the real commit pass.

### Why It Was Removed

The jumper-wire rewrite replaced the entire routing strategy. The negotiated congestion routing was incompatible with the jumper-first approach. When jumpers were later removed (`bef13f7`), the current iterative improvement (GA-inspired rip-up + elite pool) was added instead.

### Should It Be Restored?

The current iterative improvement loop serves a similar purpose (exploring different routing orders). Negotiated congestion routing could potentially be added as an additional pre-pass to generate better initial solutions, but the benefit is unclear given the current GA loop. Low priority.

---

## Optimizations That Survived

These were **not lost** — they exist in the current codebase:

| Optimization | Introduced | Status |
|---|---|---|
| Numpy-vectorized outline blocking (`contains_xy`) | `2496f9f` | **Present** in `grid.py` |
| Pre-allocated `bytearray(N)` for A* closed set | `2496f9f` | **Present** in `pathfinder.py` |
| Flat `list[int]` for g-scores and parent arrays | `2496f9f` | **Present** in `pathfinder.py` |
| `bytearray` tree membership mask | `2496f9f` | **Present** in `pathfinder.py` |
| `heappush`/`heappop` direct imports | `ed44f91` | **Present** in `pathfinder.py` |
| Scipy `distance_transform_cdt` for heuristic | `7af7d55` | **Present** as `_octile_dt` (upgraded from Manhattan to octile) |
| Fast L-shaped route attempt before full A* | post-`bef13f7` | **Present** in `pathfinder.py` |
| Segment-distance clearance (`_segment_clearance_flats`) | `696aa27` | **Present** in `grid.py` (replaced the square-offset caching, geometrically more accurate for diagonal traces) |
| Octile A* with 8-directional movement + angle-based turn penalty | `d9d33d0` | **Present** in `pathfinder.py` |

---

## Lost Micro-Optimization: Vectorized Containment in `_grid_paths_to_traces`

### What It Was

Introduced in `2496f9f`, removed in `bef13f7`.

The output conversion method batch-tested all trace waypoints for polygon containment using `shapely.contains_xy(outline, xs, ys)` with numpy arrays. Only waypoints that fell outside the outline were promoted to `Point` objects for nearest-boundary clamping.

**Optimized (2496f9f):**
```python
from shapely import contains_xy as _contains_xy

xs = np.array([wx for wx, _ in world_path])
ys = np.array([wy for _, wy in world_path])
inside = _contains_xy(outline, xs, ys)
for i, (wx, wy) in enumerate(world_path):
    if inside[i]:
        clamped.append((wx, wy))
    else:
        pt = Point(wx, wy)
        nearest = outline.exterior.interpolate(outline.exterior.project(pt))
        clamped.append((nearest.x, nearest.y))
```

**Current:**
```python
for wx, wy in world_path:
    pt = Point(wx, wy)
    if not outline.contains(pt):
        nearest = outline.exterior.interpolate(outline.exterior.project(pt))
        clamped.append((nearest.x, nearest.y))
    else:
        clamped.append((wx, wy))
```

The current code creates a `Point` object and calls `outline.contains(pt)` per waypoint. Shapely's `contains_xy` is C-backed and handles arrays in one call.

### How to Restore

Add `import numpy as np` and `from shapely import contains_xy` to `solution.py`, replace the per-point loop with the batch version. ~5 lines changed.

---

## Lost Micro-Optimization: In-Place Slice Restore

### What It Was

Introduced in `2496f9f`, removed in `bef13f7`.

The `restore()` method used in-place slice assignment to reuse the existing bytearray buffer:

```python
self.grid._cells[:] = snap.cells     # 2496f9f — reuses buffer
self.grid._cells = bytearray(snap.cells)  # current — allocates new buffer
```

Slice assignment is ~1.4x faster per call. `restore()` is called on every non-improving iteration of the GA loop (the majority of iterations), so this compounds.

### How to Restore

Change one line in `Solution.restore()`.

---

## Summary of Lost Items

| Item | Introduced | Removed In | Lines | Priority |
|---|---|---|---|---|
| Numpy-vectorized voronoi blocking | `2496f9f` | `bef13f7` | ~30 | Medium — called once per net per pathfind |
| Tree relaxation for multi-pin nets | `101a5fb` | `bef13f7` | ~80 | Medium — reduces trace length for VCC/GND |
| Vectorized containment in `_grid_paths_to_traces` | `2496f9f` | `bef13f7` | ~5 | Low — called once at end of routing |
| In-place slice restore | `2496f9f` | `bef13f7` | 1 | Low — ~1.4x faster per restore call |
| Negotiated congestion routing | `b478626` | `5a7db02` | ~300 | Low — partially replaced by GA loop |

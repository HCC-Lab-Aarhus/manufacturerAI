# Router — Comprehensive Behavioural Report

> Deep-dive into `src/pipeline/router/`, the conductive-ink trace routing
> engine that turns a placed PCB design into physical Manhattan traces.

---

## 1. Purpose & Interface

The router is **Stage 4** of the manufacturerAI pipeline.  It receives a
`FullPlacement` (components with fixed positions, an outline polygon,
and a net list) plus the component `CatalogResult`, and emits a
`RoutingResult` containing:

| Field | Type | Meaning |
|-------|------|---------|
| `traces` | `list[Trace]` | Routed traces, each a sequence of (x, y) waypoints in mm |
| `pin_assignments` | `dict[str, str]` | Dynamic pin allocations (`"net\|mcu_1:gpio" → "mcu_1:PD2"`) |
| `failed_nets` | `list[str]` | Net IDs that could not be connected |
| `debug_grids` | `list[dict]` | Base64-encoded overlay bitmaps for the web viewer |

The single entry point is `route_traces(placement, catalog, *, config=None)`.

---

## 2. File Map

| File | Responsibility | ~Lines |
|------|----------------|--------|
| `__init__.py` | Public API re-exports | 30 |
| `models.py` | `Trace`, `RoutingResult`, `RouterConfig` dataclasses; default constants | 70 |
| `grid.py` | `RoutingGrid` — discretised 2-D cell grid with blocking, clearance tracking, and trace ownership | 350 |
| `pathfinder.py` | A\* pathfinding: point-to-point (`find_path`) and point-to-tree (`find_path_to_tree`), plus L-route fast path | 330 |
| `pins.py` | Pin reference parsing, world-coordinate resolution, dynamic pool allocation | 260 |
| `engine.py` | Master algorithm: grid setup → negotiation → retry loop → output conversion | 1300 |
| `serialization.py` | `routing_to_dict` / `parse_routing` JSON round-trip | 50 |
| `bitmap.py` | Rasterises world-mm traces to a nozzle-pitch text bitmap for the silver-ink printer | 160 |
| `debug.py` | Self-contained ownership overlay for the web debug viewer | 150 |

---

## 3. Physical Constants (from `pipeline/config.py`)

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `grid_resolution_mm` | 0.5 mm | Cell side-length of the routing grid |
| `trace_width_mm` | 0.5 mm | Physical width of one conductive-ink trace |
| `trace_clearance_mm` | 1.0 mm | Minimum edge-to-edge gap between traces |
| `pin_clearance_mm` | 1.5 mm | Minimum gap from a trace edge to a foreign pin centre |
| `edge_clearance_mm` | 1.5 mm | Minimum distance from any trace to the outline boundary |
| `FLOOR_MM` | 2.0 mm | Z-height of the ironed floor surface (trace zone) |

Derived: **routing channel pitch** = trace_width + trace_clearance = **1.5 mm** = 3 grid cells.

Router-only knobs (in `RouterConfig`):

| Knob | Default | Purpose |
|------|---------|---------|
| `turn_penalty` | 5 | Extra A\* cost for each 90° direction change |
| `crossing_cost` | 50 | Extra A\* cost for crossing an existing trace (rip-up mode) |
| `max_retries` | 30 | Maximum net-ordering shuffle attempts in the retry fallback |

---

## 4. The Routing Grid (`grid.py`)

### 4.1 Construction

1. Compute the axis-aligned bounding box of the outline polygon.
2. Create a `width × height` cell array at `grid_resolution_mm` pitch.
3. Build an **inset polygon** = `outline.buffer(-edge_clearance)`.
4. Mark every cell whose centre falls **outside** the inset polygon as `PERMANENTLY_BLOCKED`.

Result: only cells safely inside the outline's routing zone start as `FREE`.

### 4.2 Cell States

| Value | Constant | Meaning |
|-------|----------|---------|
| 0 | `FREE` | Available for routing |
| 1 | `BLOCKED` | Temporarily blocked (clearance, Voronoi, etc.) — can be freed |
| 2 | `PERMANENTLY_BLOCKED` | Edge/component body — never freed |
| 3 | `TRACE_PATH` | Occupied by an actual routed trace — tracked in `_trace_owner` |

### 4.3 Ownership Tracking

Two parallel dictionaries keep track of which net owns what:

- **`_trace_owner[flat_index] → net_id`**: the net whose trace path occupies this cell.
- **`_clearance_owner[flat_index] → set[net_id]`**: all nets whose clearance zone covers this cell.

This bookkeeping is critical for the **rip-up** mechanism: `free_trace()` removes only the requesting net's contribution to clearance zones. If other nets also contribute clearance to the same cell, it stays `BLOCKED`, preventing accidental damage.

### 4.4 Protected Cells

The engine marks all pin-pad cells as **protected** (`_protected` set). Protected cells:
- Can be force-freed even if permanently blocked (to guarantee pin reachability).
- Are **skipped** by `block_trace()`'s clearance expansion — a trace running near a pin won't block the pin itself.
- Are still passable by the A\* pathfinder — traces can walk through pin pads.

### 4.5 Raised-Floor Blocking

For enclosures with non-flat bottoms, `block_raised_floor()` permanently blocks any cell where the blended bottom height ≥ `FLOOR_MM − 0.1 mm`. This prevents routing traces through areas where the shell base rises above the conductive-ink layer.

### 4.6 Trace Commit / Rip-Up

**`block_trace(path, net_id)`:**
1. Mark every cell on the path as `TRACE_PATH` and record ownership.
2. For every cell within `clearance_cells` radius of the path, mark as `BLOCKED` and record clearance ownership. Protected cells are skipped.

**`free_trace(path, net_id)`:**
1. Path cells → `FREE`, remove from `_trace_owner`.
2. Clearance cells: remove `net_id` from `_clearance_owner`. Only free the cell if no other net still claims it.

---

## 5. A\* Pathfinding (`pathfinder.py`)

### 5.1 Point-to-Point: `find_path()`

Standard A\* search on the Manhattan grid (4-directional: up/down/left/right).

**Cost model per step:**

$$\text{cost} = 1 + \text{turn\_penalty} \cdot \mathbb{1}[\text{direction changed}] + \text{cross\_extra} + \text{cost\_map}[cell]$$

- **Turn penalty** (default 5): heavily discourages unnecessary turns, producing straighter traces.
- **Crossing cost** (default 0; set to 50 for rip-up mode): allows walking through `TRACE_PATH`/`BLOCKED` cells at a steep price. Used only when the normal route fails and the rip-up heuristic fires.
- **Cost map** (optional dict): per-cell floating-point surcharges from the negotiated congestion algorithm.

**Heuristic:** Manhattan distance to the sink — admissible and consistent for a grid with unit movement cost.

**L-Route Fast Path:** Before falling into the full A\* loop, `find_path()` attempts a simple one-bend L-shaped route (horizontal-first then vertical-first). If the L-route is completely unobstructed, it returns immediately. This shortcut handles the vast majority of simple two-pin routes in O(path_length) instead of O(cells·log(cells)).

**Traversability rules:**
- `FREE` → always passable (cost 1).
- `BLOCKED` / `TRACE_PATH` → passable only if `crossing_cost > 0` (rip-up mode), at extra cost.
- `PERMANENTLY_BLOCKED` → never passable.
- Source and sink cells are always passable even if `BLOCKED`.

### 5.2 Point-to-Tree: `find_path_to_tree()`

Multi-source A\* for Steiner tree construction. Can accept either a single source point or a **set** of candidate source cells.

The heuristic is the **minimum Manhattan distance to any cell in the target tree**:

$$h(x,y) = \min_{(t_x, t_y) \in \text{tree}} \left(|x - t_x| + |y - t_y|\right)$$

The search terminates as soon as it expands any cell that belongs to the tree, then reconstructs the path back to the nearest source. This lets multi-pin nets grow incrementally: each new pin connects to the nearest point on the already-routed tree, naturally producing Steiner-like topologies.

---

## 6. Pin Resolution & Dynamic Allocation (`pins.py`)

### 6.1 Pin Reference Types

Net specs refer to pins as `"instance_id:pin_or_group"`. The resolver classifies each reference:

| Type | Example | Behaviour |
|------|---------|-----------|
| **Direct pin** | `"bat_1:V+"` | Fixed physical pin. Position looked up directly. |
| **Fixed-net group** | `"btn_1:A"` | Pin group with `fixed_net=True`. Expanded to all physical pins in the group; any can serve. |
| **Allocatable group** | `"mcu_1:gpio"` | Dynamic. The router picks the best specific pin from the pool at route time. |

### 6.2 Pool Allocation Strategy

For allocatable groups (e.g. MCU GPIO), `allocate_best_pin()` picks the pin **closest to the target position** (Euclidean distance). The target is computed as:
1. The centroid of all already-resolved pads in the same net.
2. Falling back to the grid centre if no pads are resolved yet.

Once allocated, the pin is **removed from the pool** — it cannot be reused by another net. Pin assignments are recorded in `pin_assignments` and persisted so the negotiation and retry stages use consistent allocations.

### 6.3 Coordinate Transformation

Pin positions stored in the catalog are component-local. `pin_world_xy()` applies the standard 2-D rotation:

$$\begin{pmatrix} w_x \\ w_y \end{pmatrix} = \begin{pmatrix} c_x \\ c_y \end{pmatrix} + \begin{pmatrix} \cos\theta & -\sin\theta \\ \sin\theta & \cos\theta \end{pmatrix} \begin{pmatrix} p_x \\ p_y \end{pmatrix}$$

where $(c_x, c_y)$ is the component centre, $(p_x, p_y)$ the local pin offset, and $\theta$ the rotation in radians.

---

## 7. Master Algorithm (`engine.py`)

The routing engine executes in **6 phases**:

### Phase 1 — Grid Setup & Component Blocking

1. Build `RoutingGrid` from the outline polygon.
2. `block_raised_floor()`: permanently block cells above the trace layer.
3. `_block_components()` — a 4-pass sequence:
   - **Pass 1**: Block footprint + keepout margin for every `blocks_routing=True` component (`PERMANENTLY_BLOCKED`).
   - **Pass 2**: Force-free and protect all pin cells with a `pad_radius` neighbourhood (overrides body blocks).
   - **Pass 3**: Re-block the exact footprint interior (without keepout) so traces can't route through component bodies.
   - **Pass 4**: Re-free the immediate 1-cell ring around pins of routing-blocking components (in case pass 3 re-blocked them).

This dance ensures that: (a) component bodies are impenetrable, (b) keepout zones maintain clearance, but (c) every pin pad is reachable by the pathfinder.

### Phase 2 — Voronoi Pin Proximity Map

Pre-computes a **Voronoi territory** around every pin:
- For each pin, scan a square region of radius `pin_clearance_cells`.
- For every cell in range, record the nearest pin (by squared Euclidean distance).
- Result: `pin_voronoi[flat_index] → "instance_id:pin_id"`.

When routing a net, `_block_voronoi()` temporarily blocks cells whose nearest pin belongs to a **foreign** net. This creates natural corridors: traces can approach their own pins freely but are fenced away from unrelated pins. The blocking is reversed with `_unblock_voronoi()` after each routing attempt.

### Phase 3 — Net Pin Reference Parsing

For each net:
- Parse all `"instance:pin_or_group"` references via `resolve_pin_ref()`.
- Expand `fixed_net` group references into their individual physical pins (e.g. button group "A" → physical pins "1" and "2").
- Store as `_PinRef` objects, classified as direct or group.

### Phase 4 — Isolation Routing (Path-Length Estimation)

Before any real routing, each net is routed **in isolation** on a clean grid (no other traces present). The resulting path length is stored as `iso_lengths[net_id]`.

The initial net ordering is sorted by:
1. **Pin count** (descending) — most constrained nets first.
2. **Isolation path length** (descending) — longest routes first (hardest to fit).

This heuristic gives complex, space-hungry nets priority before simpler ones fill up the grid.

### Phase 5a — Negotiated Congestion Routing (Primary Strategy)

An implementation of the **PathFinder algorithm** (McMurchie & Ebeling, 1995):

```
history_cost = {}          # accumulated per-cell penalty
cell_usage = {}            # current number of nets using each cell

for iteration in 1..30:
    for each net (shuffled after iteration 1):
        1. Remove this net's old cell usage counts
        2. Build cost_map[cell] =
             cell_usage[cell] × PRESENT_FACTOR(20)
           + history_cost[cell] × HISTORY_FACTOR(2)
        3. Route on the clean grid using A* with cost_map
           (no physical blocking — only cost penalties)
        4. Record new cell usage

    Measure congestion = cells with >1 user

    if congestion == 0 and all nets routed:
        → converged, return paths

    Update history: for each congested cell,
        history_cost[cell] += (user_count − 1)

    Stall detection: if no improvement for 8 iterations, abort
```

**Key insight:** The grid is never physically blocked during negotiation. All nets route simultaneously on a clean grid, guided only by the cost map. Cells that multiple nets compete for accumulate history cost, progressively pushing conflicting traces onto different paths until overlaps disappear.

After negotiation converges (or nearly converges with ≤5 remaining overlaps), the paths must be **committed** to the physical grid. Because clearance zones are real, the commit order matters — committing net A may block net B's path. The engine tries **7 different commit orderings:**

1. Largest traces first (most cells → hardest to reroute later).
2. Most pins first (most constrained).
3. Smallest traces first.
4–7. Random shuffles.

For each ordering:
- Commit traces that fit without conflict.
- Defer traces that hit already-blocked cells.
- Reroute deferred traces on the partially-filled grid using normal A\*.
- If reroute fails, try the crossing rip-up mechanism.
- Track the result with fewest failures.

If any ordering achieves zero failures, accept immediately.

### Phase 5b — Retry Loop (Fallback Strategy)

If negotiation fails or leaves failures, the engine falls back to a greedy retry approach:

```
for attempt in 1..31:
    Clear the grid (rip up all traces from previous attempt)
    Build fresh pin pools
    
    for each net in current ordering:
        1. Resolve pads (allocate dynamic pins)
        2. Route via _route_single_net() (A* with Voronoi blocking)
        3. If OK: commit trace + clearance to grid
        4. If FAIL: try _try_crossing_ripup()
        5. If still FAIL: record as failed
    
    If all nets routed: keep and stop
    Otherwise: keep if best so far, perturb ordering for next attempt
```

**Ordering perturbation** (`_perturb_ordering()`):
- Early attempts: promote failed nets by `attempt+1` positions towards the front (they failed because earlier nets used their space).
- Later attempts: random shuffle with failed nets biased towards the first half.
- Duplicate orderings are skipped via a `tried_orderings` set.

### Phase 5c — Crossing Rip-Up (`_try_crossing_ripup()`)

When normal A\* fails, the engine attempts a more aggressive strategy:

1. **Route with crossing cost**: Run A\* allowing the path to walk through existing traces at a penalty of 50 per cell. The path will cross other nets but finds the cheapest crossing point.
2. **Identify crossed nets**: Check grid ownership for cells on the new path.
3. **Rip up crossed nets**: Free their traces from the grid.
4. **Commit the new trace**.
5. **Reroute ripped nets**: Each crossed net tries to route on the updated grid.
6. **Accept or revert**: If all ripped nets successfully reroute, the rip-up succeeds. If any fails, revert everything (restore original traces for all affected nets).

This is a **local search** — it can untangle situations where one net's placement prevents another, but the alternative placement works for both.

### Phase 6 — Output Conversion

Grid paths (cell coordinates) are converted to world-mm traces:

1. **Path simplification** (`_simplify_path()`): Remove collinear intermediate points. A straight run of cells in the same direction collapses to just the start and end waypoints.
2. **Coordinate conversion**: `grid_to_world()` maps cell indices to mm at cell centres.
3. **Outline clamping**: Any waypoint that falls outside the outline polygon (due to grid quantisation) is projected to the nearest point on the polygon boundary.
4. **Trace emission**: Each path segment becomes a `Trace(net_id, path)` object.

---

## 8. Multi-Pin Routing (Steiner Tree Decomposition)

Nets with more than 2 pins use MST-guided Steiner tree construction:

### 8.1 MST Computation (`_compute_mst()`)

Kruskal's algorithm on all-pairs Manhattan distances:
1. Compute $\binom{n}{2}$ edges with Manhattan distance $d = |x_1 - x_2| + |y_1 - y_2|$.
2. Sort by distance.
3. Use union-find with path compression to greedily merge.
4. Stop after $n - 1$ edges.

### 8.2 Incremental Tree Growth (`_route_multi_pin()`)

For each MST edge $(p_a, p_b)$:
1. Skip if already in the same connected component.
2. Get the cell-set trees for both components.
3. Route the **smaller tree** to the **larger tree** using `find_path_to_tree()`.
4. Merge both trees and the new path into one component.

The result is a Steiner-like tree: paths may share intermediate cells, and new connections naturally attach to the nearest point on the existing tree rather than forcing point-to-point connections. Union-find tracks component membership.

---

## 9. Bitmap Generation (`bitmap.py`)

After routing, traces are rasterised to a text bitmap for the silver-ink printer:

1. Each trace path is translated from model-local to bed coordinates via `model_to_bed` offset.
2. Bed coordinates are converted to bitmap coordinates via `SweepGrid.bed_to_bitmap()`.
3. Each Manhattan segment is rasterised into bitmap cells:
   - Vertical segments: expand by `±half_trace_width` in X.
   - Horizontal segments: expand by `±half_trace_width` in Y.
4. Output is a list of text lines (one per Y position, highest Y first), each character being `'1'` (ink) or `'0'` (no ink).

Pixel size = nozzle pitch (from `SweepGrid`). This ensures 1:1 alignment with the physical encoder-driven sweep lanes.

---

## 10. Debug Visualization (`debug.py`)

`build_debug_grids()` produces a self-contained ownership overlay:

1. Reconstructs a fresh `RoutingGrid` from the placement data.
2. Blocks components and commits all routed traces.
3. Scans `_trace_owner` and `_clearance_owner` to build a per-cell palette index.
4. Emits a single dict with the bitmap as base64-encoded bytes, dimensions, origin, resolution, and a net→colour palette.

This is completely independent of the live routing grid — it can be called after routing is complete to produce web-viewable debug output.

---

## 11. Test Coverage (`tests/test_router.py`)

| Test Class | Tests | What's Verified |
|------------|-------|-----------------|
| `TestRoutingGrid` | 7 | Grid dimensions, interior/edge/outside cell states, block/free, permanent blocking, coordinate round-trip |
| `TestPathfinder` | 5 | Straight path, L-shaped path, obstacle avoidance, no-path condition, point-to-tree |
| `TestFlashlightRouting` | 7 | All 4 nets routed, correct net IDs, Manhattan-only segments, traces inside outline, no GPIO assignments, `.ok` property, **no trace crossings** |
| `TestRoutingSerialization` | 2 | JSON round-trip, dict structure |

The flashlight fixture (45×120 mm, 4 components, 4 two-pin nets) is the primary integration test.

---

## 12. Complexity & Performance Characteristics

| Aspect | Complexity | Notes |
|--------|-----------|-------|
| Grid construction | $O(W \times H)$ | Point-in-polygon for every cell |
| A\* point-to-point | $O(WH \log(WH))$ worst case | L-route shortcut handles trivial cases in $O(L)$ |
| A\* point-to-tree | $O(WH \times T \log(WH))$ | Heuristic scans all $T$ tree cells per expansion |
| Voronoi map | $O(P \times r^2)$ | $P$ = pin count, $r$ = clearance radius |
| Negotiation | $O(I \times N \times A*)$ | $I$ ≤ 30 iterations, $N$ = net count |
| Retry loop | $O(R \times N \times A*)$ | $R$ ≤ 31 attempts |
| MST | $O(n^2 \log n)$ | $n$ = pins per net (typically 2–5) |
| Bitmap rasterization | $O(T \times L)$ | $T$ = traces, $L$ = average trace length |

For a typical small board (60×40 mm, 10 nets), the grid is ~120×80 = 9600 cells. A\* runs in milliseconds. The full negotiation + retry loop finishes in under a second.

---

## 13. Failure Modes & Mitigations

| Failure | Cause | Mitigation |
|---------|-------|------------|
| **No path exists** | Component bodies + clearance zones partition the grid | Retry with different net ordering; rip-up mechanism |
| **Negotiation stall** | Two+ nets have fundamentally conflicting optimal paths | Stall detection after 8 non-improving iterations; fallback to retry loop |
| **Pin pool exhaustion** | Too many nets need the same MCU pin group | Router reports the net as failed; placer should ensure sufficient pin supply |
| **Raised-floor blockage** | Enclosure bottom rises above trace layer | `block_raised_floor()` prevents routing through these zones; may reduce routable area |
| **Grid quantisation** | 0.5 mm resolution can misplace closely-spaced pins | `force_free_cell` + `protect_cell` guarantee pin reachability |

---

## 14. Data Flow Summary

```
FullPlacement + CatalogResult
        │
        ▼
┌───────────────────┐
│  Build Grid       │  outline → inset → cell states
│  Block Components │  bodies → PERM_BLOCKED, pins → protected/free
│  Raised Floor     │  z-field → PERM_BLOCKED
│  Voronoi Map      │  pin proximity → foreign-pin blocking
└───────┬───────────┘
        │
        ▼
┌───────────────────┐
│  Isolation Routing │  each net alone → path length estimate
│  Net Ordering      │  sort by (pin_count ↓, path_length ↓)
└───────┬───────────┘
        │
        ▼
┌───────────────────────────────────────────────┐
│  Strategy 1: Negotiated Congestion (PathFinder)│
│  ─ up to 30 iterations                         │
│  ─ cost_map: present + history penalties        │
│  ─ NO physical grid blocking                    │
│  ─ converges when 0 cell conflicts              │
│  ─ 7 commit orderings tried                     │
└───────┬───────────────────────────────────────┘
        │ (fallback if negotiation fails/leaves failures)
        ▼
┌───────────────────────────────────────────────┐
│  Strategy 2: Retry Loop (greedy + rip-up)      │
│  ─ up to 31 attempts with order perturbation   │
│  ─ per-net A* with Voronoi blocking             │
│  ─ crossing rip-up as last resort               │
│  ─ best result (fewest failures) kept           │
└───────┬───────────────────────────────────────┘
        │
        ▼
┌───────────────────┐
│  Path → Trace     │  simplify, convert to mm, clamp to outline
│  Debug Grids      │  self-contained ownership overlay
│  Bitmap           │  rasterise to nozzle-pitch text for printer
└───────────────────┘
        │
        ▼
   RoutingResult
```

---

## 15. Key Design Decisions & Trade-offs

1. **Manhattan-only routing**: all traces are axis-aligned. Simplifies grid, pathfinding, clearance math, and bitmap rasterisation. The cost is ~41% longer traces than Euclidean, acceptable for this scale.

2. **Dual-strategy routing**: negotiation (global view) + retry loop (local search) complement each other. Negotiation handles congested designs well; the retry loop + rip-up handles edge cases where negotiation stalls.

3. **Voronoi pin fencing**: enforces pin clearance by *temporarily blocking* cells during pathfinding rather than inflating obstacles permanently. This allows different nets to approach the same pin from different directions.

4. **Multi-pass component blocking**: the 4-pass block/free/re-block/re-free dance in `_block_components()` is complex but necessary to satisfy two competing requirements: (a) no traces through component bodies, (b) all pins reachable.

5. **Clearance ownership tracking**: the per-net clearance bookkeeping adds memory overhead but makes rip-up safe — freeing one net never damages another net's clearance zones.

6. **No via/layer support**: this is a single-layer conductive-ink router. There are no vias, no layer pairs, no z-axis routing. Crossings are physically impossible; the rip-up mechanism tries to eliminate them.

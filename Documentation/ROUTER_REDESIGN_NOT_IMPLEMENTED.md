# Router & Placer Redesign — Implementation Plan

## Problem Statement

The current router (~1500 lines across 8 files) uses sequential A\* with iterative rip-up-and-retry. It suffers from:

- **Net ordering sensitivity** — first net wins, later nets are squeezed
- **State management bugs** — snapshots miss `_protected`, Voronoi goes stale after rip-up, 4-pass component blocking contradicts itself
- **Over-engineering** — 6 ownership maps, deep-copy snapshots, recursive rip-up depth limits, circular pin shifts, Voronoi pre-blocking
- **Reactive jumpers** — placed as panic fallback, not deliberate design

The redesign replaces this with **negotiated congestion routing** (PathFinder algorithm) and upgrades the placer with **crossing-aware pin pre-assignment**. Total router target: ~400 lines.

---

## Architecture Overview

```
  FullPlacement (from existing placer)
        │
  ┌─────▼──────┐
  │  Pin        │  Phase 0: Assign flexible pins (Hungarian / greedy)
  │  Assignment │  Output: every net has fixed physical (x, y) endpoints
  └─────┬──────┘
        │
  ┌─────▼──────┐
  │  Grid       │  Build routing grid, block component bodies + foreign pins
  │  Setup      │
  └─────┬──────┘
        │
  ┌─────▼──────┐
  │  Negotiated │  Phase 1: Route all nets (overlaps OK)
  │  Congestion │  Phase 2: Iterate — escalating costs push nets apart
  │  Loop       │  Converges when every cell has ≤ 1 net
  └─────┬──────┘
        │
  ┌─────▼──────┐
  │  Jumper     │  Phase 3: Remaining shared cells → jumper wires
  │  Resolution │
  └─────┬──────┘
        │
  ┌─────▼──────┐
  │  Wire       │  Phase 4: Individually re-route nets to shorten,
  │  Shortening │  accepting only if no new conflicts
  └─────┬──────┘
        │
  RoutingResult (same output contract as today)
```

---

## File Structure

Replace the contents of `src/pipeline/router/`. Keep the same module boundary.

```
router/
  __init__.py          # Public API: route_traces(), re-exports
  models.py            # Keep as-is (Trace, JumperWire, RoutingResult, RouterConfig)
  grid.py              # Simplified routing grid (no ownership maps)
  pathfinder.py        # A* with cost function (replaces current)
  pins.py              # Pin assignment (Hungarian + greedy fallback)
  negotiate.py         # NEW — negotiation loop (core algorithm)
  jumpers.py           # NEW — jumper resolution from remaining conflicts
  shorten.py           # NEW — post-convergence wire length optimization
  bitmap.py            # Keep as-is (rasterization)
  serialization.py     # Keep as-is (JSON I/O)
  debug.py             # Simplify (just cost-map visualization)
```

**Deleted files:** `engine.py`, `solution.py` (replaced by `negotiate.py`, `jumpers.py`, `shorten.py`)

---

## Phase 0: Pin Assignment (`pins.py`)

### Goal

Assign every flexible pin group reference (e.g. `"mcu_1:gpio"`) to a concrete physical pin **before** routing begins. This completely decouples pin allocation from routing.

### Current Problem

The current router allocates pins *during* routing — `allocate_best_pin()` picks the closest available pin to the current routing target. This creates order-dependent assignments and forces the router to manage a `PinPool` with undo semantics.

### New Approach

Pin assignment runs as a standalone pre-pass. The key insight: **the best pin assignment minimizes total net crossing count**, not just individual net lengths.

#### Algorithm

```
1. Collect all flexible assignments needed:
     flexible = [(net_id, instance_id, group_id, partner_positions)]
   where partner_positions = world coordinates of the other pins on this net.

2. Group flexible assignments by (instance_id, group_id):
     E.g., mcu_1:gpio might appear in 5 different nets.
     Each needs a different physical pin from the "gpio" group.

3. For each group, build a cost matrix:
     rows = nets needing a pin from this group
     cols = available physical pins in the group
     cost[i][j] = crossing_penalty(net_i, pin_j, all_other_nets)

   crossing_penalty is:
     - Manhattan distance from pin_j to the net's partner centroid
     - Plus: number of OTHER nets whose straight-line bounding box
       this assignment would cross (weighted × 10)

4. Solve assignment:
     If matrix is small (≤ 8×8): Hungarian algorithm — O(n³), exact
     If larger: greedy nearest-unassigned — O(n²), good enough

5. Record results:
     pin_assignments["mcu_1:gpio"] = "mcu_1:PD2"
     Replace group references in net pin lists with concrete pin IDs
```

#### Crossing Estimation

For two 2-pin nets (A₁→A₂) and (B₁→B₂), they cross if and only if the line segments A₁A₂ and B₁B₂ intersect. For Manhattan routing this is conservative but effective as a heuristic — we only need *relative* costs, not exact crossing counts.

```python
def segments_cross(a1, a2, b1, b2) -> bool:
    """True if the bounding boxes of two segments overlap in a
    way that implies a Manhattan routing conflict."""
    # Bounding-box overlap test on both axes
    if max(a1[0], a2[0]) <= min(b1[0], b2[0]): return False
    if max(b1[0], b2[0]) <= min(a1[0], a2[0]): return False
    if max(a1[1], a2[1]) <= min(b1[1], b2[1]): return False
    if max(b1[1], b2[1]) <= min(a1[1], a2[1]): return False
    # Bounding boxes overlap — conservative crossing
    return True
```

For multi-pin nets, decompose into MST edges and test each pair.

#### Output

A `dict[str, str]` of assignments (same format as today) plus a modified net list where all group references are replaced with physical pin references. Pin world positions are looked up from `PlacedComponent.pin_positions`.

---

## Grid Setup (`grid.py`)

### Goal

Minimal routing grid. No ownership tracking, no clearance maps, no protected sets.

### Data Structure

```python
@dataclass
class Grid:
    cells: np.ndarray          # 2D uint8: FREE=0, COMPONENT=1, PIN=2
    pin_net: dict[int, str]    # flat_index → net_id (which net owns this pin cell)
    width: int
    height: int
    origin_x: float            # world X of cell (0,0) center
    origin_y: float            # world Y of cell (0,0) center
    resolution: float          # mm per cell (0.5)
```

### Cell Types

| Value | Meaning | A\* behavior |
|-------|---------|-------------|
| `FREE (0)` | Routable space | base cost = 1 |
| `COMPONENT (1)` | Component body / keepout / board edge | impassable (cost = ∞) |
| `PIN (2)` | Pin pad cell | passable only by the owning net |

That's it. No `TRACE_PATH`, no `BLOCKED`, no `PERMANENTLY_BLOCKED`. Trace occupation is tracked in the negotiation loop's own data structures, not baked into the grid.

### Construction

```python
def build_grid(placement: FullPlacement, catalog: CatalogResult,
               resolution: float) -> Grid:
    1. Compute bounding box of outline + margin
    2. Allocate cells array (all FREE)
    3. Mark cells outside outline polygon as COMPONENT
    4. For each component:
         - Mark body rect + keepout_margin as COMPONENT
         - Mark pin cells as PIN, record pin_net mapping
    5. Mark cells within edge_clearance of outline as COMPONENT
    Return Grid
```

### Coordinate Transforms

Same as current: `world_to_grid(wx, wy) → (col, row)` and `grid_to_world(col, row) → (wx, wy)`, using cell-center rounding.

---

## Phase 1 & 2: Negotiated Congestion Loop (`negotiate.py`)

This is the core of the new router.

### Data Structures

```python
@dataclass
class NetState:
    net_id: str
    pins: list[tuple[int, int]]      # grid coordinates of all pins
    path: list[list[tuple[int, int]]] # current route segments (list of paths for multi-pin)
    path_cells: set[int]             # flat indices of all cells in current route

nets: dict[str, NetState]
occupancy: np.ndarray    # int16, same shape as grid — count of nets using each cell
history: np.ndarray      # float32, same shape as grid — accumulated congestion history
```

`occupancy[r][c]` = number of nets whose current route passes through cell (r, c). A cell with `occupancy > 1` is a conflict. The entire "ownership tracking" problem vanishes — it's just a counter.

### Cost Function

This is the **entire intelligence** of the router:

```python
def cell_cost(grid, occupancy, history, net_id, col, row, penalty_factor):
    flat = row * grid.width + col
    base = grid.cells[row][col]

    if base == COMPONENT:
        return INFINITY
    if base == PIN and grid.pin_net[flat] != net_id:
        return INFINITY                    # foreign pin — impassable

    cost = 1.0                             # base movement cost

    sharing = occupancy[row][col]
    if sharing > 0:
        # Other nets are here — penalize proportionally
        cost += sharing * (1.0 + history[row][col] * penalty_factor)

    return cost
```

No clearance ownership. No Voronoi. No protected sets. The cost function *is* the router's brain.

### Clearance Enforcement

Clearance is enforced softly through the cost function. When computing `cell_cost`, we also check neighboring cells within `ceil(trace_clearance_mm / resolution)` distance:

```python
clearance_cells = ceil(trace_clearance_mm / grid_resolution_mm)  # = ceil(1.0/0.5) = 2

for each neighbor within clearance_cells radius of (col, row):
    if occupancy[nr][nc] > 0 and the occupant is not net_id:
        cost += proximity_penalty          # e.g. 5.0 per adjacent foreign trace cell
```

This means traces *prefer* to stay apart by the clearance distance, but can squeeze closer when forced. The negotiation loop will naturally push them apart over iterations.

**Hard clearance** is enforced in post-processing: after convergence, any trace-to-trace gap smaller than `trace_clearance_mm` is flagged and the offending net is re-routed with stricter costs. This should rarely trigger if the soft penalties are calibrated well.

### The Negotiation Loop

```python
def negotiate(grid: Grid, net_states: dict[str, NetState],
              config: RouterConfig) -> dict[str, NetState]:

    occupancy = np.zeros((grid.height, grid.width), dtype=np.int16)
    history   = np.zeros((grid.height, grid.width), dtype=np.float32)
    penalty_factor = 1.0

    # ── Phase 1: Initial route (ignore congestion) ──────────────
    for net in net_states.values():
        route_net(grid, net, occupancy, history, penalty_factor=0.0)
        add_to_occupancy(occupancy, net)

    # ── Phase 2: Negotiation iterations ─────────────────────────
    for iteration in range(config.max_negotiate_iterations):

        # Sort nets: most conflicts first
        order = sorted(net_states.values(),
                       key=lambda n: count_conflicts(n, occupancy),
                       reverse=True)

        for net in order:
            remove_from_occupancy(occupancy, net)
            route_net(grid, net, occupancy, history, penalty_factor)
            add_to_occupancy(occupancy, net)

        # Update history for congested cells
        conflict_cells = np.where(occupancy > 1)
        history[conflict_cells] += 1.0

        penalty_factor *= 1.5              # exponential backoff

        total_conflicts = int(np.sum(occupancy > 1))
        if total_conflicts == 0:
            break                          # converged — all nets separated

    return net_states
```

### Multi-Pin Net Routing (Steiner Tree)

For nets with 3+ pins, we build a Steiner tree using MST-guided sequential connection:

```python
def route_net(grid, net, occupancy, history, penalty_factor):
    net.path.clear()
    net.path_cells.clear()

    if len(net.pins) == 2:
        path = astar(grid, net.pins[0], net.pins[1],
                      occupancy, history, net.net_id, penalty_factor)
        net.path = [path]
        net.path_cells = {flat(c) for c in path}
        return

    # Multi-pin: MST → Steiner
    # 1. Compute MST of pin positions (Manhattan distance)
    mst_edges = kruskal_mst(net.pins)

    # 2. Connect MST edges, allowing connection to existing tree
    connected = set()
    tree_cells = set()
    for (pa, pb) in mst_edges:
        source = pa if pa not in connected else find_nearest_tree_cell(pa, tree_cells)
        path = astar(grid, source, pb, occupancy, history, net.net_id, penalty_factor)
        net.path.append(path)
        tree_cells.update(path)
        connected.add(pa)
        connected.add(pb)

    net.path_cells = {flat(c) for c in tree_cells}
```

### A\* Pathfinder (`pathfinder.py`)

Simplified A\* that takes the cost function directly:

```python
def astar(grid, start, goal, occupancy, history, net_id, penalty_factor,
          turn_penalty=5) -> list[tuple[int, int]]:
    """Manhattan A* with negotiated congestion costs."""

    open_heap = [(0, start, None)]   # (f_cost, position, prev_direction)
    g_cost = {start: 0.0}
    came_from = {start: None}

    DIRS = [(1,0), (-1,0), (0,1), (0,-1)]

    while open_heap:
        f, pos, prev_dir = heappop(open_heap)

        if pos == goal:
            return reconstruct_path(came_from, pos)

        for d in DIRS:
            npos = (pos[0] + d[0], pos[1] + d[1])
            if not grid.in_bounds(npos):
                continue

            move = cell_cost(grid, occupancy, history, net_id,
                             npos[0], npos[1], penalty_factor)
            if move >= INFINITY:
                continue

            if prev_dir is not None and d != prev_dir:
                move += turn_penalty

            new_g = g_cost[pos] + move
            if new_g < g_cost.get(npos, INFINITY):
                g_cost[npos] = new_g
                h = manhattan(npos, goal)
                heappush(open_heap, (new_g + h, npos, d))
                came_from[npos] = pos

    return []    # no path — will be handled by jumper resolution
```

### Convergence Properties

The PathFinder algorithm is **guaranteed to converge** for feasible problems because:

1. History costs monotonically increase for congested cells
2. The exponential penalty factor makes congested cells arbitrarily expensive
3. Eventually, every net prefers any alternative over a congested cell
4. If no alternative exists (genuine planarity conflict), Phase 3 resolves it with jumpers

Typical convergence for 10–20 net boards: 5–15 iterations (vs. 60 in current system).

---

## Phase 3: Jumper Resolution (`jumpers.py`)

### When Jumpers Are Needed

After the negotiation loop converges (or hits `max_negotiate_iterations`), any cell with `occupancy > 1` represents a **genuine planarity conflict** — two nets that cannot be separated with Manhattan routing.

### Algorithm

```python
def resolve_jumpers(grid, net_states, occupancy) -> list[JumperWire]:
    jumpers = []

    while True:
        # Find worst remaining conflict
        conflict_cells = find_conflict_cells(occupancy)
        if not conflict_cells:
            break

        # Group contiguous conflict cells into segments
        segments = extract_conflict_segments(conflict_cells, occupancy)

        for segment in segments:
            # Identify the two nets sharing this segment
            net_a, net_b = get_conflicting_nets(segment, net_states)

            # Choose which net to jump: prefer shorter crossing segment
            jump_net = net_a if segment_length(net_a, segment) <= \
                               segment_length(net_b, segment) else net_b

            # Create jumper spanning the conflict region
            jumper = create_jumper(jump_net, segment, grid)
            jumpers.append(jumper)

            # Remove jumped segment from the net's route
            reroute_around_jumper(grid, net_states[jump_net.net_id],
                                  jumper, occupancy)

    return jumpers
```

### Jumper Endpoint Placement

```python
def create_jumper(net, conflict_segment, grid) -> JumperWire:
    # Find entry/exit points of the conflict region along the net's path
    entry_cell, exit_cell = find_segment_endpoints(net, conflict_segment)

    # Extend endpoints outward by 1 cell to ensure clean separation
    entry_world = grid.grid_to_world(*entry_cell)
    exit_world  = grid.grid_to_world(*exit_cell)

    start = JumperEndpoint(x=entry_world[0], y=entry_world[1])
    end   = JumperEndpoint(x=exit_world[0],  y=exit_world[1])

    # If either endpoint is near a component pin, offset the solder point
    offset_near_pins(start, net, grid)
    offset_near_pins(end, net, grid)

    length = abs(start.x - end.x) + abs(start.y - end.y)
    return JumperWire(net_id=net.net_id, start=start, end=end,
                      length_mm=length)
```

### Jumper-to-Jumper Clearance

Jumpers arch through the air, so they don't conflict with traces. But they conflict with:
- **Other jumpers** — two wires can't occupy the same airspace
- **Component bodies** — wire can't pass through a component

Track jumper paths as line segments in world space. New jumpers must clear existing jumpers by ≥ 1.5 mm and not cross component bounding boxes.

---

## Phase 4: Wire Shortening (`shorten.py`)

### Goal

After convergence, traces may be longer than necessary — the negotiation pushed them into detours to avoid congestion. Now that every net has exclusive territory, we can re-route each net individually to find its shortest path.

### Algorithm

```python
def shorten_traces(grid, net_states, occupancy, max_rounds=3):
    for round in range(max_rounds):
        improved = False

        # Process longest nets first (most potential for improvement)
        order = sorted(net_states.values(),
                       key=lambda n: len(n.path_cells), reverse=True)

        for net in order:
            old_length = len(net.path_cells)
            old_path = net.path[:]
            old_cells = net.path_cells.copy()

            remove_from_occupancy(occupancy, net)

            # Re-route with only hard obstacles (other nets' traces)
            route_net(grid, net, occupancy, history=None, penalty_factor=0.0)

            new_length = len(net.path_cells)

            # Check: no new conflicts and not longer
            conflicts = any(occupancy[r][c] > 0
                           for (c, r) in net.path_cells_coords())
            if conflicts or new_length > old_length:
                # Reject — restore old route
                net.path = old_path
                net.path_cells = old_cells

            add_to_occupancy(occupancy, net)

            if new_length < old_length:
                improved = True

        if not improved:
            break
```

---

## Clearance Validation Pass

After all routing is complete, run a final validation to ensure hard clearance constraints are met. This catches any edge cases the soft-cost approach may have missed:

```python
def validate_clearances(grid, net_states, trace_clearance_mm, resolution):
    clearance_cells = ceil(trace_clearance_mm / resolution)
    violations = []

    for net in net_states.values():
        for (c, r) in net.path_cells_as_coords():
            for dc in range(-clearance_cells, clearance_cells + 1):
                for dr in range(-clearance_cells, clearance_cells + 1):
                    nc, nr = c + dc, r + dr
                    if (nc, nr) in some_other_net.path_cells:
                        violations.append((net.net_id, other.net_id, (c, r)))

    return violations  # empty = all clearances satisfied
```

If violations exist, re-route the offending nets with elevated clearance costs, one at a time. This is a simple fixup, not a complex loop.

---

## Integration with Existing Pipeline

### Input Contract (unchanged)

```python
def route_traces(
    placement: FullPlacement,
    catalog: CatalogResult,
    *,
    config: RouterConfig | None = None,
) -> RoutingResult:
```

### Output Contract (unchanged)

```python
RoutingResult(
    traces: list[Trace],            # net_id + path (mm waypoints)
    pin_assignments: dict[str, str], # "mcu_1:gpio" → "mcu_1:PD2"
    failed_nets: list[str],         # always empty
    jumpers: list[JumperWire],      # planarity conflicts
)
```

### Internal Flow

```python
def route_traces(placement, catalog, *, config=None):
    cfg = config or RouterConfig()

    # Phase 0: Pin assignment
    pin_assignments, resolved_nets = assign_pins(placement, catalog)

    # Grid setup
    grid = build_grid(placement, catalog, cfg.grid_resolution_mm)

    # Build net states from resolved pins
    net_states = build_net_states(resolved_nets, grid)

    # Phase 1+2: Negotiated congestion
    negotiate(grid, net_states, cfg)

    # Phase 3: Jumper resolution
    jumpers = resolve_jumpers(grid, net_states, occupancy)

    # Phase 4: Wire shortening
    shorten_traces(grid, net_states, occupancy)

    # Clearance validation
    violations = validate_clearances(grid, net_states, cfg)
    if violations:
        fix_clearance_violations(grid, net_states, violations, cfg)

    # Convert grid paths → world-mm traces
    traces = [
        Trace(net_id=ns.net_id,
              path=simplify_path([grid.grid_to_world(*c) for seg in ns.path for c in seg]))
        for ns in net_states.values()
    ]

    return RoutingResult(
        traces=traces,
        pin_assignments=pin_assignments,
        failed_nets=[],
        jumpers=jumpers,
    )
```

### Downstream Compatibility

- **`bitmap.py`** — unchanged. Reads `Trace.path` (list of mm waypoints) → rasterizes.
- **`serialization.py`** — unchanged. Serializes `RoutingResult` fields.
- **`debug.py`** — simplified. Can visualize `occupancy` and `history` arrays directly as heatmaps instead of reconstructing ownership maps.
- **SCAD generator** — unchanged. Reads `Trace.path` and `JumperWire` endpoints from serialized JSON.

---

## RouterConfig Changes

```python
@dataclass
class RouterConfig:
    # Physical rules (from TRACE_RULES — unchanged)
    grid_resolution_mm: float = TRACE_RULES.grid_resolution_mm
    trace_width_mm: float = TRACE_RULES.trace_width_mm
    trace_clearance_mm: float = TRACE_RULES.trace_clearance_mm
    pin_clearance_mm: float = TRACE_RULES.pin_clearance_mm
    edge_clearance_mm: float = TRACE_RULES.edge_clearance_mm

    # Negotiation parameters (replace old router-only knobs)
    turn_penalty: int = 5
    max_negotiate_iterations: int = 50
    penalty_growth_factor: float = 1.5
    proximity_penalty: float = 5.0
    shorten_rounds: int = 3
```

Removed: `crossing_cost` (replaced by negotiated costs), `max_improve_iterations` / `stall_limit` (replaced by `max_negotiate_iterations`).

---

## Complexity Comparison

| Aspect | Current Router | New Router |
|--------|---------------|------------|
| Files | 8 (engine, solution, grid, pathfinder, pins, + support) | 7 (negotiate, jumpers, shorten, grid, pathfinder, pins, + support) |
| Core logic lines | ~1500 | ~400 estimated |
| Grid cell states | 4 (FREE, BLOCKED, PERMANENTLY_BLOCKED, TRACE_PATH) | 3 (FREE, COMPONENT, PIN) |
| Ownership tracking | `_trace_owner`, `_clearance_owner`, `_protected`, Voronoi | `occupancy` counter array (numpy) |
| Snapshot system | Deep copy of cells + 3 ownership maps | None needed — negotiation is monotonic |
| Pin allocation | During routing (order-dependent, PinPool with undo) | Before routing (Hungarian, standalone) |
| Jumper creation | 4-level fallback cascade | Post-convergence analysis of genuine conflicts |
| Wire optimization | Rip-up-retry within improvement loop | Separate shortening pass (risk-free) |

---

## Placer Improvements

The existing placer is solid. We make two focused additions:

### 1. Crossing Count in Annealing Cost

Add a **crossing estimation** term to the simulated annealing cost function. For each pair of nets, test if their bounding-box segments intersect. Sum crossings × weight.

```python
def crossing_count(net_positions: dict[str, list[tuple[float, float]]]) -> int:
    """Count bounding-box crossings among all net pairs."""
    segments = []
    for net_id, pins in net_positions.items():
        # MST edges for multi-pin nets
        for (a, b) in mst_edges(pins):
            segments.append((net_id, a, b))

    crossings = 0
    for i, (id_a, a1, a2) in enumerate(segments):
        for (id_b, b1, b2) in segments[i+1:]:
            if id_a != id_b and bbox_overlap(a1, a2, b1, b2):
                crossings += 1
    return crossings
```

Add to annealing cost with weight ~50.0. This encourages the placer to find arrangements where nets don't cross — reducing jumper need before routing even starts.

### 2. Pin Pre-Assignment Feedback

After simulated annealing, run the pin assignment algorithm (Phase 0 of routing) and report the crossing count back. If the SA result has more crossings than a threshold, do another SA round with crossing info baked in. This creates a lightweight placement-routing feedback loop without running the full router.

---

## Testing Strategy

### Unit Tests

1. **Grid construction** — outline masking, component blocking, pin marking
2. **A\* pathfinder** — shortest path on empty grid, path around obstacle, cost function correctness
3. **Pin assignment** — Hungarian produces minimum-cost matching, handles groups correctly
4. **Negotiation convergence** — two crossing nets separate within N iterations
5. **Jumper resolution** — unavoidable crossing produces exactly one jumper
6. **Wire shortening** — detoured path gets straightened when obstacle removed
7. **Clearance validation** — catches violations, passes clean routes

### Integration Tests

1. **Simple circuit** (2 components, 2 nets, no crossings) → zero jumpers
2. **Crossing circuit** (4 pins in crossing pattern) → exactly 1 jumper
3. **Dense circuit** (MCU + 5 components) → routes complete, clearances valid
4. **Regression**: run on existing test fixtures, compare jumper count (should be ≤ current)

### Validation

The `RoutingResult` output must satisfy:
- `failed_nets` is empty
- No two traces share grid cells (after clearance enforcement)
- No trace passes through a `COMPONENT` cell
- No trace passes through a foreign `PIN` cell
- All jumper endpoints are within the board outline
- All jumper wires clear component bodies
- All `pin_assignments` map to valid physical pins

# Router Fix Plan

## The Real Problem
The placer and the router are both fine individually. The problem is a **chicken-and-egg**: you can't know if a placement is routable without routing it, and you can't route without a placement. This is not our bug — it's a fundamental problem that the entire chip/PCB design industry has been solving for 40+ years. The key insight from that industry:

> **You don't need to predict the exact routing solution. You just need to predict whether a solution EXISTS. And that's much cheaper.**

---

## How the EDA industry solves this: Global Routing

The standard answer is to split routing into two phases:

### Phase 1 — Global Routing (fast, approximate, runs DURING placement)
Divide the board into a coarse grid of rectangular **regions** (e.g. 5×5 mm tiles). For each net, find a path through regions (not individual cells — just "this net passes through region (2,3) then (2,4) then (3,4)"). Each region has a **capacity** — how many traces can physically fit through it, based on its width and the trace pitch.

This is basically our router but on a grid that's 10× coarser, so it runs 100× faster. No clearance zones, no pin Voronoi, no trace blocking — just counting.

If any region has more nets assigned to it than its capacity, the placement has a **congestion** problem there, and routing will probably fail.

### Phase 2 — Detailed Routing (our existing router, unchanged)
Run after placement is finalised. Takes the global routing solution as a hint for net ordering, then does the real A* pathfinding on the fine grid. This is what our `router/` package already does.

**The magic:** Global routing is so cheap that you can run it **during placement scoring**. When the placer evaluates a candidate position for a component, it can ask: "if I put component X here, does global routing still have capacity for all its nets?" If not, reject the candidate.

---

## What Global Routing looks like concretely for us

### The coarse grid
- Overlay a grid of tiles on the board outline. Tile size = e.g. 3–5 mm (vs our 0.5 mm routing grid). For a 60×40 mm board, that's maybe 12×8 = 96 tiles instead of 120×80 = 9600 cells.
- Each tile has a **capacity**: how many traces can pass through it horizontally and vertically. Capacity = tile_width / (trace_width + trace_clearance). For a 5 mm tile with 0.5 mm trace and 1.0 mm clearance: capacity = 5 / 1.5 ≈ 3 traces per direction.
- Mark tiles occupied by component bodies as having reduced or zero capacity.

### Global route assignment
For each net, find the shortest path through tiles from source pin's tile to sink pin's tile (simple BFS — 96 tiles, basically free). Record that this net uses those tiles. Increment each tile's **demand**.

### Congestion check
If demand > capacity for any tile, there's a problem. The **congestion ratio** (demand / capacity) tells you how bad it is.

### Integration with the placer
When `score_candidate()` evaluates a position:
1. Temporarily place the component on the coarse grid (block its tiles).
2. For each net edge to an already-placed partner, find the coarse path.
3. Check if the path's tiles are over-capacity.
4. If congested → penalty. If clear → no penalty.

This replaces the L-corridor check from the previous plan with something much more accurate — it considers ALL nets' cumulative demand, not just one net in isolation.

---

## The Algorithm: Incremental Global Routing in the Placement Loop

```
1. Build coarse grid (tiles) from outline polygon
2. Set tile capacities based on tile dimensions and trace pitch
3. Create empty demand map: tile → count of nets passing through

For each component to place (in placement order):
   For each candidate position (cx, cy, rotation):
      a. Temporarily block tiles covered by this component's body
      b. For each net edge to an already-placed component:
         - Find coarse path from my pin's tile to partner pin's tile (BFS)
         - Check if any tile on the path has demand ≥ capacity
      c. Congestion score = sum of max(0, demand - capacity) along all paths
      d. Add congestion penalty to candidate score: score -= congestion * WEIGHT
      e. Unblock temporary tiles
   
   Place component at best-scoring candidate
   For each net edge now fully placed (both endpoints down):
      - Run global route (BFS on coarse grid)
      - Increment demand for each tile on the path
      - Record this net's global route
```

### Why this works
- Each component is placed with knowledge of how congested the board already is.
- Components that would create bottlenecks get steered away from congested areas.
- The congestion map builds incrementally — no need to redo global routing from scratch.
- Cost per candidate: a few BFS searches on a ~100-tile grid. Microseconds.

### Why this is different from "place then route then retry"
- No retry loop. The placement is informed from the start.
- No full routing during placement. Just coarse tile counting.
- The existing fine-grid router runs once at the end and succeeds because the placement doesn't have congestion bottlenecks.

---

## Implementation Plan

### Step 1 — Coarse congestion grid (new file: `placer/congestion.py`)
~60 lines. A simple class:
```
class CongestionGrid:
    - __init__(outline_poly, tile_size_mm, trace_pitch_mm)
    - block_component(cx, cy, hw, hh)        → blocks tiles, returns handle
    - unblock_component(handle)                → restores tiles
    - route_net_coarse(tile_a, tile_b)         → BFS path through tiles
    - commit_net(path)                         → increments demand
    - remove_net(path)                         → decrements demand
    - congestion_at(path)                      → max(0, demand-capacity) along path
    - world_to_tile(wx, wy)                    → (tx, ty)
```

### Step 2 — Hook into placement scoring (~20 lines in `scoring.py`)
- Accept a `CongestionGrid` parameter in `score_candidate()`.
- After computing the net proximity term (which already finds the closest pin pair), also compute the coarse route and check congestion.
- Add penalty: `score -= congestion * 20.0`

### Step 3 — Hook into placement engine (~15 lines in `engine.py`)
- Create `CongestionGrid` before the placement loop.
- After placing each component, commit its net routes to the demand map.
- Pass the grid to `score_candidate()`.

---

## What we do NOT touch
- `router/` — nothing. Zero changes.
- `placer/candidates.py` — no changes.
- `placer/engine.py` — only add ~15 lines to create the congestion grid and commit nets after each placement.
- `placer/scoring.py` — only add one new scoring term (~20 lines).

## Estimated total: ~100 lines across 3 files

The existing scoring terms (pin-facing, L-corridor checks from the previous plan) are still nice to have and easy to add. But the congestion grid is the **real** solution — it's how the industry solved this exact problem, and it's not complicated, just a coarse BFS + counter per tile.

## Fallback
If global routing during placement still sometimes produces unroutable layouts (it will be rare, but the coarse grid is an approximation), the router's existing 30-attempt ordering shuffle handles it. The difference is that instead of failing on 3 out of 12 nets, it might fail on 0–1 — and the ordering shuffle can usually fix 1.
---

## Part 2: Making the Router Smarter

The EDA congestion grid helps the placer avoid unroutable layouts. But even with a good placement, the **greedy sequential router** can fail on designs that are theoretically routable — as we saw with this mask design where one ordering can route GND but not VCC, and another ordering can route VCC but not GND. The solution exists, the router just can't find it.

### The Core Problem

Our router commits traces sequentially and permanently. When routing GND (8 MST segments), it picks the shortest path for each segment using A*. But "shortest for GND" might consume the only corridor VCC needs. The rip-up mechanism only tries displacing **one net at a time** and gives up after a single attempt — it can't explore the space of "what if GND took a slightly longer but different-shaped path."

### Approach: Negotiated Congestion Routing (PathFinder-style)

This is the standard FPGA/PCB approach (developed by McMurchie & Ebeling, 1995). Instead of routing nets one at a time and committing permanently, it routes **all nets in parallel** across multiple iterations, using a shared cost map that gradually forces overlapping traces to negotiate for space.

#### How it works:

**Iteration 1 — Optimistic routing:**
- Route every net independently on the same grid, ignoring other nets entirely.
- Nets WILL overlap. That's expected.
- Record which cells are shared by multiple nets.

**Iteration 2..N — Negotiation:**
- For every cell that has more than one net passing through it, increase a **history cost** on that cell.
- Re-route every net, but now the A* cost function includes:
  - `base_cost` (1 per cell as usual)
  - `present_congestion` = number of OTHER nets currently using this cell (high = expensive)
  - `history_cost` = accumulated penalty from previous iterations (never decreases)
- Each net independently finds its cheapest path given the current cost landscape.
- Nets that can find a non-overlapping detour will, because the congested cells are now expensive.
- Nets that genuinely need the corridor will keep using it, but the other net will detour.

**Convergence:**
- The history cost only increases, so repeated conflicts get progressively more expensive.
- Eventually every net finds a non-overlapping path, or we confirm it's truly unroutable.
- Typically converges in 5–15 iterations for our complexity level.

#### Why this solves our VCC/GND problem:

- Iteration 1: Both VCC and GND route through the battery-MCU corridor. They overlap.
- Iteration 2: Overlapping cells get expensive. GND (more flexible — 8 segments with many possible shapes) reroutes some segments to go around the other side. VCC (forced path) keeps the corridor.
- Iteration 3: No more overlaps. Done.

The key insight: **the router no longer needs to "guess" the right ordering**. All nets compete simultaneously and the cost map mediates. The 30-attempt ordering shuffle becomes unnecessary.

### What changes, what stays

**Keep completely unchanged:**
- `grid.py` — same grid, same cell states, same coordinate system
- `pathfinder.py` — same A* and L-route, just called with modified cost functions
- `pins.py` — same pin resolution and allocation
- `models.py` — same Trace, RoutingResult structures
- All constraint enforcement: pin clearance Voronoi, component body blocking, edge clearance, trace width, trace clearance

**Modify in `engine.py`:**
- Replace the outer retry loop (30 random orderings) with the negotiation loop (~10 iterations)
- Add a `congestion_map: dict[int, float]` (flat cell index → accumulated history cost)
- Modify `_route_single_net` to accept and use the cost map
- Each iteration: route all nets, check overlaps, update costs, repeat

**Modify in `pathfinder.py`:**
- Add a `cost_map: dict[int, float] | None` parameter to `find_path` and `find_path_to_tree`
- In the A* neighbor evaluation, add `cost_map.get(nkey, 0.0)` to the move cost
- This is ~3 lines of change in each function

### The negotiation algorithm in detail:

```
congestion_history = {}  # flat_index → float (never decreases)
HISTORY_FACTOR = 1.5     # how fast history grows
PRESENT_FACTOR = 5.0     # cost of current overlap
MAX_ITERATIONS = 15

for iteration in range(MAX_ITERATIONS):
    # 1. Clear all traces from previous iteration
    clear_all_traces(grid)
    
    # 2. Build present-congestion map from current routes
    #    (iteration 0: empty, all nets route freely)
    cell_usage = count_nets_per_cell(current_routes)
    
    # 3. Build combined cost map for A*
    cost_map = {}
    for cell, count in cell_usage.items():
        present = max(0, count - 1) * PRESENT_FACTOR  # 0 if only one net uses it
        history = congestion_history.get(cell, 0.0)
        cost_map[cell] = present + history
    
    # 4. Route every net using the cost map
    current_routes = {}
    for net_id in all_nets:
        path = route_net(net_id, grid, cost_map)  # A* with cost_map
        current_routes[net_id] = path
    
    # 5. Check for overlaps
    overlaps = find_overlapping_cells(current_routes)
    if not overlaps:
        break  # All nets routed without conflict!
    
    # 6. Update history cost for congested cells
    for cell in overlaps:
        congestion_history[cell] = congestion_history.get(cell, 0.0) + HISTORY_FACTOR
```

### Critical detail: trace clearance during negotiation

During negotiation iterations, we can't use the normal `block_trace()` with full clearance zones — that would prevent overlaps from being detected. Instead:

- Mark trace PATH cells as used (for overlap counting), but DON'T block clearance zones during negotiation.
- On the **final iteration** (when no overlaps remain), apply full clearance blocking to verify the solution respects all physical constraints.
- If clearance violations appear on the final check, add those cells to the history cost and run one more iteration.

### Why this doesn't break existing constraints:

1. **Trace-to-trace clearance**: Enforced on the final pass via `block_trace()` with clearance zones, exactly as today.
2. **Pin clearance (Voronoi)**: Applied every iteration — `_block_voronoi()` runs before each net's A*, exactly as today.
3. **Component body blocking**: Permanent blocks on the grid, untouched by negotiation.
4. **Edge clearance**: Permanent blocks from grid init, untouched.
5. **Pin-to-pin clearance**: Not affected — this is a placement constraint, not routing.

### Performance estimate

- 11 nets × 15 iterations = 165 A* runs (vs current 11 nets × 31 attempts = 341 A* runs)
- Each A* is slightly more expensive (cost_map lookup per cell) but the grid is the same size
- Should be roughly the same total time, possibly faster since it converges early

### Risk assessment

The biggest risk with touching the router is **regressions** — traces too close, crossings, etc. The negotiation approach minimises this because:
- All constraint checking (Voronoi, clearance, permanent blocks) is unchanged code
- The only new thing is the cost_map parameter added to A*'s cost calculation
- The final verification pass uses the exact same `block_trace()` / clearance logic as today

If negotiation produces a worse result than the current approach for any test case, we can fall back to the current retry loop as a backup — try negotiation first, if it fails, run the old 30-attempt approach.

---

## Part 3: Jumper Wires — The Real Answer for Single-Layer Boards

### Why GND/VCC keep failing

The negotiation consistently finds 11/11 paths with only ~3 path-cell overlaps. The clearance verification then rejects some paths because their clearance zones collide. Different commit orderings get 8–10/11, but never all 11. This isn't a routing algorithm problem — it's a **physical constraint problem**. The board geometry simply cannot accommodate all nets without at least one crossing on a single copper layer.

This is completely normal. Every real single-layer PCB uses **jumper wires** at crossing points. A jumper wire is a short insulated wire soldered between two pads that bridges over an underlying copper trace. The traces are effectively on two different "layers" at the crossing point — one on copper, one in air.

### The insight

The negotiation is already solving the right problem. Its 3 overlapping cells aren't failures — they're **jumper wire locations**. Instead of rejecting the negotiation result during clearance verification, we should **accept the overlaps as jumper crossings** and emit them as part of the routing solution.

### How it works

**During negotiation:**
- Path-cell overlaps identify where two nets physically must cross.
- At each overlap point, one net continues on the copper layer as normal. The other net's segment through that area becomes a "jumper wire."

**At commit time:**
- When a path cell is already occupied by another net's trace (TRACE_PATH), don't reject it. Instead, record the crossing as a `JumperCrossing(location, over_net, under_net)`.
- Skip clearance enforcement at the jumper cell and its immediate neighbours — the traces are on different physical layers at that point.

**In the output:**
- Add a `jumper_wires: list[JumperWire]` field to `RoutingResult`.
- Each `JumperWire` has: start pad position, end pad position, net_id, crossing_net_id.
- The GCode generator / enclosure design can annotate these locations.

### The algorithm for deciding who jumps

When two nets overlap at a cell:
1. The net with **fewer total path cells** (shorter/simpler) becomes the jumper, because its jump segment is shorter.
2. Alternatively, the net with **fewer pins** jumps — it has fewer overall routing constraints.
3. In practice for our mask design: VCC (5 pins, ~30 cells) would jump over GND (9 pins, ~80 cells) at the 3 crossing points.

### What this means for constrant checking

At a jumper crossing point, the clearance rules change:
- **Trace-to-trace clearance**: Not needed between the two crossing nets at the jump cells — they're on different layers.
- **All other clearance**: Unchanged. The copper trace still needs clearance from other copper traces, pins, edges, etc.
- **The jumper wire**: Needs start/end pad positions where it connects to the copper. These are just two points along the trace, close to the crossing.

### Why this is correct and not a hack

- Every single-layer PCB design tool in existence supports jumper wires. KiCad, Eagle, and Altium all have zero-ohm resistor / jumper footprints for single-layer designs.
- The industry standard for prototyping is to use 0Ω resistors or wire jumpers at unavoidable crossings.
- Our board is a perfboard / single-layer — jumper wires are the expected physical solution.
- The placer's CongestionGrid and the router's negotiation algorithm have already minimised the number of crossings to the physical minimum (~3). The jumper wire mechanism just acknowledges those crossings instead of failing.

### Three ideas to reduce jumper count even further

**1. Steiner tree topology variation for multi-pin nets**

Currently, `_compute_mst()` produces one fixed Minimum Spanning Tree for GND's 9 pins. But there are many different spanning trees (and Steiner trees) that connect the same pins. Some topologies route through different corridors, potentially avoiding the VCC conflict entirely.

Idea: generate 3–5 alternative spanning trees (by removing the longest MST edge, finding the next-best, etc.) and try each during negotiation. The cost map naturally selects the topology that creates the fewest overlaps.

**2. Power bus routing along edges**

Instead of routing GND/VCC as Steiner trees through the interior, run them as **edge bus traces** — dedicated paths along the board perimeter. Pin stubs connect from the bus to each component's power pins. This is standard single-layer practice:
- GND bus runs along the top/right edge
- VCC bus runs along the bottom/left edge
- Signal traces get the full interior

This separates the power routing problem from signal routing entirely — they compete for different space.

**3. Board size feedback**

If the router can quantify congestion (we already have the data — overflow per tile), it can suggest: "Increasing board width by 5mm would eliminate 2 jumper crossings." This gives the user actionable feedback instead of a mysterious failure.

### Implementation priority

1. **Jumper wires** — directly solves the 10/11 → 11/11 gap with correct constraint handling
2. **Steiner tree variation** — may reduce jumper count from 3 to 1–2
3. **Power bus routing** — larger change, but eliminates the GND/VCC corridor fight entirely
4. **Board size feedback** — nice-to-have, low effort since congestion data already exists
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

## ✅ Status: EDA congestion grid is IMPLEMENTED

The coarse BFS congestion grid (`placer/congestion.py`) is implemented and wired into scoring term #8 in `scoring.py`. It runs during candidate evaluation and applies a `-20.0 × congestion` penalty. The placer also has 9 scoring terms total, candidate generation from 5 sources, and a 3-pass relaxation loop.

**However, the fundamental placement strategy is still greedy-constructive.** Components are placed one-at-a-time, best-candidate-first, and committed permanently. This means:
- Early placement decisions can trap later components
- No ability to reconsider a bad early choice
- Component ordering is heuristic (largest/most-connected first)
- The congestion grid helps avoid obvious bottlenecks but can't fix a fundamentally bad placement topology

The congestion grid is a necessary ingredient, but it's not sufficient. We need a way to explore and refine the placement globally.

---

## Part 3: The Real Placement Problem (and How the Industry Solves It)

### Why Greedy-Constructive Placement Fails

Our current placer is **constructive and sequential**: it places component #1,  commits, places #2, commits, ... This is the simplest and weakest placement strategy. The problems:

1. **Order dependence** — If the battery goes down first and claims the center-left, the MCU may be forced to a corner where its 28 pins can't fan out. But if the MCU went first, both would fit fine.
2. **No backtracking** — Once placed, a component never moves. A locally-optimal choice for component #3 might make component #7 impossible.
3. **Greedy scoring is blind to global structure** — The 9-term score optimizes each component independently. There's no "board-level" objective that says "this *overall* arrangement is good."
4. **The congestion grid helps but can't save you** — It prevents placing component #5 into an already-congested tile, but it can't say "component #2 should have gone 5mm to the left so that component #5 would have room here."

This is not unique to us — **every EDA tool in history started with constructive placement and then had to add refinement.** The constructive pass gives you a reasonable starting point; the refinement pass makes it actually work.

### What the Industry Uses

#### 1. Simulated Annealing (SA) — The Industry Standard for PCB/FPGA Placement

Used by: **VPR** (academic FPGA placer, Betz & Rose 1997), **TimberWolf** (Sechen & Sangiovanni-Vincentelli 1986), **Cadence Allegro PCB**, **Altium Designer**, most commercial autoplacers.

**How it works:**
- Start from an initial placement (our constructive placer provides this)
- Repeat millions of times:
  - Pick a random perturbation (move one component by a small amount, swap two components, rotate one)
  - Compute the new cost (wirelength + congestion + overlap penalty)
  - If the new cost is better → accept
  - If worse → accept with probability `exp(-ΔCost / Temperature)`
  - Gradually decrease Temperature
- The probabilistic acceptance lets SA escape local minima that greedy search cannot

**Why it's perfect for us:**
- Our designs have ~8–15 components. SA converges in seconds for this scale. VPR runs SA on 100,000-gate FPGAs; we have 15 parts.
- We already have the cost function (9 scoring terms + congestion grid).
- We already have the initial placement (constructive pass).
- The math is trivial — it's just a while loop with `random.uniform()`.

**What it would look like for us:**
```
initial_placement = greedy_constructive_place()  # what we do now
best = initial_placement
T = T_initial                                     # e.g. 50.0

for i in range(N_ITERATIONS):                     # e.g. 5000–20000
    # Pick a random perturbation
    candidate = perturb(current_placement)        # move, swap, or rotate one component
    
    # Check hard constraints (inside outline, no overlaps, pin clearance)
    if not feasible(candidate):
        continue
    
    # Compute cost using existing scoring + congestion
    new_cost = evaluate_full_placement(candidate)
    delta = new_cost - current_cost
    
    if delta < 0 or random() < exp(-delta / T):
        current = candidate
        current_cost = new_cost
        if new_cost < best_cost:
            best = candidate
            best_cost = new_cost
    
    T *= cooling_rate                              # e.g. 0.9995
```

**Perturbation moves:**
- **Displace**: Pick a random component, move it by a random offset (scaled by T — large moves early, small moves late)
- **Swap**: Pick two non-UI components, exchange their positions (if both still feasible)
- **Rotate**: Pick a random component, change its rotation to a different valid angle
- **Weighted selection**: Prefer perturbing components that are involved in the highest-congestion nets

#### 2. Analytical Placement (Modern, Large-Scale)

Used by: **ePlace** (Lu et al. 2015), **RePlAce** (Cheng et al. 2019), **DREAMPlace** (Lin et al. 2019, GPU-accelerated).

**How it works:**
- Model each net as a spring connecting its components (quadratic wirelength approximation)
- Solve the resulting system of equations to find the minimum-energy placement (all components cluster at the centroid of their connected partners)
- Add a "spreading force" (electrostatic repulsion analogy) to prevent overlap
- Iterate: solve → spread → solve → spread until converged

**Why it's NOT right for us:**
- Designed for millions of standard cells, not 15 components
- Requires a differentiable cost function (our outline polygon and hard constraints aren't differentiable)
- Massive implementation complexity for no benefit at our scale
- Overkill — a cannon to kill a mosquito

#### 3. Force-Directed Placement

Used by: some early PCB tools, educational tools.

**How it works:**
- Each net creates an attractive force between its pins (spring)
- Overlapping components create a repulsive force
- Iterate: compute net forces on every component, move them proportionally, repeat until equilibrium

**Verdict:** Simpler than analytical placement but weaker than SA. Can get stuck in local minima. SA strictly dominates it for our scale.

#### 4. Genetic / Evolutionary Algorithms

Used by: some academic PCB placers, niche tools.

**How it works:**
- Maintain a population of placement solutions
- Crossover: combine good parts of two placements
- Mutation: randomly perturb some components
- Selection: keep the fittest (lowest cost)

**Verdict:** Works but slower to converge than SA for small designs. The crossover operator for placements is tricky (combining two placements often produces infeasible results). SA is simpler and better here.

#### 5. Multi-Start Constructive (Simplest Improvement)

**How it works:**
- Run our existing greedy placer N times (e.g. 10) with different component orderings
- Evaluate each full placement with the congestion grid
- Pick the best one

**Verdict:** Dead simple, embarrassingly parallel, gives real improvement. But it's still greedy — each individual run can still make bad choices. Good as a quick win but not a full solution.

---

### Recommended Strategy: SA Refinement on Top of Constructive Placement

The plan is a **two-phase placer:**

**Phase 1 — Constructive (what we have now)**
Run the existing greedy placer. This gives a reasonable initial placement where all hard constraints are satisfied and components are near their net partners. This is the "seed" for refinement.

**Phase 2 — Simulated Annealing Refinement (new)**
Starting from the constructive placement, run SA to globally optimize the layout for routability. The SA cost function uses:
1. **Total half-perimeter wirelength (HPWL)** — for each net, compute the bounding box of all its pins. Sum of (width + height) over all nets. This is the standard EDA wirelength proxy.
2. **Congestion penalty** — run the coarse global router on all nets, sum over-capacity violations across all tiles. This is the key routability signal.
3. **Overlap penalty** — any body/keepout overlaps get a massive penalty (hard constraint, but SA needs to be able to temporarily violate it to swap components through each other during early high-temperature phases).
4. **Outline violation** — components outside the outline get a massive penalty.

The SA cost differs from the per-component scoring we have now. It's a **global** cost over the entire placement, not a per-candidate cost. This is the key difference — SA evaluates the whole board, not one component at a time.

### Why Congestion Grid + SA Together Solve the Problem

The congestion grid alone tells you "this tile is over-capacity." But in a greedy placer, by the time you know it's over-capacity, the components responsible are already committed.

SA + congestion grid fixes this: when SA tries a perturbation that creates congestion, the cost goes up, and SA rejects it (or accepts probabilistically). When SA tries a perturbation that relieves congestion, the cost goes down, and SA accepts it. Over thousands of iterations, the placement converges to a configuration where all tiles are within capacity — which means the fine-grid router will succeed.

### Implementation Sketch

**New file: `placer/annealing.py`** (~100–150 lines)

```
class SARefiner:
    def __init__(self, placement, outline, net_graph, catalog_map, congestion_grid):
        ...
    
    def cost(self, placement) -> float:
        """Global cost: HPWL + congestion + overlap + boundary."""
        ...
    
    def perturb(self, placement, temperature) -> Placement:
        """Random move/swap/rotate, scaled by temperature."""
        ...
    
    def feasible(self, placement) -> bool:
        """Hard-constraint check (outline, pin clearance)."""
        ...
    
    def run(self, n_iterations=10000, t_initial=50.0, cooling=0.9995) -> Placement:
        """SA main loop. Returns best placement found."""
        ...
```

**Changes to `placer/engine.py`** (~10 lines):
- After the constructive loop, call `SARefiner(placement, ...).run()`
- Use the SA-refined placement as the final result

**No changes to the router.**

### Temperature Schedule & Iteration Count

For ~10–15 components on a ~60×40mm board:
- **T_initial = 50.0** — at this temperature, moves of ~20mm cost increase are accepted ~33% of the time. This allows early exploration.
- **T_final = 0.1** — at this temperature, only improvements or 0.1-cost-unit-worse moves are accepted. Fine-tuning.
- **Cooling rate = 0.9995** — with 10,000 iterations, T goes from 50 → ~0.03. Enough to converge.
- **10,000 iterations at ~0.1ms each (Python) = ~1 second.** Acceptable.

### Perturbation Move Weights

| Move | Probability | Description |
|------|-------------|-------------|
| Displace | 60% | Move one random non-UI component by `(Δx, Δy)` where `Δx, Δy ~ Normal(0, σ)`, `σ = T/T_initial × max_board_dim × 0.3` |
| Swap | 20% | Exchange positions of two random non-UI components (if same mounting style) |
| Rotate | 20% | Change one random component to a different valid rotation |

Displace is the most important move — it does fine-grained optimization. Swap is critical for breaking out of topology traps where two components are in each other's ideal positions. Rotate helps when pin-facing matters.

### Hard vs Soft Constraints in SA

| Constraint | Treatment | Why |
|------------|-----------|-----|
| Inside outline | Soft (huge penalty) | Must be able to temporarily violate during swaps |
| No body overlap | Soft (huge penalty) | Must allow swaps to pass through |
| Pin clearance (4.2mm) | Soft (large penalty) | Near-violations are tolerable during search |
| UI component positions | Hard (frozen) | User-specified, never moved |
| Mounting style matches position | Hard (skip) | Bottom-mount can't go to top position |

Making outline/overlap constraints *soft* with huge penalties (e.g., 1000 × violation amount) is standard SA practice. It lets the search explore via swaps and displaces that temporarily pass through infeasible space, while the penalty ensures the final solution is feasible.

### Fallback & Safety

- If SA produces a result with **zero hard-constraint violations** and **lower cost than the constructive placement**, use it.
- If SA's best result still has hard-constraint violations, **fall back to the constructive placement** — at least that's guaranteed feasible.
- Log the cost improvement so we can tune parameters.

### What About Multi-Start?

We can combine both approaches:
- Run the constructive placer **3 times** with different component orderings (original, reversed, random)
- Run SA refinement on the best one (or all three in parallel, pick the best post-SA result)
- This gives both exploration of placement orderings AND SA's ability to refine globally

For 3 starts × 1 second SA each = 3 seconds total. Perfectly acceptable.

---

## Summary: The Three-Layer Solution

| Layer | What | Status | Purpose |
|-------|------|--------|---------|
| **Congestion Grid** | Coarse BFS tile routing estimate | ✅ Done | Prevents obvious bottlenecks during constructive placement |
| **SA Refinement** | Global optimization of full placement | 🔲 Not started | Escapes local minima, optimizes board-level routability |
| **Router retry loop** | 30-attempt ordering shuffle | ✅ Done | Handles residual failures after good placement |

The congestion grid makes the constructive placement "not terrible." SA refinement makes it "actually good." The retry loop handles the rare case where even a good placement has a tricky net ordering.

This is exactly what the industry does: constructive placement → global refinement → detailed routing with retries. We just need the middle layer.

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


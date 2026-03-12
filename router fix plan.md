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

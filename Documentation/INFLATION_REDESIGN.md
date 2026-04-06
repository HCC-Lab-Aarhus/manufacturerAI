# Inflation Redesign

## Problems with the Current Prototype

The current inflation pipeline takes router output (thin Manhattan-grid paths) and attempts to expand them into variable-width polygons. It does this using a linear pipeline: resample → Chaikin smooth → relax centrelines → compute local half-widths → build ribbon polygon. This approach has several structural problems:

### 1. Disconnected Sections
The ribbon polygon construction offsets sample points along local normals. When the trace curves sharply or the half-width changes too quickly, the left/right offset curves self-intersect. Shapely's `buffer(0)` fix produces a MultiPolygon, and we take the largest piece — silently dropping the rest. The result: sections of the trace visually disappear.

### 2. Pin Intersection
Half-widths are computed independently per sample point. The trace can balloon outward and overlap a foreign pin's exclusion zone. The current fix (subtracting pin exclusion circles at the end) carves holes in the ribbon, which often disconnects it again — back to problem 1.

### 3. Wobbly Traces
The relaxation step uses inverse-square repulsive forces from other traces, obstacles, and the outline. This is sensitive to the iteration count and step factor. Because the force field is recomputed from scratch each iteration (from full LineString geometry), small perturbations cascade — interior points oscillate instead of converging. The result is wobbly, non-smooth centrelines.

### 4. MST Midpoint Problem
A net with 3+ pins is routed as an MST: separate trace segments connecting pin pairs. Each segment is inflated independently. When a user moves a midpoint (a junction where two MST branches meet), the two segments sharing that junction need to update their shared endpoint and re-partition space between them. The current system has no concept of junction identity — traces are just flat lists of waypoints. Moving a midpoint breaks the topology.

### 5. No Elasticity / Push Propagation
When one trace is moved or expands, neighbouring traces don't react. The relaxation step is a one-shot operation, not an incremental constraint solver. There's no mechanism for "push": if trace A expands into trace B's space, B should contract or shift — but currently B doesn't know A changed.

---

## Design Goals

1. **Continuity** — every inflated trace is a single, connected polygon. No disconnected fragments.
2. **Clearance guarantees** — no trace polygon overlaps any foreign pin exclusion zone or another trace's clearance envelope, by construction rather than post-hoc clipping.
3. **Smooth centrelines** — traces should be smooth curves, not wobbly polylines. The smoothing must converge, not oscillate.
4. **MST-aware junctions** — multi-pin nets form a tree. Junction points are first-class objects. Moving a junction re-routes the branches connected to it.
5. **Elastic push** — when a trace is moved or resized, neighbouring traces react: they contract or shift to maintain clearance. The system finds a new equilibrium.
6. **Incremental updates** — after a user drags a midpoint, only the affected traces and their neighbours need recomputation, not the entire board.

---

## Core Architecture: Medial-Axis Inflation with Constraint Relaxation

The new system replaces the linear pipeline with a two-phase approach:

### Phase 1: Topology Graph
Build an explicit graph of all traces and their spatial relationships.

### Phase 2: Iterative Constraint Solver
Solve for positions and widths simultaneously, with mutual constraints, until convergence.

---

## Data Model

### TraceGraph

```
TraceGraph
├── nodes: dict[NodeId, TraceNode]
├── edges: dict[EdgeId, TraceEdge]
└── nets:  dict[NetId, NetTree]
```

**TraceNode** — a point that matters topologically:
- `pin`: a component pin position (fixed, not movable)
- `junction`: where MST branches meet (movable)
- `waypoint`: an interior routing waypoint (movable)

```python
@dataclass
class TraceNode:
    id: str
    kind: Literal["pin", "junction", "waypoint"]
    position: tuple[float, float]
    fixed: bool  # pins are fixed; junctions/waypoints are movable
    net_id: str
```

**TraceEdge** — a trace segment between two nodes:

```python
@dataclass
class TraceEdge:
    id: str
    net_id: str
    source: str      # node id
    target: str      # node id
    waypoints: list[tuple[float, float]]  # interior control points
    half_widths: list[float]              # width at each sample point
```

**NetTree** — one per net. For a 2-pin net, it's a single edge. For an MST, it's a tree of edges sharing junction nodes.

```python
@dataclass
class NetTree:
    net_id: str
    root: str         # node id (arbitrary root)
    node_ids: set[str]
    edge_ids: set[str]
```

### Why a Graph?

The current system treats each trace as an independent polyline. With a graph:
- **Junction identity** is explicit. Moving junction J updates all edges incident to J.
- **Neighbour lookup** is fast. Each edge knows which other edges share space nearby (via a spatial index — R-tree on edge bounding boxes).
- **Incremental updates** only touch edges flagged as dirty and their spatial neighbours.

---

## Phase 1: Building the Topology Graph

### Input
`RoutingResult` — a list of `Trace(net_id, path)` where path is a list of grid-snapped waypoints.

### Steps

#### 1.1 Identify Junctions

For multi-pin nets routed via MST, the router emits one `Trace` per MST edge. Two traces in the same net that share an endpoint have an implicit junction there.

```
for each net:
    group traces by net_id
    collect all endpoints
    any endpoint shared by 2+ traces → create a junction node
    non-shared endpoints → pin nodes (lookup from pin_positions)
```

#### 1.2 Build Edges

Each trace becomes a TraceEdge. Its source/target are the nodes at its endpoints. Interior waypoints from the original Manhattan path are stored but will be re-smoothed.

#### 1.3 Smooth Edges (Improved Chaikin)

Apply Chaikin subdivision to each edge's waypoints, but:
- **Anchor endpoints** — they are node positions and must not move.
- **Resample to uniform spacing** after Chaikin (same as current, ~1.5 mm).
- **No relaxation yet** — that's Phase 2's job.

The key difference: Chaikin runs once on the raw Manhattan path. The relaxation that currently follows (and causes wobble) is replaced by the constraint solver.

---

## Phase 2: Constraint Solver

### Overview

The solver iterates over all movable points (junction positions, interior waypoints, half-widths) and adjusts them to satisfy constraints. It's a Gauss-Seidel-style relaxation: sweep through all variables, update each one in-place, repeat until convergence.

### Variables

For each TraceEdge with N sample points (including endpoints):
- `positions[0..N-1]`: (x, y) of each sample point. Endpoints are soft-locked to their node positions (pins are hard-locked, junctions are movable).
- `half_widths[0..N-1]`: the half-width at each sample point.

For each junction node:
- `position`: (x, y), movable.

### Hard Constraints (must never be violated)

| Constraint | Description |
|---|---|
| **C1: Outline containment** | Every sample point ± its half-width must lie inside `inset_outline`. |
| **C2: Obstacle avoidance** | Distance from any sample point to any obstacle ≥ half-width at that point. |
| **C3: Pin clearance** | Distance from any sample point to any foreign pin ≥ half-width + `pin_clearance_mm`. |
| **C4: Minimum width** | `half_width[i] ≥ min_half` (= `trace_width_mm / 2`). |
| **C5: Maximum width** | `half_width[i] ≤ max_half` (= `max_trace_width_mm / 2`). |

### Soft Constraints (objectives to optimise)

| Objective | Description |
|---|---|
| **O1: Maximise width** | Each half-width should be as large as possible (fill available space). |
| **O2: Smooth width transitions** | Adjacent half-widths should differ by at most `seg_len` (45° taper). |
| **O3: Smooth centreline** | Interior points should tend toward the midpoint of their neighbours (Laplacian smoothing). |
| **O4: Inter-trace clearance** | Distance between edges of different traces ≥ `trace_clearance_mm`. This means: for two sample points on different traces, `dist(p_i, p_j) ≥ hw_i + hw_j + trace_clearance_mm`. |
| **O5: Junction centering** | A junction's position should be the centroid of its incident edge directions, weighted by edge length. |

### Solver Loop

```
build spatial index (R-tree) over all edge bounding boxes

for iteration in range(MAX_ITERATIONS):
    max_delta = 0

    # --- Width pass: set half-widths to maximum allowed ---
    for each edge:
        for each sample point i:
            hw = max_half

            # C1: outline
            hw = min(hw, distance_to_outline_boundary(point_i))

            # C2: obstacles
            hw = min(hw, distance_to_nearest_obstacle(point_i))

            # C3: foreign pins
            for each foreign pin:
                hw = min(hw, dist(point_i, pin) - pin_clearance)

            # O4: inter-trace clearance
            for each nearby edge (from spatial index):
                if same net: skip
                d = min distance from point_i to other edge's centreline
                hw = min(hw, (d - trace_clearance) / 2 - wall_half)

            hw = clamp(min_half, max_half, hw)
            half_widths[i] = hw

        # O2: taper smoothing (forward + backward pass)
        apply_taper_constraint(half_widths, positions)

    # --- Position pass: adjust movable points ---
    for each junction node (movable):
        # O5: move toward centroid of incident edge directions
        new_pos = weighted_centroid(incident_edges)
        # C1: clamp inside outline
        new_pos = project_inside(new_pos, inset_outline)
        delta = dist(old_pos, new_pos)
        max_delta = max(max_delta, delta)
        junction.position = new_pos
        # update endpoints of incident edges
        for each incident edge:
            update_endpoint(edge, junction)

    for each edge:
        for each interior sample point (not endpoints):
            # O3: Laplacian smoothing
            target = midpoint(prev, next)

            # O4: repulsion from nearby traces
            for each nearby edge (from spatial index):
                if same edge: skip
                nearest = closest_point_on(other_edge, current_point)
                d = dist(current_point, nearest)
                if d < clearance_threshold:
                    push current_point away from nearest

            # Blend: 70% Laplacian, 30% repulsion
            new_pos = blend(target, repulsion_offset)

            # C1+C2: project inside outline, away from obstacles
            new_pos = project_valid(new_pos, inset_outline, obstacles)

            delta = dist(old_pos, new_pos)
            max_delta = max(max_delta, delta)
            point_i = new_pos

    # --- Convergence check ---
    if max_delta < CONVERGENCE_THRESHOLD:
        break

    # --- Rebuild spatial index if positions changed significantly ---
    if max_delta > REBUILD_THRESHOLD:
        rebuild spatial index
```

### Key Differences from Current Approach

| Current | New |
|---|---|
| Relaxation uses inverse-square forces (oscillates) | Laplacian smoothing with clamped step size (converges monotonically) |
| Half-widths computed once, independently | Half-widths recomputed each iteration, accounting for updated positions |
| Traces don't know about each other during width computation... except through LineString distances | Spatial index enables efficient neighbour queries; width and position are coupled |
| Ribbon polygon built from normals (self-intersects on sharp curves) | Polygon built after convergence from a stable, smooth centreline |
| Pin exclusions subtracted post-hoc | Pin clearance is a hard constraint during width computation — no subtraction needed |
| No junction concept | Junctions are explicit; moving one updates all incident edges |

---

## Phase 3: Polygon Construction (Post-Solve)

After the solver converges, each edge has a smooth centreline and stable half-widths. Building the polygon is straightforward:

### 3.1 Offset Curves

For each edge, compute left and right offset curves using the per-point half-widths and normals. Since the centreline is now smooth (Laplacian smoothing converged), normals vary slowly and the offset curves won't self-intersect.

### 3.2 Cap Handling

- At **pin endpoints**: round cap (circle of radius `half_width[endpoint]`).
- At **junction endpoints**: no cap — the junction is where multiple edge polygons meet. Their union forms the junction area.

### 3.3 Net Union

For each net, union all edge polygons belonging to that net's tree. This naturally merges junction areas. The result is a single connected polygon per net.

### 3.4 Final Cleanup

```python
polygon = polygon.buffer(smooth_r).buffer(-smooth_r)  # morphological closing
polygon = polygon.simplify(tolerance)                   # reduce vertex count
polygon = polygon.intersection(inset_outline)           # clip to board
```

Because clearance constraints were satisfied during the solve, this cleanup step is cosmetic — it doesn't need to fix broken geometry.

---

## MST Junction Handling

### Building the MST Tree

When the router produces multiple traces for a single net (MST routing), we reconstruct the tree:

```
for each net with multiple traces:
    collect all trace endpoints
    group by position (within grid_resolution tolerance)
    positions shared by 2+ traces → junction nodes
    positions shared by 1 trace and matching a pin → pin nodes
```

### Moving a Junction

When the user drags a junction node:

1. **Update the junction's position** to the drag target.
2. **Mark all incident edges as dirty.**
3. **Re-run the constraint solver** on dirty edges + their spatial neighbours (not the whole board).
4. **Rebuild polygons** for the affected net.

The solver naturally handles the cascade: if moving the junction pushes an edge into another trace's space, the position pass will shift that other trace's points, and the width pass will narrow both traces to maintain clearance.

### Adding/Removing MST Branches

If a junction is dragged close to another pin in the same net, the two branches could merge. If it's dragged far away, a branch might need to re-route. These are topological changes — the graph structure changes. For now, these trigger a full re-route of the affected net (fall back to the router). Interactive topological edits are a future extension.

---

## Incremental Update Protocol

### Dirty Tracking

Each `TraceEdge` has a `dirty: bool` flag. Operations that set dirty:
- User drags a junction or waypoint.
- A trace is added or removed.
- The outline or obstacle set changes.

### Incremental Solve

```
dirty_edges = {e for e in graph.edges if e.dirty}
affected_edges = dirty_edges ∪ spatial_neighbours(dirty_edges, margin=max_trace_width)
run solver on affected_edges only (other edges are frozen)
rebuild polygons for affected nets
clear dirty flags
```

This is O(k) where k is the number of affected edges, not O(n) for all edges on the board.

---

## Elastic Push Mechanism

"Elasticity" means: when trace A expands, trace B contracts. The solver already does this implicitly through the coupled width/position passes:

1. **Width pass**: trace A's half-width is limited by distance to trace B. If A's centreline moved closer to B, A gets narrower near B.
2. **Position pass**: trace B's points are repelled by trace A. If A is now wider, B shifts away.
3. **Next iteration**: with B shifted away, A has more room and can widen again.

This converges to an equilibrium where both traces share the available space proportionally. The "elasticity" is emergent from the iterative coupling — no explicit spring model needed.

### Push Priority

When two traces compete for space, which one yields? By default, both share equally (each gets half the gap). Optionally, a priority can be assigned:
- **Fixed traces** (user-locked) don't move; other traces must accommodate them.
- **Critical nets** (e.g., power) get width priority: they expand first, others contract.

This is implemented by ordering the width pass: high-priority traces compute their widths first, and their widths become constraints for lower-priority traces.

---

## Solver Parameters

| Parameter | Default | Description |
|---|---|---|
| `MAX_ITERATIONS` | 30 | Maximum solver iterations |
| `CONVERGENCE_THRESHOLD` | 0.01 mm | Stop when max point movement < this |
| `REBUILD_THRESHOLD` | 0.5 mm | Rebuild spatial index when movements exceed this |
| `LAPLACIAN_WEIGHT` | 0.7 | Blend factor for Laplacian smoothing vs repulsion |
| `TAPER_ANGLE` | 45° | Maximum width-change angle (same as current) |
| `POSITION_STEP_LIMIT` | 0.5 mm | Maximum point movement per iteration (prevents oscillation) |

---

## Module Structure

```
src/pipeline/inflation/
├── __init__.py            # public API (inflate_traces, etc.)
├── graph.py               # TraceGraph, TraceNode, TraceEdge, NetTree
├── builder.py             # build_trace_graph() from RoutingResult
├── solver.py              # ConstraintSolver — the iterative solver
├── polygon.py             # build_net_polygons() from solved graph
├── obstacles.py           # build_pin_exclusions, build_obstacle_polygons (keep)
├── serialization.py       # inflation_to_dict, parse_inflation (extend for graph)
└── spatial.py             # R-tree spatial index wrapper
```

### Entry Point

```python
def inflate_traces(result, outline, obstacles, **kwargs) -> list[InflatedTrace]:
    graph = build_trace_graph(result, pin_positions, net_pin_ids)
    solver = ConstraintSolver(graph, outline, obstacles, **kwargs)
    solver.solve()
    return build_net_polygons(graph, outline)
```

### Incremental Entry Point (for interactive editing)

```python
def update_junction(graph, junction_id, new_position, outline, obstacles, **kwargs):
    graph.move_node(junction_id, new_position)
    dirty = graph.dirty_edges()
    solver = ConstraintSolver(graph, outline, obstacles, subset=dirty, **kwargs)
    solver.solve()
    affected_nets = {graph.edges[eid].net_id for eid in dirty}
    return {net_id: build_net_polygon(graph, net_id, outline) for net_id in affected_nets}
```

---

## Migration Path

1. **Keep the current system working** — the new module is built alongside, in the same package.
2. **Feature-flag** — `inflate_traces()` checks a flag (or a parameter) to choose old vs new path.
3. **Tests** — the existing test suite (`test_inflater.py`) should pass on the new implementation with minimal changes, since the public API (`inflate_traces → list[InflatedTrace]`) is the same.
4. **Remove old code** once the new system is validated.

### Files to Add
- `graph.py`, `builder.py`, `solver.py`, `polygon.py`, `spatial.py`

### Files to Modify
- `__init__.py` — add new public API functions
- `inflater.py` — switch to new pipeline internally
- `serialization.py` — extend to serialize/parse graph state (for incremental updates)

### Files to Keep Unchanged
- `obstacles.py` — obstacle and pin exclusion logic is reusable as-is

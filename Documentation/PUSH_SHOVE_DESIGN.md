# Push-and-Shove Trace Relaxation — Design Document

## Problem

The current solver measures available space (Voronoi-style width computation) but never adjusts centreline positions. When two traces run parallel and close together, they both get narrow widths — neither "knows" it could move sideways to give both more room.

## Goal

Add a **force-directed push-and-shove pass** where nearby traces repel each other proportionally to their overlap, distributing displacement based on each point's freedom to move. This is the standard approach used in:

- **KiCad PNS (Push & Shove) interactive router** — moves traces perpendicular to make room, cascades pushes.
- **Analog VLSI layout compaction** — force-directed spacing of wires in channels.
- **Capsule-capsule collision resolution** in physics engines — resolve overlapping capsules by splitting the displacement.

## Algorithm Overview

The solver pipeline becomes:

```
1. Laplacian pre-smooth (existing, 6 iterations)
2. Compute initial widths (existing, needed for desired footprint)
3. Push-and-shove iterations (NEW, 4-6 iterations)
   a. For each sample point, find nearby foreign-net trace segments
   b. Compute overlap = desired_gap − actual_distance
   c. Distribute correction between both points by relative mobility
   d. Accumulate displacement vectors, apply with damping
   e. Clamp displaced points to outline, away from obstacles/pins
4. Light re-smooth (2 Laplacian passes to remove push artifacts)
5. Recompute final widths (existing)
```

## Detailed Design

### 3a. Pairwise Interaction

For each edge A, use the existing `EdgeIndex.nearby()` to find foreign-net edges within `interaction_radius = max_half * 2 + wall_clearance * 2`.

For each sample point `p_i` on edge A, compute the nearest point `q` on edge B's LineString:

```
d = distance(p_i, linestring_B)
q = nearest_point_on(linestring_B, p_i)
```

This uses Shapely's optimised C-level nearest-point computation — no manual segment iteration.

### 3b. Overlap Calculation

```
desired_gap = half_w_A[i] + interpolated_half_w_B(q) + wall_clearance
overlap = desired_gap − d
```

If `overlap ≤ 0`, no interaction (traces already far enough apart).

For the half-width at the interpolated point `q` on edge B, we use linear interpolation between the two bracketing sample points (Shapely's `line.project(q)` gives us the parametric position).

### 3c. Mobility-Weighted Displacement

Each sample point has a **mobility** ∈ [0, 1] describing how free it is to move:

```
mobility(p) = 1.0                          # default: fully mobile
if p is an endpoint:        mobility = 0.0  # locked
if near outline boundary:   mobility = min(mobility, d_outline / margin)
if near obstacle:           mobility = min(mobility, d_obstacle / margin)
if near foreign pin:        mobility = min(mobility, d_pin / margin)
```

The `margin` is a characteristic length (e.g., `max_half`) that defines the fade-in zone.

**Newton's Third Law distribution**: the overlap is split in inverse proportion to mobility:

```
total_mob = mob_A + mob_B
if total_mob < ε: skip  # both stuck, nothing to do

share_A = mob_A / total_mob   # how much A absorbs
share_B = mob_B / total_mob   # how much B absorbs

direction = normalize(p_i − q)  # unit vector pointing A away from B

displacement_A += direction * overlap * share_A
displacement_B -= direction * overlap * share_B
```

If one trace is pinned against a wall (`mob ≈ 0`), the other gets ≈100% of the push.
If both are equally free, they each get 50%.

### 3d. Apply with Damping

After accumulating all pairwise interactions for a single iteration:

```
for each sample point p:
    p.position += accumulated_displacement[p] * DAMPING

DAMPING = 0.4  # conservative, prevents oscillation
MAX_STEP = 0.3 mm  # displacement cap per iteration
```

The displacement vector is clamped to `MAX_STEP` magnitude before application to prevent explosions.

### 3e. Hard Constraints

After displacement, each moved point is validated:

1. **Outline containment**: if point is outside the outline, project it onto the outline boundary.
2. **Obstacle avoidance**: if point is inside an obstacle, push it to the nearest obstacle boundary.
3. **Endpoint lock**: endpoints (index 0 and N-1 of each edge) are never moved.

### Step 4: Light Re-Smooth

2 iterations of the same Laplacian averaging (endpoints locked), with a reduced weight of 0.3. This removes small zigzag artifacts from discrete push corrections while preserving the overall position shift.

## Constants

| Name | Value | Rationale |
|------|-------|-----------|
| `PUSH_ITERATIONS` | 5 | Enough for convergence on typical boards |
| `PUSH_DAMPING` | 0.4 | Standard for force-directed layout (prevents oscillation) |
| `MAX_PUSH_STEP_MM` | 0.3 | Caps per-iteration displacement; prevents spikes |
| `MOBILITY_MARGIN_MM` | `max_half` (~5.0) | Fade zone for wall/obstacle proximity |
| `POST_SMOOTH_ITER` | 2 | Just enough to de-noise without losing the push effect |
| `POST_SMOOTH_WEIGHT` | 0.3 | Lighter than pre-smooth to preserve push results |

## Edge Cases

1. **Trace can't move at all** (both endpoints pinned, surrounded by walls): overlap stays, width computation in step 5 handles it by narrowing the trace. The push phase doesn't make things worse — it just can't help.

2. **Three-way squeeze** (A pushes B, which pushes C): naturally cascades over iterations. After 5 iterations with 0.4 damping, displacements propagate ~2 traces deep.

3. **Short edges** (≤ 2 samples): all points are endpoints → mobility = 0 → no movement. Width computation handles them as before.

4. **Same-net edges**: no repulsion between edges of the same net. They share the same polygon anyway.

## Changes Required

### `solver.py`

Add two new methods to `ConstraintSolver`:

- `_compute_mobility(edge, index)` → list of floats per sample
- `_push_shove(active_eids, index)` → mutates sample positions in-place

Modify `solve()` to call them between smooth and width computation.

### `graph.py`

No changes needed. `edge.samples` is already a mutable list.

### `spatial.py`

No changes needed. `EdgeIndex` already supports `nearby()`.

## What This Does NOT Do

- Does not add new sample points or resample edges.
- Does not move endpoints (pins, junctions, waypoints all stay fixed).
- Does not change the polygon builder or any downstream code.
- Does not add new dependencies.
- Does not affect edges that have no nearby foreign traces.

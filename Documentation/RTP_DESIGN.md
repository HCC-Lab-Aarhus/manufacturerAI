# Route-Time Placement (RTP) Design

## Problem

The current pipeline places **all** components before routing begins. Small series passives like resistors — which exist only to bridge two nets — are placed blind to actual trace paths. The placer guesses where they should go using proximity heuristics, then the router must detour to reach those guesses. This wastes routing space and increases trace length.

## Core Idea

**Defer placement of simple series passives to routing time.** Instead of placing a resistor and then routing two nets to/from it, treat the resistor as a *waypoint* inserted into a single logical route between the components it actually connects.

---

## 1. Component Classification

Classification is derived entirely from catalog properties. No component is special-cased by name or ID.

### 1.1 Route-Deferred (position decided at route time)

A component **instance** is route-deferred if its catalog entry satisfies all of:

| Criterion | Catalog property | Rationale |
|-----------|-----------------|-----------|
| Not user-positioned | `ui_placement == false` | User hasn't pinned it to the layout |
| Exactly 2 pins | `len(pins) == 2` | Series element — sits on a single logical path |
| Doesn't block routing | `mounting.blocks_routing == false` | Body doesn't obstruct other nets |
| All pins bidirectional | every pin has `direction == "bidirectional"` | Either pin can serve either net endpoint (non-polarised) |
| Small enough to fit inline | `max(body.width, body.length) ≤ routing_channel_threshold` | Body can be inserted along a trace segment |

The `routing_channel_threshold` is a config constant (e.g. 12mm) — large enough to accept axial resistors (11mm body) but reject bulky components.

### 1.2 Rotation-Deferred (rotation decided at route time)

A pre-placed component is rotation-deferred if it satisfies **any** of these sufficient conditions:

**Condition A — Circular body:**

| Criterion | Catalog property |
|-----------|-----------------|
| Circular body | `body.shape == "circle"` |

Rotation doesn't change the physical footprint or the 3D cutout. All 4 rotations are geometrically valid; the algorithm just needs to filter by electrical equivalence (see §1.3).

**Condition B — Internal-net pin symmetry (rectangular body):**

| Criterion | Catalog property |
|-----------|-----------------|
| Rectangular body | `body.shape == "rect"` |
| Has `internal_nets` | `len(internal_nets) > 0` |
| Every net-facing pin belongs to a shorted group | All pins that appear in any circuit net are covered by an `internal_nets` entry |

When pins on each electrical side are interchangeable (shorted together), the router can pick which physical pin to route to. Rotation rearranges which pin is closest to an approaching trace but doesn't change the electrical connectivity.

**Condition C — Square body:**

| Criterion | Catalog property |
|-----------|-----------------|
| Square body | `body.shape == "rect"` and `body.width == body.length` |

A square footprint is geometrically invariant under 90° rotation, so all 4 rotations preserve clearance constraints.

### 1.3 Rotation Equivalence Classes

Not all 4 rotations are always electrically valid. The system must compute **rotation equivalence classes** from the catalog:

```
COMPUTE_VALID_ROTATIONS(component, net_assignments):

  base_pin_map = {pin.id: net_id for each pin connected to a net}

  valid = []
  for θ in [0°, 90°, 180°, 270°]:
    rotated_pin_map = apply_rotation(θ, pins) → new positions
    # For each net, check if the set of pin positions reachable
    # by that net is unchanged (considering internal_nets as
    # equivalence groups):
    if net_connectivity_preserved(base_pin_map, rotated_pin_map,
                                   internal_nets):
      valid.append(θ)

  return valid
```

**`net_connectivity_preserved`** checks: for each net, the set of shorted-group IDs it connects to is the same before and after rotation. Individual pin IDs within a shorted group may swap — that's fine.

This naturally handles:

| Scenario | Valid rotations |
|----------|----------------|
| Non-polarised, 2 pins, circular body | {0°, 90°, 180°, 270°} |
| Polarised, 2 pins, circular body | {0°, 180°} or {90°, 270°} depending on pin axis |
| 4 pins, 2 shorted pairs, rectangular body | {0°, 90°, 180°, 270°} (all preserve group ↔ net mapping) |
| 4 pins, no internal nets, rectangular body | Typically {0°} only — not rotation-deferred |
| N pins, complex connectivity | Whatever the equivalence check produces |

### 1.4 Footprint Stability

When a rotation changes the bounding rectangle (e.g. 10×8mm → 8×10mm for a non-square rectangular body), the rotated footprint must be re-validated:
- Outline containment (body + keepout envelope)
- Clearance to neighbouring components

Rotations that fail these checks are pruned from the valid set for that specific instance's position. A circular body never fails this check. A square body never fails this check. A non-square rectangular body may have only {0°, 180°} valid at a tight position.

---

## 2. Modified Pipeline Flow

```
┌─────────────────────────────────────────────────────────┐
│  PLACER  (modified)                                      │
│                                                          │
│  1. Classify components → pre-placed vs route-deferred   │
│  2. Collapse nets through deferred components            │
│     (bat:V+ ─[net_A]─ res:1 ... res:2 ─[net_B]─ led:+) │
│     becomes logical net: bat:V+ ↔ led:+ (with waypoint) │
│  3. Place pre-placed components using collapsed nets     │
│  4. Output FullPlacement with deferred_components list   │
│     and collapsed_net_map                                │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│  ROUTER  (modified — new Phase 2.5 + Phase 4 changes)   │
│                                                          │
│  Phase 1: Grid setup (as before, but no cells blocked   │
│           for deferred components — they don't exist yet)│
│                                                          │
│  Phase 2: Voronoi pin proximity (pre-placed pins only)  │
│                                                          │
│  Phase 2.5 [NEW]: Logical net expansion                 │
│     Expand collapsed nets back to physical net pairs     │
│     with deferred component metadata attached            │
│                                                          │
│  Phase 3: Net ordering                                   │
│     Logical nets (with deferred waypoints) sorted with   │
│     their full fanout weight                             │
│                                                          │
│  Phase 4: Negotiation loop (modified)                   │
│     For each net:                                       │
│       IF net has a deferred waypoint:                   │
│         → INSERTION ALGORITHM (see §3)                  │
│       ELSE:                                             │
│         → Standard A* routing                           │
│                                                          │
│  Phase 4.5 [NEW]: Post-route rotation for position-     │
│     fixed rotation-deferred components                  │
│                                                          │
│  Phase 5-6: Retry + output (as before)                  │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Insertion Algorithm (Phase 4 detail)

For a logical net  `src_pin ──[deferred R]── dst_pin`:

```
INSERTION_ALGORITHM(src_pin, dst_pin, deferred_component):

  1. SCOUT ROUTE
     Run A* from src_pin to dst_pin, ignoring the deferred component
     entirely. This produces a candidate centreline path P.

  2. FIND CANDIDATE SEGMENTS
     Walk P and collect every maximal straight segment S_i
     whose length ≥ component.pin_spacing + 2 × grid_resolution.
     (The component must fit collinear with the segment.)

  3. SCORE EACH CANDIDATE
     For each segment S_i, for each candidate position t along S_i
     (sliding at grid_resolution steps):

       a. Compute tentative component center and rotation
          (aligned with segment direction: 0° or 90°)
       b. Compute world pin positions for both orientations
          (pin 1 ↔ src vs pin 1 ↔ dst — pick whichever is shorter)
       c. CHECK CONSTRAINTS:
          - Body + keepout inside outline
          - No overlap with pre-placed components (Chebyshev gap)
          - Pin clearance to foreign pins ≥ min_pin_clearance
          - Body doesn't cover any foreign pin pad
       d. SCORE:
          - Total trace length: dist(src, pin_near) + dist(pin_far, dst)
            (should be minimal — close to scout route length)
          - Congestion at body location (from congestion grid)
          - Distance to nearest foreign net trace (maximise)
          - Whether insertion avoids adding trace turns

  4. SELECT BEST
     Pick candidate with lowest score. If no valid candidate exists,
     fall back to placing the component at the midpoint of the scout
     route (perpendicular offset if needed) and routing two separate
     nets traditionally.

  5. COMMIT
     a. Record component placement (x, y, rotation, pin_positions)
     b. Block body cells + keepout on grid
     c. Update Voronoi map with new pin positions
     d. Route segment 1: src_pin → near_pin (sub-path of scout route)
     e. Route segment 2: far_pin → dst_pin (sub-path of scout route)
     f. Commit both trace segments normally (block + clearance)
```

---

## 4. Net Collapsing in the Placer

When the placer encounters a deferred resistor `R` that bridges `net_A` and `net_B`:

```
Before collapsing:
  net_A: [bat:V+, R:1]
  net_B: [R:2, led:anode]

After collapsing:
  logical_net_AB: [bat:V+, led:anode]
  deferred_waypoints: {logical_net_AB: (R, net_A, net_B)}
```

**Effect on placer scoring**: `bat` and `led` now appear directly connected. The placer's `NetEdge` graph and connectivity scoring pull them together appropriately — without having to guess where `R` sits. The result is that the pre-placed components end up in better relative positions for the eventual route.

For chains (e.g., `VCC → R1 → R2 → LED`), collapse transitively: all endpoints merge into one logical net with an ordered waypoint list.

---

## 5. Post-Route Rotation (Phase 4.5)

For all **rotation-deferred** components (as classified in §1.2):

```
POST_ROUTE_ROTATION(component, routed_traces):

  1. Compute valid_rotations via COMPUTE_VALID_ROTATIONS (§1.3),
     using the component's catalog entry and its net assignments.
  2. Collect all traces connected to this component's pins
     (or pin groups, for components with internal_nets).
  3. For each electrical connection point, determine the trace
     approach direction (direction of the first/last segment
     touching that pin or pin group).
  4. For each θ in valid_rotations:
     a. Compute world pin positions under θ.
     b. For components with internal_nets (shorted pin groups):
        select the nearest pin from each shorted group to the
        approaching trace endpoint.
     c. Sum the angular deviation between each selected pin's
        position vector (from component center) and its trace
        approach direction.
     d. If body.shape == "rect" and body is non-square:
        verify the rotated footprint still satisfies placement
        constraints (outline containment, neighbour clearance).
        Skip this θ if violated.
  5. Select the rotation that minimises total angular deviation
     (and trace detour to reach selected pins).
  6. Re-route the final grid segments (just the last few cells
     entering the pin) to match the updated pin positions.
```

### How Each Body Type Behaves

These are not special cases — they fall out naturally from the general algorithm:

**Circular body** (`body.shape == "circle"`): Step 4d is always satisfied — rotation never changes the footprint. All electrically-valid rotations compete purely on trace alignment.

**Square rectangular body** (`body.width == body.length`): Same as circular — footprint is invariant under 90° rotation. All electrically-valid rotations compete on trace alignment.

**Non-square rectangular body**: 0°/180° preserve the original footprint; 90°/270° swap width and length. Step 4d filters out 90°/270° when the swapped rectangle violates constraints at that position. When they do fit, they compete on trace alignment alongside 0°/180°.

**Components with internal_nets**: Step 4b lets the router pick the nearest physical pin within each shorted group. This reduces stub length regardless of pin count — works for 4-pin components with 2 shorted pairs, 6-pin components with 3 pairs, etc.

**Components without internal_nets**: Step 4b reduces to a direct pin lookup. Rotation freedom comes only from the electrically-valid set (§1.3), which may be just {0°} for fully-polarised multi-pin components.

---

## 6. Joint GPIO Allocation + Passive Placement

When a deferred resistor connects an allocatable MCU pin group to a fixed component:

```
net_A: [mcu_1:gpio, R:1]
net_B: [R:2, led:anode]
```

The current router already chooses the best GPIO pin from the pool. With RTP, this becomes a **joint optimisation**:

```
JOINT_ALLOCATION(gpio_pool, deferred_R, dst_pin):

  For each candidate gpio_pin in pool:
    1. Run scout route: gpio_pin → dst_pin
    2. Run insertion algorithm for R along scout route
    3. Record total_cost = trace_length + congestion + ...

  Select (gpio_pin, R_position) pair with lowest total_cost.
```

This avoids the current sequential problem where the GPIO pin is allocated first (by proximity to a component that hasn't been placed yet), and the resistor is placed somewhere that forces a detour.

---

## 7. Additional Optimisations Enabled by RTP

| Optimisation | Description |
|---|---|
| **Keepout reclamation** | Deferred components don't reserve keepout during placement. The placer has more space for pre-placed components, potentially fitting designs that currently fail. |
| **Congestion relief** | The placer's congestion grid is more accurate — it doesn't include phantom congestion from unplaced passives. This improves SA refinement quality. |
| **Chain insertion** | Multiple series passives on the same logical path (e.g., R + ferrite bead) are placed as a chain along one scout route, maintaining alignment and minimising total path length. |
| **Body-over-trace** | Since deferred components have `blocks_routing: false`, their bodies can hover above already-routed foreign traces. The insertion algorithm only checks *pin* clearance to foreign nets, not body overlap with traces. This allows denser layouts. |
| **Retry flexibility** | During rip-up & retry (Phase 5), a deferred component can be *repositioned* along a re-routed path. Pre-placed components can't move during retry — deferred ones can, giving the router an extra degree of freedom. |
| **Pin-swap for non-polarised** | For route-deferred components where all pins are bidirectional, the insertion algorithm tests both pin assignments (pin 1↔src or pin 1↔dst) and picks whichever produces a shorter or less congested route. |
| **Diagonal bridging** | A deferred component can bridge an L-shaped route at the corner: one pin connects to the horizontal segment, the other to the vertical segment, with the component body placed diagonally across the turn. This saves trace length vs. routing around a placed component. |
| **Shorted-group pin selection** | For any component with `internal_nets` entries, rotation-deferral lets the router choose which physical pin on each shorted side it routes to — always picking the nearest, reducing stub length. |

---

## 8. Data Model Changes

**New fields on `FullPlacement`:**

```python
@dataclass
class FullPlacement:
    components: list[PlacedComponent]          # pre-placed only
    deferred: list[DeferredComponent]          # NEW
    collapsed_nets: dict[str, CollapsedNet]    # NEW
    outline: Outline
    nets: list[Net]
    enclosure: EnclosureSpec

@dataclass
class DeferredComponent:
    instance_id: str
    catalog_id: str
    original_nets: tuple[str, str]   # (net_A_id, net_B_id)
    logical_net_id: str              # collapsed net they belong to

@dataclass
class CollapsedNet:
    logical_net_id: str
    endpoint_pins: list[str]         # pins from pre-placed components
    waypoints: list[DeferredComponent]  # ordered series passives
    original_net_ids: list[str]      # nets that were merged
```

**New fields on `RoutingResult`:**

```python
@dataclass
class RoutingResult:
    traces: list[Trace]
    placed_deferred: list[PlacedComponent]     # NEW — fully resolved
    pin_assignments: dict
    failed_nets: list[str]
    rotations_adjusted: dict[str, float]       # NEW — post-route rotation updates
```

---

## 9. Failure Modes & Fallbacks

| Failure | Fallback |
|---------|----------|
| No valid insertion point found along scout route | Place deferred component at midpoint of scout route with perpendicular offset; route two nets conventionally |
| Scout route itself fails | Defer to standard placement: run the placer's candidate generator for this component using current grid state, place it, then route both nets normally |
| Joint GPIO+passive allocation finds no valid pair | Allocate GPIO pin first (existing strategy), then attempt insertion; if that fails, place component via placer fallback |
| Chain of deferred components too long for any straight segment | Place components individually with spacing, falling back incrementally |
| Post-route rotation fails constraint check (rectangular body doesn't fit) | Keep original placer rotation; re-route last segments to original pin positions |

---

## 10. Net Ordering Impact

Logical nets that contain deferred waypoints should be routed **after** their endpoint components' other nets are partially routed. Reasoning: the scout route benefits from seeing where neighbouring traces already went, so the insertion point avoids congestion. However, they should not be routed *last* — they need routing room.

Proposed heuristic for ordering score:

$$S_{net} = \alpha \cdot \text{fanout} + \beta \cdot \text{has\_deferred} + \gamma \cdot \text{allocatable\_pins}$$

where $\beta$ is a mild positive penalty (e.g., +2) that pushes deferred-waypoint nets slightly later in the order, but not to the end.

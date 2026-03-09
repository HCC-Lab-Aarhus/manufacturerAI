# SCAD Refactor Plan: Per-Component Dynamic Generation

## Background

The old SCAD pipeline is monolithic — all component geometry knowledge is hardcoded in `cutouts.py` as Python if/else branches dispatching on `mounting.style`. Adding or changing a component's 3D shape means editing central Python code. Battery hatch geometry, LED hole sizing, button cap clearance — all baked into one file. Pin shapes are fixed to circular holes. Nothing is data-driven or extensible per-component.

The new architecture makes each component responsible for declaring its own SCAD geometry contributions, driven by catalog data.

### What stays unchanged

| Module | Reason |
|---|---|
| `compiler.py` | OpenSCAD CLI wrapper, independent of SCAD content |
| `outline.py` | Tessellating the outline polygon is component-independent |
| `layers.py` | Shell body generation only uses outline + enclosure + height field (see invariants below) |
| `generator.py` **artifact-reading block** | Loading placement.json, routing.json, design.json, catalog from session |

### `layers.py` polyhedron invariants

The variable-height shell is a single OpenSCAD `polyhedron()` built from stacked vertex rings. Each ring has exactly `N` vertices (one per footprint edge), so the face index table is a regular grid of triangulated quads. This regularity imposes two structural invariants that **must** be preserved:

1. **Vertex uniqueness across rings.** No two rings may contain vertices at the same `(x, y, z)` position. When CGAL 4.x converts a `polyhedron()` to a Nef polyhedron for boolean `difference()`, coincident vertices from separate rings create a mixed-topology half-edge structure that triggers assertion failures in `SNC_FM_decorator.h` (specifically `target(s1).vertex()==target(s2).vertex()`). The mesh itself is valid — watertight, manifold, consistent winding — but CGAL's exact-arithmetic kernel cannot decompose it.

2. **Minimum wall gap (`_MIN_WALL_GAP = 0.2 mm`).** The bottom-profile's last ring sits at `z = bot_size` with inset 0 (full-width polygon). The top-profile's first ring sits at `z = top_zs[i] − eff_ts` with inset 0 for vertex `i`. When `top_zs[i] − bot_size < top_size`, the effective top-profile size `eff_ts` is compressed so that these two rings remain separated by at least 0.2 mm vertically. The calculation:

   ```python
   avail  = max(top_zs[i] - last_bot_z, _MIN_WALL_GAP)
   eff_ts = max(min(top_size, avail - _MIN_WALL_GAP), 0.01)
   ```

   Without this gap, vertices where the ceiling is barely above the bottom profile would land at the exact same Z as the bottom ring, violating invariant 1. This is a per-vertex calculation — some vertices may have full-size top profiles while others are heavily compressed, all within the same ring.

These invariants are invisible when the shell is uniform-height (all `top_zs` identical and well above `bot_size + top_size`), but become critical in variable-height shells where some outline vertices have ceilings close to the base.

---

## Step 1 — Catalog Schema Extensions

**Goal:** Make component geometry data-driven by extending the catalog JSON schema and Python models.

### 1a. Add `PinShape` to catalog models

Extend `Pin` with an optional `shape` field. Components like the battery holder can specify wide rectangular contact pads instead of only circular holes.

```jsonc
{
  "id": "V+",
  "position_mm": [0, 27.0],
  "hole_diameter_mm": 1.2,
  "shape": {
    "type": "rect",        // "circle" | "rect" | "slot"
    "width_mm": 1.5,
    "length_mm": 3.0
  }
}
```

Missing `"shape"` → defaults to `{ "type": "circle" }` using `hole_diameter_mm`.

**Files:**
- `src/catalog/models.py` — add `PinShape` dataclass, add `shape: PinShape | None` to `Pin`
- `src/catalog/loader.py` — parse `shape` from JSON (optional, default None)

### 1b. Add `scad` property to catalog models

Each catalog JSON gets a new top-level `"scad"` field — a dict keyed by mounting style, describing the SCAD fragments that component contributes when placed in that style:

```jsonc
// catalog/battery_holder_2xAAA.json
{
  "id": "battery_holder_2xAAA",
  // ...existing fields...
  "scad": {
    "bottom": {
      "body_pocket": { /* parametric shape descriptor */ },
      "floor_opening": { /* hatch cutout descriptor */ },
      "ledge_recesses": { /* ledge geometry */ },
      "pin_bridges": { /* slot geometry from pins to pocket */ },
      "additions": []   // geometry added TO the shell (e.g. snap-fit tabs)
    }
  }
}
```

Components without a `"scad"` field fall back to generic geometry derived from `body.*` + `mounting.*` (equivalent to current behavior).

**Files:**
- `src/catalog/models.py` — add `scad: dict | None` to `Component`
- `src/catalog/loader.py` — pass through `scad` from JSON (optional, default None)

### 1c. Update catalog JSON files

Add `"scad"` and pin `"shape"` fields to catalog entries. Can be done incrementally — missing fields use defaults.

**Files:**
- `catalog/*.json` — add fields per component (deferred until resolver geometry is designed)

---

## Step 2 — ScadFragment Dataclass

**Goal:** Define the universal data structure for geometry contributions.

A `ScadFragment` represents a single geometry contribution from a component, trace, or pinhole:

```python
@dataclass
class ScadFragment:
    type: str           # "cutout" | "addition"
    geometry: ...       # shape descriptor (rect, cylinder, polygon, etc.)
    z_base: float
    depth: float
    label: str
```

- **cutout** fragments get subtracted from the shell (`difference()`)
- **addition** fragments get unioned onto the shell (`union()`) — e.g. snap-fit rails, retention clips

**Files:**
- `src/pipeline/scad/fragment.py` — new file with `ScadFragment` and geometry descriptor types

---

## Step 3 — Resolver Infrastructure

**Goal:** Build the per-component resolver pattern that replaces the monolithic cutout dispatch.

### 3a. Base resolver and registry

```
src/pipeline/scad/resolvers/
    __init__.py          # registry: catalog_id → resolver function
    base.py              # BaseResolver — shared helpers (pocket, pinhole defaults)
    generic.py           # fallback: derives geometry from body.shape + mounting.style
```

Each resolver:
- Takes `(PlacedComponent, Component, context)` where context carries enclosure dims, outline, height field
- Reads the catalog `scad` descriptor for the active mounting style
- Returns `list[ScadFragment]`

The registry looks up by `catalog_id` first; if no specific resolver is registered, falls back to `generic.py` which produces the same geometry as the old code.

### 3b. Component-specific resolvers

One file per component (or component family) that needs custom geometry beyond what generic provides:

- `resolvers/battery_holder.py` — hatch floor opening, ledge recesses, pin bridge slots, snap-fit additions
- `resolvers/led.py` — surface hole (circle) + body pocket
- `resolvers/button.py` — cap hole + body pocket
- (more as needed)

**Files:**
- `src/pipeline/scad/resolvers/__init__.py`
- `src/pipeline/scad/resolvers/base.py`
- `src/pipeline/scad/resolvers/generic.py`
- `src/pipeline/scad/resolvers/battery_holder.py`
- `src/pipeline/scad/resolvers/led.py`
- `src/pipeline/scad/resolvers/button.py`

---

## Step 4 — Trace & Pinhole Extraction

**Goal:** Extract trace channels and pinhole logic from old `cutouts.py` into a standalone module that produces `ScadFragment` objects.

- Trace channels: one fragment per routed segment (same logic as old `_trace_channels`)
- Pinholes: shaft + taper per pin, now using `pin.shape` for appropriate geometry (rect pad vs. circular hole)

**Files:**
- `src/pipeline/scad/traces.py` — `build_trace_fragments()` and `build_pinhole_fragments()` returning `list[ScadFragment]`

---

## Step 5 — New Emitter

**Goal:** Replace `emit.py` to accept `list[ScadFragment]` instead of `list[Cutout]`.

The new emitter receives:
- Shell body lines (from `layers.py`)
- `list[ScadFragment]` with `type="cutout"` → assembled into `difference()` block
- `list[ScadFragment]` with `type="addition"` → assembled into `union()` block

The Shapely merge optimization (grouping cutouts by z-layer and merging polygons) remains for performance.

**Files:**
- `src/pipeline/scad/emit.py` — rewritten

---

## Step 6 — New Generator Orchestration

**Goal:** Rewrite `generator.py` to use the resolver pattern while keeping the artifact-reading and shell-body logic intact.

New flow:

```
1. Read artifacts                              ← KEEP (unchanged)
2. Tessellate outline                          ← KEEP (outline.py)
3. Compute height field                        ← KEEP (from design module)
   Height values must satisfy: for every footprint vertex i,
   top_zs[i] ≥ bot_size + _MIN_WALL_GAP (0.2 mm).
   This is enforced inside layers.py via eff_ts compression,
   but the height field should avoid producing values that
   collapse the straight-wall section to near zero.
4. Build shell body lines                      ← KEEP (layers.py)
5. For each placed component:                  ← NEW
     a. Look up resolver (catalog_id → specific, or fallback to generic)
     b. Get effective mounting style
     c. Read catalog scad descriptor for that style
     d. Resolver produces list[ScadFragment]
6. Build trace channel fragments               ← EXTRACTED from old cutouts.py
7. Build pinhole fragments using pin.shape     ← MODIFIED (uses new pin shapes)
8. Assemble with new emit.py                   ← MODIFIED emitter
9. Write .scad + optional STL compile          ← KEEP
```

**Files:**
- `src/pipeline/scad/__init__.py` — package init, exports `run_scad_step`
- `src/pipeline/scad/generator.py` — new orchestrator
- `src/pipeline/scad/compiler.py` — copied from old, unchanged
- `src/pipeline/scad/outline.py` — copied from old, unchanged
- `src/pipeline/scad/layers.py` — copied from old, unchanged.  Preserves the `_MIN_WALL_GAP` ring-separation invariant and per-vertex `eff_ts` compression required by OpenSCAD 2021.01 / CGAL 4.11

---

## Step 7 — Placer & Router Awareness

**Goal:** Update placer and router to use pin shape data for keepout/clearance calculations.

### Placer
- Account for non-circular pin keepout zones from `pin.shape`
- Use `scad.{style}` descriptors to know about floor openings (e.g. battery hatch shouldn't overlap another component's floor space)

### Router
- Pin shapes affect trace landing pads — a rectangular pin needs different clearance than a circular one
- Read `pin.shape` when computing pad geometry

**Files:**
- `src/pipeline/placer/` — update keepout/clearance logic
- `src/pipeline/router/` — update pad geometry logic

---

## Step 8 — Retire Old Code

**Goal:** Remove old SCAD modules once the new pipeline is validated.

| Old file | Status |
|---|---|
| `old scad/cutouts.py` | Retired — logic distributed into resolvers + traces.py |
| `old scad/emit.py` | Retired — replaced by new emit.py |
| `old scad/generator.py` | Retired — replaced by new generator.py |
| `old scad/compiler.py` | Retired — copied unchanged into new location |
| `old scad/outline.py` | Retired — copied unchanged into new location |
| `old scad/layers.py` | Retired — copied unchanged into new location |

---

## Decisions Deferred

1. **Exact descriptor format** — what goes inside `catalog.scad.bottom.body_pocket` etc. (declarative JSON shapes vs. something more expressive)
2. **Specific SCAD geometry** per component — the actual shapes, clearances, and parameters for each resolver
3. **Structured fragments vs. raw SCAD strings** — structured is safer for merge/optimization, but some components may need escape hatches
4. **Addition geometry details** — snap-fit tabs, retention rails, clips that get `union()`ed onto the shell

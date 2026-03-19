# 2D CSG Shape Refactor — Pipeline Impact Analysis

## Overview

Replace the raw-vertex outline with a **2D CSG (Constructive Solid Geometry)** shape definition. The LLM builds shapes from boolean operations on 2D primitives (rectangle, ellipse, polygon). The system tessellates the CSG tree into the existing `Outline` vertex list, so the entire downstream pipeline continues working on vertex arrays.

### Why

The current system asks the LLM to draw shapes by listing clockwise `(x, y)` vertices. This works for simple shapes (4–6 vertices) but degrades on organic forms — the firefly tree needed 17 hand-placed vertices, and on iteration the LLM dropped the enclosure and all LED placements because the outline consumed too much cognitive budget.

CSG lets the LLM think compositionally: "mushroom = ellipse cap + rectangle stem" instead of "11 vertices at these exact coordinates." Modification is also trivial: "make the cap wider" changes one radius instead of recalculating 5+ vertices.

### What Changes

| Layer | Change | Scope |
|---|---|---|
| **LLM input** (`shape`) | New 2D CSG tree format | New field alongside `outline` |
| **Tessellator** | New module: CSG → `Outline` | New file |
| **Parsing** | Accept `shape` or `outline` | Small addition |
| **Validation** | Validate CSG tree structure | New function |
| **design.json** | Stores both `shape` (source) and `outline` (derived) | Additive |
| **Downstream pipeline** | **No changes** | Zero |
| **Frontend** | **No changes** | Zero |

---

## Architecture: The Tessellation Boundary

The refactor introduces a single clean boundary:

```
LLM → shape (CSG tree)
         ↓
    tessellate_shape()      ← new module
         ↓
    Outline (vertex list)   ← existing type, unchanged
         ↓
    Everything downstream   ← unchanged
```

The `shape` field is the **source of truth** for the LLM. The `outline` field is the **derived polygon** consumed by validation, height field, SCAD, placer, router, and frontend. Both are stored in `design.json` — the shape for re-editing, the outline for pipeline consumption.

This means:
- `parse_physical_design()` always produces an `Outline` object — either from `data["outline"]` directly (backward compat) or from tessellating `data["shape"]`
- Every downstream consumer sees the same `Outline` it sees today
- The LLM never needs to see or produce raw vertices

---

## Per-Primitive z_top and z_bottom

Each CSG primitive can carry optional `z_top` and `z_bottom` values. During tessellation, every output vertex inherits the z value from the primitive it came from.

### How It Works

```json
{
  "shape": {
    "op": "union",
    "children": [
      {"type": "rectangle", "center": [48, 110], "size": [28, 60], "corner_radius": 8},
      {"type": "ellipse", "center": [6, 56], "radius": [18, 14], "z_top": 26},
      {"type": "ellipse", "center": [6, 104], "radius": [18, 14], "z_top": 26}
    ]
  }
}
```

The trunk rectangle inherits `z_top` from the enclosure default (no override). The branch ellipses have `z_top: 26` — every vertex produced by tessellating those ellipses carries `z_top = 26`.

### How z_top Flows Through the Pipeline Today

The existing height field system uses **IDW (inverse distance weighting)** interpolation from vertex z_top values. At any point `(x, y)` inside the polygon, the ceiling height is computed as a weighted average of nearby vertices, weighted by `1/d⁴`. This means:

1. Vertices near a branch tip (with `z_top: 26`) dominate the height at that tip
2. Vertices on the trunk (with `z_top` inherited from enclosure default, e.g. 16mm) dominate the trunk interior
3. The transition zone between trunk and branch gets a smooth blend

This works naturally with tessellated CSG output. A branch ellipse tessellated into ~20 vertices all carrying `z_top: 26` produces the same IDW result as the current 2–3 hand-placed vertices with `z_top: 26`, but with better spatial coverage and smoother boundaries.

### z_top Resolution at Union Boundaries

When two CSG primitives are unioned and share a boundary, the tessellated boundary vertices may have conflicting z_top values (one from each primitive). Resolution rule:

**At union boundaries, take `max(z_top)`.** This matches the physical intuition — if a branch rises to 26mm and meets the trunk at 16mm, the union boundary should be at 26mm (the branch rises above the trunk).

For `z_bottom`, the same rule applies: **take `max(z_bottom)`** at union boundaries, since raised floor regions override flat regions.

### Impact on Downstream Consumers

| Consumer | Uses z_top? | Uses z_bottom? | CSG Impact |
|---|---|---|---|
| `height_field._interpolate_vertex_heights()` | Yes — IDW over all vertices | — | More vertices = finer interpolation, same algorithm |
| `height_field._interpolate_vertex_bottom_heights()` | — | Yes — IDW over all vertices | Same as above |
| `height_field.sample_height_grid()` | Yes — calls `blended_height()` | — | No change — grid resolution is independent of vertex count |
| `validation.validate_physical_design()` | Yes — checks each vertex `z_top >= min_required` | — | Iterates more vertices, same logic |
| `router/grid.py` | — | Yes — quick check for any `z_bottom > 0` | Iterates more vertices, same logic |
| `placer/engine.py` | — | Yes — checks `any(z_bottom)` | Same |
| `placer/feasibility.py` | — | Yes — raised-floor masking | Same |
| `scad/generator.py` | Yes — via `blended_height()` | Yes — via `blended_bottom_height()` | No change |
| `scad/layers.py` | Indirect — pre-computed arrays | Indirect — pre-computed arrays | No change |
| `web/routes/_deps.py` | Yes — samples grids | Yes — samples grids | No change |

**No downstream consumer needs modification.** They all operate on the `Outline` object and its vertices, which remain identical in structure.

---

## Detailed File-by-File Impact

### New Files

#### `src/pipeline/design/shape2d.py` — 2D CSG Tessellator

New module. Responsibilities:
- Parse a CSG tree dict into an internal representation
- Evaluate boolean operations using Shapely (already a dependency, v2.1.2)
- Track per-primitive z_top/z_bottom through operations
- Output an `Outline` with properly attributed vertices

Primitives:
- `rectangle` → `center: [x, y]`, `size: [w, h]`, optional `corner_radius`
- `ellipse` → `center: [x, y]`, `radius` (scalar or `[rx, ry]`)
- `polygon` → `points: [[x,y], ...]` (raw vertices, fallback/advanced use)

Operations: `union`, `difference`, `intersection` — direct mapping to Shapely.

z_top/z_bottom tracking approach:
- Each tessellated vertex remembers which primitive it originated from
- After boolean ops, Shapely returns new boundary vertices — need to attribute them
- Shapely's `union()` etc. produce coordinate tuples; for each output vertex, find the nearest input primitive and inherit its z values
- At boundaries between primitives with different z, use `max()` (for z_top) or `max()` (for z_bottom)

Output: standard `Outline(points=[OutlineVertex(...), ...])` — identical to what `_parse_outline()` produces today.

#### `src/pipeline/design/validation_shape2d.py` — CSG Validation

New module (or section in existing `validation.py`). Validates:
- CSG tree structure: every node has `type` (primitive) or `op` (operation)
- Primitives have required fields (center, size/radius)
- Operations have ≥2 children
- Numeric values are positive
- Build plate bounds (from bounding box of resulting polygon)
- Result is a valid polygon (non-empty, non-degenerate after booleans)

### Modified Files

#### `src/agent/tools.py` — Tool Schema

Replace the `outline` field in `submit_design` with a `shape` field:

```python
"shape": {
    "type": "object",
    "description": "2D CSG shape tree defining the device silhouette.",
    "properties": {
        "type": {"type": "string", "description": "'rectangle', 'ellipse', or 'polygon'"},
        "op": {"type": "string", "description": "'union', 'difference', or 'intersection'"},
        "children": {"type": "array", "items": {"type": "object"}},
        "center": {"type": "array", "items": {"type": "number"}, "description": "[x, y] in mm"},
        "size": {"type": "array", "items": {"type": "number"}, "description": "[width, height] in mm (rectangle)"},
        "corner_radius": {"type": "number", "description": "Corner rounding radius in mm (rectangle)"},
        "radius": {"description": "Radius: number for circle, [rx, ry] for oval (ellipse)"},
        "points": {"type": "array", "description": "Vertex list [[x,y], ...] (polygon)"},
        "z_top": {"type": "number", "description": "Ceiling height for this primitive"},
        "z_bottom": {"type": "number", "description": "Floor height for this primitive"},
    },
}
```

Keep `outline` in `update_design` as read-only context (the LLM can see the derived vertices but edits via `shape`).

The `submit_design` required fields change: `["device_description", "shape", "enclosure", "ui_placements"]`.

#### `src/pipeline/design/parsing.py` — `parse_physical_design()`

Add shape→outline tessellation path:

```python
def parse_physical_design(data: dict) -> PhysicalDesign:
    if "shape" in data:
        outline = tessellate_shape(data["shape"])
    else:
        outline = _parse_outline(data["outline"])
    return PhysicalDesign(
        outline=outline,
        enclosure=_parse_enclosure(data.get("enclosure") or {}),
        ui_placements=_parse_ui_placements(data.get("ui_placements", [])),
        device_description=data.get("device_description", ""),
    )
```

The downstream `PhysicalDesign` object is unchanged — it still has an `Outline` field.

#### `src/agent/core.py` — `_tool_submit_design()`

After validation, tessellate+store both the CSG source and derived outline:

```python
def _tool_submit_design(self, input_data: dict):
    # ... parse & validate as before ...
    
    # Store both shape (source) and outline (derived) in design.json
    save_data = dict(input_data)
    if "shape" in input_data:
        save_data["outline"] = [v.to_dict() for v in physical.outline.points]
    
    self.session.write_artifact("design.json", save_data)
```

This means design.json contains:
- `shape` — the CSG tree (what the LLM wrote, what it edits)
- `outline` — the derived vertex list (what the pipeline consumes)
- `enclosure`, `ui_placements`, `device_description` — unchanged

#### `src/agent/core.py` — `_tool_update_design()`

The existing merge logic handles `shape` naturally — if the LLM sends a new `shape`, it replaces the old one. If not sent, the old shape is kept. After merging, re-tessellate:

```python
if "shape" in input_data:
    merged["shape"] = input_data["shape"]
# After merge, re-derive outline from shape
if "shape" in merged:
    merged["outline"] = [v.to_dict() for v in tessellate_shape(merged["shape"]).points]
```

#### `src/agent/prompt.py` — System Prompt

Replace the "Outline (Device Shape)" section and vertex documentation with CSG shape documentation. Key sections:

1. **Primitives**: rectangle, ellipse, polygon — with center, size, radius, corner_radius
2. **Operations**: union, difference, intersection — nested tree
3. **Per-primitive z_top**: how height varies across the shape
4. **Examples**: mushroom (2 primitives), VR controller (2 primitives), tree with branches (9 primitives), remote with notch (difference)

The prompt shrinks significantly — CSG examples are more concise than vertex-list examples, because each example conveys more shape information in fewer tokens.

#### `src/pipeline/design/validation.py` — `validate_physical_design()`

The existing validation already works on the `Outline` object (post-tessellation). No structural changes needed. The validation of z_top per vertex, polygon validity, build plate bounds, UI placement containment — all operate on the derived outline.

Add a pre-tessellation validation step for the CSG tree itself (valid types, required fields, positive dimensions). This can be a separate function called before tessellation.

#### `src/web/routes/_deps.py` — Shape field enrichment

The `_get_shape_fields()` function reads `outline_data = data.get("outline", [])`. Since design.json will contain both `shape` and `outline`, this continues to work with zero changes — it reads the derived `outline`.

### Unchanged Files (no modifications needed)

| File | Why unchanged |
|---|---|
| `src/pipeline/design/models.py` | `Outline`, `OutlineVertex` — unchanged types |
| `src/pipeline/design/height_field.py` | Operates on `Outline` — same input |
| `src/pipeline/design/serialization.py` | Uses `OutlineVertex.to_dict()` — same |
| `src/pipeline/scad/generator.py` | Consumes `Outline` + `Enclosure` — same |
| `src/pipeline/scad/outline.py` | Bézier expansion of `Outline.points` — same |
| `src/pipeline/scad/layers.py` | Pre-computed arrays — no outline access |
| `src/pipeline/scad/buttons.py` | Height functions — same |
| `src/pipeline/scad/resolver.py` | Component z calculations — same |
| `src/pipeline/router/grid.py` | Outline polygon + z_bottom — same |
| `src/pipeline/placer/engine.py` | Shapely polygon from vertices — same |
| `src/pipeline/placer/feasibility.py` | Outline parsing + z_bottom — same |
| `src/pipeline/circuit/validation.py` | No outline usage |
| `src/web/routes/design.py` | Reads outline xy for placement validation — same |
| `src/web/routes/circuit.py` | Passes outline as context string — same |
| `src/web/routes/manufacture/*.py` | All read derived outline — same |
| `src/session.py` | `invalidate_design_smart()` keys on ui_placements — same |
| **Frontend (all files)** | Reads `outline` array from API — same |

---

## Easing at the Tessellation Boundary

Current system: each `OutlineVertex` has `ease_in` / `ease_out` for Bézier corner smoothing. CSG tessellation produces already-smooth curves (ellipses have smooth perimeters, rectangles use `corner_radius` for rounding).

**Decision: set `ease_in = ease_out = 0` on all tessellated vertices.**

The existing `_bezier_expand_outline()` function already handles this: when `ease_in == 0 and ease_out == 0`, it copies the vertex as-is. So the Bézier expansion pass becomes a no-op after CSG tessellation, which is correct — the tessellator already produced the smooth polygon.

This means `ease_in`/`ease_out` are irrelevant in the CSG path. They only matter for the fallback raw-vertex `outline` path (backward compat).

---

## design.json Schema (Post-Refactor)

```json
{
  "device_description": "A mushroom-shaped night light...",
  "shape": {
    "op": "union",
    "children": [
      {"type": "ellipse", "center": [48, 35], "radius": [47, 35]},
      {"type": "rectangle", "center": [48, 100], "size": [40, 60], "corner_radius": 7}
    ]
  },
  "outline": [
    {"x": 1.0, "y": 35.0},
    {"x": 1.2, "y": 32.1},
    ...
  ],
  "enclosure": {
    "height_mm": 17.5,
    "top_surface": { "type": "dome", ... }
  },
  "ui_placements": [
    {"instance_id": "led_top", "catalog_id": "led_5mm", "x_mm": 48, "y_mm": 22},
    ...
  ]
}
```

- `shape` — what the LLM authored (CSG tree), what `update_design` edits
- `outline` — derived by tessellation, consumed by everything downstream
- Both stored so the LLM can re-read its own CSG definition, while the pipeline never needs to re-tessellate

---

## Tessellation Detail Level

Shapely's buffer/unary_union operations produce polygon approximations. The resolution matters:

- **Ellipses**: tessellated to N-gon. Shapely's `Point.buffer(r, resolution=N)` uses `N` quarter-circle segments, so `resolution=16` gives 64 vertices per circle. For typical device sizes (20–100mm), this is smooth enough.
- **Rectangles with corner_radius**: use `box().buffer(r).intersection(box_with_margin)` or build directly. 4 arcs × `resolution` segments + 4 straight edges.
- **Boolean results**: Shapely simplifies collinear segments. Output vertex count is typically manageable (20–80 vertices for 2–5 primitive combinations).

The `Outline` objects today have 4–17 vertices. CSG tessellation will produce 20–80. This is well within tolerance for all downstream consumers — the height field IDW interpolation, SCAD generation, and placer containment tests all scale linearly with vertex count and handle hundreds of vertices without issue.

---

## Backward Compatibility

The raw-vertex `outline` path remains fully functional:
- If `shape` is absent in the tool input, `parse_physical_design()` falls back to `_parse_outline(data["outline"])`, exactly as today
- Old design.json files without `shape` continue to load and render
- The LLM prompt can mention both paths, but the examples and instructions will favor `shape`

---

## Implementation Order

1. **`shape2d.py`** — tessellator module (rectangle, ellipse, polygon + union/difference/intersection + z_top tracking). Can be developed and tested standalone with unit tests.
2. **`validation_shape2d.py`** or addition to `validation.py` — CSG tree validation. Also standalone-testable.
3. **`parsing.py`** — add the `if "shape" in data` branch. One small conditional.
4. **`core.py`** — store derived outline alongside shape in design.json. Small additions to `_tool_submit_design()` and `_tool_update_design()`.
5. **`tools.py`** — new tool schema for `shape` field. Replace `outline` in `submit_design` schema.
6. **`prompt.py`** — rewrite the outline documentation section with CSG primitives, operations, and examples.

Steps 1–2 are isolated and testable. Steps 3–4 are small wiring changes. Steps 5–6 are schema/prompt changes that can be iterated independently.

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|---|---|---|
| Shapely polygon simplification drops important boundary detail | Low | Control `simplify()` tolerance; validate result polygon area vs. input |
| z_top attribution at boolean boundaries produces jagged height transitions | Medium | Use nearest-primitive-centroid attribution + IDW smoothing handles the rest |
| LLM produces degenerate CSG (difference removes entire body) | Medium | Validate result polygon is non-empty and has sufficient area |
| More vertices slows height grid sampling | Low | Grid sampling is resolution-based, not vertex-count-based |
| Front-end rendering of higher-vertex outlines is slower | Very low | Three.js and SVG handle hundreds of vertices trivially |

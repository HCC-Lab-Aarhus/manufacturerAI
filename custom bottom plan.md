# Custom Bottom Surface Plan

## Current State

Right now the enclosure is always flat on the bottom — every vertex sits at z=0. The **top** surface is fully customizable:

- Each `OutlineVertex` has an optional `z_top` field that sets the ceiling height at that vertex.
- `blended_height()` interpolates per-vertex `z_top` values (IDW, power=4) across the interior and optionally adds a dome/ridge bump via `TopSurface`.
- The generator computes a `top_zs` list (one height per tessellated footprint vertex) and passes it into `shell_body_lines()`.
- In the polyhedron path, `_build_rings()` builds rings from **z=0** (bottom) up to **z=top_zs[i]** (top), with edge profiles (chamfer/fillet) applied to both ends — but the bottom ring geometry is always anchored to z=0 with uniform insets.

The bottom edge profile (chamfer/fillet) already exists and shapes the *transition* from z=0 upward, but the base plane itself is always flat at z=0.

---

## Goal

Allow the bottom surface to be sculpted per-vertex, just like the top. A vertex could specify `z_bottom = 5` meaning the floor at that corner is raised 5 mm, while another vertex stays at 0. The bottom surface would interpolate smoothly between them, creating a contoured underside (e.g., a boat-hull shape, angled battery compartment floor, recessed grip area, or a raised pedestal at one end).

---

## Ideas

### 1. Per-vertex `z_bottom` on `OutlineVertex`

Mirror `z_top` with a new optional field `z_bottom: float | None = None`. When `None`, it defaults to 0.0 (current behaviour — flat floor). When set, it defines the floor height at that vertex.

Constraints:
- `z_bottom` must always be **less than** `z_top` at the same vertex (the agent/parser should validate this).
- A reasonable minimum wall height should be enforced (e.g., `z_top - z_bottom >= 5 mm`) so the enclosure doesn't become paper-thin.

### 2. `BottomSurface` descriptor (mirror of `TopSurface`)

Add a `BottomSurface` dataclass to `Enclosure`, parallel to `TopSurface`:

```
BottomSurface:
    type: "flat" | "dome" | "ridge" | "bowl"
    (same positional params as TopSurface, but inverted — raises the floor instead of the ceiling)
```

- **"flat"** — no bump, pure per-vertex interpolation (default).
- **"dome"** — a dome shape pushing the floor *upward* at a point (e.g., a bump under the grip area).
- **"ridge"** — a ridge pushing the floor upward along a line.
- **"bowl"** — (new, bottom-only) the floor dips *down* at a centre point, creating a concave bowl. This could be useful for recessed battery compartments or grip cavities. (Though note that dipping below z=0 means the print would need supports or a different orientation — might want to clamp at z=0 minimum for printability.)

### 3. `blended_bottom_height()` function

A new function in `height_field.py`, mirroring `blended_height()`:

```
blended_bottom_height(x, y, outline, enclosure) -> float
```

- Interpolates per-vertex `z_bottom` values using the same IDW scheme.
- Adds any `BottomSurface` bump on top of that (raising the floor, or dipping it for "bowl").
- Returns the floor Z at that point.

The generator would compute `bottom_zs` the same way it computes `top_zs`:

```python
bottom_zs = [
    blended_bottom_height(x, y, outline, enclosure)
    for x, y in flat_pts
]
```

### 4. Changes to `_build_rings()`

Currently the bottom rings are hardcoded to start at z=0. The idea:

- Accept `bottom_zs: list[float]` alongside `top_zs`.
- The bottom-most ring uses `z = bottom_zs[i]` instead of `z = 0.0` for each vertex.
- Bottom edge profiles (chamfer/fillet) are applied *relative to* `bottom_zs[i]` — the fillet/chamfer curves upward from `bottom_zs[i]` just as they currently curve upward from 0.
- The straight wall section spans from `bottom_zs[i] + bot_size` up to `top_zs[i] - top_size`.
- Must validate that `bottom_zs[i] + bot_size + MIN_WALL_GAP <= top_zs[i] - top_size` at every vertex. If not, clamp the edge profile sizes locally (already done for top, would need the same for bottom).

### 5. Bottom cap surface (polyhedron path)

Currently the bottom cap is a simple fan from the bottom centroid at a single z. With variable bottom heights:

- Build concentric bottom-cap rings (mirroring the top-cap rings) that interpolate from the bottom perimeter ring inward to the centroid using IDW — same technique already used for the top cap.
- The bottom cap centroid z would be the IDW-blended average of all bottom perimeter z values.
- Face winding stays the same (the bottom faces already point downward).

### 6. Uniform path (linear_extrude) considerations

The uniform `linear_extrude` path currently handles the case where all `top_zs` are equal. If *both* top_zs and bottom_zs are uniform (but bottom is not zero), it's just a `translate([0,0, z_bottom]) linear_extrude(height = z_top - z_bottom)`. Easy extension.

If bottom_zs vary but top_zs don't (or vice versa), we'd need the polyhedron path. The selection logic should be:

```
use_polyhedron = (top_z_range >= threshold) OR (bottom_z_range >= threshold)
```

### 7. Impact on other systems

- **Cutouts / cavities**: Currently measured from `CAVITY_START_MM` downward. If the floor is raised at certain vertices, cavities might poke through the bottom. The resolver should check that each component's cavity doesn't extend below `bottom_zs` at its placement position. Could use `blended_bottom_height(comp_x, comp_y, ...)` as the local floor level.

- **Trace channels**: Currently carved into the ceiling. No direct impact from bottom changes, but if someone wanted traces on the bottom surface in the future, they'd need a similar treatment.

- **3D viewport (web frontend)**: The JS viewport builds a THREE.js mesh from the height grid. Would need a second grid (`bottom_height_grid`) and modify the mesh builder to use it for the underside. This is a frontend-only change — the backend just needs to emit the grid data.

- **STL printability**: A non-flat bottom means the print can't just sit flat on the build plate. This is fine (people print things with curved bottoms all the time using supports or orientation changes), but might be worth flagging in the UI: "This enclosure has a non-flat bottom — may require supports or reorientation for printing."

### 8. Data model summary

```
OutlineVertex:
    + z_bottom: float | None = None     # floor height; None = 0.0

Enclosure:
    + bottom_surface: BottomSurface | None = None

BottomSurface:   (new dataclass, mirrors TopSurface)
    type: "flat" | "dome" | "ridge"
    (same params as TopSurface)
```

### 9. Implementation order (when ready to code)

1. Add `z_bottom` to `OutlineVertex` and `BottomSurface` / field to `Enclosure` (models.py)
2. Add `blended_bottom_height()` to `height_field.py`
3. Compute `bottom_zs` in `generator.py`
4. Thread `bottom_zs` through `shell_body_lines()` → `_build_rings()` → `_polyhedron_shell()`
5. Build bottom cap rings (mirror of top cap rings)
6. Update uniform path to handle `translate(z_bottom)`
7. Update existing tests, add new tests for variable-bottom polyhedron
8. Update web frontend 3D viewport to render bottom surface
9. Update design parsing to read `z_bottom` / `bottom_surface` from JSON

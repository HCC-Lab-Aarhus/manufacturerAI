# Two-Part Enclosure Plan (Snap-Fit Top + Bottom)

## Overview

The enclosure generation system will support **two modes** selectable by the user:

| Mode | Value | Description |
|------|-------|-------------|
| **Solid** | `"solid"` | The current single-piece monolithic print. Everything in one shell — floor, walls, ceiling, cavities, cutouts. **This is the default and remains unchanged.** |
| **Two-part** | `"two_part"` | The enclosure is split into a bottom tray and a snap-on top lid, printed as two separate pieces. |

The mode is stored on the `Enclosure` model as `enclosure_style: str = "solid"`. All existing code paths remain untouched when `enclosure_style == "solid"` — the two-part logic is a parallel branch that only activates when explicitly selected. No regressions, no breaking changes.

### Two-Part Mode Summary

| Part | Description |
|------|-------------|
| **Bottom** | The existing enclosure roughly up to the cavity zone — contains the ironed floor, trace channels, component support platforms, and pin holes. Has snap-fit **posts** (male). |
| **Top** | A hollow shell (walls + ceiling) with button/LED cutouts. Has snap-fit **clips** (female) that click onto the bottom's posts. |

The bottom is printed **upside-down** as it is today (ironed surface face-up on the build plate). The top is printed **right-side-up** (open side down on build plate, ceiling on top). After printing, the bottom gets ink printed and components inserted, then the top snaps on.

---

## Current Z-Layer Architecture (for reference)

```
Z (mm)
  0.0              Build plate
  │
  1.6  FLOOR_MM    Ironed surface (trace layer base)
  1.9              FLOOR_MM + TRACE_HEIGHT_MM (trace channels end)
  2.2  CAVITY_START_MM   Component cavity zone starts
  │
  │    ...components, pin holes, bridges...
  │
  ceil_start       base_height - CEILING_MM (e.g. 23.0 for 25mm enclosure)
  │
  base_height      Top surface (e.g. 25.0)
```

---

## Part 1: Bottom ("Base Tray")

### What it contains
- **Floor**: 0 → FLOOR_MM (1.6 mm) — the ironed surface for ink printing (unchanged)
- **Trace channels**: Carved into the floor surface (unchanged)
- **Walls**: From 0 up to the **split height** — short perimeter walls that form the tray
- **Component support platforms**: Solid filament pillars/shelves under each component so they have something to rest on (NEW)
- **Pin holes + funnels**: Through the floor for pin-to-trace contact (unchanged)
- **Pin bridges**: For pins outside body pockets (unchanged)
- **Bottom edge profile**: Chamfer/fillet on the bottom edge (unchanged)
- **Snap-fit posts**: Male snap tabs on the inner wall perimeter at the split height (NEW)

### New: Component Support Platforms

Currently components sit in cavities that hang from the ceiling. In the two-part design, the bottom needs **solid support under each component** so they don't fall through when the top isn't attached.

For each component:
- **Top-mounted** (buttons, LEDs): Generate a solid platform at CAVITY_START_MM that fills the component body footprint (plus small clearance). Height = `body_floor - CAVITY_START_MM` where `body_floor` is where the component body sits. This platform acts as a shelf.
- **Bottom-mounted** (batteries): Already sit at CAVITY_START_MM — no change needed
- **Side-mounted**: Support shelf at CAVITY_START_MM, filling the slot area
- **Internal**: Support platform as needed

The platforms are **additions** (union), not cutouts. They are solid rectangular or circular blocks matching each component's body footprint + 0.3 mm clearance, extruded from CAVITY_START_MM up to the component's resting height.

### Split Height

The split plane where bottom meets top. Recommended: **`CAVITY_START_MM + max_component_body_height + 1.0`** or a simpler fixed ratio like **`base_height * 0.4`** — whichever is higher. This ensures:
- All component support platforms fit in the bottom
- The snap joint is above the component zone
- The walls are tall enough to be structurally sound

Alternatively, a clean constant: **`SPLIT_Z_MM`** computed as `CAVITY_START_MM + 5.0` (≈7.2 mm). This gives enough room for most components and leaves the rest for the top shell. The exact value can be configurable via the `Enclosure` model.

### Snap-Fit Posts (Male)

Small rectangular tabs protruding **upward** from the inner face of the bottom wall at the split height. Placed at regular intervals around the perimeter (e.g. every 20–30 mm, minimum 4 posts).

Each post:
- Width: 3.0 mm
- Height (protrusion above split): 4.0 mm
- Thickness: 1.2 mm (wall-normal direction)
- Has a small **barb/overhang** at the tip (0.3 mm outward bump) for the snap click

---

## Part 2: Top ("Lid Shell")

### What it contains
- **Walls**: From the split height up to the ceiling — the upper portion of the perimeter
- **Ceiling**: The solid `CEILING_MM` (2.0 mm) thick top plate
- **Ceiling cutouts**: Button stem holes, LED holes (unchanged logic, just in the top part)
- **Top edge profile**: Chamfer/fillet on the top edge (unchanged)
- **Top surface**: Dome/ridge if defined (unchanged)
- **Hollow interior**: No component pockets or pins — this is just a shell
- **Snap-fit clips**: Female snap slots on the inner wall at the bottom edge of the top (NEW)

### What it does NOT contain
- No floor / ironed surface
- No trace channels
- No pin holes / funnels
- No component body pockets (components are held by the bottom's shelves + gravity + the top pressing down)
- No pin bridges

### Snap-Fit Clips (Female)

Rectangular slots cut into the inner wall at the bottom edge of the top, aligned to mate with the bottom's posts.

Each clip:
- Slot width: 3.0 mm + 0.3 mm clearance = 3.3 mm
- Slot height: 4.0 mm + 0.3 mm = 4.3 mm
- Slot depth: 1.2 mm + 0.15 mm = 1.35 mm
- Small **ledge/undercut** inside the slot for the barb to catch on

### Alignment

The top slides straight down onto the bottom. The perimeter walls of the top sit **outside** the bottom's walls (or vice versa — see options below). A small **lap joint** overlap of 2–3 mm ensures alignment:

**Option A — Top outside (recommended for this project):**
- Bottom walls have full thickness up to split height
- Top walls start at split height and overlap the bottom by 2 mm on the **outside**
- The snap posts/clips sit in this overlap zone
- Pros: Simple, the bottom's wall provides the alignment ridge

**Option B — Interlocking step:**
- Both walls have a step (rabbet joint) at the split height
- More complex polyhedron generation but better alignment
- Can be a future improvement

---

## Implementation Plan

### Phase 1: Configuration & Models

**File: `src/pipeline/config.py`**
- Add constants:
  ```python
  SPLIT_OVERLAP_MM = 2.0      # Lap joint overlap between top and bottom
  SNAP_POST_WIDTH = 3.0       # Snap tab width
  SNAP_POST_HEIGHT = 4.0      # Snap tab protrusion
  SNAP_POST_THICKNESS = 1.2   # Snap tab wall-normal depth
  SNAP_BARB_MM = 0.3          # Barb overhang for click
  SNAP_CLEARANCE_MM = 0.3     # Clearance in female slot
  SNAP_SPACING_MM = 25.0      # Max distance between snap posts
  MIN_SNAP_POSTS = 4          # Minimum number of snap posts
  ```

**File: `src/pipeline/design/models.py`**
- Extend `Enclosure` with:
  ```python
  enclosure_style: str = "solid"      # "solid" (default, one-piece) | "two_part" (snap-fit top+bottom)
  split_z_mm: float | None = None     # Custom split height (auto-computed if None, only used when two_part)
  ```
- `enclosure_style` replaces any boolean flag — it's a string enum so we can add more styles later (e.g. `"sliding_lid"`, `"hinged"`) without model changes.

**File: `src/pipeline/design/parsing.py`**
- Parse `enclosure_style` from `design.json` (default `"solid"` if absent — backward compatible)

### Phase 2: Split Height Computation

**File: `src/pipeline/scad/split.py`** (NEW)
- `compute_split_z(enclosure, placement, catalog)` → float
  - If `split_z_mm` is explicitly set, use that
  - Otherwise: `max(CAVITY_START_MM + 5.0, max_component_top + 1.0)`
  - Clamp to `[CAVITY_START_MM + 2.0, ceil_start - 3.0]` to guarantee structural minimum for both parts

### Phase 3: Snap-Fit Geometry

**File: `src/pipeline/scad/snap_fit.py`** (NEW)

- `compute_snap_positions(flat_pts, split_z, spacing) → list[(x, y, angle)]`
  - Walk the outline perimeter, place snap posts evenly
  - Compute wall-normal angle at each position

- `snap_post_fragments(positions, split_z) → list[ScadFragment]`
  - Generate male posts as small `RectGeometry` additions at each position, rotated by wall-normal angle
  - Include barb geometry (small triangular overhang)

- `snap_clip_fragments(positions, split_z) → list[ScadFragment]`
  - Generate female slot cutouts at each position
  - Include clearance

### Phase 4: Component Support Platforms

**File: `src/pipeline/scad/resolver.py`** — extend existing resolver

- Add method `_support_platform_fragments(comp, cat, ctx) → list[ScadFragment]`
  - For top-mounted components: solid rect/circle addition from CAVITY_START_MM up to component body_floor
  - For other styles: similar logic as needed
  - These are `type="addition"` fragments

- Call this from `resolve_component()` when `ctx.part` is `"bottom"`

### Phase 5: Bottom Part Generation

**File: `src/pipeline/scad/generator.py`** — extend `run_scad_step()`

When `enclosure.enclosure_style == "two_part"`:

1. Compute `split_z` via `compute_split_z()`
2. Generate **bottom shell body** using modified `shell_body_lines()`:
   - Same bottom edge profile
   - Walls go up to `split_z` (no top edge profile, flat cut)
   - No ceiling — open top
   - No ceiling cutouts
3. Collect fragments:
   - Trace channels (unchanged — they're in the floor)
   - Pin holes + funnels (unchanged)
   - Pin bridges (unchanged)
   - Component support platforms (NEW)
   - Snap-fit posts (NEW, additions)
4. Emit as `enclosure_bottom.scad`

### Phase 6: Top Part Generation

**File: `src/pipeline/scad/generator.py`** — extend `run_scad_step()`

1. Generate **top shell body** using modified `shell_body_lines()`:
   - Walls start at `split_z - SPLIT_OVERLAP_MM` (overlap zone)
   - Straight wall up to ceiling
   - Top edge profile applied normally
   - Solid ceiling included
   - Interior is hollow (no floor)
2. Collect fragments:
   - Ceiling cutouts only (button holes, LED holes, SCAD features that punch through ceiling)
   - Snap-fit clip slots (NEW, cutouts)
   - NO trace channels, NO pin holes, NO body pockets
3. Emit as `enclosure_top.scad`

### Phase 7: Modify `shell_body_lines()` to Support Partial Shells

**File: `src/pipeline/scad/layers.py`**

Current signature:
```python
def shell_body_lines(outline, enclosure, flat_pts, top_zs, bottom_zs)
```

Add parameters:
```python
def shell_body_lines(
    outline, enclosure, flat_pts, top_zs, bottom_zs,
    *,
    z_cut_top: float | None = None,    # Hard ceiling override (for bottom part)
    z_cut_bottom: float | None = None,  # Hard floor override (for top part)
    open_top: bool = False,             # Skip ceiling cap (bottom part)
    open_bottom: bool = False,          # Skip floor cap (top part)
    skip_edge_top: bool = False,        # No top profile (bottom part)
    skip_edge_bottom: bool = False,     # No bottom profile (top part)
)
```

For the **bottom part**: `z_cut_top=split_z, open_top=True, skip_edge_top=True`
For the **top part**: `z_cut_bottom=split_z - overlap, open_bottom=True, skip_edge_bottom=True`

### Phase 8: Fragment Filtering

**File: `src/pipeline/scad/resolver.py`**

Add a `ResolverContext` field:
```python
part: str = "full"  # "full" | "bottom" | "top"
```

In `resolve_component()`:
- When `part="bottom"`: skip ceiling cutouts (cap holes, LED holes), add support platforms
- When `part="top"`: skip pin holes, pin bridges, body pockets; only emit ceiling cutouts
- When `part="full"`: current behavior (unchanged)

### Phase 9: Mode Branching in `run_scad_step()`

**File: `src/pipeline/scad/generator.py`**

The top-level orchestrator branches on `enclosure.enclosure_style`:

```python
if enclosure.enclosure_style == "solid":
    # Existing code path — completely unchanged
    # Produces: enclosure.scad  (+  extras.scad)
elif enclosure.enclosure_style == "two_part":
    # New code path (Phases 2–8)
    # Produces: enclosure_bottom.scad, enclosure_top.scad  (+  extras.scad)
```

- **Solid mode**: The current `run_scad_step()` body, untouched.
- **Two-part mode**: Calls a new helper `_generate_two_part(session, ...)` which runs Phases 2–8 and writes `enclosure_bottom.scad` + `enclosure_top.scad`.
- Both modes still generate `extras.scad` (buttons, hatches) identically.
- If `compile_stl=True`, compile whichever SCAD files were produced.

### Phase 10: Extras Adjustment

**File: `src/pipeline/scad/extras.py`**

- Buttons / snap caps are still `extras.scad` (unchanged — they're separate parts anyway)
- No changes needed unless we want to place bottom + top side by side on the build plate

### Phase 11: Frontend Updates

**File: `manufacturerAI-Frontend/src/types/models.ts`**
- Add `enclosure_style?: "solid" | "two_part"` to the enclosure type (default `"solid"`)

**File: `manufacturerAI-Frontend/src/components/pipeline/DesignPanel.tsx`** (or similar)
- Add a dropdown / toggle: "Enclosure style" → Solid / Two-part
- When "Two-part" is selected, the 3D viewport shows both STLs (bottom + top, offset vertically for clarity)
- When "Solid" is selected, viewport behavior is exactly as today

**File: `manufacturerAI-Frontend/src/lib/scene3dBuilder.ts`**
- Handle displaying one or two STL files depending on `enclosure_style`
- For two-part: load `enclosure_bottom.stl` + `enclosure_top.stl`, show top semi-transparent or offset upward

---

## File Change Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `src/pipeline/config.py` | Modify | Add snap-fit constants |
| `src/pipeline/design/models.py` | Modify | Add `enclosure_style`, `split_z_mm` to Enclosure |
| `src/pipeline/design/parsing.py` | Modify | Parse `enclosure_style` from design.json (defaults to `"solid"`) |
| `src/pipeline/scad/split.py` | **New** | Split height computation |
| `src/pipeline/scad/snap_fit.py` | **New** | Snap-fit post/clip geometry |
| `src/pipeline/scad/layers.py` | Modify | Add partial shell parameters |
| `src/pipeline/scad/resolver.py` | Modify | Add support platforms, part filtering |
| `src/pipeline/scad/generator.py` | Modify | Branch on `enclosure_style`, orchestrate two-part generation |
| `src/pipeline/scad/emit.py` | No change | Already generic enough |
| `src/pipeline/scad/extras.py` | Minor | Maybe place parts on shared build plate |
| Frontend types + panels | Modify | Style selector + conditional STL display |

### Backward Compatibility Guarantee

- `enclosure_style` defaults to `"solid"` — **all existing sessions, tests, and design.json files continue to work identically without any changes**
- The solid code path in `run_scad_step()` is untouched; the two-part branch is additive
- `split_z_mm` is ignored when `enclosure_style == "solid"`
- Frontend shows the style selector but defaults to "Solid"
- STL output names: solid mode → `enclosure.stl` (as today); two-part mode → `enclosure_bottom.stl` + `enclosure_top.stl`

---

## Assembly Sequence (User's Perspective)

1. **Print bottom part** (upside down, ironed surface up)
2. **Print top part** (open side down on build plate)
3. **Print extras** (buttons, hatches — as today)
4. **Inkjet print** conductive traces on the bottom's ironed surface
5. **Insert components** into the bottom — they rest on the support platforms
6. **Snap the top onto the bottom** — clips engage with posts, audible click
7. **Insert button caps** through the top's ceiling holes (as today)

---

## Design Decisions & Trade-offs

### Why `enclosure_style` string instead of a boolean?
- A string enum (`"solid"`, `"two_part"`) is extensible — if we ever add `"sliding_lid"` or `"hinged"`, the model doesn't change
- Cleaner than accumulating boolean flags (`two_part`, `hinged`, `sliding`) that conflict
- Easy to validate: just check membership in an allowed set
- The default `"solid"` means zero impact on existing data

### Why overlap lap joint (Option A)?
- Minimal change to polyhedron generation — just truncate height
- The overlap naturally provides alignment (bottom walls act as an inner ridge)
- Easy to add snap-fit geometry as additions/cutouts on the wall surface

### Why not a rabbet/step joint?
- Would require modifying the polyhedron ring generation to have two different wall thicknesses at the split plane
- More complex, diminishing returns for a snap-fit joint
- Can be added later as an improvement

### Support platform vs. hanging pocket?
- Current system uses **hanging pockets** (component hangs from ceiling, cavity cut from above)
- Two-part bottom needs **rising platforms** (support from below)
- Both can coexist: the solid enclosure still uses hanging pockets; only the two-part bottom adds platforms

### Split height trade-offs
- Too low: bottom is too shallow, snap posts won't fit, components may stick out
- Too high: top is too shallow, not enough ceiling for structural strength
- Sweet spot: ~30–40% of total height, or `CAVITY_START_MM + max_body_height + margin`

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Snap-fit tolerance varies by printer | Make clearances configurable; default 0.3 mm is conservative |
| Components not held firmly without top | Support platforms + gravity; pins in trace channels add friction |
| Polyhedron changes break existing tests | Keep `enclosure_style="solid"` as default; all existing paths unchanged |
| Variable-height outlines complicate split | Split plane is a constant Z — outline height variation only affects wall height above/below split |
| Button caps need to span the joint | Button stems already extend from ceiling through cavity — unchanged since they go through the top part only |

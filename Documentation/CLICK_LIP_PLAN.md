# Click-Lip Snap-Fit Plan

## Problem

The current two-part enclosure uses **discrete snap posts/clips** — small rectangular tabs spaced around the perimeter (every ~25 mm). These provide point-contact retention and are adequate for prototyping, but have weaknesses:

- Uneven clamping pressure between posts
- Stress concentration at each post base
- Gaps between posts allow the halves to separate slightly
- No continuous seal along the split line

The attached `click_lip_cut.dxf` defines a **continuous lip profile** that runs the entire perimeter, creating a uniform snap-fit joint. This is the standard approach for production enclosures (battery covers, remote controls, etc.).

## DXF Geometry Analysis

The DXF contains two mating cross-section profiles. Each profile is a 2D wall section as seen from the **side** (X = wall-thickness direction, Y = Z height):

### Profile A — Bottom Part Lip (entity 102)

```
Vertices (closed polyline, 7 points):
  (0, 0)            ← outer wall face, top of overlap zone
  (3, 0)            ← inner wall face, top of overlap zone
  (3, -3)           ← inner wall face, split plane
  (3, -8)           ← inner wall face, bottom of hook
  (0.869, -5.674)   ← hook tip (barb peak)
  (1.785, -4.973)   ← hook curve midpoint
  (0, -3)           ← outer wall face, split plane
```

The 3 mm wall (x=0..3) extends from the split plane (y=-3) up through the overlap zone (y=0). The hook protrudes **downward** from the inner face (x=3), curving inward to x≈0.87 before returning to the outer face at the split plane.

### Profile B — Top Part Lip (entity 104)

```
Vertices (closed polyline, 7 points):
  (10, -3)          ← inner face, split plane
  (10, -8)          ← inner face, bottom of hook
  (8.415, -6.271)   ← hook tip (barb peak)
  (9.328, -5.573)   ← hook curve midpoint
  (7, -3)           ← outer face, split plane
  (7, 0)            ← outer face, top of overlap zone
  (10, 0)           ← inner face, top of overlap zone
```

Same geometry at x=7..10. The hook also protrudes downward from the inner face, curving inward.

### Interpretation

- **Split plane** is at y = -3 (the horizontal LINE entities in the DXF confirm this).
- **Overlap zone**: 3 mm tall (y = -3 to y = 0). This is the lap joint where the top part slides over the bottom.
- **Hook zone**: 5 mm tall (y = -3 to y = -8). The barbed lip that provides snap retention.
- **Wall thickness**: 3 mm (consistent with typical enclosure wall constants).
- The two profiles are identical in shape but offset in X. In practice, Profile A is on the **bottom part** (lip protruding inward) and Profile B is on the **top part** (matching lip protruding inward). When assembled, the hooks interlock.

### Normalized Profile Coordinates

Translating to origin and normalizing (wall thickness 0..W, height 0..H):

**Bottom lip cross-section** (origin at outer-wall / split-plane corner):

| Point | X (wall-normal) | Y (height, up from split) |
|-------|-----------------|---------------------------|
| 0     | 0.0             | 0.0   (split plane)       |
| 1     | W               | 0.0   (split plane)       |
| 2     | W               | -5.0  (hook bottom)       |
| 3     | 0.869           | -2.674 (barb tip)         |
| 4     | 1.785           | -1.973 (curve midpoint)   |
| 5     | 0.0             | 0.0   (closes back)       |

Plus the overlap rectangle above: (0,0) → (W,0) → (W, 3.0) → (0, 3.0).

Where W = 3.0 mm (wall thickness), and the hook extends 5 mm below the split.

## Design Decision: DXF Loader vs Procedural

### Option A: DXF Loader (Recommended)

Parse the DXF file in Python, extract the 2D profile polylines, and use them as the cross-section for the lip geometry. This is the most flexible approach:

- Designers can modify the lip shape in any CAD tool (FreeCAD, LibreCAD, AutoCAD)
- Different lip profiles for different use cases (heavy-duty, light-duty, waterproof)
- The system treats the DXF as a **parametric input**, not hardcoded geometry
- Reusable for other swept-profile features in the future

### Option B: Procedural (Parametric)

Define the hook geometry in Python with configurable parameters (hook depth, barb width, curve radius). More constrained but doesn't need file I/O.

### Recommendation

**Option A (DXF Loader)** with a **procedural fallback**. The DXF loader is the primary path; if no DXF is provided, generate a default profile procedurally from config constants. The procedural profile mimics the DXF geometry using the same parameterization.

## Architecture

### New Files

| File | Purpose |
|------|---------|
| `src/pipeline/scad/dxf_profile.py` | DXF parser: extracts 2D polyline profiles from DXF files |
| `src/pipeline/scad/click_lip.py` | Click-lip geometry: sweeps a 2D profile along the outline perimeter, producing OpenSCAD polyhedron lines or fragment geometry |

### Modified Files (future, not in this phase)

| File | Change |
|------|--------|
| `src/pipeline/scad/generator.py` | In `_generate_two_part()`: call `click_lip` instead of / alongside `snap_fit` |
| `src/pipeline/scad/layers.py` | Possibly: truncate shell body at split plane (lip geometry handles the joint zone) |
| `src/pipeline/config.py` | New constants for click-lip dimensions |
| `src/pipeline/design/models.py` | New field on `Enclosure`: `snap_style: str = "post"` (or `"click_lip"`) |

### Unchanged Files

All single-piece ("solid") enclosure paths remain untouched. The click lip only activates when `enclosure_style == "two_part"` and `snap_style == "click_lip"`.

## Implementation Plan

### Phase 1: DXF Profile Loader (`dxf_profile.py`)

A minimal DXF parser that extracts closed LWPOLYLINE entities as 2D point lists. No need for a full DXF library — the format for simple polylines is well-structured plaintext.

**Input**: Path to a `.dxf` file.

**Output**: List of `ClosedProfile` objects, each containing:
- `points: list[tuple[float, float]]` — the 2D vertices
- `layer: str` — DXF layer name (for filtering)
- `is_closed: bool`

**Parsing strategy**:
1. Read entity section
2. For each LWPOLYLINE: extract vertex count (group 90), closed flag (group 70), and vertex pairs (groups 10/20)
3. For each LINE: extract start (10/20) and end (11/21)
4. Optionally support ARC and CIRCLE for future profile types
5. Ignore HEADER, TABLES, BLOCKS, OBJECTS sections (pass through)

**Edge cases**:
- Bulge values on polyline vertices (arcs between vertices) — subdivide into line segments
- Multiple profiles in one DXF — return all, let caller select by layer or index
- Units: respect `$INSUNITS` header (the current DXF uses millimeters, code 4)

**Dependencies**: None (pure Python string parsing). Could optionally use `ezdxf` library if already available, but a lightweight parser keeps dependencies minimal.

**Alternative**: Use `ezdxf` for robustness. It handles edge cases (arc bulges, splines, nested blocks) that a hand-rolled parser would struggle with. The tradeoff is an added dependency.

### Phase 2: Profile Normalization

The raw DXF coordinates need normalization before they can be swept along any perimeter:

1. **Split into mating halves**: Identify which polyline is the bottom lip vs top lip. Convention: the polyline whose hook extends in the -Y direction from the inner face (max X) is the bottom lip. Or: simply label them by DXF layer, or by order (first = bottom, second = top).

2. **Translate to local coordinates**: Origin at the outer-wall / split-plane corner. X axis = wall-normal (outward → inward). Y axis = height (positive = above split, negative = below split).

3. **Scale to actual wall thickness**: The DXF uses W=3mm. If the enclosure wall is different, scale X proportionally. Y (hook depth) may remain absolute or scale with a separate parameter.

4. **Store as `LipProfile` dataclass**:
   ```python
   @dataclass
   class LipProfile:
       overlap_height_mm: float          # height above split (3.0 in DXF)
       hook_depth_mm: float              # depth below split (5.0 in DXF)
       wall_thickness_mm: float          # reference wall width (3.0 in DXF)
       bottom_cross_section: list[tuple[float, float]]  # normalized 2D points
       top_cross_section: list[tuple[float, float]]      # normalized 2D points
   ```

### Phase 3: Perimeter Sweep (`click_lip.py`)

This is the core geometric step: sweeping the 2D lip profile along the enclosure outline to produce 3D geometry.

#### Approach: Polyhedron Ring Extension

The most natural fit for this codebase. The shell body in `layers.py` is built from stacked vertex rings. The click lip extends this concept:

**For each wall segment** of the outline perimeter:
1. Compute the wall-normal direction (already done in `snap_fit.py`)
2. At each profile vertex, compute the 3D position:
   - X/Y position = outline vertex + (profile X) × wall-normal
   - Z position = split_z + profile Y
3. Connect adjacent segments' profile vertices into quad faces (same as the ring approach in layers.py)

**Corner handling**: At outline corners, the profile vertices from two adjacent segments must be joined. Options:
- **Miter**: Extend both segments' profiles to their intersection. Works for obtuse angles, spikes at acute angles (use miter limit like `_inset_polygon_pts` already does).
- **Round**: Insert extra profile instances at intermediate angles around the corner. Smoother but more vertices.
- **Miter with limit** (recommended): Use the existing `_inset_polygon_pts` miter logic. At each outline vertex, compute the miter-normal direction and place the profile vertices along it. This is exactly what the existing ring builder does — each ring is an inset of the flat polygon at a different distance, so the click lip profile naturally maps to a set of inset rings at different Z heights.

**This means the click lip can be implemented as additional rings in the existing `_build_rings()` function.** At the split zone, instead of ending the bottom part with a flat ring and starting the top with a flat ring, insert profile-shaped rings:

```
Bottom part rings (bottom → top):
  ... existing bottom edge profile rings ...
  ... straight wall rings ...
  ring at Z = split_z - hook_depth (inset = 0, the wall bottom of the hook zone)
  ring at Z = split_z - barb_offset (inset = barb_protrusion, the hook tip)
  ring at Z = split_z - curve_mid (inset = curve_inset, the curve midpoint)
  ring at Z = split_z (inset = 0, the split plane)
  ring at Z = split_z + overlap_height (inset = 0, top of overlap zone)

Top part rings (bottom → top):
  ring at Z = split_z - hook_depth (inset = wall_thickness, the hook approaches from inside)
  ring at Z = split_z - barb_offset (inset = wall_thickness - barb_protrusion)
  ... mirror of bottom hook ...
  ring at Z = split_z (inset = 0)
  ... existing top edge profile rings ...
```

Wait — this doesn't quite work because the hook creates an **undercut** (the barb protrudes inward past the wall surface). A simple ring-based polyhedron can't represent an undercut because each ring must be a simple (non-self-intersecting) polygon, and rings can't "fold back" on themselves.

#### Revised Approach: Separate Lip Body

Generate the click lip as a **separate polyhedron** (or set of extruded fragments) that gets unioned with the shell body. The shell body is truncated cleanly at the split plane, and the lip geometry is added on top.

**For the bottom part:**
1. Shell body: rings from floor up to `split_z + overlap_height`. The overlap zone is a simple extension (same outline, no inset change).
2. Lip hook: A separate polyhedron built by sweeping the hook cross-section around the perimeter. The hook only protrudes from the **inner** face of the wall.

**For the top part:**
1. Shell body: rings from `split_z` (or `split_z - overlap_height`) up to ceiling.
2. Lip hook: Matching hook on the inner face, mirrored to interlock with the bottom's hook.

**Generating the lip polyhedron:**

For each consecutive pair of outline vertices (V_i, V_{i+1}):
1. Compute the edge direction and wall-inward normal
2. At V_i: place the 2D hook profile in the plane perpendicular to the edge, oriented so X = wall-inward, Y = Z-axis
3. At V_{i+1}: place the same profile
4. Connect corresponding profile vertices between V_i and V_{i+1} to form quad faces
5. Cap the first and last profiles with triangulated faces

At **corners** (where edge direction changes):
- Use the **miter bisector** direction (already computed by `_inset_polygon_pts`) to orient the shared profile instance
- The profile at a corner vertex is shared between two adjacent wall segments
- This naturally handles obtuse corners; for acute corners, apply miter limit

This produces a watertight polyhedron for the lip that follows the entire outline.

**OpenSCAD output:**

```openscad
// Bottom part
union() {
    polyhedron(points=[...], faces=[...]);   // shell body (floor to split+overlap)
    polyhedron(points=[...], faces=[...]);   // click lip hook
}
```

This is clean CSG and avoids modifying the existing shell body generation.

#### Alternative Approach: OpenSCAD `linear_extrude` with rotation

For each straight wall segment, the hook cross-section can be `linear_extrude`d along the segment length:

```openscad
translate([seg_start_x, seg_start_y, split_z])
  rotate([0, 0, seg_angle])
    rotate([90, 0, 0])
      linear_extrude(height=seg_length)
        polygon(points=hook_profile);
```

Corners are handled with `hull()` between the end of one segment and the start of the next. This is simpler to emit but produces more OpenSCAD operations (one per wall segment + one hull per corner).

**Recommendation**: Use the **polyhedron sweep approach** for the lip body. It's consistent with how `layers.py` works, produces a single watertight mesh, and handles variable-height outlines naturally. The segment-wise `linear_extrude` approach is a valid fallback if the polyhedron sweep proves too complex for acute corners.

### Phase 4: Integration with Generator

In `_generate_two_part()`:

1. **Add snap style branching:**
   ```python
   if enclosure.snap_style == "click_lip":
       lip_profile = load_lip_profile(enclosure.lip_dxf_path or DEFAULT_LIP_DXF)
       bottom_lip_lines = sweep_lip(flat_pts, lip_profile.bottom, split_z, "bottom")
       top_lip_lines = sweep_lip(flat_pts, lip_profile.top, split_z, "top")
       # Add lip polyhedrons as additions in the SCAD output
   else:
       # Existing snap post/clip logic (unchanged)
       snap_positions = compute_snap_positions(flat_pts)
       bottom_frags.extend(snap_post_fragments(snap_positions, split_z))
       top_frags.extend(snap_clip_fragments(snap_positions, split_z))
   ```

2. **Shell body adjustment:** When using click lip, the bottom shell extends to `split_z + overlap_height` (not just `split_z`). The top shell starts at `split_z` (not `split_z - SPLIT_OVERLAP_MM`). The overlap is now handled by the lip geometry, not by the shell body.

3. **SCAD emit:** Extend `generate_scad()` to accept optional extra polyhedron body lines (for the lip), or emit them as a separate `union()` member alongside the shell body.

### Phase 5: Configuration

New constants in `config.py`:

```python
# Click-lip defaults (mm) — used when no DXF is provided
CLICK_LIP_OVERLAP_MM = 3.0        # height of overlap zone above split
CLICK_LIP_HOOK_DEPTH_MM = 5.0     # hook extension below split
CLICK_LIP_BARB_MM = 0.5           # barb protrusion (inward from wall)
CLICK_LIP_CLEARANCE_MM = 0.15     # gap between mating surfaces
```

New field on `Enclosure` model:

```python
snap_style: str = "post"           # "post" (discrete tabs) or "click_lip" (continuous lip)
lip_profile_dxf: str | None = None # path to custom DXF profile (optional)
```

### Phase 6: Procedural Fallback

If no DXF is provided, generate the default lip profile procedurally:

```python
def default_lip_profile(wall_mm: float = 3.0) -> LipProfile:
    """Generate the standard click-lip profile matching click_lip_cut.dxf."""
    W = wall_mm
    hook_depth = 5.0
    barb_inward = W * 0.71  # barb tip at ~29% from outer wall (0.869/3.0)

    bottom_profile = [
        (0.0, 0.0),                    # outer face at split
        (W, 0.0),                      # inner face at split
        (W, -hook_depth),              # inner face at hook bottom
        (barb_inward, -hook_depth + 2.326),  # barb tip
        (W * 0.595, -hook_depth + 3.027),    # curve midpoint
        (0.0, 0.0),                    # back to outer face (closed)
    ]
    # ... (mirror/offset for top profile)
```

## Rendering & Printability Notes

- **FDM printability**: The hook's undercut prints fine if the overhang angle stays under 45°. The DXF profile's barb tip at (0.869, -2.674) relative to the inner face at (3.0, 0.0) gives an overhang of ~40°. This is within FDM limits without supports.
- **Material**: PLA/PETG snap characteristics differ. The hook depth and barb size may need per-material tuning (future: expose as design parameters).
- **Assembly direction**: The current design assumes **vertical assembly** (top slides straight down onto bottom). The hooks engage vertically. No lateral sliding.
- **Disassembly**: The continuous lip is harder to pry apart than discrete posts. This is a feature (better retention) but may need a designated pry point / notch. Consider adding an optional pry slot as a cutout fragment at one location on the perimeter.

## Testing Strategy

1. **Unit test: DXF parser** — Load `click_lip_cut.dxf`, verify extracted vertex counts, closure, and coordinate values.
2. **Unit test: Profile normalization** — Verify origin translation, wall-thickness scaling.
3. **Unit test: Lip sweep** — Given a simple rectangular outline (4 vertices), verify the swept polyhedron has correct vertex count and face count. Verify watertightness via Euler formula: V - E + F = 2.
4. **Integration test: Full two-part generation** — Generate bottom + top SCAD with click lip enabled, compile with OpenSCAD, verify no CGAL errors.
5. **Visual test: Cross-section** — Generate a test SCAD that projects the lip onto a plane (using `projection()`) and verify the profile matches the DXF.

## Migration Path

1. Implement `dxf_profile.py` and `click_lip.py` with unit tests (no production code changes)
2. Add `snap_style` field to `Enclosure` model (backward-compatible default: `"post"`)
3. Wire click lip into `_generate_two_part()` behind the `snap_style` flag
4. Keep discrete snap posts as the default; click lip is opt-in
5. Once validated on real prints, consider making click lip the default for two-part enclosures

## File Dependency Graph

```
click_lip_cut.dxf (or custom DXF)
        │
        ▼
  dxf_profile.py          ← Phase 1: parse DXF → 2D point lists
        │
        ▼
  click_lip.py             ← Phase 3: sweep 2D profile → 3D polyhedron lines
        │
        ├──► generator.py  ← Phase 4: integrate into _generate_two_part()
        │
        └──► emit.py       ← Phase 4: emit lip polyhedron in SCAD output
              │
              ▼
        enclosure_bottom.scad / enclosure_top.scad
```

## Summary

| Aspect | Current (snap posts) | Proposed (click lip) |
|--------|---------------------|---------------------|
| Joint type | Discrete rectangular tabs | Continuous perimeter lip |
| Retention | Point contact at 4-8 locations | Full perimeter engagement |
| Geometry source | Procedural (config constants) | DXF file (or procedural fallback) |
| OpenSCAD output | RectGeometry fragments (additions + cutouts) | Polyhedron body (union) |
| Shell modification | None (fragments overlay) | Overlap zone extends shell height |
| Printability | Any printer | Requires ≤45° overhang capability (standard FDM) |
| Disassembly | Easy (flex tabs) | Harder (continuous lip) — may need pry notch |

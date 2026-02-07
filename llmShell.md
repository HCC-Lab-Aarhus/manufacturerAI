# Plan: LLM-Designed Parametric Outline with Extruded Shell

**TL;DR**: Replace the hardcoded rectangular box with an LLM-powered Designer Agent that generates a **2D parametric outline** (polygon vertices) plus button placements as JSON. The system inserts this outline into a fixed SCAD template that `linear_extrude`s the profile to the specified height and adds top/bottom faces — producing a "shaped cylinder" shell. The existing PCB/routing pipeline then places internal components and routes traces within the outline. A validation loop ensures the outline is geometrically valid and the internals fit. The web UI streams every iteration in real time. The current functional pipeline (PCB placement, routing, trace channels, pinholes) is preserved — only the shell shape source changes.

**Mission statement**: Replace the template-based enclosure generation with an LLM-driven outline designer that has creative freedom over the remote's 2D profile shape. The LLM generates a parametric polygon outline + a structured metadata JSON (button positions). The system validates the polygon geometry (closed, non-self-intersecting, within bounds), inserts it into a fixed SCAD template that extrudes the outline into a 3D shell with top and bottom faces, and compiles via fast CSG export. Once the shell compiles, the existing PCB/routing pipeline optimizes internal components (battery, controller, traces) within the outline. If optimization fails, feedback is sent back to the designer LLM to adjust the outline. The web UI streams intermediate renders and trace drawings in real time throughout the process.

## Steps

### 1. Extend design_spec with style description

- Add `style_description: string` to `schemas/design_spec.schema.json` — free-text creative intent (e.g. "teardrop shape", "ergonomic waist grip", "guitar pick silhouette").
- Update `prompts/consultant_v2.md` to extract style descriptions from the user prompt and pass them through. Anything beyond dimensions/buttons goes into `style_description`.
- Update `_validate_and_fill_defaults()` in `src/llm/consultant_agent.py` to default `style_description` to `"standard rectangular remote"` when absent.

### 2. Create the Designer Agent (`src/design/designer_agent.py` — new)

A new class `DesignerAgent` that calls the LLM to produce a 2D outline:

- **Input**: `design_spec` (dimensions, buttons, style description) + optional `optimization_report` from previous outer-loop iteration.
- **Output**:
  ```json
  {
    "outline": [[x0, y0], [x1, y1], ...],
    "button_positions": [{"id": "BTN1", "x": ..., "y": ...}, ...]
  }
  ```
  The `outline` is a closed 2D polygon (list of `[x, y]` vertex pairs in mm). The polygon defines the XY cross-section of the remote — the system handles all 3D extrusion.
- **Validation loop** (geometry check — no OpenSCAD needed):
  1. Prompt LLM with system prompt + design spec → LLM returns outline polygon + button positions as JSON
  2. Validate the polygon:
     - At least 3 vertices
     - Non-self-intersecting (no edge crossings)
     - All vertices within `device_constraints` bounding box (0..width, 0..length)
     - Polygon area ≥ minimum viable area (enough for battery + controller)
     - All button positions fall inside the polygon (point-in-polygon test)
  3. If validation fails → append error description to conversation history, ask LLM to fix → repeat (max ~5 attempts)
  4. If validation succeeds → insert outline into SCAD template, compile-check with `openscad` → return result
- **LLM conversation**: Extend `GeminiClient` in `src/llm/client.py` with `complete_chat(system, messages) -> str` for multi-turn conversation (validation-fix loop and optimization outer loop).
- **Conventions for the LLM**: The LLM only outputs 2D polygon vertices. Coordinate system: origin bottom-left, X=width, Y=length. All values in mm. The polygon should be ordered counter-clockwise. The system handles: extrusion to 3D, hollowing, top/bottom faces, rounding, and all functional cutouts.

### 3. Write the Designer system prompt (`prompts/designer.md` — repurpose empty `style_agent.md`)

Content to include:
- Role: "You are a product designer specializing in remote control shapes. You output a 2D outline polygon that defines the top-down silhouette of a remote control."
- Explain that the polygon will be extruded into a 3D shell by the system — the LLM only controls the XY profile
- Coordinate system and units (mm): origin at bottom-left, X = width axis, Y = length axis
- Constraints:
  - Polygon must fit within `device_constraints` width × length bounding box
  - All vertices as `[x, y]` pairs, counter-clockwise winding
  - Polygon must be simple (no self-intersections)
  - Minimum interior area to fit battery + controller (hint from design_spec)
  - Button positions must lie inside the polygon with ≥ edge_clearance margin
- Output format: JSON with `outline` (array of `[x, y]`) and `button_positions` (array of `{id, x, y}`)
- Examples: 2-3 sample outlines:
  - Simple rounded rectangle as series of arc-sampled points
  - Teardrop / tapered shape (wider at bottom, narrow at top near IR diode)
  - Ergonomic waisted shape (concave sides for grip)
- When receiving an optimization report: interpret issues (e.g. "battery doesn't fit" → widen the polygon near y=20-40mm) and adjust vertices accordingly
- Tip: use 20-60 vertices for smooth curves; fewer for angular/geometric designs

### 4. Shell SCAD template (`src/design/shell_template.py` — new)

A Python module that generates the SCAD code from the LLM's outline polygon:

- **`generate_shell_scad(outline, height, wall_thickness, floor_thickness, ceil_thickness, fillet_radius) -> str`**:
  - Takes the 2D polygon vertices and dimensional parameters
  - Produces OpenSCAD code that:
    1. Defines the outline as a `polygon(points=[[x0,y0], [x1,y1], ...])` 
    2. Creates the **outer solid**: `linear_extrude(height=total_height) offset(r=fillet_radius) offset(delta=-fillet_radius) polygon(...)` — this extrudes the outline while rounding sharp corners via the offset trick
    3. Creates the **inner cavity** (hollowing): `translate([0, 0, floor_thickness]) linear_extrude(height=cavity_height) offset(r=fillet_radius) offset(delta=-fillet_radius) offset(delta=-wall_thickness) polygon(...)` — same outline inset by wall_thickness
    4. **Top face**: the ceiling is the solid between `floor_thickness + cavity_height` and `total_height` — formed naturally by the `difference()` of outer minus inner
    5. **Bottom face**: the floor is the solid between `z=0` and `z=floor_thickness` — formed naturally by the inner cavity starting at `floor_thickness`
    6. Full shell: `difference() { outer_solid; inner_cavity; }`
  - The result is a "shaped cylinder" — an extruded custom profile with solid top and bottom caps and uniform wall thickness
  - Uses `$fn=64` for smooth offset curves

- **`generate_outline_polygon_scad(outline) -> str`**: helper that just returns the `polygon(points=...)` snippet, used for rendering the 2D preview

- **Why `offset()` instead of `minkowski()`**: The 2D `offset(r=...)` is fast, numerically stable, and directly produces the inset/outset needed for wall thickness and fillet rounding. No 3D Minkowski (which is extremely slow in OpenSCAD) required since we stay in 2D before extruding.

### 5. Outline validation utilities (`src/core/outline_validator.py` — new)

Pure-Python geometry validation (no OpenSCAD dependency):

- **`validate_outline(outline, bounds, min_area) -> list[str]`**: returns list of error strings (empty = valid)
  - Checks vertex count ≥ 3
  - Checks all vertices within bounds `(0..width, 0..length)`
  - Checks no self-intersecting edges (sweep-line or O(n²) segment intersection test)
  - Checks polygon area via shoelace formula ≥ `min_area`
  - Checks winding order is counter-clockwise (signed area > 0)
- **`point_in_polygon(x, y, outline) -> bool`**: ray-casting algorithm
- **`polygon_area(outline) -> float`**: shoelace formula
- **`ensure_ccw(outline) -> list`**: reverses vertex order if clockwise
- **`inset_polygon(outline, margin) -> list`**: uses `pyclipper` for robust inward offset (wall thickness). This produces the PCB board boundary from the shell outline.
- Add `pyclipper` to `requirements.txt`

### 6. Modify PCB Agent for polygon outline + fixed buttons

Changes to `src/pcb_python/pcb_agent.py`:
- `generate_layout()` gains optional params: `outline_polygon: list | None` and `fixed_button_positions: list[dict] | None`
- When `outline_polygon` is provided:
  - Board outline = the inset polygon (not a rectangle computed from device_constraints)
  - `boardWidth`/`boardHeight` derived from polygon bounding box
- When `fixed_button_positions` is provided:
  - Skip `_place_buttons_smart()` — instead place buttons at exact designer-specified XY coordinates
  - Validate each position is inside the polygon; report out-of-bounds buttons in feasibility
- `_place_battery_smart()` and `_place_controller_smart()` get polygon-aware variants: grid-scan candidate positions inside polygon, pick best that avoids fixed buttons and each other (as outlined in RouterOutline.md Step 4)
- `_place_diode()`: find the polygon edge closest to "top" (max Y), place diode centered on it pointing outward

### 7. Extend TS Router for polygon board boundaries

Changes to `src/pcb/src/types.ts`:
- Add optional `boardOutline?: number[][]` to `BoardParameters`

Changes to `src/pcb/src/grid.ts`:
- New method `blockOutsidePolygon(outline)`: ray-casting point-in-polygon test per cell center; block cells outside polygon + within clearance of boundary
- When `boardOutline` is present, use this instead of `blockBoardEdges()`

Changes to `src/pcb_python/ts_router_bridge.py`:
- `_convert_layout()`: if `pcb_layout["board"]["outline_polygon"]` is not a simple rectangle, pass it as `boardOutline` in the router input

### 8. Refactor Enclosure Agent to assemble shell from outline + functional features

`src/design/enclosure_agent.py` gains a new method to work with outline-based shells:

- New method `generate_from_outline(outline, button_positions, pcb_layout, design_spec, routing_result, output_dir)`:
  - Calls `shell_template.generate_shell_scad(outline, ...)` to produce the base shell SCAD (extruded hollow shape with top/bottom faces)
  - Appends functional cutouts to the SCAD via existing methods:
    1. Cuts button holes through the top face (`_generate_button_holes_scad()`)
    2. Cuts battery access hatch through the bottom face (`_generate_battery_cutout_scad()`)
    3. Carves trace channels into the floor (`_generate_trace_channels_scad()`)
    4. Drills pinholes (`_generate_pinholes_scad()`)
    5. Cuts IR diode window through the end wall (`_generate_ir_diode_slits_scad()`)
    6. Adds battery guard walls (`_generate_battery_guards_scad()`)
  - Since the shell is produced from a known template structure (not arbitrary LLM SCAD), the coordinate system for cutouts is deterministic and predictable — floor at `z=0..floor_thickness`, ceiling at `z=total_height-ceil_thickness..total_height`, walls follow the offset outline
- Existing `generate_from_pcb_layout()` remains as fallback for when no outline is provided (backward compat with rectangular mode)
- **Key advantage over the old plan**: No complicated CSG hollowing of an unknown LLM shape. The system controls the extrusion, so wall positions, floor/ceiling Z-coordinates, and interior dimensions are all known exactly.

### 9. New pipeline states and orchestrator flow

Add to `PipelineState` in `src/core/state.py`:
- `DESIGN_OUTLINE` — Designer Agent generates 2D outline + button positions
- `VALIDATE_OUTLINE` — geometry validation + SCAD template compile check
- `OPTIMIZE_INTERNALS` — PCB placement + TS routing within polygon
- `CHECK_OPTIMIZATION` — evaluate if internals fit; loop back to `DESIGN_OUTLINE` or proceed

Add to `PipelineContext`:
- `outline_polygon: list`, `button_positions: list`, `shell_scad: str`, `optimization_report: dict`, `design_iteration: int`, `style_description: str`

New orchestrator flow in `src/core/orchestrator.py`:
```
COLLECT_REQUIREMENTS
  → DESIGN_OUTLINE (designer agent → 2D polygon + buttons)
    → VALIDATE_OUTLINE (geometry checks + SCAD compile)
      → GENERATE_PCB (with polygon + fixed buttons)
        → CHECK_PCB_FEASIBILITY
          → if feasible: GENERATE_ENCLOSURE (assemble shell from outline + cutouts)
          → if not feasible: CHECK_OPTIMIZATION → DESIGN_OUTLINE (with optimization report)
            (max N outer iterations)
```

When no `style_description` is present (or it equals the default), skip the designer and fall into the existing rectangular flow — **zero regression**.

### 10. Build optimization report for designer feedback

When routing or placement fails, construct a structured report:
- `feasible: bool`
- `problems: [{ type: "battery_no_fit" | "trace_failed" | "component_outside_outline" | "outline_too_narrow", component_id, description, suggestion }]`
- `placed_components: [{ id, type, center, status: "placed" | "failed" }]`
- `routing_summary: { total_nets, routed_nets, failed_nets }`

This report is appended to the designer agent's conversation so it can adjust the outline (e.g. "widen the polygon between y=20-40mm by at least 8mm to fit the battery").

### 11. Web UI streaming for real-time design visualization

Extend SSE events in `src/web/server.py`:
- New event type `outline_preview`: sent after each designer iteration with the 2D polygon vertices — rendered as an SVG/canvas overlay in the browser showing the remote silhouette
- New event type `scad_preview`: sent after SCAD template compilation with the generated SCAD source (or rendered STL thumbnail)
- New event type `optimization_report`: sent with the feasibility/optimization result
- Existing `debug_images` event already streams routing debug PNGs — reuse for each outer iteration
- Existing `progress` event gains new stage names: `DESIGN_OUTLINE`, `VALIDATE_OUTLINE`, `OPTIMIZE_INTERNALS`, `CHECK_OPTIMIZATION`
- Front-end changes to `src/web/static/`: 2D outline canvas (draw polygon with button markers), show optimization status, toggle between 2D outline view and 3D STL preview

### 12. Multi-turn LLM support

Extend `src/llm/client.py`:
- Add `complete_chat(system: str, messages: list[dict]) -> str` to `GeminiClient` — takes a list of `{"role": "user"|"model", "parts": ["..."]}` messages for multi-turn conversation (needed for the validation-fix inner loop and the optimization outer loop)
- Preserve the existing `complete_json()` method unchanged
- Add the same method to `MockLLMClient` with a simple fallback (return the default rectangular outline — a simple 4-vertex rectangle matching device_constraints)
- Usage tracking per-call same as current

## Verification

- **Outline validation test**: Generate various polygons (valid, self-intersecting, out-of-bounds, too small), verify validation catches all invalid cases
- **SCAD template test**: Feed a simple polygon (e.g. rounded rectangle as 24 vertices) into `generate_shell_scad()`, compile with OpenSCAD, verify the STL is a valid solid with uniform wall thickness
- **Polygon routing test**: Feed a non-rectangular outline to the TS router, verify cells outside are blocked, traces route correctly
- **End-to-end test**: "Make a TV remote with 3 buttons shaped like a teardrop" → LLM produces teardrop outline, system extrudes it, internal components placed and routed, valid STL output
- **Regression test**: Run existing test suite with no `style_description` — all current outputs unchanged, rectangular flow still works
- **Fillet/offset test**: Verify the `offset(r=...) offset(delta=-...)` trick in the SCAD template correctly rounds corners of both convex and concave outlines
- **Web streaming test**: Verify SSE events fire for each stage, 2D outline preview and 3D STL preview appear in browser

## Decisions

- **LLM generates 2D outline, system extrudes to 3D**: Far simpler than having the LLM write full 3D OpenSCAD. The LLM only needs to understand 2D shapes — no CSG, no `hull()`, no `minkowski()`. Validation is pure geometry (no SCAD compilation needed for the initial check). The system controls all 3D aspects (height, wall thickness, floor/ceiling, hollowing) deterministically.
- **Fixed SCAD template over LLM-generated SCAD**: The template approach means hollowing, wall thickness, and coordinate systems are always correct. No risk of the LLM producing non-manifold geometry, incorrect winding, or incompatible CSG. The only creative freedom is the 2D silhouette — which is where the aesthetic identity of a remote actually lives.
- **`offset()` for both filleting and inset**: OpenSCAD's 2D `offset()` handles rounding (positive `r`) and wall inset (negative `delta`) efficiently. This avoids the extremely slow 3D `minkowski()` operation entirely, since all shaping happens in 2D before extrusion.
- **Multi-turn conversation for validation loop**: Gemini SDK natively supports multi-turn `ChatSession`; using it keeps token context clean and enables the fix loop naturally. The validation errors are geometric and descriptive — easy for the LLM to interpret.
- **Rectangular fallback when no style_description**: Zero regression for existing usage. The designer agent is bypassed entirely, orchestrator falls into the current path.
- **Designer places buttons, system places everything else**: Clean separation — the designer controls the silhouette and user-facing button ergonomics, the system handles engineering constraints (battery placement, routing, diode orientation).
- **No DXF extraction step**: Since the LLM directly outputs polygon vertices (not SCAD), there is no need to render SCAD to DXF and parse it back. The outline goes straight from LLM JSON → PCB agent → TS router → SCAD template. This eliminates the `ezdxf` dependency and a fragile parsing step.

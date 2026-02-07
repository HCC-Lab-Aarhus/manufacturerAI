# Plan: Custom-Outline Router & Dynamic Component Placement

**TL;DR**: The Designer LLM outputs a 3D shell as OpenSCAD. We extract a 2D outline polygon via `projection(cut=true)` → DXF parsing. That polygon (inset by wall thickness) becomes the board shape for the TS router, which gets extended with polygon-based grid masking instead of rectangular edge blocking. The PCB agent then dynamically places battery, controller, and diode inside the arbitrary outline while respecting the LLM-provided button positions as fixed constraints. Each iteration streams results back to the web UI.

## Steps

### Step 1 — DXF outline extraction from SCAD (`src/core/scad_outline.py` — new)

- New function `extract_outline_from_scad(scad_path) -> list[list[float]]`:
  - Writes a wrapper SCAD file: `projection(cut=true) translate([0,0,<mid_height>]) import("<shell>.scad");` — this slices the shell at its midpoint to get the cross-section outline
  - Calls `openscad -o outline.dxf wrapper.scad` (OpenSCAD natively exports DXF from 2D geometry)
  - Parses the DXF output to extract the polygon vertices — DXF `LWPOLYLINE` or `LINE` entities → ordered point list
  - Uses a lightweight DXF parser (the `ezdxf` library, or a minimal custom parser for the simple output OpenSCAD produces)
  - Returns the polygon as `[[x, y], ...]` in mm coordinates
- Separate function `inset_polygon(polygon, margin) -> list[list[float]]` that computes the inward offset (Minkowski erosion) to get the PCB boundary from the outer shell outline. Use `pyclipper` (Clipper library bindings) for robust polygon offsetting, or a simpler approach for convex/near-convex shapes.
- Both of these functions will be reused by the orchestrator to bridge the Designer Agent's SCAD → the PCB Agent & TS router.

### Step 2 — Extend TS router for polygon board boundaries

Changes to `src/pcb/src/types.ts`:
- Add optional `boardOutline?: number[][]` to `BoardParameters` (polygon vertices in mm). When absent, current rectangular behavior is preserved (backward compat).

Changes to `src/pcb/src/grid.ts`:
- New private method `blockOutsidePolygon(outline: number[][])`: rasterizes the polygon onto the grid using a scanline or ray-casting point-in-polygon test. Every cell whose center falls outside the polygon is set to `BLOCKED`. Cells within `blockedRadius` of the polygon edge are also blocked (trace clearance from boundary).
- Modify constructor: if `board.boardOutline` is provided, call `blockOutsidePolygon()` instead of `blockBoardEdges()`. Grid dimensions still come from the polygon's bounding box (`boardWidth` / `boardHeight` computed from the polygon's extents).
- The `isInBounds()` / `isFree()` / `isBlocked()` methods remain unchanged — everything works through cell state, so the pathfinder works without modification.

Changes to `src/pcb/src/visualizer.ts`:
- Draw the actual polygon outline as a line overlay on the debug image, so the non-rectangular boundary is visible.

Changes to `src/pcb/src/cli.ts`:
- No changes needed — it already passes the full `BoardParameters` object through.

### Step 3 — Update TS Router Bridge to pass polygon

Changes to `src/pcb_python/ts_router_bridge.py` `_convert_layout()`:
- Read `pcb_layout["board"]["outline_polygon"]` and pass it as `boardOutline` in the router input (currently it only extracts the bounding box).
- Compute `boardWidth` / `boardHeight` from the bounding box as before (the grid still needs these for array allocation), but now also include the polygon itself.
- The conversion is: `"boardOutline": [[p[0], p[1]] for p in outline]`.

### Step 4 — Refactor PCB Agent for arbitrary outlines and dynamic placement

Major changes to `src/pcb_python/pcb_agent.py`:

- New `generate_layout()` signature: accepts `outline_polygon` (from SCAD extraction) and `button_positions` (from Designer Agent) as optional parameters. When provided, these override the computed rectangle / auto-placed buttons.
- New utility `_point_in_polygon(x, y, polygon) -> bool`: ray-casting algorithm for arbitrary polygon containment checks.
- New utility `_inset_polygon(polygon, margin) -> polygon`: calls the same inset logic from Step 1 (or inlined simplified version).
- Refactored `_clamp_to_board()` → `_clamp_to_polygon()`: instead of `max(min_margin, min(board_width - min_margin, x))`, finds the nearest valid position inside the inset polygon. For components that end up outside, projects them to the nearest point on the polygon boundary.
- Refactored `_place_battery_smart()`: instead of hardcoded "bottom center," searches for the best position inside the polygon that:
  - Is inside the inset polygon (with battery footprint clearance)
  - Does not overlap any fixed button positions
  - Maximizes distance from buttons (to leave room for traces)
  - Prefers the "bottom" region (largest y-distance from buttons)
  - Uses a grid-scan approach: evaluate candidate positions on a coarse grid (e.g. 2mm steps) inside the polygon, score each by distance-from-buttons and distance-from-edges.
- Refactored `_place_controller_smart()`: same grid-scan approach — find position inside polygon that avoids buttons and battery, prefers center area.
- Refactored `_place_diode()`: place near the polygon boundary at the "top" end (max-Y region of the polygon), pointing outward. Find the edge segment closest to the top and place the diode centered on it.
- New `_place_buttons_from_designer()`: takes the Designer Agent's button positions (in shell coordinates), transforms them to board coordinates (offset by wall inset), validates each is inside the board polygon, returns button components at those exact positions. Reports any out-of-bounds buttons in the optimization feedback.
- The `reserved_regions` system (rectangular boxes) is replaced with a spatial conflict check: for each candidate placement, verify no overlap with any already-placed component's keepout circle/rectangle.

### Step 5 — Optimization report for designer feedback

New schema `schemas/optimization_report.schema.json`:
```json
{
  "feasible": "bool",
  "problems": [{
    "type": "battery_no_fit | trace_failed | component_outside_outline | button_outside_outline | outline_too_narrow | component_overlap",
    "component_id": "string",
    "description": "string (human-readable, LLM will read this)",
    "region": {"x": 0, "y": 0, "w": 0, "h": 0},
    "suggestion": "string (e.g. 'Widen outline near y=20-40mm by at least 8mm')"
  }],
  "placed_components": [{"id": "", "type": "", "center": [0, 0], "status": "placed | failed"}],
  "routing_summary": {"total_nets": 0, "routed_nets": 0, "failed_nets": [""]}
}
```

The PCB agent populates the component-placement problems. The TS router result populates the routing summary. Together they form the optimization report that gets sent back to the Designer Agent for the next iteration.

### Step 6 — Orchestrator wiring (new states)

Add to `PipelineState` in `src/core/state.py`:
- `DESIGN_SHELL` — Designer Agent generates/iterates SCAD
- `EXTRACT_OUTLINE` — SCAD → DXF → polygon extraction
- `OPTIMIZE_INTERNALS` — PCB Agent (with custom outline) + TS Router
- `CHECK_OPTIMIZATION` — evaluate optimization report, loop or proceed
- `ASSEMBLE_FINAL` — merge shell SCAD + internal geometry → render STLs

New `PipelineContext` fields: `shell_scad: str`, `outline_polygon: list`, `button_positions: list`, `optimization_report: dict`, `design_iteration: int`.

Orchestrator flow in `src/core/orchestrator.py`:
1. `_design_shell()` → calls Designer Agent with design_spec + optional optimization feedback → gets SCAD + button positions
2. `_extract_outline()` → calls `extract_outline_from_scad()` → stores polygon, streams shell STL preview to web UI
3. `_optimize_internals()` → calls modified PCB Agent with polygon + fixed button positions → calls TS Router with polygon → builds optimization report
4. `_check_optimization()` → if feasible, proceed to assembly; if not, send report back to `_design_shell()` (max N iterations)
5. `_assemble_final()` → merge shell with functional cutouts (button holes, trace channels, pinholes, battery cutout) → render final STLs

### Step 7 — Backward compatibility (no designer → rectangular fallback)

When no `style_description` is present in the design_spec (or designer is disabled), the orchestrator skips `DESIGN_SHELL` / `EXTRACT_OUTLINE` and falls into the existing rectangular flow. The PCB agent's `generate_layout()` without `outline_polygon` parameter works exactly as today. The TS router without `boardOutline` uses `blockBoardEdges()` as today. Zero regression.

## Verification

- **Router polygon masking test**: Create a circular outline polygon, verify cells outside the circle are blocked and A* routes correctly around the curved boundary. Verify rectangular fallback is identical to current behavior.
- **DXF extraction test**: Generate a simple SCAD shell (e.g. hull of two circles), extract outline, verify polygon matches expected shape within grid resolution tolerance.
- **Dynamic placement test**: Given an L-shaped outline with 2 buttons fixed by designer, verify battery/controller/diode are placed in valid positions inside the L, not in the cutout region.
- **Optimization feedback loop test**: Create an outline too narrow for the battery, verify the optimization report says `battery_no_fit` with a useful suggestion, and the designer agent can respond.
- **Regression test**: Run existing test suite with no `boardOutline` / no `style_description` — all existing outputs unchanged.

## Decisions

- **DXF over SVG for outline extraction**: OpenSCAD's DXF output gives precise vertex coordinates; SVG gives paths with curves that need additional parsing. DXF is the cleaner machine-readable format for polygons.
- **Grid-scan for dynamic placement over optimization solver**: A brute-force grid scan (2mm resolution → ~hundreds of candidates for a typical remote) is simple, fast, and debuggable versus a constraint solver. Good enough for 3 components.
- **PCB conforms to outline shape**: More usable area than inscribed rectangle, and the grid-masking approach in the router handles it cleanly.
- **`pyclipper` for polygon inset**: Battle-tested C++ Clipper library with Python bindings, handles concave polygons, holes, and edge cases that a naive offset would break on.

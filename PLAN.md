# Implementation Plan

## Overview

A device designer where an LLM agent picks components from a catalog, decides how they connect electrically, designs the device outline, and places UI elements — then automated stages (placer, router, SCAD generator, manufacturing pipeline) handle the rest.

The physical manufacturing process:
1. **3D printer** (PLA) prints the enclosure shell in a single continuous print, with two pauses
2. **Silver ink printer** (laserjet-like, separate machine) deposits conductive traces on the ironed floor surface during the first print pause
3. **Component insertion** during the second pause — pins poke down through holes into the ink traces
4. **3D printer** resumes and seals the ceiling

```
User request
    ↓
┌─────────────────────────────────────────────────────┐
│  AGENT (LLM)                                        │
│  Reads: component catalog (catalog/*.json)           │
│  Outputs:                                            │
│    1. Component list + quantities                    │
│    2. Net list (which pins connect to which)         │
│    3. Device outline polygon (with corner easing)    │
│    4. UI component positions (buttons, LEDs, etc.)   │
│    5. Mounting style overrides (LED: top vs side)    │
└─────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────┐
│  PLACER                                              │
│  Reads: agent output + component dimensions          │
│  Places: non-UI components (MCU, battery, resistors, │
│          transistors, capacitors)                     │
│  Optimizes: minimize total trace length, no overlaps,│
│             respect keepouts and routing-blocked zones│
│  Outputs: full placement (all components with x,y,r) │
└─────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────┐
│  ROUTER                                              │
│  Reads: placement + net list                         │
│  Does: Manhattan routing inside outline polygon      │
│  Also: dynamic pin allocation (MCU GPIO ↔ buttons)   │
│  Outputs: trace paths + final pin assignments        │
└─────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────┐
│  SCAD GENERATOR                                      │
│  Reads: outline, placement, traces, component specs  │
│  Generates: enclosure shell, trace channels,         │
│             component cutouts (top/side/bottom/       │
│             internal), lid                            │
│  Outputs: .scad files → compiled to .stl             │
└─────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────┐
│  MANUFACTURING PIPELINE                              │
│  1. Slice STL → G-code (PrusaSlicer CLI)             │
│  2. Compute pause Z-heights (ink layer, component    │
│     insertion layer)                                 │
│  3. Generate ink trace pattern (for silver ink        │
│     printer — coordinates in mm on the floor surface)│
│  4. Post-process G-code: inject M601 pauses at the   │
│     two Z-heights                                    │
│  Outputs: staged .gcode + ink trace pattern          │
└─────────────────────────────────────────────────────┘
```

### Physical Z-layer cross-section

The enclosure is one continuous print with two pauses. From bottom to top:

```
Z=0          ┌──────────────────────────────────┐
             │          SOLID FLOOR              │  PLA, 2mm thick
Z=2.0 (ink)  ├──────────────────────────────────┤  ← PAUSE 1: iron surface, then
             │  ░░░ CONDUCTIVE INK TRACES ░░░   │    silver ink printer deposits traces
             │  ┊  (in shallow channels)   ┊    │    on the ironed floor
Z=3.0        ├──┊──────────────────────────┊────┤
             │  ┊     CAVITY / AIR         ┊    │  Walls print around components
             │  ┊  ┌───────┐               ┊    │  Pinholes connect component pins
             │  ┊  │ comp  │← pocket       ┊    │  down to the trace layer
             │  ↓  │ pins↓ │               ┊    │
             │  ●──┤ ● ● ●├───────────────●    │  ● = pinhole (0.7mm Ø, goes from
             │     └───────┘                    │      pocket floor to ink layer)
Z=h-2 (ins)  ├──────────────────────────────────┤  ← PAUSE 2: insert components
             │          SOLID CEILING            │    (drop into pockets, pins contact ink)
Z=h          └──────────────────────────────────┘    then resume to seal ceiling
```

Key dimensions:
- **Floor:** 0–2mm solid PLA
- **Ink layer:** Z=2mm — the ironed floor surface where traces are deposited
- **Pinholes:** 0.7mm diameter, drilled from Z=3mm down to Z=0.5mm (reaching into the ink layer)
- **Cavity:** Z=3mm to Z=(h-2mm) — where components sit
- **Ceiling:** top 2mm solid PLA, with holes for top-mount components (buttons, LEDs)

---

## Stage 1: Catalog Loader ✅

**Goal:** Load all `catalog/*.json` files into Python dataclasses.

**Files:** `src/catalog/` package — `models.py`, `loader.py`, `serialization.py`, `__init__.py`

**Status:** Complete. All 11 catalog components load and validate. Also includes `catalog_to_dict()` and `component_to_dict()` serialization for the web API.

**What it does:**
- Glob `catalog/*.json`, parse each into a `Component` dataclass
- Provide `load_catalog() -> CatalogResult` and `get_component(catalog, id) -> Component`
- Validate on load: pin IDs unique, internal_nets reference valid pins, pin_groups reference valid pins, body dimensions > 0
- Returns `CatalogResult` with `.components` list and `.errors` list (`.ok` property)

**Dataclasses** (mirrors the JSON schema exactly):
```
Component
  id: str
  name: str
  description: str
  category: str        # "indicator"|"switch"|"passive"|"active"|"power"|"mcu"
  ui_placement: bool
  body: Body
  mounting: Mounting
  pins: list[Pin]
  internal_nets: list[list[str]]
  pin_groups: list[PinGroup] | None      # MCU only
  configurable: dict | None              # e.g. resistor value
  source_file: str                       # path of source JSON (for errors)

Body
  shape: str           # "rect"|"circle"
  width_mm: float | None
  length_mm: float | None
  diameter_mm: float | None
  height_mm: float

Cap
  diameter_mm: float
  height_mm: float
  hole_clearance_mm: float

Hatch
  enabled: bool
  clearance_mm: float
  thickness_mm: float

Mounting
  style: str           # "top"|"side"|"internal"|"bottom"
  allowed_styles: list[str]
  blocks_routing: bool
  keepout_margin_mm: float
  cap: Cap | None
  hatch: Hatch | None

Pin
  id: str
  label: str
  position_mm: tuple[float, float]
  direction: str       # "in"|"out"|"bidirectional"
  voltage_v: float | None
  current_max_ma: float | None
  hole_diameter_mm: float
  description: str

PinGroup
  id: str
  pin_ids: list[str]
  description: str
  fixed_net: str | None
  allocatable: bool
  capabilities: list[str] | None

CatalogResult
  components: list[Component]
  errors: list[ValidationError]
  ok: bool             # property: len(errors) == 0
```

---

## Stage 2: Design Schema ✅

**Goal:** Define the exact JSON schema the agent outputs after reasoning.

**Files:** `src/pipeline/design/` package — `models.py`, `parsing.py`, `validation.py`, `serialization.py`, `__init__.py`

**Status:** Complete. Includes `parse_design()`, `validate_design()`, and `design_to_dict()` serialization.

**What the agent decides:**

```json
{
  "components": [
    {"catalog_id": "battery_holder_2xAAA", "instance_id": "bat_1"},
    {"catalog_id": "atmega328p_dip28",     "instance_id": "mcu_1"},
    {"catalog_id": "resistor_axial",       "instance_id": "r_1", "config": {"resistance_ohms": 150}},
    {"catalog_id": "led_5mm_red",          "instance_id": "led_1", "mounting_style": "top"},
    {"catalog_id": "tactile_button_6x6",   "instance_id": "btn_1"},
    {"catalog_id": "tactile_button_6x6",   "instance_id": "btn_2"},
    {"catalog_id": "capacitor_100nf",      "instance_id": "c_1"},
    {"catalog_id": "resistor_axial",       "instance_id": "r_reset"}
  ],

  "nets": [
    {"id": "VCC",         "pins": ["bat_1:V+", "mcu_1:VCC1", "mcu_1:AVCC", "r_reset:1", "c_1:1"]},
    {"id": "GND",         "pins": ["bat_1:GND", "mcu_1:GND1", "mcu_1:GND2", "led_1:cathode", "c_1:2", "btn_1:B", "btn_2:B"]},
    {"id": "LED_DRIVE",   "pins": ["r_1:2", "led_1:anode"]},
    {"id": "MCU_TO_R1",   "pins": ["mcu_1:gpio", "r_1:1"]},
    {"id": "BTN1_SIG",    "pins": ["btn_1:A", "mcu_1:gpio"]},
    {"id": "BTN2_SIG",    "pins": ["btn_2:A", "mcu_1:gpio"]},
    {"id": "RESET_PU",    "pins": ["r_reset:2", "mcu_1:PC6"]}
  ],

  "outline": [
    {"x": 0, "y": 0},
    {"x": 50, "y": 0},
    {"x": 50, "y": 120, "ease_in": 10},
    {"x": 0, "y": 120, "ease_in": 10}
  ],

  "ui_placements": [
    {"instance_id": "btn_1", "x_mm": 15, "y_mm": 60},
    {"instance_id": "btn_2", "x_mm": 35, "y_mm": 60},
    {"instance_id": "led_1", "x_mm": 25, "y_mm": 100}
  ]
}
```

**Key rules:**
- `"instance_id:pin_id"` is the universal pin address format
- When a net references `"mcu_1:gpio"`, the pin is **unresolved** — the router will pick the best physical MCU pin from the gpio pin_group. This is how dynamic pin allocation works. The agent uses group references for flexibility; the router resolves them to physical pins. Components with `internal_nets` (like buttons with pins 1↔2 shorted, 3↔4 shorted) use group references (`"btn_1:A"`, `"btn_1:B"`) instead of raw pin IDs.
- The agent only places `ui_placement: true` components. Everything else is auto-placed.
- **Outline is a flat list** of vertex objects. Each vertex has `x`, `y` and optional `ease_in`/`ease_out` (mm) for corner rounding. Sharp corners omit both. If only one ease value is provided, the other mirrors it.
- Winding is clockwise.
- A pin can only appear in one net (group refs are dynamic — each use allocates a different pin).
- **Side-mount UI placements** include `edge_index` to specify which outline edge the component protrudes through.

**Dataclasses:**
```
DesignSpec
  components: list[ComponentInstance]
  nets: list[Net]
  outline: Outline
  ui_placements: list[UIPlacement]

ComponentInstance
  catalog_id: str
  instance_id: str
  config: dict | None
  mounting_style: str | None   # override from allowed_styles

Net
  id: str
  pins: list[str]   # "instance_id:pin_id" or "instance_id:group_id" for dynamic

OutlineVertex
  x: float
  y: float
  ease_in: float     # mm along incoming edge where curve begins (0 = sharp)
  ease_out: float    # mm along outgoing edge where curve ends (0 = sharp)

Outline
  points: list[OutlineVertex]
  vertices: property  # list[tuple[float, float]] — for polygon ops

UIPlacement
  instance_id: str
  x_mm: float
  y_mm: float
  edge_index: int | None   # side-mount only: which outline edge (0-based)
```

**Validation** (run before passing to placer):
- All catalog_ids exist in catalog
- All instance_ids unique
- All pin references in nets are valid (pin exists on that component, or group exists for dynamic)
- Mounting style overrides must be in `allowed_styles`
- Config keys must exist in component's `configurable` dict
- Group allocation counts don't exceed pool size
- UI placements only reference ui_placement=true components
- All ui_placement=true components must have a placement
- Side-mount UI placements must have `edge_index`; non-side-mount must not
- Outline is valid polygon (>= 3 vertices, non-self-intersecting, positive area)
- UI placements (non-side-mount) must be inside the outline polygon (Shapely)

---

## Stage 3: Placer

**Goal:** Auto-place all non-UI components inside the outline, optimizing for short traces and no overlaps.

**Files:** `src/pipeline/placer.py`

**Status:** 🔜 Next to implement.

**Input:** `DesignSpec` + loaded `CatalogResult`

**Output:** `FullPlacement` — adds (x, y, rotation_deg) for every component not in ui_placements. Saved to `session/placement.json`.

```
PlacedComponent
  instance_id: str
  catalog_id: str
  x_mm: float
  y_mm: float
  rotation_deg: int    # 0, 90, 180, 270

FullPlacement
  components: list[PlacedComponent]   # ALL components (UI + auto-placed)
  outline: Outline                    # pass-through
  nets: list[Net]                     # pass-through
```

**Algorithm:**

1. Start with UI components at their fixed positions. For UI components that are side-mount (`edge_index` is set), snap them to the specified outline edge and compute the correct rotation so their active face is flush against the wall.
2. Sort remaining (non-UI) components by size (largest first — battery, MCU, then small passives).
3. **Side-mount non-UI components** (if any): must be placed with their active face flush against an outline wall. The placer scans along each outline edge, testing positions where the component protrudes through the wall. Rotation is constrained to align the component's active face with the wall normal.
4. For each non-side-mount component, grid-scan over the inset polygon (inset by keepout_margin_mm):
   - Check: fits inside outline, no overlap with already-placed components (using AABB + margin)
   - Check: if blocks_routing=true, ensure it doesn't block needed routing corridors
   - Score: weighted sum of:
     - **Net proximity** — sum of distances from this component's net-connected pins to their connected components' pins (lower = better). This is the main driver.
     - **Edge clearance** — distance to nearest outline edge (higher = better, with a minimum threshold)
     - **Compactness** — distance to centroid of all placed components (lower = better)
   - Try all valid rotations (0, 90, 180, 270).
   - Pick position + rotation with best score.

**Reference:** `old/src/pcb/placer.py` lines 80-300 — the grid scan logic and clearance scoring. The old code special-cases each component type; the new code is generic.

**Test:** Place a 2xAAA battery + resistor inside a 30×80mm rectangle with a button and LED pre-placed (the flashlight fixture). Verify no overlaps, all inside outline.

---

## Stage 4: Router

**Goal:** Manhattan trace routing between all net pins, inside the device outline.

**Files:** `src/pipeline/router.py`

**Input:** `FullPlacement` + net list + component pin specs

**Output:**
```
RoutingResult
  traces: list[Trace]
  pin_assignments: dict[str, str]   # "mcu_1:gpio" -> "mcu_1:PD2"
  failed_nets: list[str]

Trace
  net_id: str
  path: list[tuple[float, float]]   # waypoints in mm, Manhattan segments
```

**Algorithm:**

1. **Resolve pin positions.** For each net, convert every `"instance_id:pin_id"` into world coordinates:
   - Look up the component's placed position (x, y, rotation) from `FullPlacement`
   - Look up the pin's relative position from the catalog
   - Apply rotation and translate to world coordinates

2. **Build routing grid.** Discretize the outline polygon to a grid (0.5mm resolution):
   - Mark cells outside the outline as blocked
   - Mark cells under `blocks_routing=true` components as blocked
   - Mark cells inside component keepout zones as high-cost (not blocked, but penalized)

3. **Route nets.** For each net, connect all pins using A* pathfinding:
   - For 2-pin nets: A* between the two pin positions (Manhattan movement only)
   - For 3+ pin nets: iteratively connect the nearest unconnected pin to the existing tree (greedy Steiner approximation)
   - After routing each net, mark those cells as occupied (with clearance buffer) so subsequent nets avoid them

4. **Net ordering.** Route shorter/simpler nets first — they're less likely to cause blockages.

**Dynamic pin allocation:** When a net references `"mcu_1:gpio"` (a group ID instead of a specific pin), the router picks the optimal physical MCU pin from that group to minimize trace length. The router tries all unassigned pins in the group and picks the one closest to the other pin(s) in the net. After routing, `pin_assignments` maps each group reference to the chosen pin (e.g., `"mcu_1:gpio" → "mcu_1:PD2"`).

**Reference:** `old/pcb/src/router.ts` — the TypeScript A* router. Port the core pathfinding to Python. `old/pcb/src/pathfinder.ts` has the A* implementation. `old/pcb/src/grid.ts` has the grid setup.

**Test:** Route the flashlight design (4 nets, each 2-pin). Verify all traces are Manhattan, within outline, no crossings.

---

## Stage 5: SCAD Generator

**Goal:** Generate OpenSCAD enclosure files from placement + routing data.

**Files:** `src/pipeline/scad.py`

**Input:** `FullPlacement` + `RoutingResult` + component specs + outline

**Output:** `.scad` file(s) as strings, compiled to `.stl`

**Enclosure structure** matches the Z-layer cross-section above. The SCAD generates a solid shell with cutouts subtracted via `difference()`.

**Cutout types by mounting style:**

| `mounting.style` | Cutout behavior |
|---|---|
| `"top"` | Hole punched through the ceiling (buttons, upward-facing LEDs). Component pocket in the cavity. |
| `"side"` | Hole punched through the wall at the component's placed edge. The component body protrudes through the wall, with a shaped cutout matching the body cross-section + clearance. Component pocket in the cavity extends to the wall. The placer positions side-mount components so their active face aligns with the nearest outline edge. |
| `"internal"` | Pocket in the cavity only — no external holes (resistors, MCU, transistors, caps). |
| `"bottom"` | Hole/hatch through the floor (battery holders). `blocks_routing=true` means no trace channels below. |

**Cutouts generated:**
1. **Component pockets** — body-shaped cavities in the cavity space, sized from `body` + `keepout_margin_mm`
2. **Pinholes** — small cylinders (0.7mm Ø) at each pin's world position, from pocket floor down through to the ink layer
3. **Trace channels** — shallow grooves (0.4mm deep, `trace_width` wide) on the floor surface for ink to sit in
4. **Top holes** — for `"top"` mount components, punched through the ceiling (with button cap clearance)
5. **Side holes** — for `"side"` mount components, punched through the wall
6. **Bottom hatch** — for `"bottom"` mount components, removable panel in the floor

**SCAD construction approach:**
`polygon()` + `linear_extrude()` + `difference()` + `union()` for fillet stacking. No hull(), no high-$fn cylinders.

**Corner rounding:** Each outline vertex with non-zero `ease_in`/`ease_out` gets a rounded corner. `ease_in` controls how far along the incoming edge the curve starts; `ease_out` controls how far along the outgoing edge the curve ends. These define two control points for a quadratic Bezier curve sampled at sufficient resolution (e.g., 10–20 segments). Equal `ease_in`/`ease_out` gives a symmetric arc; different values give an asymmetric/oblong curve.

**Fillet:** The enclosure gets a stacked-layer quarter-circle fillet on all vertical edges. Each Z-layer's polygon is inset by `r - sqrt(r² - (z-z₀)²)` where `r` is the fillet radius (e.g., 2mm) and `z₀` is the fillet start height. This is implemented as a `union()` of `linear_extrude(height=layer_h)` slices at each layer, matching the approach in the old code (`old/src/scad/shell.py`).

**Reference:** `old/src/scad/shell.py` for the shell construction. `old/src/scad/cutouts.py` for component cutout generation — same logic, now generic via `mounting.style`.

**Test:** Generate SCAD for the flashlight, compile with OpenSCAD CLI, verify it renders without errors. Inspect that fillet layers are present and outline edge rounding is applied to any `"round"` edges.

---

## Stage 6: Manufacturing Pipeline

**Goal:** Turn the SCAD output + routing data into printable files.

**Files:** `src/pipeline/manufacturing.py`

**This is the bridge to physical fabrication.** It takes the compiled STL and routing data and produces everything needed for the two-machine manufacturing process.

**Input:** compiled `.stl` + `RoutingResult` + `FullPlacement` + enclosure dimensions

**Output:**
```
ManufacturingResult
  staged_gcode_path: Path       # G-code with ironing, M601 pauses, trace highlights
  ink_trace_svg_path: Path      # SVG file for the silver ink printer (1:1 scale)
  ink_trace_paths: list[Trace]  # Trace paths in mm (world coords on floor surface)
  pause_points: PausePoints     # Z-heights for the two pauses
  ink_layer_z: float            # Z-height of the ink deposition surface
```

**Pipeline steps:**

0. **Configure slicer profile for ironing:**
   - Enable ironing on the floor top surface (Z=2mm) in the slicer profile
   - This is set via PrusaSlicer config overrides passed to the CLI

1. **Compute pause Z-heights:**
   - Ink layer Z = floor thickness (2.0mm), snapped to nearest layer boundary
   - Component insertion Z = shell_height - ceiling thickness, snapped to layer
   - (See `old/src/gcode/pause_points.py` — straightforward arithmetic)

2. **Slice STL → G-code:**
   - Call PrusaSlicer CLI with the printer profile
   - (See `old/src/gcode/slicer.py` — subprocess call to `prusa-slicer-console`)

3. **Generate ink trace data:**
   - Convert router trace paths from grid coordinates to world mm coordinates
   - These are the paths the silver ink printer needs to deposit on the floor surface
   - Output as mm-coordinate polylines with net labels AND convert to the ink printer's native format (SVG with trace paths at 1:1 scale, suitable for the laserjet-like silver ink printer)
   - Also generate pad landing areas at each pin position (small filled circles/rectangles for reliable contact)
   - (See `old/src/gcode/ink_traces.py` for the old G-code-based approach)

4. **Add ironing pass:**
   - Configure slicer profile to iron the top surface of the floor layer (Z=2mm)
   - This creates a smooth, flat surface for reliable ink adhesion
   - (See `old/src/gcode/postprocessor.py` for how the old code handled ironing layers)

5. **Post-process G-code:**
   - Walk through the slicer G-code, find layer-change markers
   - At ink_layer_z: inject `M601` pause (printer stops, user moves to ink printer)
   - At component_insert_z: inject `M601` pause (printer stops, user inserts components)
   - Also inject a trace highlight pass: after the ink pause, extrude a thin PLA line over each trace path to protect and insulate the silver ink
   - (See `old/src/gcode/postprocessor.py` — regex-based line scanner)

**Reference:** The entire `old/src/gcode/` package. The pipeline orchestrator is `old/src/gcode/pipeline.py`. The postprocessor handles ironing, trace highlight passes, and pause injection — we implement all of these.

**Test:** Generate manufacturing output for the flashlight. Verify G-code has exactly two M601 pauses at the correct Z-heights, ironing is enabled on the floor layer, and trace highlight G-code is present. Verify ink trace SVG output has valid paths within the outline with pad landings at pin positions.

---

## Stage 7: Agent Integration ✅

**Goal:** Wire the LLM agent to read the catalog and output a valid DesignSpec.

**Files:** `src/agent/` package — `config.py`, `tools.py`, `prompt.py`, `messages.py`, `core.py`, `__init__.py`

**Status:** Complete. Uses Claude Sonnet 4.6 with extended thinking and the Anthropic streaming API. Yields token-level deltas for real-time UI updates.

**Package layout:**
- `config.py` — constants: `MODEL`, `MAX_TOKENS`, `THINKING_BUDGET`, `MAX_TURNS`, `TOKEN_BUDGET`
- `tools.py` — Anthropic tool definitions (`list_components`, `get_component`, `submit_design`)
- `prompt.py` — `_build_system_prompt()`, `_catalog_summary()`
- `messages.py` — `_serialize_content()`, `_sanitize_messages()`, `_prune_messages()`
- `core.py` — `DesignAgent` class, `AgentEvent` dataclass

**System prompt construction:**
- Catalog summary table in system prompt (ID, category, name, pin count, mounting style)
- Full design rules: components, nets (with dynamic pin allocation), outline format (vertex-based with ease_in/ease_out), UI placements (with edge_index for side-mount)
- Flashlight example included in system prompt

**Tool interface:**
The agent has three tools:
1. `list_components` — catalog summary table (also in system prompt)
2. `get_component(component_id)` — full component details as JSON
3. `submit_design(components, nets, outline, ui_placements)` — parses, validates via `validate_design()`, saves `design.json` to session

**Error recovery:**
- If validation fails, error details returned to agent for self-correction
- Agent retries via the conversation loop (up to `MAX_TURNS=25`)

**Message pruning:**
- Old `get_component`/`list_components` tool results (older than 6 assistant turns) replaced with `"[pruned]"` in the API-sent messages
- Keeps tool_use IDs intact so the API message structure remains valid
- Only affects what goes to the API — full history preserved on disk in `conversation.json`

**Agent loop pattern:**
- `DesignAgent(catalog, session)` — loads existing conversation from session for multi-turn
- `async for event in agent.run(prompt)` — yields `AgentEvent` objects (thinking_start/delta, message_start/delta, block_stop, tool_call, tool_result, token_usage, design, error, done)
- Conversation persisted to `conversation.json` in session folder
- Content block serialization strips SDK extras (parsed_output, citations, etc.) to avoid API rejection on re-submission

---

## Stage 8: Web UI ✅

**Goal:** Browser-based chat interface that shows design previews in real-time.

**Files:** `src/web/` (server + static assets)

**Status:** Complete (design stage). FastAPI server with SSE streaming. Serves static HTML/CSS/JS.

**Server** (`src/web/server.py`):
- Session management API: create, list, load sessions
- Catalog API: load, reload, per-component lookup
- Design agent API: SSE-streaming endpoint that auto-creates sessions
- Session-scoped artifact access (catalog, design, placement, routing)
- Token counting endpoint (`/api/session/tokens`) using Anthropic `count_tokens()` API
- Loads `.env` / `.env.local` for API keys

**Session naming** (`src/web/naming.py`):
- Auto-generates session names via Claude Haiku after design submission
- Uses only the last 3 user turns (not full conversation) for efficiency

**Static UI** (`src/web/static/`):
- Chat interface with real-time thinking/message streaming
- Design viewport that renders the outline polygon with corner easing curves (Y-axis flipped: math Y-up → SVG Y-down)
- Component visualization (body shapes, pins, UI placements, side-mount markers)
- Token usage pie chart above the send button
- Session picker (list, create, resume sessions)

**What remains for later stages:** Preview updates for placement, routing, and 3D model views will be added as those pipeline stages are built.

## Session System ✅ (infrastructure, not in original plan)

**Files:** `src/session.py` (stays as a flat module — small and cohesive)

**Status:** Complete. Added as cross-cutting infrastructure for the pipeline.

Each session is a folder under `outputs/sessions/<session_id>/` containing:
- `session.json` — metadata (id, created, last_modified, name, description, pipeline_state)
- `catalog.json` — catalog snapshot at session creation time
- `conversation.json` — agent conversation history
- `design.json` — agent's DesignSpec (once submitted)
- `placement.json` — placer output (future)
- `routing.json` — router output (future)
- `enclosure.scad` / `enclosure.stl` (future)
- `manufacturing/` — G-code + ink SVG (future)

Session IDs are timestamp-based (e.g. `20260227_000915`). The `pipeline_state` dict tracks which stages are complete (e.g. `{"catalog": "loaded", "design": "complete"}`).

**API:** `create_session()`, `load_session(id)`, `list_sessions()`, `session.write_artifact()`, `session.read_artifact()`, `session.has_artifact()`.

---

## Build Order

| Step | What | Status | Depends on |
|------|------|--------|------------|
| **1** | `src/catalog/` — catalog loader + dataclasses | ✅ Done | catalog/*.json |
| **2** | `src/pipeline/design/` — DesignSpec dataclasses + validation | ✅ Done | Stage 1 |
| **∗** | `src/session.py` — session management | ✅ Done | — |
| **7** | `src/agent/` — LLM integration | ✅ Done | Stage 1, 2 |
| **8** | `src/web/` — browser UI (design stage) | ✅ Done | Stage 1, 2, 7 |
| **3** | `src/pipeline/placer.py` — component placement | 🔜 Next | Stage 1, 2 |
| **4** | `src/pipeline/router.py` — trace routing + dynamic pins | Not started | Stage 1, 2, 3 |
| **5** | `src/pipeline/scad.py` — enclosure generation | Not started | Stage 1–4 |
| **6** | `src/pipeline/manufacturing.py` — slice, pause points, ink traces, G-code | Not started | Stage 1–5 |

The agent and web UI were built early (before the mechanical pipeline) because the design stage is independent of placement/routing/SCAD. The pipeline stages (3–6) are built next, each reading the prior stage's output from the session folder.

For stages 3–6, we use a **hardcoded flashlight DesignSpec** as the test fixture — no LLM needed.

---

## Deferred Features

Things we skip for now, to be added later:

| Feature | Now (skip) | Later |
|---|---|---|
| **Binary G-code** | ASCII .gcode only | .bgcode conversion |
| **Multiple printer profiles** | Single hardcoded printer | MK3S, Core One, etc. |

Everything else ships in the initial build — outline corner rounding, SCAD fillet, dynamic pin allocation, side-mount components, ink printer format conversion, ironing + trace highlight.

The goal is: **a hardcoded flashlight design goes through all 6 stages → produces a filleted .scad that renders, a .gcode with ironing/pauses/trace highlights, and ink trace output in the printer's native format.** The agent already produces valid designs; stages 3–6 complete the pipeline.

---

## Codebase Structure

After the refactoring, each domain is its own Python package with clear internal boundaries:

```
src/
  __init__.py
  __main__.py              ← python -m src
  session.py               ← session management (flat module)
  catalog/                  ← Stage 1
    __init__.py             ← re-exports public API
    models.py               ← Body, Cap, Hatch, Mounting, Pin, PinGroup, Component, etc.
    loader.py               ← load_catalog(), get_component(), validation, parsing
    serialization.py        ← catalog_to_dict(), component_to_dict()
  agent/                    ← Stage 7 (LLM agent, calls into pipeline)
    __init__.py             ← re-exports
    config.py               ← MODEL, THINKING_BUDGET, TOKEN_BUDGET, etc.
    tools.py                ← TOOLS list (Anthropic format)
    prompt.py               ← _build_system_prompt(), _catalog_summary()
    messages.py             ← _serialize_content(), _sanitize_messages(), _prune_messages()
    core.py                 ← DesignAgent, AgentEvent
  pipeline/                 ← Stages 2–6 (all pipeline stages)
    __init__.py
    design/                 ← Stage 2 (design schema)
      __init__.py           ← re-exports
      models.py             ← ComponentInstance, Net, OutlineVertex, Outline, UIPlacement, DesignSpec
      parsing.py            ← parse_design(), _parse_outline()
      validation.py         ← validate_design()
      serialization.py      ← design_to_dict()
    placer.py               ← Stage 3 (future)
    router.py               ← Stage 4 (future)
    scad.py                 ← Stage 5 (future)
    manufacturing.py        ← Stage 6 (future)
  web/                      ← Stage 8
    __init__.py
    server.py               ← FastAPI routes
    naming.py               ← session naming via Claude Haiku
    static/                 ← HTML, CSS, JS
```

---

## Flashlight Test Fixture (End-to-End Validation)

The flashlight is the simplest possible device that exercises every stage. We hardcode this as `tests/flashlight_fixture.py` and use it to validate stages 3–6 without any LLM.

**Circuit:** Battery → button → resistor → LED → ground. No MCU.

```
     ┌──────────┐
     │ 2xAAA    │
     │ bat_1    │
     │ V+(3V)   │──── VCC net ────┐
     │ GND      │──── GND net ──┐ │
     └──────────┘               │ │
                                │ │
     ┌──────────┐               │ │
     │ Button   │               │ │
     │ btn_1    │               │ │
     │ pin 1  ←─│── SWITCHED ───│─┘  (button side A to VCC)
     │ pin 3  ←─│── BTN_GND ───│──── (button side B through resistor)
     └──────────┘               │
                                │
     ┌──────────┐               │
     │ Resistor │               │
     │ r_1 50Ω  │              │
     │ lead 1 ←─│── BTN_GND    │    (from button side B)
     │ lead 2 ←─│── LED_DRIVE  │
     └──────────┘               │
                                │
     ┌──────────┐               │
     │ Red LED  │               │
     │ led_1    │               │
     │ anode  ←─│── LED_DRIVE   │
     │ cathode←─│── GND net ────┘
     └──────────┘
```

**Hardcoded DesignSpec:**

```json
{
  "components": [
    {"catalog_id": "battery_holder_2xAAA", "instance_id": "bat_1"},
    {"catalog_id": "tactile_button_6x6",   "instance_id": "btn_1"},
    {"catalog_id": "resistor_axial",       "instance_id": "r_1", "config": {"resistance_ohms": 50}},
    {"catalog_id": "led_5mm_red",          "instance_id": "led_1", "mounting_style": "top"}
  ],

  "nets": [
    {"id": "VCC",       "pins": ["bat_1:V+", "btn_1:A"]},
    {"id": "BTN_GND",   "pins": ["btn_1:B", "r_1:1"]},
    {"id": "LED_DRIVE", "pins": ["r_1:2", "led_1:anode"]},
    {"id": "GND",       "pins": ["led_1:cathode", "bat_1:GND"]}
  ],

  "outline": [
    {"x": 0, "y": 0},
    {"x": 30, "y": 0},
    {"x": 30, "y": 80},
    {"x": 0, "y": 80}
  ],

  "ui_placements": [
    {"instance_id": "btn_1", "x_mm": 15, "y_mm": 45},
    {"instance_id": "led_1", "x_mm": 15, "y_mm": 70}
  ]
}
```

**What gets auto-placed:** `bat_1` (battery, bottom mount) and `r_1` (resistor, internal). Button and LED are pre-positioned by the user/agent.

**Expected pipeline results:**
- **Placer:** Battery at bottom of enclosure (~y=15), resistor somewhere between button and LED (~y=55-65)
- **Router:** 4 nets, each 2-pin, simple Manhattan paths. No dynamic pin allocation needed (no MCU)
- **SCAD:** 30×80mm filleted rectangle box. Button hole on top, LED hole on top, battery hatch on bottom, resistor pocket internal
- **Manufacturing:** G-code with ironing + 2 pauses + trace highlights, ink trace SVG with pad landings

**This fixture validates the entire data flow** from catalog JSON → dataclasses → placement → routing → SCAD → manufacturing output, without needing an LLM or any intelligence. It exercises the pipeline while the agent already produces more complex designs (like the IR remote with MCU, transistor, side-mount components, and dynamic pin allocation).

---

## Data Flow Summary

Every stage has a clear input/output boundary. Data flows forward only — no stage reaches back. All artifacts are persisted to the session folder.

```
catalog/*.json
    │
    ▼
┌─ CATALOG LOADER (✅) ─────────────────────────────────┐
│  CatalogResult                                        │
│    .components: list[Component]                       │
│    .errors: list[ValidationError]                     │
│  Saved to: session/catalog.json                       │
└───────────────────────────────────────────────────────┘
    │
    ▼
┌─ DESIGN SPEC (✅ from agent or test fixture) ─────────┐
│  DesignSpec:                                          │
│    .components   (what + how many)                    │
│    .nets         (which pins connect)                 │
│    .outline      (device shape w/ corner easing)      │
│    .ui_placements (UI positions + edge_index for side)│
│  Saved to: session/design.json                        │
└───────────────────────────────────────────────────────┘
    │
    ▼
┌─ PLACER (🔜 next) ────────────────────────────────────┐
│  FullPlacement:                                       │
│    .components  (ALL with x, y, rotation)             │
│    .outline     (pass-through)                        │
│    .nets        (pass-through)                        │
│  Saved to: session/placement.json                     │
└───────────────────────────────────────────────────────┘
    │
    ▼
┌─ ROUTER ──────────────────────────────────────────────┐
│  RoutingResult:                                       │
│    .traces          (Manhattan polylines in mm)        │
│    .pin_assignments (resolved dynamic pins, if any)   │
│    .failed_nets     (empty if all succeeded)           │
│  Saved to: session/routing.json                       │
└───────────────────────────────────────────────────────┘
    │
    ▼
┌─ SCAD GENERATOR ─────────────────────────────────────┐
│  .scad source string → compiled to .stl               │
│  Saved to: session/enclosure.scad + enclosure.stl     │
└───────────────────────────────────────────────────────┘
    │
    ▼
┌─ MANUFACTURING ───────────────────────────────────────┐
│  ManufacturingResult:                                 │
│    .staged_gcode_path   (ironing + M601 pauses +      │
│                          trace highlights)             │
│    .ink_trace_svg_path  (SVG for ink printer)          │
│    .ink_trace_paths     (mm polylines)                 │
│    .pause_points        (Z-heights)                   │
│  Saved to: session/manufacturing/                     │
└───────────────────────────────────────────────────────┘
```

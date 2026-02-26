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
│    3. Device outline polygon (with edge styles)      │
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

## Stage 1: Catalog Loader

**Goal:** Load all `catalog/*.json` files into Python dataclasses.

**Files:** `src/catalog.py`

**What it does:**
- Glob `catalog/*.json`, parse each into a `Component` dataclass
- Provide `load_catalog() -> list[Component]` and `get_component(id) -> Component`
- Validate on load: pin IDs unique, internal_nets reference valid pins, pin_groups reference valid pins, body dimensions > 0

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

Body
  shape: str           # "rect"|"circle"
  width_mm: float | None
  length_mm: float | None
  diameter_mm: float | None
  height_mm: float

Mounting
  style: str           # "top"|"side"|"internal"|"bottom"
  allowed_styles: list[str]
  blocks_routing: bool
  keepout_margin_mm: float
  cap: dict | None
  hatch: dict | None

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
  fixed_net: str | None
  allocatable: bool
  capabilities: list[str] | None
  description: str
```

**Test:** Load all catalog files, verify no validation errors, check counts.

---

## Stage 2: Agent Design Schema

**Goal:** Define the exact JSON schema the agent outputs after reasoning.

**Files:** `src/agent_schema.py`

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
    {"catalog_id": "resistor_10k_pullup",  "instance_id": "r_reset"}
  ],

  "nets": [
    {"id": "VCC",         "pins": ["bat_1:V+", "mcu_1:VCC1", "mcu_1:AVCC", "r_reset:1", "c_1:1"]},
    {"id": "GND",         "pins": ["bat_1:GND", "mcu_1:GND1", "mcu_1:GND2", "led_1:cathode", "c_1:2", "btn_1:3", "btn_2:3"]},
    {"id": "LED_DRIVE",   "pins": ["r_1:2", "led_1:anode"]},
    {"id": "MCU_TO_R1",   "pins": ["mcu_1:PD5", "r_1:1"]},
    {"id": "BTN1_SIG",    "pins": ["btn_1:1", "mcu_1:PD2"]},
    {"id": "BTN2_SIG",    "pins": ["btn_2:1", "mcu_1:PD3"]},
    {"id": "RESET_PU",    "pins": ["r_reset:2", "mcu_1:PC6"]}
  ],

  "outline": {
    "vertices": [[0,0], [50,0], [50,120], [0,120]],
    "edges": [
      {"style": "sharp"},
      {"style": "sharp"},
      {"style": "round", "curve": "ease_in_out", "radius_mm": 10},
      {"style": "round", "curve": "ease_in_out", "radius_mm": 10}
    ]
  },

  "ui_placements": [
    {"instance_id": "btn_1", "x_mm": 15, "y_mm": 60},
    {"instance_id": "btn_2", "x_mm": 35, "y_mm": 60},
    {"instance_id": "led_1", "x_mm": 25, "y_mm": 100}
  ]
}
```

**Key rules:**
- `"instance_id:pin_id"` is the universal pin address format
- When a net references `"mcu_1:gpio"`, the pin is **unresolved** — the router will pick the best physical MCU pin from the gpio pin_group. This is how dynamic pin allocation works. The agent uses group references for flexibility; the router resolves them to physical pins.
- The agent only places `ui_placement: true` components. Everything else is auto-placed.
- Each edge in `outline.edges[i]` describes the edge from `vertices[i]` to `vertices[(i+1) % n]`.
- Winding is clockwise.
- A pin can only appear in one net. If a pin needs to connect to a power rail, it's listed in the VCC or GND net directly.

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

Outline
  vertices: list[tuple[float, float]]
  edges: list[EdgeStyle]

EdgeStyle
  style: str         # "sharp"|"round"
  curve: str | None  # "ease_in"|"ease_out"|"ease_in_out"
  radius_mm: float | None

UIPlacement
  instance_id: str
  x_mm: float
  y_mm: float
```

**Validation** (run before passing to placer):
- All catalog_ids exist in catalog
- All instance_ids unique
- All pin references in nets are valid (pin exists on that component, or group exists for dynamic)
- UI placements only reference ui_placement=true components
- All ui_placement=true components have a placement
- Outline is valid polygon (>= 3 vertices, non-self-intersecting)

**Test:** Build a DesignSpec by hand for a simple flashlight (battery + resistor + LED + button, no MCU), validate it passes.

---

## Stage 3: Placer

**Goal:** Auto-place all non-UI components inside the outline, optimizing for short traces and no overlaps.

**Files:** `src/placer.py`

**Input:** `DesignSpec` + loaded `Component` catalog data

**Output:** `Placement` — adds (x, y, rotation_deg) for every component not in ui_placements.

```
PlacedComponent
  instance_id: str
  catalog_id: str
  x_mm: float
  y_mm: float
  rotation_deg: int    # 0, 90, 180, 270

FullPlacement
  components: list[PlacedComponent]   # ALL components (UI + auto-placed)
  outline: Outline
  nets: list[Net]
```

**Algorithm:**

1. Start with UI components at fixed positions.
2. Sort remaining components by size (largest first — battery, MCU, then small passives).
3. **Side-mount components** get special treatment: they must be placed with their active face flush against an outline wall. The placer scans along each outline edge, testing positions where the component protrudes through the wall. Rotation is constrained to align the component's active face with the wall normal.
4. For each non-side-mount component, grid-scan over the inset polygon (inset by keepout_margin_mm):
   - Check: fits inside outline, no overlap with already-placed components (using AABB + margin)
   - Check: if blocks_routing=true, ensure it doesn't block needed routing corridors
   - Score: weighted sum of:
     - **Net proximity** — sum of distances from this component's net-connected pins to their connected components' pins (lower = better). This is the main driver.
     - **Edge clearance** — distance to nearest outline edge (higher = better, with a minimum threshold)
     - **Compactness** — distance to centroid of all placed components (lower = better)
   - Try all valid rotations (0, 90, 180, 270).
   - Pick position + rotation with best score.

This is a greedy approach similar to what the old placer does (see `old/src/pcb/placer.py` `_place_rect()` and `_place_rect_with_rotation()` for the grid-scan pattern), but generalized to any component rather than special-casing battery/controller/diode.

**Reference:** `old/src/pcb/placer.py` lines 80-300 — the grid scan logic and clearance scoring. The old code special-cases each component type; the new code is generic.

**Test:** Place a 2xAAA battery + ATmega328P + resistor inside a 60x130mm rectangle with two buttons pre-placed. Verify no overlaps, all inside outline.

---

## Stage 4: Router

**Goal:** Manhattan trace routing between all net pins, inside the device outline.

**Files:** `src/router.py`

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

**Files:** `src/scad.py`

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

**Edge rounding:** When `outline.edges[i].style == "round"`, the edge is replaced with a Bezier curve (ease_in, ease_out, or ease_in_out) using `radius_mm` to define the control point offset. The SCAD polygon vertices are generated by sampling the Bezier curve at sufficient resolution (e.g., 20 segments per curve).

**Fillet:** The enclosure gets a stacked-layer quarter-circle fillet on all vertical edges. Each Z-layer's polygon is inset by `r - sqrt(r² - (z-z₀)²)` where `r` is the fillet radius (e.g., 2mm) and `z₀` is the fillet start height. This is implemented as a `union()` of `linear_extrude(height=layer_h)` slices at each layer, matching the approach in the old code (`old/src/scad/shell.py`).

**Reference:** `old/src/scad/shell.py` for the shell construction. `old/src/scad/cutouts.py` for component cutout generation — same logic, now generic via `mounting.style`.

**Test:** Generate SCAD for the flashlight, compile with OpenSCAD CLI, verify it renders without errors. Inspect that fillet layers are present and outline edge rounding is applied to any `"round"` edges.

---

## Stage 6: Manufacturing Pipeline

**Goal:** Turn the SCAD output + routing data into printable files.

**Files:** `src/manufacturing.py`

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

## Stage 7: Agent Integration

**Goal:** Wire the LLM agent to read the catalog and output a valid DesignSpec.

**Files:** `src/agent.py`

**System prompt construction:**
- Serialize the full catalog to the system prompt (component names, descriptions, pin descriptions, categories)
- Explain the DesignSpec JSON schema the agent must output
- Explain the rules: pick components, define nets, define outline, place UI components
- Include examples (the flashlight example from Stage 2)

**Tool interface:**
The agent has tools to:
1. `submit_design(design_spec_json)` — validates and runs the pipeline (place → route → SCAD → manufacturing)
2. `send_message(text)` — chat with the user
3. `preview_outline(outline, ui_placements)` — quick 2D preview before committing

**Error recovery:**
- If validation fails, return error details to agent for self-correction
- If placer fails (component doesn't fit), return what failed and suggestion
- If router fails (net unroutable), return which nets failed
- Agent retries silently (up to 3 times) before telling user

**Reference:** `old/src/agent/tools.py` for the tool pattern and event emission. `old/src/agent/prompts.py` for how the system prompt was structured (but the new one will be much more general since it's not remote-specific).

---

## Stage 8: Web UI

**Goal:** Browser-based chat interface that shows design previews in real-time.

**Files:** `src/web/` (server + static assets)

**This is largely the same as the old web UI** (`old/src/web/`). Server-sent events stream pipeline stages to the browser. The main changes:
- Preview renderer needs to handle arbitrary polygons (not just rectangles)
- Component visualization is generic (render body shapes from catalog data)
- Trace visualization from router output

**This stage is last because everything above can be tested from Python directly.**

---

## Build Order

| Step | What | Depends on | Test |
|------|------|------------|------|
| **1** | `src/catalog.py` — catalog loader + dataclasses | catalog/*.json | Load all 11 components, validate |
| **2** | `src/schema.py` — DesignSpec dataclasses + validation | Stage 1 | Hand-build flashlight spec, validate |
| **3** | `src/placer.py` — component placement | Stage 1, 2 | Place flashlight components, no overlaps |
| **4** | `src/router.py` — trace routing + dynamic pins | Stage 1, 2, 3 | Route flashlight nets, all succeed |
| **5** | `src/scad.py` — enclosure generation | Stage 1-4 | Render flashlight in OpenSCAD |
| **6** | `src/manufacturing.py` — slice, pause points, ink traces, G-code post-process | Stage 1-5 | G-code with 2 pauses + ink trace coordinates |
| **7** | `src/agent.py` — LLM integration | Stage 1-6 | Agent designs a flashlight end-to-end |
| **8** | `src/web/` — browser UI | Stage 1-7 | Chat → design → preview in browser |

Each stage is independently testable. We build and verify one at a time.

For stages 1–6, we use a **hardcoded flashlight DesignSpec** as the test fixture — no LLM needed. This lets us validate the entire pipeline mechanically before wiring up the agent.

---

## Deferred Features

Things we skip for now, to be added later:

| Feature | Now (skip) | Later |
|---|---|---|
| **Binary G-code** | ASCII .gcode only | .bgcode conversion |
| **Multiple printer profiles** | Single hardcoded printer | MK3S, Core One, etc. |

Everything else ships in the initial build — outline edge rounding, SCAD fillet, dynamic pin allocation, side-mount components, ink printer format conversion, ironing + trace highlight.

The goal is: **a hardcoded flashlight design goes through all 6 stages → produces a filleted .scad that renders, a .gcode with ironing/pauses/trace highlights, and ink trace output in the printer's native format.**

---

## Flashlight Test Fixture (End-to-End Validation)

The flashlight is the simplest possible device that exercises every stage. We hardcode this as `tests/flashlight_fixture.py` and use it to validate stages 1–6 without any LLM.

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
    {"id": "VCC",       "pins": ["bat_1:V+", "btn_1:1"]},
    {"id": "BTN_GND",   "pins": ["btn_1:3", "r_1:1"]},
    {"id": "LED_DRIVE", "pins": ["r_1:2", "led_1:anode"]},
    {"id": "GND",       "pins": ["led_1:cathode", "bat_1:GND"]}
  ],

  "outline": {
    "vertices": [[0,0], [30,0], [30,80], [0,80]],
    "edges": [
      {"style": "sharp"},
      {"style": "sharp"},
      {"style": "sharp"},
      {"style": "sharp"}
    ]
  },

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

**This fixture validates the entire data flow** from catalog JSON → dataclasses → placement → routing → SCAD → manufacturing output, without needing an LLM or any intelligence. Once it works, we wire up the agent.

---

## Data Flow Summary

Every stage has a clear input/output boundary. Data flows forward only — no stage reaches back.

```
catalog/*.json
    │
    ▼
┌─ CATALOG LOADER ─────────────────────────────────────┐
│  list[Component]                                      │
└───────────────────────────────────────────────────────┘
    │
    ▼
┌─ DESIGN SPEC (from agent or test fixture) ───────────┐
│  DesignSpec:                                          │
│    .components   (what + how many)                    │
│    .nets         (which pins connect)                 │
│    .outline      (device shape)                       │
│    .ui_placements (user-facing component positions)   │
└───────────────────────────────────────────────────────┘
    │
    ▼
┌─ PLACER ──────────────────────────────────────────────┐
│  FullPlacement:                                       │
│    .components  (ALL with x, y, rotation)             │
│    .outline     (pass-through)                        │
│    .nets        (pass-through)                        │
└───────────────────────────────────────────────────────┘
    │
    ▼
┌─ ROUTER ──────────────────────────────────────────────┐
│  RoutingResult:                                       │
│    .traces          (Manhattan polylines in mm)        │
│    .pin_assignments (resolved dynamic pins, if any)   │
│    .failed_nets     (empty if all succeeded)           │
└───────────────────────────────────────────────────────┘
    │
    ▼
┌─ SCAD GENERATOR ─────────────────────────────────────┐
│  .scad source string → compiled to .stl               │
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
└───────────────────────────────────────────────────────┘
```

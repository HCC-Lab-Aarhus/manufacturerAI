# Full Pipeline Documentation: manufacturerAI ↔ silver3dprinter

## 1. System Overview

**manufacturerAI** is an AI-driven design-to-manufacturing system for custom 3D-printable electronics enclosures. A user describes a device in a chat interface, an LLM agent (Claude) designs the physical layout through iterative tool-use, and a 6-stage automated pipeline produces 3D-printable files, multi-stage G-code with embedded component insertion pauses, conductive ink trace bitmaps, and Arduino firmware.

**silver3dprinter** is a Raspberry Pi-controlled inkjet printer system that deposits silver conductive ink onto 3D-printed objects in-situ using a Xaar 128 printhead, synchronized with an FDM printer (Prusa MK3S running Marlin firmware).

The two systems operate as a single pipeline — from a user's natural-language description of a device, to a fully manufactured electronics enclosure with embedded conductive traces.

---

## 2. The Full Pipeline (Start to Finish)

```
USER DESCRIBES DEVICE (chat interface — Next.js 16 / React 19)
  │
  ▼
══════════════════════════════════════════════════════════════
  manufacturerAI — 6-stage pipeline
══════════════════════════════════════════════════════════════
  │
  │  Stage 1: DesignAgent (LLM — Claude, iterative tool-use)
  │    → design.json (outline polygon, enclosure shape, UI placements)
  │
  │  Stage 2: Placer (algorithmic)
  │    → placement.json (x, y, rotation per component, pin positions)
  │
  │  Stage 3: Router (Python A*, Manhattan traces on 0.5mm grid)
  │    → routing.json (trace paths in mm, pin assignments, jumpers)
  │
  │  Stage 4: SCAD Generator (parametric OpenSCAD)
  │    → enclosure.scad → enclosure.stl
  │
  │  Stage 5: GCode Pipeline (PrusaSlicer + post-processing)
  │    → enclosure.gcode (with M601 pauses + ink deposition commands)
  │    → trace_bitmap.txt (nozzle-native resolution text bitmap)
  │
  │  Stage 6: Firmware Generation
  │    → main.ino (Arduino firmware for the product MCU)
  │
══════════════════════════════════════════════════════════════
           ▼▼▼  THE SPLIT (manual file transfer)  ▼▼▼
══════════════════════════════════════════════════════════════
  │
  │  Files handed off:
  │    - enclosure.gcode (PLA print with ;silverink markers)
  │    - trace_bitmap.txt (conductive ink pattern)
  │
══════════════════════════════════════════════════════════════
  silver3dprinter — print execution
══════════════════════════════════════════════════════════════
  │
  │  sweep_generator.py
  │    → sweep_allNozzels.gcode (X_INCREMENT=4.3872mm lanes)
  │
  │  rasp_main.py orchestrator:
  │    1. load_bin_img() — flip bitmap vertically
  │    2. slice_image() — split into 32-pixel-wide strips (3 padding each side)
  │    3. combine_slices() — combine 4 strips → 128-pixel rows
  │    4. print_model() — parse gcode, detect ;silverink marker
  │       ├── Send PLA gcode lines to Marlin via /dev/ttyACM0
  │       └── On ;silverink:
  │            ├── Cool nozzle to 120°C, bed to 40°C
  │            ├── M0 pause for silver printer connection
  │            ├── Send sweep gcode (lane positioning)
  │            ├── send_slice() — serial 0xAA + 16 bytes per row to Arduino
  │            ├── Restore original temperatures
  │            └── M0 pause for silver printer removal
  │
  │  Arduino MEGA firmware (arduino_src.ino):
  │    - Receive 17-byte packets via serial (0xAA + 16 bytes)
  │    - Load 128 bits into two 64-bit SPI shift registers
  │    - Wait for motion trigger (distance_x >= 60, currently stubbed)
  │    - Fire Xaar 128 nozzles (120μs pulse, configurable n_fire 1–5)
  │    - Send 'K' handshake back to Pi
  │
  │  Xaar 128 Printhead:
  │    - 128 nozzles → silver ink drops on PLA surface
  │
══════════════════════════════════════════════════════════════
           MANUFACTURED DEVICE WITH CONDUCTIVE TRACES
══════════════════════════════════════════════════════════════
```

---

## 3. User Interaction (Frontend)

The frontend is a **Next.js 16** application (React 19, TypeScript, Tailwind CSS) presenting a split-screen interface:

- **Left pane:** Chat conversation with the DesignAgent. Supports text and voice input (Web Speech API). Displays assistant thinking, messages, tool calls, and a token budget ring.
- **Right pane:** Real-time 3D viewport (Three.js) that updates live as the agent modifies the design.

**User flow:**

1. User types or speaks a device description (e.g., "a TV remote with 6 buttons and a power LED")
2. Frontend sends `POST /sessions/{sid}/design` with the prompt
3. Backend spawns the DesignAgent, which streams events via SSE (`GET /sessions/{sid}/design/stream`)
4. Frontend renders streamed events: thinking chunks, messages, tool calls (`edit_design`, `get_component`, `list_components`), and design updates
5. Agent iteratively sculpts the design — validation errors guide refinement until the design is feasible
6. User triggers each pipeline stage sequentially via the manufacture panel:
   - `POST /sessions/{sid}/manufacture/placement` → place components
   - `POST /sessions/{sid}/manufacture/routing` → route traces
   - `POST /sessions/{sid}/manufacture/scad` → generate OpenSCAD model
   - `POST /sessions/{sid}/manufacture/gcode` → slice, post-process, generate bitmap
7. User downloads `enclosure.gcode` and `trace_bitmap.txt`

---

## 4. Enclosure Z-Layer Cross-Section

The enclosure is a single continuous print with pauses for ink deposition, jumper insertion, and component insertion:

```
Z = 0.0 mm    ┌─────────────────────────────┐
              │   BUILD PLATE               │
Z = 2.0 mm    ├─────────────────────────────┤  Solid floor (PLA, ironed top)
(FLOOR_MM)    │  → PAUSE 1: "ink" (M601)    │
              │  → Silver ink deposited here │
Z = 2.4 mm    ├─────────────────────────────┤  Trace zone top (+TRACE_HEIGHT_MM)
              │  → PAUSE 2: "jumpers"       │
              │    (if jumper_count > 0)     │
Z = 3.0 mm    ├─────────────────────────────┤  Cavity start (CAVITY_START_MM)
(CAVITY)      │  → PAUSE 3+: "components"   │
              │    (one or more stages,      │
              │     grouped by pause Z)      │
              │  Insert MCU, battery, etc.   │
              │  Pins contact ink traces     │
Z = h-2 mm    ├─────────────────────────────┤  Ceiling start (h - CEILING_MM)
(ceiling)     │  SOLID CEILING (PLA)         │
Z = h mm      └─────────────────────────────┘
```

**Z-Layer Constants** (from `src/pipeline/config.py`):

| Constant | Value | Description |
|---|---|---|
| `FLOOR_MM` | 2.0 mm | Solid printed floor with ironed top surface |
| `TRACE_HEIGHT_MM` | 0.4 mm | Depth of conductive ink trace channels |
| `COMPONENT_OFFSET_MM` | 1.0 mm | Gap between floor top and cavity start |
| `CAVITY_START_MM` | 3.0 mm | = FLOOR_MM + COMPONENT_OFFSET_MM |
| `CEILING_MM` | 2.0 mm | Solid printed ceiling |
| `PAUSE_NOZZLE_CLEARANCE_MM` | 2.0 mm | Z gap from tallest component to nozzle |

**Pause Z computation:** Component pause heights are calculated as `body_floor + body_height + PAUSE_NOZZLE_CLEARANCE_MM`, snapped to layer boundaries (`⌊z / layer_height⌋ × layer_height`).

---

## 5. Stage 1: DesignAgent

An LLM-driven agent (Claude) that designs the physical enclosure through iterative conversation with the user.

**Tools available to the agent:**

| Tool | Purpose |
|---|---|
| `edit_design` | Find-and-replace on the design JSON; validated after each edit |
| `get_component` | Fetch catalog component details (mounting style, options) |
| `list_components` | Catalog summary table |

**design.json structure:**

```json
{
  "name": "Device Name",
  "device_description": "...",
  "outline": [
    {"x": 0, "y": 0},
    {"x": 100, "y": 0, "ease_in": 8, "ease_out": 8},
    {"x": 100, "y": 80, "z_top": 25.0},
    {"x": 0, "y": 80}
  ],
  "holes": [
    [{"x": 30, "y": 30}, {"x": 40, "y": 30}, {"x": 40, "y": 40}, {"x": 30, "y": 40}]
  ],
  "enclosure": {
    "height_mm": 25.0,
    "top_surface": {"type": "flat|dome|ridge", "peak_height_mm": 28.0, "...": "..."},
    "bottom_surface": {"type": "flat|dome|ridge", "...": "..."},
    "edge_top": {"type": "none|chamfer|fillet", "size_mm": 2.0},
    "edge_bottom": {"type": "none|chamfer|fillet", "size_mm": 2.0}
  },
  "ui_placements": [
    {
      "instance_id": "button_1",
      "catalog_id": "tactile_button_6x6",
      "x_mm": 20.0, "y_mm": 40.0,
      "edge_index": 0,
      "mounting_style": "top"
    }
  ]
}
```

The agent targets **UI-facing placements only** (buttons, LEDs, switches). Internal components (MCU, resistors, battery) are handled in subsequent pipeline stages. Per-vertex `ease_in`/`ease_out` values produce Bézier-rounded corners. Per-vertex `z_top`/`z_bottom` allow variable ceiling/floor heights across the outline.

### Component Catalog

Each catalog entry is a JSON file in `catalog/` containing physical specifications:

- **`body`** — 3D dimensions (shape, width, length, height, diameter)
- **`mounting`** — placement style (internal/top/side), keepout margins, z-heights
- **`pins`** — array of pin objects with position_mm, hole_diameter_mm, voltage/current specs
- **`pin_groups`** — logical grouping for dynamic allocation
- **`internal_nets`** — internally-connected pin pairs
- **`configurable`** — optional variants (e.g., LED colors with wavelength + forward voltage)

---

## 6. Stage 2: Placer

An algorithmic component placement engine.

**Design rules** (from `TraceRules`):

| Rule | Value |
|---|---|
| Grid scan step | 1.0 mm |
| Valid rotations | 0°, 90°, 180°, 270° |
| Trace width | 1.0 mm |
| Trace clearance (edge-to-edge) | 1.0 mm |
| Routing channel width | 2.0 mm (trace_width + clearance) |
| Pin clearance (trace edge to foreign pin centre) | 1.5 mm |
| Edge clearance (trace to outline edge) | 1.5 mm |

**Placement algorithm:**

1. Build net connectivity graph (which components share nets)
2. Detect raised-floor zones (`z_bottom ≥ FLOOR_MM - 0.1`)
3. Reserve bottom-inset space for edge profiles (fillet/chamfer)
4. Resolve mounting styles from UIPlacement → ComponentInstance → catalog defaults
5. For each component:
   - If UI-placed: fix position/rotation from design.json
   - Else: generate candidates near connected components, score by overlap/fit/clearance/congestion, pick best (falls back to full grid scan)
6. Compute pin positions for all resolved component locations

**placement.json structure:**

```json
{
  "components": [
    {
      "instance_id": "mcu_1",
      "catalog_id": "atmega328p_dip28",
      "x_mm": 50.0, "y_mm": 40.0,
      "rotation_deg": 0,
      "mounting_style": "top",
      "pin_positions": {"PD2": [50.0, 44.3], "VCC": [55.2, 40.0]}
    }
  ],
  "outline": {"points": [[0, 0], [100, 0], [100, 80], [0, 80]]},
  "nets": [{"id": "GND", "pins": ["mcu_1:PD0", "button_1:pin1"]}],
  "enclosure": {"height_mm": 25.0}
}
```

---

## 7. Stage 3: Router

A Python A* pathfinder routing conductive traces on a Manhattan grid.

### Routing Grid

```
Grid resolution:    0.5 mm per cell
Trace width:        1.0 mm
Trace clearance:    1.0 mm (edge-to-edge)
Pin clearance:      1.5 mm
Edge clearance:     1.5 mm

Cell states:
  FREE (0)                — available for routing
  BLOCKED (1)             — temporary block (can be freed)
  PERMANENTLY_BLOCKED (2) — outside outline or component body
  TRACE_PATH (3)          — occupied by routed trace (+ clearance radius)
```

**Coordinate conversion:** `world_to_grid(wx, wy) = (⌊(wx - origin) / 0.5 - 0.5⌋, ...)`, clamped to bounds. `grid_to_world(gx, gy)` returns cell centre coordinates.

### Routing Algorithm

1. Build routing grid, block component bodies, protect pin cells
2. Resolve pin positions for all nets (with dynamic MCU pin allocation)
3. Sort nets by isolation-length priority (hardest first)
4. Route all nets sequentially (jumper wires allowed for planarity conflicts)
5. Iterative improvement (up to 60 iterations, stall limit 20):
   - Rip up worst nets + neighbors
   - Re-route in perturbed order
   - Keep best result seen

**A* Cost Parameters:**

| Parameter | Value |
|---|---|
| `turn_penalty` | 5 |
| `crossing_cost` | 50 |
| `max_improve_iterations` | 60 |
| `stall_limit` | 20 |

### routing.json structure

```json
{
  "traces": [
    {"net_id": "GND", "path": [[10.5, 20.3], [10.5, 45.2], [35.0, 45.2]]}
  ],
  "pin_assignments": {"mcu_1:gpio0": "mcu_1:PD2"},
  "failed_nets": [],
  "jumpers": [
    {
      "net_id": "SIG",
      "start": {"x": 12.0, "y": 20.0, "pin_center": [11.5, 19.8], "pin_radius_mm": 0.6},
      "end": {"x": 40.0, "y": 30.0},
      "length_mm": 18.5
    }
  ]
}
```

---

## 8. Stage 4: SCAD Generator

Generates a parametric OpenSCAD model of the enclosure.

**Process:**

1. Tessellate footprint polygon (outline with Bézier-expanded corners, ~84 vertices for a 28-point outline)
2. Compute per-vertex ceiling and floor heights (accounting for `z_top`, `z_bottom`, dome/ridge surfaces)
3. Generate shell body as a SCAD polyhedron with tessellated walls and edge profiles
4. For each placed component: generate body cavity, pad, and pin hole fragments
5. For each routed trace: generate a trace channel fragment
   - Width: 1.2 mm (path width including overshoot)
   - Z base: `FLOOR_MM` (2.0 mm)
   - Depth: `TRACE_HEIGHT_MM` (0.4 mm)
6. For each jumper wire: generate pinhole capsules and wire channels
   - Wire channel Z base: `FLOOR_MM + TRACE_HEIGHT_MM` (2.4 mm)
   - Wire channel depth: `CAVITY_START_MM - trace_roof` (0.6 mm)
7. Emit OpenSCAD code; optionally compile to STL

**Output:** `enclosure.scad` (~50KB typical), optionally `enclosure.stl` (binary)

---

## 9. Stage 5: GCode Pipeline

### PrusaSlicer Pass

The STL is sliced using PrusaSlicer with a printer-specific profile (e.g., `slicer_profile_mk3s.ini`). PrusaSlicer emits layer markers:

```gcode
;LAYER_CHANGE
;Z:3.200
;HEIGHT:0.2
```

### Post-Processing

The post-processor (`postprocessor.py`) walks the slicer G-code and inserts:

1. **M601 pause at ink Z** (2.0 mm) — plus G-code for conductive ink deposition from routing.json
2. **M601 pause at jumper Z** (3.0 mm, if jumpers exist) — for jumper wire insertion
3. **M601 pauses at component Z heights** — for component insertion (grouped by shared pause Z)
4. **Ironing skip filter** — avoids ironing over trace channel zones
5. **`;silverink` marker** — signals the silver3dprinter system to begin ink deposition

### Ink Deposition G-code

`ink_traces.py` generates G-code commands from the trace paths in routing.json:

```
INK_TRAVEL_SPEED = 3000 mm/min     (rapid travel to trace start)
INK_DRAW_SPEED   = 300 mm/min      (slow linear move while dispensing)
INK_Z_HOP        = 1.0 mm          (lift between traces)
```

For each trace: retract filament → lift to safe Z → travel to start → lower to ink Z (2.0 mm) → trace path at draw speed → lift. Collinear points are simplified to corners only.

### Bitmap Generation

Traces are rasterized at **nozzle-native resolution** into a text bitmap (`trace_bitmap.txt`).

**Rasterization** (`src/pipeline/router/bitmap.py`):

1. For each trace in routing.json: translate from design coordinates to bed coordinates via `model_to_bed` offset
2. Convert bed coordinates to bitmap coordinates via `SweepGrid.bed_to_bitmap()`
3. Rasterize each Manhattan segment with a width of `trace_width_mm` (1.0 mm → ~7 pixels at 0.1371 mm/px)
4. Output as text: row 0 = highest Y (top of bed), columns left-to-right = low-X to high-X

**Output format:** Plain text, one row per line, characters `'0'` (no ink) or `'1'` (deposit ink).

---

## 10. Stage 6: Firmware Generation

Maps routed pin assignments to Arduino pin numbers for the product MCU (ATmega328P DIP-28).

**Pin mapping chain:**

```
routing.json pin_assignments (e.g., "mcu_1:gpio0" → "mcu_1:PD2")
  → ATmega port name (PD2) → Arduino pin number (2) → physical DIP-28 pin (4)
```

**PWM-capable pins:** 3, 5, 6, 9, 10, 11 (required for IR LED and similar).

**Output:** Modified Arduino `.ino` sketch with correct `#define` pin assignments.

---

## 11. Physical Constants & Printer Configuration

### Printhead (Xaar 128)

| Parameter | Value |
|---|---|
| Nozzle count | 128 |
| Nozzle pitch | 0.1371 mm (~185 DPI) |
| Printhead width | 128 × 0.1371 = **17.5488 mm** |
| Lane step | 32 nozzles |
| Lane width | 32 × 0.1371 = **4.3872 mm** |
| Lane overlap | 96 nozzles (75% — 4× overprint pattern) |
| Pixel size (X and Y) | 0.1371 mm (**square pixels**) |
| Fire pulse duration | 120 μs (5 μs assert + 115 μs hold) |
| Max fire frequency | ~5500 Hz (limited by READY signal cycle) |
| SPI clock | 1 MHz, MSB first, Mode 2 |
| Two daisy-chained 64-bit shift registers | buf2 → nozzles 1–64, buf1 → nozzles 65–128 |

### Printer Definitions

```
                    mk3s / mk3s_plus           coreone
                    ──────────────────         ──────────────────
Nominal bed         250.0 × 210.0 mm          250.0 × 250.0 mm
Inkjet offset X     -57.6 mm                  -57.6 mm
Inkjet offset Y     -32.0 mm                  -32.0 mm
Usable bed width    192.4 mm                  192.4 mm
Usable bed depth    178.0 mm                  218.0 mm
Calibration X       -1.8 mm                   -1.8 mm
Calibration Y       +2.7 mm                   +2.7 mm
Max Z               210.0 mm                  220.0 mm
Default printer     —                         ✓ (DEFAULT_PRINTER)
```

The inkjet offsets represent the physical displacement between the FDM nozzle and the Xaar printhead on the carriage. The calibration offsets are residual empirical corrections.

### SweepGrid (Bitmap Coordinate Transform)

The `SweepGrid` class computes bitmap dimensions dynamically from the printer and printhead definitions:

```
num_lanes = 1 + ⌊(X_END - X_START) / lane_width_mm⌋
          = 1 + ⌊(250.0 - 57.6) / 4.3872⌋
          = 1 + 43 = 44 lanes (for mk3s)

_PADDING_STRIPS = 3   (rasp_main.py adds 3 × 32-nozzle padding strips per side)

data_cols = (num_lanes - _PADDING_STRIPS) × 32 = (44 - 3) × 32 = 1312 pixels
data_rows = ⌈(bed_depth - y_start) / nozzle_pitch⌉ = ⌈(210.0 - 32.0) / 0.1371⌉ = 1299 rows

Bitmap dimensions for mk3s: 1312 × 1299 pixels
```

**Coordinate transformation** (`bed_to_bitmap`):

```
bitmap_x = bed_x - data_x_start_mm - inkjet_offset_x + calibration_offset_x
bitmap_y = bed_y - y_start_mm - inkjet_offset_y + calibration_offset_y

where:
  data_x_start_mm = X_START + _PADDING_STRIPS × 32 × nozzle_pitch
                  = 57.6 + 3 × 32 × 0.1371 = 70.76 mm
  y_start_mm = 32.0 mm
```

---

## 12. silver3dprinter — Detailed Architecture

### 12.1 Raspberry Pi Controller (rasp_main.py)

**Constants:**

| Parameter | Value | Source |
|---|---|---|
| `NOZZLE_PITCH_MM` | 0.1371 | Hardcoded |
| `SWEEP_SPEED_MM_MIN` | 600 | config.txt `SLOW_FEED` |
| `TIME_PER_ROW_S` | 0.01371 s (13.71 ms) | Computed: `0.1371 / (600 / 60)` — **currently commented out** |
| `BAUD_RATE` | 115200 | Hardcoded |
| `PORT` (Marlin) | `/dev/ttyACM0` | Hardcoded |
| `PRINT_PORT` (Arduino) | `/dev/ttyACM1` | Hardcoded |

**Command-line usage:**

```
python3 rasp_main.py <model.gcode> <bitmap.txt> [silver_passes] [--debug] [--noheating] [--cleaning] [--alive]
```

| Flag | Effect |
|---|---|
| `--debug` | Fire all 128 nozzles constantly (ignore bitmap) |
| `--noheating` | Skip temperature control during silver deposition |
| `--cleaning` | Indefinite nozzle firing for maintenance |
| `--alive` | Keep-alive mode: fire all nozzles every 10 minutes |
| `silver_passes` | Number of ink passes (default 1) |

**Main execution flow:**

1. `sweep_generator.py` runs to produce `sweep_allNozzels.gcode`
2. `load_bin_img()` — reads bitmap, flips rows vertically, writes `_flipped.txt`
3. `slice_image()` — splits flipped bitmap into 32-column-wide strips with 3 padding strips on each side
4. `combine_slices()` — sliding window: concatenates 4 adjacent strips → 128-column files (`c_slice_0.txt`, `c_slice_1.txt`, ...)
5. `print_model()` — parses model G-code line by line:
   - Normal lines: forwarded to Marlin via `send_gcode()`
   - `;silverink` marker triggers: temperature cooldown → M0 pause → sweep execution → temperature restore → M0 pause

**Bitmap processing pipeline:**

```
Input bitmap (data_cols wide, text file of 0s and 1s)
  │
  ▼ load_bin_img() — reverse row order (flip vertically)
  │
  ▼ slice_image() — split into 32-pixel-wide column strips
  │   → 3 zero-padding strips + N data strips + 3 zero-padding strips
  │   → slice_0.txt through slice_{N+5}.txt
  │
  ▼ combine_slices() — sliding window: combine 4 adjacent strips → 128 columns
  │   → c_slice_0.txt  = strips [0, 1, 2, 3]
  │   → c_slice_1.txt  = strips [1, 2, 3, 4]
  │   → ...
  │   → One combined file per sweep lane
  │
  ▼ During each sweep lane:
    send_slice() → for each row in the 128-wide file:
      pack 128 bits → 16 bytes → serial: [0xAA] + [16 bytes] = 17-byte packet
      wait for 'K' handshake from Arduino
```

### 12.2 Sweep Generator (sweep_generator.py)

Generates G-code for lane-by-lane sweeps of the printhead across the bed.

**Parameters:**

| Parameter | Value | Derivation |
|---|---|---|
| `X_START` | 57.6 mm | Inkjet carriage offset |
| `X_END` | 250.0 mm | Full bed width |
| `X_INCREMENT` | 4.3872 mm | 32 × 0.1371 mm (32 nozzle pitches) |
| `Y_START` | 32.0 mm | Inkjet Y offset |
| `Y_END` | 210.0 mm | Full bed depth |
| `Z_HEIGHT` | 4.5 mm | Relative Z lift for clearance |
| `FAST_FEED` | 2000 mm/min | From config.txt — positioning speed |
| `SLOW_FEED` | 600 mm/min | From config.txt — printing speed |
| Number of lanes | ~44 | `⌈(250 - 57.6) / 4.3872⌉` |

**Output G-code pattern:**

```gcode
G91                           ; Relative mode
G1 Z4.50 F2000                ; Lift printhead
G90                           ; Absolute mode
G1 X57.60 Y32.00 F2000        ; Fast move to lane 0 start
G1 X57.60 Y210.00 F600        ; Slow sweep (nozzles firing during this move)
G1 X61.99 Y32.00 F2000        ; Fast move to lane 1 start
G1 X61.99 Y210.00 F600        ; Slow sweep
...
;silver_done                   ; End-of-sweep marker
G91
G1 Z-4.50 F2000               ; Lower printhead (if lowering=True)
G90
```

### 12.3 Arduino Firmware (arduino_src.ino)

**Serial protocol:** 17-byte packets — `[0xAA start byte] + [16 data bytes]` at 115200 baud.

Each data byte encodes 8 nozzles (MSB first). Bytes 0–7 → nozzles 1–64 (buf2, driven by nSS2). Bytes 8–15 → nozzles 65–128 (buf1, driven by nSS1).

**Execution loop:**

```
loop():
  1. Wait for serial packet (0xAA + 16 bytes, 500ms timeout)
  2. wait_for_motion() — accumulate encoder distance until >= inter_line_dist (60)
  3. print_line() — load data into SPI shift registers, fire nozzles
  4. Send 'K' handshake to Pi
```

**Motion synchronization (currently stubbed):**

`get_new_position()` always returns `x = 1`, meaning `wait_for_motion()` counts 60 serial-loop iterations before triggering. The PMW3360 optical motion sensor is wired but its SPI read is not implemented — the trigger is time-based by accident, not position-based.

The `wait_for_motion()` function has a 2-second safety timeout to prevent indefinite hangs.

**Fire sequence** (xaar128.cpp):

1. Wait for Xaar READY signal HIGH (start of printhead cycle)
2. Assert nFIRE LOW (active low)
3. Hold for 5 μs, verify READY went LOW (success check)
4. Hold for 115 μs more (120 μs total pulse)
5. Release nFIRE HIGH
6. Repeat `n_fire` times (configurable 1–5 via OLED menu), with 180 μs delay between fires

**Pin assignments (Arduino Mega 2560):**

| Pin | Function |
|---|---|
| 10 (nSS1) | SPI chip select — Xaar IC 2 (nozzles 65–128) |
| 7 (nSS2) | SPI chip select — Xaar IC 1 (nozzles 1–64) |
| 6 (nFIRE) | Fire pulse output (active low) |
| 2 (READY) | Xaar ready signal input |
| 8 (nRESET) | Xaar reset (active low) |
| 11 (nCLK) | 1 MHz clock output (Timer1 toggle) |
| 23 (xVDD) | Xaar power control |
| 49 (relayVHCH) | High-voltage relay |
| 48 (relayVHCL) | Low-voltage relay |
| 3 (SENSOR_SS) | PMW3360 motion sensor chip select |
| 42 (TRIGGER) | External trigger input |

**Xaar power-up sequence:** Reset LOW (120ms) → VDD HIGH (120ms) → low relay HIGH (10ms) → high relay HIGH (120ms) → reset release (10ms).

---

## 13. Speed, Frequency & Pixel Size Calculations

### Hardware Constraints (Bottom-Up)

1. **Xaar 128 max fire rate:** ~5500 Hz (180 μs READY cycle)
2. **Serial transmission:** 17 bytes × 10 bits / 115200 baud = **1.48 ms per row → ~676 rows/sec**
3. **Bottleneck: serial communication at 676 Hz** (not the printhead)

### Current Operating Point

| Parameter | Value | Derivation |
|---|---|---|
| Pixel size (X and Y) | 0.1371 mm | = nozzle pitch (square pixels) |
| Effective DPI | ~185 × 185 | = 25.4 / 0.1371 |
| Sweep speed | 600 mm/min (10 mm/s) | From config.txt |
| Row fire rate | 73 Hz | = 10 / 0.1371 |
| Time per row | 13.71 ms | = 0.1371 / 10 |
| Serial budget | 1.48 ms (at 115200) | Well within 13.71 ms budget |

At the current 600 mm/min sweep speed, there is substantial headroom — the serial link could support up to 676 Hz (92 mm/s), while the printhead could support 5500 Hz (754 mm/s).

### If Speed Is Increased to 2100 mm/min (35 mm/s)

```
Row fire rate = 35 / 0.1371 = 255 Hz
Time per row  = 3.92 ms
Serial budget = 1.48 ms — still within budget
```

### If Serial Baud Increased to 921600

```
Time per row = 17 × 10 / 921600 = 0.184 ms → 5434 rows/sec
Max speed = 5434 × 0.1371 = 745 mm/s
```

At that point the printhead's 5500 Hz becomes the bottleneck (~754 mm/s). Both limits converge at ~750 mm/s.

---

## 14. The Handoff: What Crosses the Split

### Files Transferred

| File | Content | Producer | Consumer |
|---|---|---|---|
| `enclosure.gcode` | PLA print G-code with `;silverink` marker at ink layer Z | manufacturerAI | rasp_main.py |
| `trace_bitmap.txt` | Binary text bitmap (0s and 1s), 1312×1299 px (mk3s) | manufacturerAI | rasp_main.py |

### Coordinate Alignment

Both systems share the nozzle pitch (0.1371 mm) and the sweep geometry (X_START=57.6, X_INCREMENT=4.3872). The bitmap is generated at nozzle-native resolution with dimensions computed from the same sweep grid that silver3dprinter uses. The SweepGrid coordinate transform accounts for:

- Inkjet-to-FDM nozzle offset (-57.6 mm X, -32.0 mm Y)
- Per-printer calibration offsets (-1.8 mm X, +2.7 mm Y)
- Padding strips (3 × 32 nozzles)
- Part placement on bed (from PrusaSlicer)

This means bitmap column N maps to nozzle position N within the sweep lane, and bitmap row M maps to a Y position at `Y_START + M × nozzle_pitch`.

### Remaining Mismatches

| Issue | Description |
|---|---|
| Ink G-code conflict | manufacturerAI generates ink deposition G-code (`G1 X... Y... F300` trace-drawing commands) **and** a bitmap. These are two different ink deposition strategies. The G-code approach moves the nozzle along traces at 300 mm/min; the bitmap approach fires 128 nozzles during lane sweeps at 600 mm/min. |
| Motion sensor stubbed | `get_new_position()` returns `x = 1` always — ink placement is not synchronized with actual printhead motion |
| Row timing disabled | `TIME_PER_ROW_S` (13.71 ms) is computed but commented out — actual timing depends on serial round-trip |
| Calibration offsets | The -1.8/+2.7 mm offsets are empirical corrections, not derived from geometry |

---

## 15. Inkjet Print Simulator (Planned)

### 15.1 Goal

Build a software simulator so that `rasp_main.py` can run **unchanged** on a development machine and produce a visual of stepper motor movement and ink deposition. The code under test must not distinguish the simulator from real hardware.

### 15.2 Interfaces to Simulate

`rasp_main.py` talks to the physical world through exactly **three interfaces**:

```
rasp_main.py
  │
  ├── Serial /dev/ttyACM0 (Marlin)      ← G-code ASCII, 115200 baud
  │     sends: "G1 X50.0 Y30.0 F600\n"
  │     receives: "ok P15 B3\n"
  │
  ├── Serial /dev/ttyACM1 (Arduino)      ← Binary packets, 115200 baud
  │     sends: 0xAA + 16 bytes (128-bit nozzle row)
  │     receives: 'K' (0x4B)
  │
  └── GPIO BCM 18                        ← Output pin, not used in main flow
```

### 15.3 Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                   DEVELOPMENT MACHINE                            │
│                                                                  │
│  ┌──────────────┐      pty pair 0      ┌──────────────────────┐ │
│  │              │◄────────────────────►│  Marlin Simulator     │ │
│  │              │  /tmp/pty_marlin      │  (G-code parser,     │ │
│  │              │                       │   XYZ stepper model)  │ │
│  │ rasp_main.py │                       └──────────┬───────────┘ │
│  │  (unmodified)│                                  │ position    │
│  │              │      pty pair 1      ┌───────────▼───────────┐ │
│  │              │◄────────────────────►│  Printhead Simulator  │ │
│  │              │  /tmp/pty_printhead   │  (packet decoder,     │ │
│  └──────────────┘                       │   Xaar 128 model)    │ │
│                                         └──────────┬───────────┘ │
│                                                    │ nozzle data │
│                                          ┌─────────▼──────────┐  │
│                                          │  Ink Deposition     │  │
│                                          │  Engine             │  │
│                                          │  (physical model)   │  │
│                                          └─────────┬──────────┘  │
│                                                    │ bed image   │
│  ┌─────────────────────────────────────────────────▼──────────┐  │
│  │  Visualization (matplotlib or browser via Socket.IO)       │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

**Virtual serial ports:** Pseudo-TTY pairs (Linux/Mac via `pty.openpty()`) or COM port pairs (Windows via `com0com`). `rasp_main.py` needs a 2-line change to read port paths from environment variables:

```python
PORT       = os.environ.get('SIM_MARLIN_PORT', '/dev/ttyACM0')
PRINT_PORT = os.environ.get('SIM_PRINT_PORT',  '/dev/ttyACM1')
```

### 15.4 Simulator Components

**Marlin Simulator:** Parses G-code, maintains XYZ stepper state, supports G0/G1 (linear moves), G90/G91 (absolute/relative), G28 (home), M0/M601 (pause), M104/M109/M140/M190 (heating — no-ops), M400 (wait for moves). During sweeps, interpolates position linearly over move duration so the printhead simulator can query "where is the head right now?"

**Printhead Simulator:** Reads 17-byte binary packets, decodes 128 nozzle bits, maps each fired nozzle to a physical position using current head position from Marlin + nozzle index × nozzle pitch. Responds with `'K'`.

**Ink Deposition Engine:** Maintains a 2D NumPy accumulator at nozzle-native resolution. Each fired nozzle adds an ink dot at its computed physical position. Overlapping lanes accumulate (making the 4× overprint pattern visible).

**Visualization:** matplotlib window (standalone) or Socket.IO stream to the Next.js frontend.

### 15.5 Simulation Modes

| Mode | Behavior |
|---|---|
| Real-time | Sleeps for actual move durations |
| Fast-forward | No sleeps — as fast as serial exchange allows |
| Step | Pauses after each G-code command or bitmap row |

Pacing is controlled by how quickly the Marlin simulator responds with `ok\n` — `rasp_main.py` is naturally throttled by waiting for serial responses. The simulator directory currently contains only `__pycache__/` — no implementation exists yet.

### 15.6 Verification

After simulation, the output bed image can be compared against the source bitmap:

```python
expected = load_bitmap("trace_bitmap.txt")
actual = crop_bed_to_part(bed_image, manifest)
misalignment_px = np.count_nonzero(np.abs(expected - actual))
```

This closes the loop — the pipeline can be end-to-end verified without printing a physical part.

---

## 16. Supporting Hardware

### Xaar High-Voltage Circuit

KiCAD PCB design files in `Xaar_High_Volt_Circuit/` — schematic and PCB layout for the high-voltage relay control circuit that drives the Xaar 128 printhead. Includes Gerber exports for manufacturing.

### Reusable G-code

`reusable_gcode/sweep_allNozzels.gcode` — the generated sweep lane file (44 lanes of XY motion). Regenerated by `sweep_generator.py` before each print.

### Utility Scripts (other/)

Test and calibration utilities: single-lane printing (`1lane_xaar.py`), GPIO testing, clock frequency verification, calibration G-code patterns, and various test print files.

---

## 17. Summary: Complete Path from Idea to Product

```
1. HUMAN HAS AN IDEA
   "I want a TV remote with 6 buttons and a power LED"
   │
2. CHAT WITH DESIGN AGENT
   User types description → DesignAgent (Claude) sculpts outline, places buttons/LED
   → design.json
   │
3. AUTOMATED PIPELINE (manufacturerAI)
   a. Placer positions MCU, resistors, battery inside the enclosure
      → placement.json
   b. Router connects all pins with Manhattan traces on 0.5mm grid
      → routing.json (traces, jumpers, pin assignments)
   c. SCAD generator produces parametric 3D model with cavities and trace channels
      → enclosure.scad → enclosure.stl
   d. GCode pipeline slices STL, injects pause markers, generates ink commands & bitmap
      → enclosure.gcode + trace_bitmap.txt
   e. Firmware generator maps pins to Arduino sketch
      → main.ino
   │
4. FILE TRANSFER TO PRINT STATION
   Copy enclosure.gcode and trace_bitmap.txt to Raspberry Pi
   │
5. PHYSICAL MANUFACTURING (silver3dprinter)
   a. Pi processes bitmap: flip → slice to 32px strips → combine to 128px lanes
   b. Pi streams enclosure.gcode to Prusa MK3S via serial
   c. Printer prints PLA floor (0→2.0mm), irons top surface
   d. At ;silverink marker (Z=2.0mm):
      - Cool nozzle to 120°C, bed to 40°C
      - Operator connects silver printer carriage
      - Pi executes 44 sweep lanes at 600 mm/min
      - Each lane: Arduino receives 128-bit nozzle rows, fires Xaar printhead
      - Silver ink deposits in trace channels
      - Operator disconnects silver printer
      - Temperatures restored
   e. Printer continues: cavity walls (2.4→3.0mm)
   f. At jumper pause (Z=3.0mm): operator inserts jumper wires
   g. At component pause(s): operator inserts MCU, battery, sensors
      - Component pins contact silver ink traces
   h. Printer finishes: ceiling closes enclosure
   │
6. POST-PRINT
   Flash main.ino to ATmega328P via ISP
   Insert batteries
   │
7. WORKING DEVICE
   Self-contained electronics enclosure with embedded conductive traces,
   no traditional PCB required
```

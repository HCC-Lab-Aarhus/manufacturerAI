# ManufacturerAI — Complete System Documentation

## Table of Contents

1.  [Overview](#overview)
2.  [Architecture](#architecture)
3.  [How to Run](#how-to-run)
4.  [Directory Structure](#directory-structure)
5.  [System Flow — End to End](#system-flow--end-to-end)
6.  [Web Server (`src/web/server.py`)](#web-server)
7.  [LLM Agent Loop (`src/agent/loop.py`)](#llm-agent-loop)
8.  [System Prompt (`src/agent/prompts.py`)](#system-prompt)
9.  [Tool Definitions (`src/agent/tools.py`)](#tool-definitions)
10. [Manufacturing Pipeline (`src/agent/pipeline.py`)](#manufacturing-pipeline)
11. [Component Placer (`src/pcb/placer.py`)](#component-placer)
12. [Routability Scoring (`src/pcb/routability.py`)](#routability-scoring)
13. [TypeScript Trace Router (`pcb/`)](#typescript-trace-router)
14. [Router Bridge (`src/pcb/router_bridge.py`)](#router-bridge)
15. [SCAD Shell Generation (`src/scad/shell.py`)](#scad-shell-generation)
16. [Cutout Generation (`src/scad/cutouts.py`)](#cutout-generation)
17. [OpenSCAD Compiler (`src/scad/compiler.py`)](#openscad-compiler)
18. [Polygon Geometry (`src/geometry/polygon.py`)](#polygon-geometry)
19. [Hardware Configuration (`src/config/hardware.py`)](#hardware-configuration)
20. [Configuration Files](#configuration-files)
21. [Frontend (`src/web/static/`)](#frontend)
22. [Cross-Section Geometry](#cross-section-geometry)
23. [Coordinate System](#coordinate-system)

---

## Overview

ManufacturerAI is an AI-powered design-to-manufacturing system for custom 3D-printable remote controls. A user describes what they want in natural language via a chat interface, and an LLM agent (Google Gemini 2.5 Pro) designs the remote control, then an automated pipeline places electronic components, routes PCB traces, generates 3D enclosure geometry as OpenSCAD files, and compiles them to printable STL models — all in one continuous flow.

The core idea is that the user only specifies the **shape** (a polygon outline) and **button positions**. Everything else — battery compartment placement, microcontroller placement, IR diode placement, electrical net assignment, trace routing, enclosure shell generation, battery hatch, and STL compilation — is fully automated.

**Key technologies:**
- **Python 3.13** — Backend language for all server, agent, geometry, and SCAD generation code
- **Google Gemini 2.5 Pro** — LLM powering the conversational designer agent
- **FastAPI + uvicorn** — Web server with SSE streaming
- **OpenSCAD** — Programmatic 3D CAD for enclosure geometry → STL compilation
- **Shapely ≥ 2.0** — Polygon inset computation (pre-computed fillet layers)
- **TypeScript (Node.js)** — A* trace router for single-layer PCB routing
- **Three.js** — Browser-based 3D STL viewer

---

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│                        Browser (Frontend)                      │
│  ┌─────────┐  ┌───────────┐  ┌──────────┐  ┌──────────────┐    │
│  │  Chat   │  │  Outline  │  │  PCB     │  │  3D Model    │    │
│  │  Panel  │  │  SVG      │  │  Debug   │  │  (Three.js)  │    │
│  │         │  │  Preview  │  │  Image   │  │  + Curve     │    │
│  │         │  │           │  │          │  │  Editor      │    │
│  └────┬────┘  └───────────┘  └──────────┘  └──────────────┘    │
│       │              SSE (Server-Sent Events)                  │
└───────┼───────────────────────────────────────────────────────┘
        │  POST /api/generate/stream
        ▼
┌───────────────────────────────────────────────────────────────┐
│                   FastAPI Server (Python)                     │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  Agent Loop (Gemini multi-turn chat)                   │   │
│  │  ┌──────────┐    ┌─────────────────────┐               │   │
│  │  │  think() │    │  submit_design()    │               │   │
│  │  │ (reason) │    │  → Pipeline steps   │               │   │
│  │  └──────────┘    └─────────┬───────────┘               │   │
│  └────────────────────────────┼───────────────────────────┘   │
│                               ▼                               │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  Manufacturing Pipeline                                │   │
│  │  1. Validate outline geometry                          │   │
│  │  2. Send outline preview to UI                         │   │
│  │  3. Place components (battery, controller, diode)      │   │
│  │  4. Route PCB traces (TypeScript A* router)            │   │
│  │  5. Generate OpenSCAD files                            │   │
│  │  6. Compile SCAD → STL + merge print plate             │   │
│  └────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────┘
```

---

## How to Run

### Prerequisites
- Python 3.11+
- Node.js (for the TypeScript trace router)
- OpenSCAD installed at `C:\Program Files\OpenSCAD\openscad.exe` (or on PATH)
- A Google Gemini API key

### Setup

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Build the TypeScript trace router
cd pcb
npm install
npm run build
cd ..

# 3. Create .env file with your API key
echo GOOGLE_API_KEY=your_key_here > .env

# 4. Start the server
python -m src serve
```

The server starts on `http://127.0.0.1:8000`. Open it in a browser to use the chat interface.

### CLI Options
```
python -m src serve [--port PORT] [--host HOST]
```
Default: `--host 127.0.0.1 --port 8000`

---

## Directory Structure

```
manufacturerAI/
├── .env                          # GOOGLE_API_KEY (not in git)
├── requirements.txt              # Python deps: fastapi, uvicorn, google-generativeai, shapely
├── configs/
│   ├── base_remote.json          # Single source of truth for all hardware constants
│   ├── default_params.json       # Legacy default parameters (not actively used)
│   ├── printer_limits.json       # Max print dimensions (200×200mm)
│   └── materials.json            # Material definitions
├── src/
│   ├── __init__.py
│   ├── __main__.py               # CLI entry point: `python -m src serve`
│   ├── agent/
│   │   ├── loop.py               # Gemini multi-turn conversation + tool dispatch
│   │   ├── pipeline.py           # 6-step manufacturing pipeline
│   │   ├── prompts.py            # System prompt builder for the LLM
│   │   └── tools.py              # Tool function implementations + registry
│   ├── config/
│   │   └── hardware.py           # Typed accessor for base_remote.json (`hw` singleton)
│   ├── geometry/
│   │   └── polygon.py            # Pure-Python polygon utilities (area, CCW, PIP, inset)
│   ├── pcb/
│   │   ├── placer.py             # Component placement engine (grid scan + scoring)
│   │   ├── router_bridge.py      # Python↔TypeScript router bridge (subprocess)
│   │   └── routability.py        # Pre-routing routability estimator
│   ├── scad/
│   │   ├── shell.py              # OpenSCAD enclosure body generator + battery hatch
│   │   ├── cutouts.py            # Cutout polygon builder (buttons, battery, traces, pins)
│   │   └── compiler.py           # OpenSCAD CLI wrapper + STL merge utility
│   └── web/
│       ├── server.py             # FastAPI app with all HTTP endpoints + SSE
│       └── static/
│           ├── index.html        # Main HTML page (split-pane layout)
│           ├── app.js            # Frontend JS: chat, SVG preview, Three.js, curve editor
│           └── styles.css        # Dark theme styling
├── pcb/                          # TypeScript trace router (separate Node.js project)
│   ├── package.json
│   ├── tsconfig.json
│   └── src/
│       ├── index.ts              # Router entry point + CLI
│       ├── router.ts             # Main routing logic with rip-up/reroute
│       ├── pathfinder.ts         # A* + L-shaped path finding
│       ├── grid.ts               # 2D occupancy grid
│       ├── types.ts              # TypeScript type definitions
│       ├── output.ts             # SVG/PNG output generator
│       └── visualizer.ts         # Debug visualization
└── outputs/
    └── web/
        └── run_YYYYMMDD_HHMMSS/  # Each design session creates a timestamped folder
            ├── pcb_layout.json
            ├── routing_result.json
            ├── enclosure.scad
            ├── enclosure.stl
            ├── battery_hatch.scad
            ├── battery_hatch.stl
            ├── print_plate.scad
            ├── print_plate.stl   # Merged binary STL (enclosure + hatch side-by-side)
            ├── manifest.json
            └── pcb/              # Router debug outputs
```

---

## System Flow — End to End

Here is what happens when a user types "Make me a remote with 4 buttons" and presses Send:

### 1. User sends message → Server
The browser `POST`s to `/api/generate/stream` with `{ "message": "Make me a remote with 4 buttons" }`. The server creates (or reuses) a timestamped output directory under `outputs/web/` and spawns a background thread.

### 2. Agent loop processes the message
`run_turn()` in `loop.py` sends the user's message to Gemini 2.5 Pro along with the full system prompt and conversation history. Gemini responds with either text (shown to user) or tool calls.

### 3. LLM reasons with `think()`
The LLM typically first calls `think()` to internally plan the outline geometry and button positions. The think content is streamed to the browser as a collapsible "Agent thinking…" block but is not visible text to the user.

### 4. LLM submits design via `submit_design()`
The LLM calls `submit_design()` with:
- `outline`: polygon vertices `[[x,y], ...]` in mm, CCW winding
- `button_positions`: `[{id, label, x, y}, ...]`
- `top_curve_length`, `top_curve_height`: top edge fillet parameters
- `bottom_curve_length`, `bottom_curve_height`: bottom edge fillet parameters

### 5. Manufacturing pipeline executes (6 steps)

**Step 1 — Validate outline:** Checks vertex count ≥ 3, no self-intersections, area ≥ 400 mm², all buttons inside polygon with ≥ edge clearance from every edge, dimensions within printer limits (200×200mm).

**Step 2 — Outline preview:** Sends the outline polygon + button positions to the browser via SSE. The browser renders an SVG preview.

**Step 3 — Place components:** `place_components_optimal()` generates up to 50 candidate placements by varying battery position preferences (bottom, center), controller orientations (0°, 90°), and selects the single layout with the best minimum clearance score. Places:
- **Battery** (25×48mm compartment) — prefers bottom 40% of outline
- **Controller** (ATmega328P, 10×36mm body) — prefers center, avoids button Y-band, tries 0° and 90° rotation
- **IR Diode** (5mm LED) — placed at top center

**Step 4 — Route PCB traces:** The Python bridge converts the pcb_layout into the TypeScript router's input format and runs `node dist/cli.js` as a subprocess. The TypeScript A* router:
- Creates a 0.5mm grid covering the board outline
- Marks component bodies and edge clearance zones as blocked
- Assigns controller pins to button signal nets (with optimization of AUTO_SIGNAL assignments)
- Routes each net using L-shaped routes first, then A* fallback
- Supports rip-up/reroute (up to 15 attempts) for congested layouts
- Generates debug PNG images showing the routed board

**Step 5 — Generate OpenSCAD:** Three SCAD files are generated:
- `enclosure.scad` — The main shell body with all cutouts subtracted
- `battery_hatch.scad` — Battery compartment cover with spring latch
- `print_plate.scad` — Legacy import-based plate (unused; replaced by STL merge)

**Step 6 — Compile STL + Merge:**
- `enclosure.scad` → `enclosure.stl` (OpenSCAD CLI, 600s timeout)
- `battery_hatch.scad` → `battery_hatch.stl`
- Both are merged into `print_plate.stl` using binary STL merge (the hatch is translated 80mm to the right)
- The print plate STL is emitted as the 3D model event

### 6. Results stream back to browser
Throughout the pipeline, SSE events stream to the browser:
- `progress` — stage updates shown in progress bar
- `outline_preview` — SVG outline rendering
- `pcb_layout` — component placement overlay on outline
- `debug_image` — PCB routing debug PNG
- `model` — STL model URL → Three.js viewer loads it
- `chat` — LLM text messages

### 7. LLM reports results
After the pipeline succeeds, the LLM receives the result (including `pin_mapping`) and responds with a brief summary: shape, dimensions, button count, edge rounding parameters, and a pin-assignment table (e.g., "Power → PD2, Vol+ → PD3").

### 8. User can iterate
The user can continue chatting: "make it wider", "add another button", "give it a diamond shape". The LLM maintains conversation history and can redesign. The curve editor widget allows real-time edge profile adjustment without re-running placement/routing.

---

## Web Server

**File:** `src/web/server.py` (~300 lines)

FastAPI application serving the frontend and providing all API endpoints.

### Environment Loading
On import, `_load_env()` reads `.env` / `.env.local` from the project root and sets any key=value pairs as environment variables (if not already set). This is how `GOOGLE_API_KEY` gets loaded.

### Session State
The server maintains module-level state across requests:
- `_conversation_history: list` — List of Gemini `Content` proto objects (the full multi-turn conversation)
- `_run_dir: Path | None` — Output directory for the current session (e.g., `outputs/web/run_20260209_185430`)

This means the server supports **one concurrent session**. Resetting clears both variables.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET /` | Serves `index.html` |
| `POST /api/generate/stream` | Main endpoint. Runs one agent turn in a background thread, returns SSE stream |
| `POST /api/update_curve` | Re-generates SCAD + compiles STL with new curve params. Uses cached layout/routing — no re-placement or re-routing |
| `POST /api/reset` | Clears conversation history and run directory |
| `GET /api/shell_height` | Returns `DEFAULT_HEIGHT_MM` (16.5mm) for the curve editor |
| `GET /api/model/{name}` | Serves an STL file from the current run (inline) |
| `GET /api/model/download/{name}` | Serves an STL as attachment download |
| `GET /api/images/{name}` | Serves debug PNG images from the current run |
| `GET /api/outputs/{run_id}/{path}` | Generic file serving from any run |

### SSE Streaming (`/api/generate/stream`)
1. Creates a `Queue` for inter-thread communication
2. Spawns a daemon thread that calls `run_turn()` with an `emit` callback that pushes events into the queue
3. An async generator reads from the queue and yields SSE `data:` lines
4. When the thread finishes, it pushes `None` (sentinel) to signal the stream is done
5. Events are JSON objects with a `type` field: `thinking`, `chat`, `outline_preview`, `pcb_layout`, `debug_image`, `model`, `progress`, `error`, `tool_call`, `tool_error`

### Curve Update (`/api/update_curve`)
This endpoint allows the user to adjust the edge profile (top and bottom fillet) without re-running the entire pipeline:
1. Reads cached `pcb_layout.json` and `routing_result.json` from the run directory
2. Rebuilds cutouts from the cached data
3. Regenerates all three SCAD files with the new curve parameters
4. Compiles all SCAD → STL
5. Returns the model name for the browser to reload

---

## LLM Agent Loop

**File:** `src/agent/loop.py` (~409 lines)

Manages the multi-turn conversation with Google Gemini 2.5 Pro.

### `run_turn(user_message, history, emit, output_dir) → updated_history`

This is the core function called by the server for each user message.

**Flow:**
1. Configures the Gemini model with:
   - Model: `gemini-2.5-pro-preview-06-05`
   - System instruction: built by `build_system_prompt()`
   - Tools: `think` and `submit_design` (function declarations)
   - Generation config: temperature 1.0, top_p 0.95, top_k 64, max 8192 tokens
2. Creates a `ChatSession` with the conversation history
3. Sends the user message via `_safe_send()`
4. Enters a loop (max `MAX_TURNS = 20`):
   - If the response has text → emit as `chat` event, break
   - If the response has function calls → dispatch each one:
     - `think(reasoning)` → emit as `thinking` event, return acknowledgment
     - `submit_design(...)` → run the full manufacturing pipeline
   - Send the function results back to Gemini and loop (Gemini may call more tools or respond with text)
5. Returns the updated conversation history for persistence

### Tool Dispatch
The loop only dispatches two tools:
- **`think`** — Internal reasoning scratchpad. The LLM writes its thought process here. Streamed to the browser as a collapsible block. Returns `{"status": "ok"}`
- **`submit_design`** — Triggers `run_pipeline()` from `pipeline.py`. Passes outline, button positions, and curve parameters. Returns the pipeline result dict

### Error Handling
- **Empty responses:** If Gemini returns nothing, sends a "nudge" message asking it to continue
- **429 Rate limits:** Exponential backoff with increasing delays (10s → 2ⁿ×5s)
- **IndexError:** Retries once (Gemini SDK issue)
- **API logging:** Every request/response is appended to `api_calls.jsonl` for debugging

### Conversation History
The history is a list of Gemini `Content` proto objects. Each turn adds the user message and all assistant responses (including tool call/result pairs). The history persists across HTTP requests via the server's module-level `_conversation_history` variable.

---

## System Prompt

**File:** `src/agent/prompts.py` (~210 lines)

`build_system_prompt()` constructs the full system instruction for Gemini using live hardware constants from `base_remote.json`.

### Key Instructions to the LLM

1. **Be action-oriented:** Don't describe what you'll do — just do it. Submit a design on the first response whenever possible.

2. **Never ask for confirmation:** Just submit the design. The user can iterate after seeing the result.

3. **Coordinate system:**
   - X axis = width (short side), Y axis = length (long side)
   - Origin at bottom-left (0, 0)
   - Max printable: 200mm × 200mm (from printer_limits.json)
   - Remote is held vertically (Y is the long axis)

4. **Design rules:**
   - Polygon vertices in mm, CCW winding, no self-intersections
   - Don't repeat the first vertex (auto-closed)
   - Every button center ≥ edge_clearance mm from every polygon edge
   - Minimum button spacing: cap_diameter + keepout_padding mm center-to-center
   - Use 8-20 vertices for organic shapes

5. **Edge rounding defaults:**
   - Top: `curve_length=2`, `curve_height=3`
   - Bottom: `curve_length=1.5`, `curve_height=2`

6. **Error handling:** On pipeline errors, fix silently using `think()` and resubmit. Only tell the user after 3+ failed attempts.

7. **After success:** Report shape, dimensions, button count, edge rounding params, and a pin-assignment table.

---

## Tool Definitions

**File:** `src/agent/tools.py` (~338 lines)

Contains all tool function implementations. The `TOOLS` dict at the bottom maps tool names to functions. While 9 tools are defined in this file, only 2 are actually declared to the LLM in the agent loop:

### Tools Declared to the LLM
| Tool | Description |
|------|------------|
| `think(reasoning)` | Internal scratchpad — LLM reasons here, user doesn't see it directly |
| `submit_design(outline, button_positions, ...)` | Triggers the full manufacturing pipeline |

### Additional Tools (in TOOLS registry but not declared to LLM)
These are older tools from a previous architecture where the LLM called each step individually. They're still functional but the current agent loop only exposes `think` and `submit_design`:
- `send_message` — Send chat text to user
- `send_outline_preview` — Send 2D outline preview
- `validate_outline` — Validate polygon geometry
- `place_components` — Run component placement
- `route_traces` — Run trace routing
- `generate_enclosure` — Generate SCAD files
- `compile_models` — Compile SCAD → STL
- `finalize` — Save design manifest

### Module-Level Configuration
`configure(emit, output_dir, run_id)` — Called by the agent loop before tool dispatch. Sets the SSE emit callback, output directory path, and run identifier.

---

## Manufacturing Pipeline

**File:** `src/agent/pipeline.py` (~454 lines)

The pipeline is the automated manufacturing process triggered by `submit_design()`. It executes 6 sequential steps, streaming progress events to the browser at each stage.

### `run_pipeline(outline, button_positions, emit, output_dir, *, curve params) → result dict`

### Step 0 — Normalize
- Assigns default IDs/labels to buttons if missing
- Strips duplicate closing vertex (if first == last)
- Shifts outline + buttons so bottom-left is at origin (0, 0)

### Step 1 — Validate Outline
- Checks dimensions against `printer_limits.json` (max 200×200mm)
- Runs `validate_outline()` from `geometry/polygon.py`:
  - ≥ 3 vertices
  - No vertices outside bounding box
  - Area ≥ 400mm²
  - No self-intersecting edges
  - All buttons inside polygon
  - All buttons ≥ edge_clearance from every edge (reports the specific nearest segment)
- Returns structured error with suggestions if validation fails

### Step 2 — Outline Preview
Emits an `outline_preview` SSE event with the outline polygon and button positions. The browser renders this as an SVG.

### Step 3 + 4 — Place & Route (combined)
Calls `_place_and_route()`:

**Placement (instant, pure geometry):**
- Calls `place_components_optimal()` which generates up to 50 candidates and selects the best one by minimum clearance score
- If placement fails → returns `(None, None)`

**Routing (single attempt, full budget):**
- Calls `route_traces()` (the TypeScript router bridge)
- On success → saves `pcb_layout.json` and `routing_result.json`, emits debug image
- On failure → runs crossing analysis and bottleneck detection, returns structured feedback

### Step 5 — Generate SCAD
- `build_cutouts(layout, routing_result)` → list of `Cutout` objects
- `generate_enclosure_scad(outline, cutouts, curve_params)` → SCAD string
- `generate_battery_hatch_scad()` → standalone hatch SCAD
- `generate_print_plate_scad()` → legacy import-based SCAD (unused)
- All three files written to the output directory

### Step 6 — Compile STL + Merge
- Compiles `enclosure.scad` and `battery_hatch.scad` via OpenSCAD CLI (600s timeout each)
- Skips compiling `print_plate.scad` (would need CGAL union of imported STLs, which fails)
- Instead, merges `enclosure.stl` + `battery_hatch.stl` into `print_plate.stl` using `merge_stl_files()`:
  - Parses both STLs (handles both ASCII and binary formats)
  - Translates the hatch 80mm to the right on the X axis
  - Writes a merged binary STL
- Emits the print plate as the 3D model (falls back to enclosure-only if merge fails)

### Success Response
```python
{
    "status": "success",
    "stl_files": ["enclosure", "battery_hatch", "print_plate"],
    "component_count": 7,      # buttons + battery + controller + diode
    "routed_traces": 6,
    "pin_mapping": [
        {"button_id": "btn_1", "label": "Power", "signal_net": "btn_1_SIG", "controller_pin": "PD2"},
        ...
    ],
    "top_curve_length": 2.0,
    "top_curve_height": 3.0,
    "bottom_curve_length": 1.5,
    "bottom_curve_height": 2.0,
    "message": "Design manufactured successfully! 3 STL models generated."
}
```

---

## Component Placer

**File:** `src/pcb/placer.py` (~943 lines)

The placer positions the battery, microcontroller, and IR diode inside an arbitrary polygon outline, working around the buttons (which are placed by the LLM).

### Algorithm: Grid-Scan Scoring

For each component, the placer scans a 1mm grid across the polygon interior and scores every valid position. The score is:

```
score = min(polygon_edge_distance, occupied_component_distance)
        - bottleneck_penalty
        - button_band_penalty
        + directional_preference
```

The position with the highest score wins.

#### Score Components

1. **Edge clearance:** Minimum distance from all 4 corners of the component rectangle to the nearest polygon edge. Capped at `clearance_cap` (default 4mm, battery uses 3mm) so that positions far from edges don't gain unfair advantage.

2. **Occupied clearance:** Minimum distance to any already-placed component. Also capped at `clearance_cap`.

3. **Bottleneck penalty:** `_bottleneck_penalty()` measures how narrow the polygon is at the component's Y position. For each scanline through the component's height, it computes the available horizontal width minus the component width. If narrower than `min_channel` (default 10mm, battery uses 3mm), a penalty of 2 points per mm of deficit is applied. This prevents components from being placed in narrow regions where traces can't route past.

4. **Button Y-band penalty** (controller only): Penalizes positions where the controller body overlaps the vertical band occupied by buttons. This keeps the controller clear of the button zone, reducing trace crossings.

5. **Directional preference:** A weak pull toward a preferred direction:
   - Battery uses `prefer="bottom"`, `prefer_weight=0.15`, `y_zone=(0.0, 0.40)` — strongly prefers the bottom 40% of the outline
   - Controller uses `prefer="center"` — prefers the vertical center
   - Diode uses `prefer="top"` — prefers the top

### Placement Order
1. **Buttons** — fixed from LLM; added to occupied list
2. **Battery** (25×48mm compartment) — placed first because it's the largest component
3. **Controller** (ATmega328P, 10×36mm) — placed second; tries both 0° and 90° rotation, picks the better one. Avoids the Y-band of existing buttons.
4. **IR Diode** (5mm) — placed last at the top center

### Optimal Placement (`place_components_optimal`)
Instead of a single greedy placement, this function:
1. Calls `generate_placement_candidates()` to produce up to 50 varied layouts (different battery positions, controller rotations)
2. Scores each layout with `_score_layout_spacing()`:
   - Primary sort: `min_gap` — the smallest gap between any component pair or component-to-edge (higher = better)
   - Secondary sort: `adjusted_mean` — mean gap plus MC-proximity-to-buttons bonus (prefers layouts where the controller is closer to buttons than the battery)
3. Returns the single layout with the best (min_gap, adjusted_mean) tuple

### Output Format (pcb_layout.json)
```json
{
  "board": {
    "outline_polygon": [[x,y], ...],
    "width_mm": 50.0,
    "height_mm": 150.0
  },
  "components": [
    {
      "id": "btn_1",
      "type": "button",
      "center": [25.0, 120.0],
      "keepout": {"type": "circle", "radius_mm": 7.5}
    },
    {
      "id": "battery",
      "type": "battery",
      "center": [25.0, 30.0],
      "body_width_mm": 25.0,
      "body_height_mm": 48.0,
      "keepout": {"type": "rectangle", "width_mm": 29.0, "height_mm": 52.0}
    },
    {
      "id": "controller",
      "type": "controller",
      "center": [25.0, 90.0],
      "rotation_deg": 0,
      "keepout": {"type": "rectangle", "width_mm": 18.0, "height_mm": 44.0}
    },
    {
      "id": "diode",
      "type": "diode",
      "center": [25.0, 145.0],
      "keepout": {"type": "circle", "radius_mm": 4.0}
    }
  ]
}
```

---

## Routability Scoring

**File:** `src/pcb/routability.py` (~487 lines)

Estimates how likely a placement is to route successfully **before** invoking the slow A* trace router. Used for failure feedback to the LLM.

### Core Insight
On a single-layer PCB, two nets can only cross if there is enough horizontal space at the crossing Y-level. By scanning vertical cross-sections, we can identify bottleneck bands and predict failures.

### `score_placement(layout, outline) → (score, bottlenecks)`

**Algorithm:**
1. Divides the board into horizontal bands every 2mm
2. For each band, counts how many distinct nets need routing channels passing through (nets with pads both above and below the band)
3. Computes: `available = board_width - 2×edge_clearance - body_width`
4. Computes: `required = crossing_count × trace_corridor` (where trace_corridor = trace_width + trace_clearance = 3.5mm)
5. If `available < required` → bottleneck detected
6. Overall score = tightest margin across all bands minus crossing penalty

### `detect_crossings(layout) → crossings`
Builds MST (minimum spanning tree) edges for each net's pads, then checks all net pairs for segment crossings. Each crossing means the router needs extra space to route around.

### `format_feedback(...) → dict`
Builds structured feedback that the LLM can act on, describing where the board is too narrow and by how many mm it needs to be widened.

---

## TypeScript Trace Router

**Files:** `pcb/src/` (separate Node.js project)

A single-layer PCB trace router written in TypeScript. It operates on a 0.5mm grid and uses A* pathfinding to route electrical nets between component pads.

### Architecture
- **`Grid`** — 2D occupancy grid at 0.5mm resolution. Marks component bodies, edge clearance zones, and board outline boundary as blocked. Tracks which cells are occupied by routed traces.
- **`Pathfinder`** — Path finding with two strategies:
  1. **L-shaped routes** (fast): Tries horizontal-then-vertical and vertical-then-horizontal paths. Used first because they produce clean right-angle traces.
  2. **A\* fallback** (thorough): Manhattan-distance heuristic, explores grid cells to find any valid path.
- **`Router`** — Main routing orchestrator:
  - Assigns controller pins to signal nets (AUTO_SIGNAL optimization — tries permutations to minimize crossings)
  - Routes nets in priority order: VCC first, then signals, GND last
  - Uses MST (minimum spanning tree) to decompose multi-pad nets into source-sink pairs
  - Supports **rip-up/reroute** (up to 15 attempts): if a net fails, it removes previously routed traces that conflict and retries in a different order
  - Applies trace padding (clearance zones around each routed trace)
- **`Visualizer`** — Generates debug PNG images showing the grid, blocked cells, routed traces, and pads
- **`OutputGenerator`** — Generates PCB fabrication outputs (SVG, PNG negative images)

### Input Format
The router receives JSON on stdin and writes JSON to stdout. The Python bridge (`router_bridge.py`) handles the conversion.

### CLI
```bash
echo '{"board": ..., "placement": ...}' | node dist/cli.js --output path/to/output
```

---

## Router Bridge

**File:** `src/pcb/router_bridge.py` (~237 lines)

Bridges the Python pipeline to the TypeScript trace router via subprocess.

### `route_traces(pcb_layout, output_dir, *, max_attempts=None) → result`

1. Converts `pcb_layout` to the TypeScript router's input format via `_convert_layout()`:
   - Normalizes coordinates so the outline starts at origin (min_x=0, min_y=0)
   - Extracts buttons, controllers, batteries, diodes into the router's placement format
   - Maps controller pins to signal nets using `_controller_pins()`
   - Includes board parameters, manufacturing constraints, and footprint dimensions
2. Saves `ts_router_input.json` for debugging
3. Finds and builds the CLI (auto-runs `npm install && npm run build` if `dist/cli.js` doesn't exist)
4. Runs `node dist/cli.js` with JSON on stdin
5. Saves stdout/stderr to `ts_router_stdout.txt` / `ts_router_stderr.txt`
6. Parses the JSON output
7. Returns `{success, traces, failed_nets}`

### `build_pin_mapping(pcb_layout, button_positions) → list`
Creates a human-readable mapping of button labels to ATmega328P controller pins:
- Extracts signal net assignments from the hardware config
- Maps each button's signal net to its controller pin
- Returns `[{button_id, label, signal_net, controller_pin}, ...]` (e.g., `{button_id: "btn_1", label: "Power", controller_pin: "PD2"}`)

### Pin Assignment Logic (`_controller_pins`)
Buttons and diodes are assigned to the controller's digital pins in order:
- Digital pin order: PD0, PD1, PD2, ..., PD7, PB0, ..., PB5, PC0, ..., PC5
- Power pins (VCC, GND, AVCC) and unused pins (PC6, PB6, PB7) are pre-assigned
- Buttons get `SWn_SIG` nets, diodes get `LEDn_SIG` nets
- The bridge renames generic `SW1_SIG` → `btn_1_SIG` using actual component IDs

---

## SCAD Shell Generation

**File:** `src/scad/shell.py` (~416 lines)

Generates OpenSCAD source code for the enclosure shell body, battery hatch, and print plate.

### Enclosure Body (`generate_enclosure_scad`)

The enclosure is a solid extrusion of the user's outline polygon with all cutouts subtracted via a single `difference()` block.

**When no edge rounding:**
```openscad
linear_extrude(height = 16.500)
    polygon(points = [...]);
```

**When edge rounding is enabled:**
The shell body is built from **stacked layers** (a union of thin `linear_extrude` slices):

```
                 ┌── top fillet (15 layers)
                 │   Each layer is an inset polygon extruded to a thin height
    h ───────────┤   inset(θ) = curve_length × (1 − cos θ)
                 │   z(θ)     = h_below + curve_height × sin θ
                 │
                 ├── straight wall (middle section)
                 │   Full outline polygon, single extrude
                 │
                 ├── bottom fillet (15 layers)
    0 ───────────┘   inset(θ) = bcl × (1 − cos θ)
                     z(θ)     = bch × (1 − sin θ)
```

**Fillet profile formula (quarter-arc):**
- Top: `inset(θ) = curve_length × (1 − cos θ)`, `z(θ) = h_below + curve_height × sin θ` for θ ∈ [0, π/2]
- Bottom: `inset(θ) = bcl × (1 − cos θ)`, `z(θ) = bch × (1 − sin θ)` for θ ∈ [π/2, 0]

Both use 15 stacked layers (`_CURVE_STEPS = 15`). The inset polygons are **pre-computed in Python using Shapely's `buffer(-inset)`** rather than using OpenSCAD's `offset()`. This is the key optimization — it eliminates the most expensive CGAL operation and allows many more layers without meaningful compile-time cost. Compile time is ~185 seconds.

**Cutout subtraction:**
All cutouts (buttons, battery, traces, pinholes) are simple `polygon()` + `linear_extrude()` blocks — no `hull()`, no high-`$fn` cylinders. This keeps the OpenSCAD CSG tree lightweight.

### Battery Hatch (`generate_battery_hatch_scad`)

A standalone parametric SCAD module:
- **Main body:** Rectangular plate (`hatch_width × hatch_height × 1.5mm`)
- **Spring latch:** A U-shaped spring arm at the front edge:
  - Outward arm (goes up/away)
  - Curved top (180° rotate_extrude connecting the arms)
  - Return arm (comes back)
  - Hook base at the tip of the outward arm
- **Slit cutout:** Hole through the hatch for the spring to flex through
- **Ledge tab:** 8×2×1.5mm protrusion on the back edge that hooks into a dent in the enclosure wall

Parameters come from `base_remote.json`:
- Hatch clearance: 0.3mm
- Spring loop: 15mm wide, 6mm height, 0.8mm thickness

### Print Plate (`generate_print_plate_scad`)
Legacy SCAD that imports the enclosure and hatch STLs side-by-side. No longer used for compilation — the pipeline merges the STLs directly in Python instead (to avoid CGAL union failures with imported STLs).

---

## Cutout Generation

**File:** `src/scad/cutouts.py` (~447 lines)

Converts the PCB layout and routing result into a list of `Cutout` objects that `generate_enclosure_scad` subtracts from the solid shell.

### Cutout Data Type
```python
@dataclass
class Cutout:
    polygon: list[list[float]]  # 2D polygon vertices [x, y]
    depth: float                # extrusion height (mm)
    z_base: float               # z-coordinate where cut starts (0 = bottom)
    label: str                  # comment in SCAD output
```

### Shell Cross-Section (Z layers)
```
z = 0           ── bottom of shell
z = 2.0 (FLOOR) ── solid floor (no cuts except pinholes + battery through-hole)
z = 3.0         ── pinholes end; traces + component pockets start
z = 14.5        ── solid ceiling begins (CAVITY_END = h - 2)
z = 16.5        ── top of shell (only button cap holes penetrate)
```

### Component Cutouts

**Buttons:**
1. **Cap hole** — Circular 16-gon, 13mm diameter, 8.3mm deep from top (+ 0.5mm overshoot to cut through fillet). This is the hole the button cap presses through.
2. **Body pocket** — Rectangular, 12mm + margins, full cavity depth (3mm to 14.5mm). Houses the 12×12mm tactile switch body.

**Battery Compartment** (matches the "standard remote" design):
1. **Left + Right ledge recesses** — Shallow shelves (1.8mm deep) on the two long sides where the hatch panel rests. Punches through the floor.
2. **Center through-hole** — Full height, narrower by ledge width on each side. Punches the entire floor so batteries can be accessed from below. No bridges — the spring hook catches on the floor edge.
3. **Ledge tab dent** — Pocket at the back (+Y) end that extends 1mm beyond the compartment into the enclosure wall, accommodating the hatch's 8×2×1.5mm ledge tab.
4. **Cavity pocket** — Standard pocket above floor level for the battery compartment interior.

**IR Diode:**
1. **Body pocket** — Small rectangular pocket in the cavity zone
2. **Wall-through slot** — Rectangle extending from the diode position past the outline boundary, punching through the wall so IR can transmit outward

**Controller:**
1. **Rectangular pocket** — Sized to the keepout zone (body + padding), full cavity depth

### Trace Channels
For each routed trace segment:
- The grid-step path is simplified to corners only
- Each segment becomes a rectangular cutout (trace_width = 1.5mm)
- All traces carved through the full cavity height (3mm to 14.5mm) so conductive filament fills the full depth

### Pinholes
Every component pad gets a two-layer pinhole:
1. **Main shaft** — 0.7mm square hole (DIP pins are 0.46mm, giving ~0.12mm press-fit clearance per side). FDM printers shrink holes slightly, making the fit even tighter.
2. **Entry taper** — 1.2mm square opening at the top 0.5mm, for guided pin insertion and conductive filament bridging.
3. **Button pinholes** use 1.2mm diameter (button legs are thicker ~1.0mm)

---

## OpenSCAD Compiler

**File:** `src/scad/compiler.py` (~185 lines)

### `compile_scad(scad_path, stl_path) → (ok, message, stl_path)`
- Finds OpenSCAD on PATH or at common Windows install locations
- Runs `openscad -o output.stl input.scad` with a 600-second timeout
- Returns success/failure with the stderr output

### `check_scad(scad_path) → (ok, message)`
Syntax-check only, no rendering. 30-second timeout.

### `merge_stl_files(stl_paths, output_path) → bool`
Merges multiple STL files into one binary STL with translation offsets:
1. Parses each STL using `_parse_stl()` which handles both:
   - **ASCII STL:** Regex-parses `facet normal ... vertex ... endfacet` blocks
   - **Binary STL:** Reads 80-byte header, 4-byte triangle count, 50 bytes per triangle (12 floats + 2-byte attribute)
2. Applies XYZ translation to each vertex
3. Writes a single binary STL with all triangles combined

Used by the pipeline to create `print_plate.stl` (enclosure at origin + hatch translated 80mm right).

---

## Polygon Geometry

**File:** `src/geometry/polygon.py` (~275 lines)

Pure-Python polygon utilities. No external dependencies (except that `inset_polygon` is a simpler fallback; the main code uses Shapely).

### Functions

| Function | Description |
|----------|-------------|
| `polygon_area(outline)` | Signed area via shoelace formula (positive = CCW) |
| `ensure_ccw(outline)` | Returns a copy with counter-clockwise winding |
| `point_in_polygon(x, y, outline)` | Ray-casting point-in-polygon test |
| `polygon_bounds(outline)` | Returns `(min_x, min_y, max_x, max_y)` |
| `segments_intersect(a1, a2, b1, b2)` | Checks if two line segments properly intersect |
| `validate_outline(outline, ...)` | Full outline validation (area, self-intersection, button clearance) |
| `inset_polygon(outline, margin)` | Approximate inward polygon offset (moves each edge inward) |

### `validate_outline`
Returns a list of error strings (empty = valid):
1. ≥ 3 vertices
2. All vertices within the bounding box (with ±0.5mm tolerance)
3. Area ≥ 400mm² (minimum to fit battery + controller)
4. No self-intersecting edges (O(n²) edge-crossing check)
5. All buttons inside polygon (ray-casting)
6. All buttons ≥ edge_clearance from every edge (point-to-segment distance, reports the specific nearest edge segment)

### `inset_polygon`
Moves each edge inward by `margin` mm using inward normals and line-line intersection. Works well for convex and mildly concave shapes. For the main fillet computation, the code uses Shapely's `buffer(-inset)` instead (more robust for complex shapes).

---

## Hardware Configuration

**File:** `src/config/hardware.py` (~200 lines)

Singleton `_HW` class loaded from `configs/base_remote.json` via `@lru_cache(maxsize=1)`. Provides typed property accessors for all hardware constants.

### The `hw` Object

```python
from src.config.hardware import hw

hw.wall_clearance    # 2.0 mm
hw.grid_resolution   # 0.5 mm
hw.component_margin  # 1.0 mm
hw.edge_clearance    # 3.0 mm (was 5.0 default)

hw.button            # dict: pin_spacing_x, cap_diameter, min_hole_diameter, etc.
hw.controller        # dict: pin_spacing, row_spacing, body_width, body_height
hw.battery           # dict: compartment_width, compartment_height, pad_spacing
hw.diode             # dict: pad_spacing, diameter, hole_clearance

hw.trace_width       # 1.5 mm
hw.trace_clearance   # 2.0 mm
hw.trace_channel_depth # 5.0 mm (was 0.4)
hw.pinhole_depth     # 2.5 mm
hw.pinhole_diameter  # 0.7 mm

hw.wall_thickness    # 1.6 mm
hw.floor_thickness   # 3.0 mm
hw.ceil_thickness    # 1.5 mm
hw.shell_height      # 12.0 mm (cavity only; total = 12 + 3 + 1.5 = 16.5)
hw.corner_radius     # 3.0 mm

hw.controller_pins   # Pin assignment definitions
hw.pin_assignments(button_count, diode_count)  # Generate pin→net map
hw.router_footprints()   # TypeScript router format
hw.router_manufacturing()  # TypeScript router format
```

---

## Configuration Files

### `configs/base_remote.json`
**Single source of truth** for all hardware constants. Every module reads from this file.

**Board section:**
- `enclosure_wall_clearance_mm`: 2.0 — clearance between PCB edge and enclosure wall
- `grid_resolution_mm`: 0.5 — 0.5mm per grid cell for the trace router
- `component_margin_mm`: 1.0 — extra clearance around component pockets
- `edge_clearance_mm`: 3.0 — minimum distance from traces/pads to board edge

**Footprints:**
- Button: 6×6mm tactile switch, 12.5×5.0mm pin spacing, 9mm cap, 13mm hole diameter, 0.4mm hole clearance, 3mm keepout padding, 1.2mm pinhole diameter
- Controller: ATmega328P DIP-28, 2.54mm pin spacing, 7.62mm row spacing, 14 pins per side, 10×36mm body
- Battery: 2×AAA, 25×48mm compartment, 6mm pad spacing
- Diode: 5mm IR LED, 5mm pad spacing

**Manufacturing:**
- Trace width: 1.5mm, clearance: 2.0mm
- Trace channel depth: 5.0mm
- Pinhole: 0.7mm shaft, 1.2mm taper, 2.5mm deep

**Enclosure:**
- Wall: 1.6mm, floor: 3.0mm, ceiling: 1.5mm
- Shell height: 12.0mm (cavity only → total = 16.5mm)
- Battery hatch: 1.5mm thick, 0.3mm clearance
- Spring latch: 15mm wide, 6mm height, 0.8mm thick

**Controller pins:**
- Power: VCC, GND1, GND2, AVCC, AREF
- Digital (in assignment order): PD0→PD7, PB0→PB5, PC0→PC5
- Unused: PC6, PB6, PB7

### `configs/printer_limits.json`
```json
{
  "max_width_mm": 200,
  "max_length_mm": 200,
  "max_thickness_mm": 35
}
```

### `configs/default_params.json`
Legacy configuration from an older design system. Contains default remote dimensions, button layouts, electronics placement, and wiring parameters. Not actively used by the current pipeline — `base_remote.json` is the active config.

---

## Frontend

**Files:** `src/web/static/index.html`, `app.js`, `styles.css`

### Layout
The UI is a dark-themed split-pane layout:
- **Left panel:** Chat interface + text input + Send/Reset buttons + debug toggle
- **Resizer:** Draggable divider
- **Right panel:** Three tabbed views (Outline / PCB Debug / 3D Model)

### Chat Panel
- User messages displayed right-aligned with dark background
- Assistant messages left-aligned with border
- Agent thinking shown as collapsible `<details>` blocks
- Tool calls shown as dashed-border badges
- Microphone button for speech-to-text dictation (Web Speech API)

### Outline View
SVG rendering of the polygon outline with button positions:
- Polygon drawn as a semi-transparent blue fill with blue stroke
- Buttons as red circles with labels
- When components are placed, shows colored bounding boxes:
  - Battery: yellow
  - Controller: green
  - Diode: purple
  - Buttons: red
- Supports zoom (scroll wheel + buttons) and pan (drag)

### PCB Debug View
Displays the PNG debug image generated by the TypeScript router:
- Dropdown to switch between "Debug" and "Negative" images
- Zoom and pan support

### 3D Model View (Three.js)
- Loads STL from `/api/model/{name}` using `THREE.STLLoader`
- Blue metallic material (`#93c5fd`, metalness 0.1, roughness 0.5)
- Dark background (`#0b1120`)
- OrbitControls for rotation/zoom/pan
- Auto-centers and auto-frames the model
- Zoom buttons (+/−) and reset view button

### Curve Editor Widget
An interactive edge profile control that appears after a model is generated:

- **Two tabs:** Top and Bottom (independent fillet profiles)
- **Canvas visualization:** Draws a cross-section showing:
  - The straight wall
  - The fillet curve (quarter-arc using the `1−cos θ` formula)
  - The flat surface
  - Grid lines at 2mm intervals
  - A draggable handle at θ = π/4
- **Drag interaction:** Clicking and dragging on the canvas adjusts `curve_length` (horizontal) and `curve_height` (vertical), snapped to 0.5mm increments
- **Double-click:** Resets the active tab to zero (flat edge)
- **Real-time compile:** On mouse-up after dragging, sends a `POST /api/update_curve` request with the new parameters. While compiling:
  - A "Recompiling model…" overlay with spinner appears
  - The canvas is dimmed and locked
  - If the user changes values while compiling, only the last update is sent when the current compile finishes
- **Max range:** 10mm for both length and height (equal axes)
- **Value labels:** Show current length and height in mm

### Progress Bar
Appears during pipeline execution with stage-based progress:
- Validating outline: 5%
- Placing components: 10-15%
- Routing traces: 30%
- Generating enclosure: 70%
- Compiling STL: 85%
- Complete: 100%

### SSE Event Handling
The frontend connects to `/api/generate/stream` and processes each SSE event:
- `thinking` → collapsible thought block
- `chat` → assistant message bubble
- `outline_preview` → render SVG, switch to Outline tab
- `pcb_layout` → render components on outline, stay on Outline tab
- `debug_image` → load PNG, switch to PCB Debug tab
- `model` → load STL in Three.js, show curve editor, switch to 3D tab
- `progress` → update progress bar
- `error` → show error message, hide progress

### Download
The "Download STL" button becomes active when a model is loaded. Downloads the STL as an attachment via `/api/model/download/{name}`.

### Panel Resizer
The divider between left and right panels can be dragged to resize. Minimum left panel width: 200px, maximum: 60% of total width.

---

## Cross-Section Geometry

The enclosure shell has a fixed cross-section (viewed from the side):

```
z = 16.5mm ┬─── top of shell (ceiling surface)
           │    Ceiling zone: 2.0mm solid (only button cap holes penetrate)
z = 14.5mm ┤─── cavity top
           │
           │    Cavity zone: 11.5mm
           │    Contains: component pockets, trace channels, pin tapers
           │
z = 3.0mm  ┤─── cavity bottom / floor top
           │    Floor zone: 3.0mm solid
           │    Contains: pinhole shafts (extend 2.5mm down from z=3)
           │    Battery: through-hole punches entire floor
z = 0.0mm  ┴─── bottom of shell
```

**Shell height breakdown:**
- Floor: 3.0mm (`bottom_thickness_mm`)
- Cavity: 12.0mm (`shell_height_mm`, but effective cavity = 16.5 - 3 - 2 = 11.5mm)
- Ceiling: 1.5mm (`top_thickness_mm`)
- **Total:** 16.5mm (`DEFAULT_HEIGHT_MM`)

**Fillet profile:**
When edge rounding is enabled, the outer perimeter of each stacked layer is inset from the full outline:
- At the flat surface (z = h_top or z = 0): maximum inset = `curve_length`
- At the wall junction: zero inset (flush with wall)
- Profile follows `inset(θ) = curve_length × (1 − cos θ)`, which gives smooth tangent-continuous transitions

---

## Coordinate System

```
Y ▲  (LENGTH — the long axis of the remote)
  │
  │      Remote held vertically
  │      like a TV remote
  │
  │      Battery at bottom
  │      Buttons in middle/top area
  │      IR diode at top
  │
  └──────────────────────► X  (WIDTH — the short axis)
(0,0)
```

- **Origin:** Bottom-left corner of the outline bounding box
- **X axis:** Width (left ↔ right), typically 40-80mm
- **Y axis:** Length (bottom ↔ top), typically 100-200mm
- **Z axis:** Height (floor ↔ ceiling), 0mm at bottom, ~16.5mm at top
- **Winding:** Counter-clockwise (CCW) for outlines and cutout polygons
- **Grid resolution:** 0.5mm (for the trace router)
- **Printer limits:** Max 200mm × 200mm × 35mm
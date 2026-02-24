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
18. [G-code Pipeline (`src/gcode/`)](#g-code-pipeline)
19. [PrusaSlicer Bridge (`src/gcode/slicer.py`)](#prusaslicer-bridge)
20. [G-code Post-Processor (`src/gcode/postprocessor.py`)](#g-code-post-processor)
21. [Pause Points (`src/gcode/pause_points.py`)](#pause-points)
22. [Conductive Ink Toolpaths (`src/gcode/ink_traces.py`)](#conductive-ink-toolpaths)
23. [Binary G-code Converter (`src/gcode/bgcode.py`)](#binary-g-code-converter)
24. [Firmware Generator (`firmware/`)](#firmware-generator)
25. [Polygon Geometry (`src/geometry/polygon.py`)](#polygon-geometry)
26. [Hardware Configuration (`src/config/hardware.py`)](#hardware-configuration)
27. [Configuration Files](#configuration-files)
28. [Frontend (`src/web/static/`)](#frontend)
29. [Cross-Section Geometry](#cross-section-geometry)
30. [Coordinate System](#coordinate-system)
31. [User-Controllable Parameters](#user-controllable-parameters)
32. [Multi-Stage Print Process](#multi-stage-print-process)
33. [Why Parametric Design](#why-parametric-design)

---

## Overview

ManufacturerAI is an AI-powered design-to-manufacturing system for custom 3D-printable remote controls. A user describes what they want in natural language via a chat interface, and an LLM agent (Google Gemini 2.5 Pro) designs the remote control, then an automated pipeline places electronic components, routes PCB traces, generates 3D enclosure geometry as OpenSCAD files, compiles them to printable STL models, slices the STL into G-code with embedded print pauses for mid-print component insertion and conductive ink deposition, generates customized Arduino firmware, and optionally converts to Prusa Binary G-code — all in one continuous flow.

The core idea is that the user only specifies the **shape** (a polygon outline) and **button positions**. Everything else — battery compartment placement, microcontroller placement, IR diode placement, electrical net assignment, trace routing, enclosure shell generation, battery hatch, STL compilation, G-code slicing with multi-stage print pauses, conductive ink toolpath generation, and firmware generation — is fully automated.

**Key technologies:**
- **Python 3.13** — Backend language for all server, agent, geometry, SCAD generation, G-code processing, and firmware generation code
- **Google Gemini 2.5 Pro** — LLM powering the conversational designer agent
- **FastAPI + uvicorn** — Web server with SSE streaming
- **OpenSCAD** — Programmatic 3D CAD for enclosure geometry → STL compilation
- **PrusaSlicer** — CLI-driven STL → G-code slicing with multi-printer support (MK3S, MK3S+, Core One)
- **Shapely ≥ 2.0** — Polygon inset computation (pre-computed fillet layers)
- **TypeScript (Node.js)** — A* trace router for single-layer PCB routing
- **Three.js** — Browser-based 3D STL viewer
- **Arduino / ATmega328P** — Target platform for the generated IR remote firmware

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
│  │  5. Generate OpenSCAD files + instant 3D preview       │   │
│  │  6. Compile SCAD → STL + merge print plate             │   │
│  │  7. Slice STL → G-code + inject pauses + ink paths     │   │
│  │  8. Generate Arduino firmware with routed pins          │   │
│  └────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────┘
```

---

## How to Run

### Prerequisites
- Python 3.11+
- Node.js (for the TypeScript trace router)
- OpenSCAD installed at `C:\Program Files\OpenSCAD\openscad.exe` (or on PATH)
- PrusaSlicer installed at `C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer-console.exe` (or on PATH) — required for G-code generation
- A Google Gemini API key
- NumPy + Pillow (optional, for binary G-code thumbnail rendering)

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
│   ├── materials.json            # Material definitions
│   ├── slicer_profile.ini        # PrusaSlicer profile for MK3S
│   ├── slicer_profile_mk3s_plus.ini  # PrusaSlicer profile for MK3S+
│   └── slicer_profile_coreone.ini    # PrusaSlicer profile for Core One+
├── firmware/
│   ├── __init__.py
│   ├── firmware_generator.py     # Generates Arduino .ino with routed pin assignments
│   ├── UniversalIRRemote.ino     # Template Arduino sketch for IR remote
│   ├── README.md
│   ├── STANDALONE_GUIDE.md
│   └── WIRING_GUIDE.md
├── src/
│   ├── __init__.py
│   ├── __main__.py               # CLI entry point: `python -m src serve`
│   ├── agent/
│   │   ├── loop.py               # Gemini multi-turn conversation + tool dispatch
│   │   ├── pipeline.py           # 8-step manufacturing pipeline
│   │   ├── prompts.py            # System prompt builder for the LLM
│   │   └── tools.py              # Tool function implementations + registry
│   ├── config/
│   │   └── hardware.py           # Typed accessor for base_remote.json (`hw` singleton)
│   ├── gcode/
│   │   ├── __init__.py           # Module docstring
│   │   ├── pipeline.py           # G-code pipeline orchestrator (slice → post-process)
│   │   ├── slicer.py             # PrusaSlicer CLI bridge (multi-printer support)
│   │   ├── postprocessor.py      # G-code injection: pauses, ironing filter, trace highlights
│   │   ├── pause_points.py       # Computes ink/component pause Z-heights
│   │   ├── ink_traces.py         # Generates conductive ink deposition G-code
│   │   └── bgcode.py             # Pure-Python ASCII → Binary G-code converter
│   ├── geometry/
│   │   └── polygon.py            # Polygon utilities (area, CCW, PIP, inset, smooth, ellipse)
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
            ├── enclosure_raw.gcode     # Raw PrusaSlicer output
            ├── enclosure_staged.gcode  # Post-processed with pauses + ink paths
            ├── enclosure_staged.bgcode # Binary G-code for Prusa Core One
            ├── manifest.json
            ├── firmware/
            │   ├── UniversalIRRemote.ino   # Generated Arduino sketch
            │   └── PIN_ASSIGNMENT_REPORT.txt
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

**Step 5b — Instant 3D Preview:** Before the slow STL compile, a `shell_preview` event is sent to the browser with the outline, height, wall thickness, curve parameters, and component positions. The browser can render an approximate 3D preview immediately while OpenSCAD compiles.

**Step 6 — Compile STL + Merge:**
- `enclosure.scad` → `enclosure.stl` (OpenSCAD CLI, 600s timeout)
- `battery_hatch.scad` → `battery_hatch.stl`
- Both are merged into `print_plate.stl` using binary STL merge (the hatch is translated 80mm to the right)
- The print plate STL is emitted as the 3D model event

**Step 7 — Slice & Generate Custom G-code:**
If PrusaSlicer is available, the pipeline runs the full G-code generation sub-pipeline:
1. Computes pause Z-heights from the enclosure geometry (ink layer at Z=3.0mm, component insertion at Z=14.5mm)
2. Slices `print_plate.stl` (or `enclosure.stl`) via PrusaSlicer CLI with the appropriate printer profile
3. Generates conductive ink deposition G-code from the routing data
4. Post-processes the slicer G-code to:
   - Strip ironing from all layers except the ink layer (saves ~40% print time)
   - Filter ironing moves over trace channels at the ink layer
   - Inject a trace highlight extrusion pass (single-width filament lines over trace channels)
   - Inject `M601` firmware pause at the ink layer Z-height (for conductive ink deposition)
   - Inject `M601` firmware pause at the component insertion Z-height
   - Recalculate M73 progress/remaining-time commands to reflect stripped ironing
5. Converts the staged ASCII G-code to Prusa Binary G-code (`.bgcode`) with CRC32 checksums and STL-rendered thumbnails
- Emits a `gcode_ready` event with pause point metadata
- Non-fatal: if PrusaSlicer is not installed, the pipeline continues without G-code

**Step 8 — Generate Firmware:**
The `firmware_generator` module takes the pin mapping from the router and generates a customized Arduino sketch:
- Reads the `UniversalIRRemote.ino` template
- Replaces the `PIN DEFINITIONS` block with the actual routed pin assignments
- Maps button labels to firmware variable names (e.g., "Power" → `POWER_BTN`, "Vol+" → `VOL_UP_BTN`)
- Converts ATmega port names (PD2, PB3) to Arduino pin numbers
- Ensures the IR LED is assigned to a PWM-capable pin
- Writes the generated `.ino` file and a `PIN_ASSIGNMENT_REPORT.txt` to the output directory

### 6. Results stream back to browser
Throughout the pipeline, SSE events stream to the browser:
- `progress` — stage updates shown in progress bar
- `outline_preview` — SVG outline rendering
- `pcb_layout` — component placement overlay on outline
- `debug_image` — PCB routing debug PNG
- `shell_preview` — lightweight 3D preview data (sent before slow STL compile)
- `model` — STL model URL → Three.js viewer loads it
- `gcode_ready` — G-code generation complete with pause point metadata
- `chat` — LLM text messages
- `routing_result` — raw routing data for downstream use

### 7. LLM reports results
After the pipeline succeeds, the LLM receives the result (including `pin_mapping` and `firmware_path`) and responds with a brief summary: shape, dimensions, button count, edge rounding parameters, and a pin-assignment table (e.g., "Power → PD2, Vol+ → PD3").

### 8. User can iterate
The user can continue chatting: "make it wider", "add another button", "give it a diamond shape". The LLM maintains conversation history and can redesign. The curve editor widget allows real-time edge profile adjustment without re-running placement/routing. The Realign mode in the outline view allows the user to drag components to new positions, which triggers re-routing and full SCAD/STL regeneration. The user can also re-slice the model for a different printer via the `/api/slice` endpoint.

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
- `_printer_id: str | None` — Last-used printer id (e.g., `"mk3s"`, `"coreone"`)
- `_layout_gen: int` — Generation counter bumped by `update_layout`; prevents the pipeline thread from overwriting user-realigned layouts
- `_pipeline_gate: threading.Event` — Used to pause/resume the pipeline during realign mode

This means the server supports **one concurrent session**. Resetting clears all variables.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET /` | Serves `index.html` |
| `POST /api/generate/stream` | Main endpoint. Runs one agent turn in a background thread, returns SSE stream |
| `POST /api/update_curve` | Re-generates SCAD + compiles STL with new curve params. Uses cached layout/routing — no re-placement or re-routing |
| `POST /api/update_layout` | Moves components to new positions (realign mode), re-routes traces, rebuilds SCAD/STL |
| `POST /api/realign/pause` | Pauses the pipeline while user is in realign mode |
| `POST /api/realign/resume` | Resumes the pipeline after exiting realign mode |
| `POST /api/reset` | Clears conversation history and run directory |
| `GET /api/shell_height` | Returns `DEFAULT_HEIGHT_MM` (16.5mm) for the curve editor |
| `GET /api/printers` | Returns list of supported printers (MK3S, MK3S+, Core One) |
| `POST /api/slice` | Slices the enclosure STL and generates staged G-code with pauses |
| `GET /api/model/{name}` | Serves an STL file from the current run (inline) |
| `GET /api/model/download/{name}` | Serves an STL as attachment download |
| `GET /api/gcode/{name}` | Serves a G-code file from the current run (inline) |
| `GET /api/gcode/download/{name}` | Serves a G-code file as attachment download |
| `GET /api/gcode/download-bgcode` | Downloads the binary `.bgcode` file |
| `GET /api/gcode/preview/{name}` | Returns G-code metadata: layers, pauses, line count |
| `POST /api/gcode/open-viewer` | Launches PrusaSlicer's G-code viewer externally |
| `GET /api/images/{name}` | Serves debug PNG images from the current run |
| `GET /api/outputs/{run_id}/{path}` | Generic file serving from any run |

### SSE Streaming (`/api/generate/stream`)
1. Creates a `Queue` for inter-thread communication
2. Spawns a daemon thread that calls `run_turn()` with an `emit` callback that pushes events into the queue
3. An async generator reads from the queue and yields SSE `data:` lines
4. When the thread finishes, it pushes `None` (sentinel) to signal the stream is done
5. Events are JSON objects with a `type` field: `thinking`, `chat`, `outline_preview`, `pcb_layout`, `debug_image`, `shell_preview`, `model`, `gcode_ready`, `routing_result`, `progress`, `error`, `tool_call`, `tool_error`

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
   - Use 8-20 vertices for organic polygon shapes

5. **`outline_type` parameter** (strongly recommended for curves):
   - `"polygon"` (default) — use the exact vertices provided. Best for rectangles, T-shapes, diamonds, hexagons.
   - `"ellipse"` — the pipeline auto-generates a perfect smooth 32-vertex ellipse from a bounding rectangle. The LLM just provides `[[0,0],[W,0],[W,L],[0,L]]`.
   - `"racetrack"` — a stadium shape (rectangle with semicircular ends). Same as ellipse: just provide a bounding rectangle.
   - This eliminates the LLM's need to compute trigonometry. For oval/circular shapes, always use `outline_type="ellipse"`.   For pill/capsule shapes, always use `outline_type="racetrack"`.

6. **Edge rounding defaults:**
   - Top: `curve_length=2`, `curve_height=3`
   - Bottom: `curve_length=1.5`, `curve_height=2`

7. **Error handling:** On pipeline errors, fix silently using `think()` and resubmit. Only tell the user after 3+ failed attempts.

8. **After success:** Report shape, dimensions, button count, edge rounding params, and a pin-assignment table.

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

**File:** `src/agent/pipeline.py` (~622 lines)

The pipeline is the automated manufacturing process triggered by `submit_design()`. It executes 8 sequential steps, streaming progress events to the browser at each stage.

### `run_pipeline(outline, button_positions, emit, output_dir, *, outline_type, curve params) → result dict`

### Step 0 — Normalize
- Assigns default IDs/labels to buttons if missing
- Strips duplicate closing vertex (if first == last)
- If `outline_type` is `"ellipse"`, generates a 32-vertex ellipse inscribed in the bounding rectangle using `generate_ellipse()`
- If `outline_type` is `"racetrack"`, generates a stadium shape using `generate_racetrack()`
- For standard polygons, runs `smooth_polygon()` which uses Chaikin’s corner-cutting subdivision: if ≥70% of interior angles exceed 130° (i.e., the polygon looks like a coarsely-approximated curve), applies 3 iterations of subdivision (8 verts → 64 verts). Shapes with intentional sharp corners (rectangles, diamonds) are left alone.
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
- **Instant 3D preview:** Emits a `shell_preview` event with lightweight component info for client-side rendering before the slow STL compile begins

### Step 6 — Compile STL + Merge
- Compiles `enclosure.scad` and `battery_hatch.scad` via OpenSCAD CLI (600s timeout each)
- Skips compiling `print_plate.scad` (would need CGAL union of imported STLs, which fails)
- Instead, merges `enclosure.stl` + `battery_hatch.stl` into `print_plate.stl` using `merge_stl_files()`:
  - Parses both STLs (handles both ASCII and binary formats)
  - Translates the hatch 80mm to the right on the X axis
  - Writes a merged binary STL
- Emits the print plate as the 3D model (falls back to enclosure-only if merge fails)

### Step 7 — Slice & Generate Custom G-code
If the enclosure STL was compiled successfully and PrusaSlicer is installed:
- Calls `run_gcode_pipeline()` from `src/gcode/pipeline.py`
- Slices `print_plate.stl` (preferring the combined model) via PrusaSlicer CLI
- Computes ink and component insertion pause Z-heights
- Generates conductive ink deposition G-code from routing data
- Post-processes the slicer output to inject pauses, filter ironing, add trace highlights
- Converts to Prusa Binary G-code (`.bgcode`) with STL-rendered thumbnails
- Emits `gcode_ready` event with all metadata
- **Non-fatal:** If PrusaSlicer is not installed or slicing fails, the pipeline continues and only logs a warning

### Step 8 — Generate Firmware + Save Manifest
- Builds pin mapping from the routing result via `build_pin_mapping()`
- Generates a customized Arduino sketch via `generate_firmware(pin_mapping)` with the actual routed pin assignments
- Saves `PIN_ASSIGNMENT_REPORT.txt` for debugging
- Saves `manifest.json` with all output file paths and G-code metadata

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
    "firmware_path": "outputs/web/run_.../firmware/UniversalIRRemote.ino",
    "gcode": {
        "staged_gcode": "outputs/web/run_.../enclosure_staged.gcode",
        "ink_layer_z": 3.0,
        "component_z": 14.5,
        "total_layers": 82,
    },
    "top_curve_length": 2.0,
    "top_curve_height": 3.0,
    "bottom_curve_length": 1.5,
    "bottom_curve_height": 2.0,
    "message": "Design manufactured successfully! 3 STL models generated. Firmware generated with PCB pin assignments. Custom G-code with print pauses generated."
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

## G-code Pipeline

**File:** `src/gcode/pipeline.py` (~165 lines)

The G-code pipeline orchestrator — the single entry point that the manufacturing pipeline and web server call for all slicing and post-processing. It runs the full **slice → post-process → convert** flow.

### `run_gcode_pipeline(stl_path, output_dir, pcb_layout, routing_result, ...) → GcodePipelineResult`

**Steps:**
1. **Determine printer** — Resolves the printer id to a `PrinterDef` (MK3S, MK3S+, or Core One). Each printer has a distinct bed size, slicer profile, and native PrusaSlicer profile names.
2. **Compute pause points** — Calls `compute_pause_points()` to determine the ink layer Z-height (top of solid floor, Z=3.0mm) and component insertion Z-height (Z=14.5mm), snapped to layer boundaries.
3. **Prefer print_plate.stl** — If a merged `print_plate.stl` exists (enclosure + battery hatch), slices that instead of the enclosure alone.
4. **Slice via PrusaSlicer CLI** — Invokes `prusa-slicer-console --export-gcode` with the appropriate profile. Output: `enclosure_raw.gcode`.
5. **Generate ink deposition G-code** — Converts trace routing data to G-code move commands for conductive ink.
6. **Extract trace segments** — Pulls trace path segments for the ironing filter and highlight pass.
7. **Compute bed offset** — PrusaSlicer auto-centres the model on the bed; computes `(dx, dy)` offset from model-local coords to bed coords so trace/ink coordinates match the sliced model.
8. **Post-process** — Injects pauses, filters ironing, adds trace highlights, recalculates M73 progress. Output: `enclosure_staged.gcode`.
9. **Convert to Binary G-code** — Produces `enclosure_staged.bgcode` with CRC32 checksums and STL-rendered thumbnails.

### `GcodePipelineResult`
```python
@dataclass
class GcodePipelineResult:
    success: bool
    message: str
    raw_gcode_path: Path | None       # PrusaSlicer output
    staged_gcode_path: Path | None    # Post-processed with pauses
    bgcode_path: Path | None          # Binary G-code
    pause_points: PausePoints | None
    postprocess: PostProcessResult | None
    stages: list[str]                 # Human-readable log of each step
```

---

## PrusaSlicer Bridge

**File:** `src/gcode/slicer.py` (~395 lines)

Finds and invokes PrusaSlicer's CLI (`prusa-slicer-console`) to slice STL models into G-code. Supports multiple printers with distinct profiles.

### Multi-Printer Support

```python
PRINTERS = {
    "mk3s": PrinterDef(id="mk3s", label="Prusa MK3S", bed=250×210, profile="slicer_profile.ini"),
    "mk3s_plus": PrinterDef(id="mk3s_plus", label="Prusa i3 MK3S+", bed=250×210, profile="slicer_profile_mk3s_plus.ini"),
    "coreone": PrinterDef(id="coreone", label="Prusa Core One+", bed=250×220, profile="slicer_profile_coreone.ini",
                          native_printer="Prusa CORE One HF0.4 nozzle",
                          native_print="0.20mm BALANCED @COREONE HF0.4",
                          native_material="Prusament PLA @COREONE HF0.4"),
}
```

For the Core One, the slicer uses PrusaSlicer's **built-in native profiles** (via `--printer-profile`, `--print-profile`, `--material-profile` flags) overlaid with a custom `.ini` file (via `--load`) that applies overrides like:
- `binary_gcode = 0` — Forces ASCII output so the post-processor can manipulate the text (the pipeline converts to binary afterwards)
- Ironing enabled at 15% flow, 15mm/s
- Thumbnail generation for the Core One's LCD display

For MK3S/MK3S+, complete standalone `.ini` profiles are provided (no built-in profile dependency).

### `slice_stl(stl_path, output_gcode, profile_path, *, printer) → (ok, message, gcode_path)`

Auto-creates default profiles on first use if they don't exist. Runs PrusaSlicer with a 300-second timeout.

### `find_prusaslicer() → str | None`

Searches PATH and common Windows install locations for `prusa-slicer-console.exe`.

---

## G-code Post-Processor

**File:** `src/gcode/postprocessor.py` (~1020 lines)

The core of the G-code customization. Reads slicer G-code line by line, watches for PrusaSlicer's layer-change markers (`;LAYER_CHANGE` / `;Z:3.200` / `;HEIGHT:0.2`), and injects custom blocks at the correct Z-heights.

### Multi-Stage Print Sequence

The post-processor implements the following print stages (bottom to top):

```
Stage 1: Print floor layers (Z = 0 → 3.0mm)
         PrusaSlicer irons the top floor surface (ironing ON)
         ↓ Ironing over trace channels is suppressed
         ↓ Trace highlight pass: single-width filament lines over channels
Stage 2: M601 PAUSE — deposit conductive ink into trace channels
         ↓ Ink deposition G-code (generated from routing data)
         M601 PAUSE — allow ink to cure
Stage 3: Print cavity walls (Z = 3.0mm → 14.5mm)
         ↓ All ironing stripped from these layers (saves ~40% time)
Stage 4: M601 PAUSE — insert electronic components
         ↓ User places IR diode, tactile switches, ATmega328P
Stage 5: Print ceiling to completion (Z = 14.5mm → 16.5mm)
         ↓ Ironing stripped here too
```

### Key Operations

**Ironing filter at ink layer:**
Instead of deleting ironing moves over traces (which would corrupt E-counter continuity), extrusion moves (G1) crossing trace channels are converted to travel moves (G0). The nozzle follows the same path without extruding, leaving trace channels un-ironed for clean ink adhesion. Uses point-to-segment distance sampling to detect proximity.

**Ironing stripping from non-ink layers:**
Ironing on the outer shell, cavity walls, and ceiling is purely cosmetic and wastes ~40% of total print time. The post-processor strips all `; TYPE:Ironing` sections from layers other than the ink layer, along with their retract/travel preambles and postambles. When another print section follows the stripped ironing, the post-processor emits clean retract/travel/unretract sequences to maintain proper nozzle positioning.

**Trace highlight extrusion pass:**
After suppressing ironing over traces, a single-extrusion-width pass is generated along every trace path. This prints a thin filament line directly over each trace channel so they're visually marked. Includes commented-out `M600` filament change commands for using a contrasting color. Uses M83 relative E distances with proper retraction between polylines.

**M73 recalculation:**
After ironing is stripped, the original M73 progress/remaining-time commands no longer reflect reality. The post-processor counts total G0/G1 moves, computes what fraction preceded each M73 command, and recalculates P (progress %) and R (remaining minutes) values. Also updates the `estimated printing time` metadata in the footer.

**Bed offset correction:**
PrusaSlicer auto-centres the model on the build plate. All trace/ink coordinates are in model-local space. The post-processor shifts all ink G-code and trace segment coordinates by `(bed_centre - model_bbox_centre)` so they align with the sliced model.

**M601 pause blocks:**
Each pause inserts a block with:
- Descriptive comments explaining what the user should do
- `M601` command (Prusa firmware: retracts, parks head, beeps, shows LCD prompt, waits for knob press)

### `postprocess_gcode(gcode_path, output_path, ink_z, component_z, ...) → PostProcessResult`

---

## Pause Points

**File:** `src/gcode/pause_points.py` (~90 lines)

Computes the two critical Z-heights where the print must pause, based on the enclosure's Z-layer stack (must match `cutouts.py`).

### `compute_pause_points(shell_height, layer_height) → PausePoints`

```python
@dataclass
class PausePoints:
    ink_layer_z: float          # 3.0mm (top of solid floor) — iron this, then deposit ink
    component_insert_z: float   # 14.5mm (top of cavity) — insert components here
    total_height: float         # 16.5mm
    layer_height: float         # 0.2mm
    ink_layer_number: int       # Layer 15
    component_layer_number: int # Layer 72
```

Z-heights are snapped down to the nearest layer boundary (e.g., `floor(z / layer_h) * layer_h`).

---

## Conductive Ink Toolpaths

**File:** `src/gcode/ink_traces.py` (~206 lines)

Converts trace routing data (grid-coordinate paths from the TypeScript router) into G-code move commands for conductive ink deposition.

### `generate_ink_gcode(routing_result, pcb_layout, ink_z) → list[str]`

For each routed trace:
1. Converts grid coordinates to mm using `grid_resolution` (0.5mm) and the board outline origin offset
2. Simplifies the path to direction-change points only (removes collinear intermediates)
3. Generates G-code:
   - Retracts filament to prevent ooze during long travels
   - Rapid travel (G0) to trace start position (lifted by Z-hop of 1.0mm)
   - Lowers to ink Z
   - Linear moves (G1) along the trace path at 300 mm/min (slow for controlled deposition)
   - Lifts after each trace

### `extract_trace_segments(routing_result, pcb_layout) → list[(x1, y1, x2, y2)]`

Returns trace paths as line segments in mm, used by the post-processor for ironing filtering and trace highlight generation.

### Ink Deposition Constants
- Travel speed: 3000 mm/min (fast non-dispensing moves)
- Draw speed: 300 mm/min (slow dispensing)
- Z-hop: 1.0mm between traces

---

## Binary G-code Converter

**File:** `src/gcode/bgcode.py` (~543 lines)

Pure-Python implementation of the Prusa Binary G-code specification (version 1). Converts the post-processed ASCII `.gcode` into `.bgcode` so Prusa printers (especially the Core One) accept it without compatibility warnings.

### Why Binary G-code?
The Prusa Core One firmware expects `.bgcode` files. While it can read ASCII G-code, it shows compatibility warnings. The binary format also supports embedded thumbnails and structured metadata that the printer's LCD can display.

### Format Structure
```
File header (10 bytes): magic "GCDE" + version 1 + CRC32 checksum type
File metadata block: Producer, timestamp
Printer metadata block: printer_model, temperatures, filament info
Thumbnail blocks: PNG images at 220×124 and 16×16 (for LCD display)
Print metadata block: filament usage, estimated time
Slicer metadata block: all PrusaSlicer settings
G-code blocks: raw G-code split into ≤640KB chunks
```

Each block has: `type(2) + compression(2) + uncompressed_size(4) + compressed_size(4) + payload + CRC32(4)`.

### STL Thumbnail Rendering
If no thumbnails exist in the source G-code (which happens when we override `binary_gcode=0`), the converter renders thumbnails from the STL model using NumPy + Pillow:
- Parses the binary STL to extract triangle vertices and face normals
- Applies an isometric projection (45° Z rotation + 30° X tilt)
- Renders filled triangles with normal-based shading
- Outputs 220×124 and 16×16 PNG thumbnails

### `gcode_to_bgcode(gcode_path, bgcode_path, *, stl_path) → Path`

### `_parse_ascii_gcode(text) → dict`
Parses an ASCII G-code file into structured components:
- File metadata (producer, version)
- Printer metadata (model, temperatures, nozzle)
- Print metadata (filament usage, estimated time)
- Slicer metadata (full `prusaslicer_config` section)
- Thumbnails (base64-decoded from embedded comment blocks)
- G-code lines (everything else)

---

## Firmware Generator

**File:** `firmware/firmware_generator.py` (~337 lines)

Takes the pin mapping from the PCB router and generates an updated Arduino sketch with the correct pin definitions based on how the traces were actually routed.

### How It Works

1. **Read template:** Loads `firmware/UniversalIRRemote.ino` — a complete Arduino IR remote sketch with placeholder pin definitions
2. **Parse pin mapping:** The router output maps each button/diode to an ATmega328P port name (e.g., `PD2`, `PB3`)
3. **Convert ports to Arduino pins:** Uses lookup tables:
   - Port D → Arduino 0-7 (PD0→0, PD1→1, ..., PD7→7)
   - Port B → Arduino 8-13 (PB0→8, ..., PB5→13)
   - Port C → Arduino 14-19 (PC0→14, ..., PC5→19)
4. **Map button labels to firmware variables:** Normalizes labels like "Power" → `POWER_BTN`, "Vol+" → `VOL_UP_BTN`, "CH3" → `CH3_BTN`
5. **IR LED pin validation:** Checks that the IR LED is assigned to a PWM-capable pin (3, 5, 6, 9, 10, 11) — required for the 38kHz carrier signal
6. **Status LED pin:** Auto-assigns pin 13 (or next available) for the status LED
7. **Replace in template:** Uses regex to find the `// PIN DEFINITIONS` block and replaces it with auto-generated `#define` statements
8. **Write output:** Saves the generated `.ino` to the run's `firmware/` directory

### `generate_firmware(pin_mapping, output_path, *, status_led_pin) → str`

### `generate_pin_assignment_report(pin_mapping) → str`
Produces a human-readable table mapping component → label → ATmega port → Arduino pin → physical DIP-28 pin number.

### Template: `UniversalIRRemote.ino`
A complete Arduino sketch for a universal IR remote control that:
- Scans for and learns IR codes from any remote
- Transmits learned codes on button press
- Supports power, volume, channel buttons, and a "brand scan" mode
- Uses the IRremote library for 38kHz IR carrier generation
- Stores learned codes in EEPROM

---

## Polygon Geometry

**File:** `src/geometry/polygon.py` (~470 lines)

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
| `generate_ellipse(width, length, n=32)` | Generates an *n*-vertex ellipse inscribed in a bounding box, CCW, origin at bottom-left |
| `generate_racetrack(width, length, n_cap=16)` | Generates a stadium-shaped polygon (straight sides + semicircular ends) |
| `smooth_polygon(outline, *, iterations, max_vertices, angle_threshold)` | Chaikin subdivision smoothing for coarsely-approximated curves |

### `smooth_polygon` — Automatic Curve Refinement

Designed for LLM-generated outlines that attempt rounded/oval shapes but only produce 6–20 vertices (resulting in visible faceting). The algorithm:
1. Computes interior angles at every vertex using `_interior_angle()`
2. If ≥ 70% of angles exceed 130° (the polygon looks like a coarse circle/ellipse), applies Chaikin's corner-cutting subdivision
3. Each Chaikin iteration replaces each edge with two new points at 25% and 75%, doubling the vertex count
4. After 3 iterations: 8 vertices → 16 → 32 → 64 vertices (smooth)
5. Polygons with intentional sharp corners (rectangles, diamonds, T-shapes) are detected by their low "smooth ratio" and left untouched
6. Caps at `max_vertices=128` to avoid excess geometry

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

### `configs/materials.json`
Material database used by the slicer bridge to look up temperature and flow settings. Contains entries for PLA, PETG, TPU, etc. Each material specifies nozzle temperature, bed temperature, retraction distance, and flow multiplier.

### Slicer Profile Files

Three printer-specific PrusaSlicer CLI profiles live in `configs/`:

| File | Printer | Key differences |
|---|---|---|
| `slicer_profile.ini` | MK3S (original) | Marlin G-code, 250×210 bed, `M862.3 P "MK3S"` check |
| `slicer_profile_mk3s_plus.ini` | MK3S+ | Marlin G-code, 250×210 bed, `M862.3 P "MK3S+"` check |
| `slicer_profile_coreone.ini` | Core One | Marlin2 G-code, 250×220 bed, `M862.3 P "CoreOne"` check, no `M221 S95` |

All profiles share the same layer/speed/temperature settings (0.2mm layers, 215°C PLA, 60°C bed, 15% infill). The only differences are the printer model check in start G-code, bed dimensions, and firmware-specific commands. These profiles are selected automatically by `slicer.py` based on the printer model chosen at slice time.

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

---

## User-Controllable Parameters

The user communicates with the LLM in natural language. The LLM interprets the user's intent and translates it into the concrete parameters accepted by `submit_design()`. Below is the complete list of everything a user can specify — directly or indirectly — organized by what the user says versus what the system receives.

### Parameters the User Can Specify via Chat

| What the user says | What the LLM translates it to | `submit_design` parameter | Type | Example |
|---|---|---|---|---|
| Shape description ("oval", "diamond", "T-shape", "classic remote") | A polygon outline — list of [x, y] vertices in mm, CCW winding | `outline` | `[[x,y], ...]` | `[[5,0],[40,0],[45,10],[45,140],[40,150],[5,150],[0,140],[0,10]]` |
| Shape type ("ellipse", "racetrack", "custom polygon") | Sets `outline_type` to generate mathematically smooth shapes | `outline_type` | `string` | `"ellipse"`, `"racetrack"`, `"polygon"` |
| Overall dimensions ("50mm wide and 150mm long") | The outline vertices are scaled to match | `outline` (implicit) | — | Width mapped to X axis, length to Y axis |
| Number of buttons ("5 buttons") | Button position objects with auto-generated IDs and labels | `button_positions` | `[{id, label, x, y}]` | `[{id:"btn_1", label:"Button 1", x:25, y:30}, ...]` |
| Button labels ("Power, Vol+, Vol−, CH+, CH−") | The `label` field on each button object | `button_positions[].label` | `string` | `"Power"`, `"Vol+"` |
| Button layout ("buttons in a column", "2 rows of 3", "one at each tip") | The `x` and `y` coordinates of each button | `button_positions[].x`, `.y` | `number` (mm) | `x:25, y:120` |
| Edge rounding ("sharp edges", "very rounded top", "gentle slope") | Curve length and height values for top and/or bottom | `top_curve_length`, `top_curve_height`, `bottom_curve_length`, `bottom_curve_height` | `number` (mm) | `top_curve_length: 3.0, top_curve_height: 5.0` |
| Flat edges ("flat top", "no rounding") | Curve params set to 0 | `top_curve_length: 0`, etc. | `number` | `0.0` |

### Parameters the User Can Adjust Post-Generation

After a design is generated, the user has additional interactive controls:

| Control | UI Element | What it does | Backend endpoint |
|---|---|---|---|
| **Top edge profile** | Curve editor widget (drag on canvas) | Adjusts `top_curve_length` and `top_curve_height` in 0.5mm increments | `POST /api/update_curve` |
| **Bottom edge profile** | Curve editor widget (Bottom tab) | Adjusts `bottom_curve_length` and `bottom_curve_height` | `POST /api/update_curve` |
| **Iterate on design** | Chat ("make it wider", "add a button", "change to diamond shape") | LLM redesigns and resubmits with new parameters | `POST /api/generate/stream` |
| **Download STL** | Download button | Downloads the compiled print plate STL | `GET /api/model/download/{name}` |
| **Move components** | Realign mode (drag on canvas) | Pauses pipeline, lets user reposition layout, then resumes | `POST /api/realign/pause`, `POST /api/update_layout`, `POST /api/realign/resume` |
| **Slice & G-code** | Slice button | Generates printer-ready G-code (select printer model) | `POST /api/slice` |
| **Download G-code** | Download G-code button | Downloads ASCII or binary G-code | `GET /api/gcode/download/{name}` |
| **Reset** | Reset button | Clears conversation and starts fresh | `POST /api/reset` |

### Parameters the User Does NOT Control

These are determined entirely by the automated pipeline and hardware configuration:

| Parameter | Value | Why it's fixed |
|---|---|---|
| Battery placement (position inside outline) | Auto — prefers bottom 40% | Placed by scoring algorithm for optimal clearance |
| Controller placement (position + rotation) | Auto — prefers center, avoids buttons | Placed by scoring algorithm, tries 0° and 90° |
| IR diode placement | Auto — top center | Faces outward for IR transmission |
| Electrical wiring (which pin connects to which button) | Auto — sequential digital pin assignment | Assigned by `_controller_pins()` in order PD0→PC5 |
| Trace routing paths | Auto — A* pathfinding on 0.5mm grid | Solved by TypeScript router with rip-up/reroute |
| Shell height | 16.5mm (12mm cavity + 3mm floor + 1.5mm ceiling) | Fixed by enclosure config for consistent manufacturing |
| Wall thickness | 1.6mm | Fixed for structural integrity |
| Battery compartment dimensions | 25×48mm | Fixed for 2×AAA battery holder |
| Button hole diameter | 13mm | Fixed for push-button switch cap fit |
| Pinhole dimensions | 0.7mm shaft, 1.2mm taper | Fixed for DIP pin press-fit |
| Trace width / clearance | 1.5mm / 2.0mm | Fixed for conductive filament reliability |
| G-code pause heights | ink Z=3.0mm, component Z=14.5mm | Computed from floor/ceiling/cavity dimensions |
| Slicer profile | Per-printer .ini file (MK3S / MK3S+ / Core One) | Tuned per printer model for correct hardware offsets |
| Ironing removal | Automatic for trace/pause layers | Ironing interferes with ink deposition — auto-filtered |
| Firmware pin assignment | Auto from routed PCB | Arduino pins assigned from routing result |
| Material / colour | Whatever filament is loaded in the 3D printer | Not controllable via software |

### The `submit_design` Function Signature

This is the complete interface between the LLM and the manufacturing pipeline — the full set of parameters that define a design:

```
submit_design(
    outline:              [[x, y], ...]     # REQUIRED — polygon shape (6+ vertices, CCW, mm)
    button_positions:     [{id, label, x, y}, ...]  # REQUIRED — button locations
    outline_type:         string            # OPTIONAL — "polygon" (default), "ellipse", or "racetrack"
    top_curve_length:     float             # OPTIONAL — top edge inset (mm), default 0
    top_curve_height:     float             # OPTIONAL — top edge vertical extent (mm), default 0
    bottom_curve_length:  float             # OPTIONAL — bottom edge inset (mm), default 0
    bottom_curve_height:  float             # OPTIONAL — bottom edge vertical extent (mm), default 0
)
```

When `outline_type` is `"ellipse"` or `"racetrack"`, the `outline` field only needs to be a simple bounding rectangle (e.g., `[[0,0],[50,0],[50,150],[0,150]]`). The pipeline generates the actual smooth shape mathematically — the LLM never needs to compute trigonometry.

### Examples of User Intent → LLM Translation

**User:** "Make me a remote with 5 buttons"
**LLM decides:** Classic rounded rectangle ~50×140mm, 5 buttons evenly spaced along the center Y axis, default edge rounding.

**User:** "Diamond shape, 60mm wide, 180mm long, 3 buttons labelled Power, Up, Down"
**LLM decides:** Diamond polygon (4 vertices at midpoints of bounding box edges, widened tips for button clearance), 3 buttons positioned avoiding the narrow tips, default edge rounding.

**User:** "Make the top edge sharper and the bottom completely flat"
**LLM decides:** Resubmits with `top_curve_length: 1.0, top_curve_height: 1.5` (smaller = sharper), `bottom_curve_length: 0, bottom_curve_height: 0` (flat).

**User:** "Move the buttons closer together"
**LLM decides:** Recalculates button Y positions with reduced spacing (while maintaining minimum 12mm center-to-center), resubmits.

**User:** "I want a T-shaped remote with buttons on the wide top part"
**LLM decides:** T-shaped polygon with narrow lower body and wide upper head. Buttons placed only in the wide head region. Default rounding.

---

## Multi-Stage Print Process

The G-code post-processor transforms a standard single-material print into a **multi-stage manufacturing process** that interleaves 3D printing with manual operations. The printer pauses at specific heights (injected as `M601` commands), and the operator performs tasks before resuming.

### Print Stages (Bottom to Top)

```
Z = 0.0 mm   ┌──────────────────────────────────────────┐
              │  STAGE 1: Print floor (3.0mm thick)      │
              │  Standard PLA, no pauses                 │
              │  Creates the flat base with trace         │
              │  channels recessed into the top surface   │
Z = 3.0 mm   ├──────────────────────────────────────────┤
              │  ◆ PAUSE 1 — Conductive Ink Deposition   │
              │  Printer stops (M601). Ironing removed   │
              │  on this layer. Ink G-code deposited:     │
              │  conductive filament traces fill the      │
              │  channels at Z=3.0mm.                     │
              │                                          │
              │  Operator action: Switch to conductive    │
              │  filament (or let auto-toolpath run),     │
              │  then resume print.                       │
              ├──────────────────────────────────────────┤
              │  STAGE 2: Print cavity walls              │
              │  (Z = 3.0 → 14.5mm)                      │
              │  Walls rise around the component pockets. │
              │  Traces are now encapsulated under walls. │
Z = 14.5 mm  ├──────────────────────────────────────────┤
              │  ◆ PAUSE 2 — Component Insertion          │
              │  Printer stops (M601). All pockets are    │
              │  now open from above.                     │
              │                                          │
              │  Operator action: Insert battery holder,  │
              │  ATmega328P, IR diode, tactile buttons    │
              │  into their respective pockets. Push DIP  │
              │  pins through pinholes into trace pads.   │
              ├──────────────────────────────────────────┤
              │  STAGE 3: Print ceiling (1.5mm thick)     │
              │  Bridges over components and pockets.     │
              │  Button holes remain open. Battery hatch  │
              │  area remains open.                       │
Z = 16.5 mm  └──────────────────────────────────────────┘
```

### How Pauses Are Computed

The `compute_pause_points()` function in [src/gcode/pause_points.py](src/gcode/pause_points.py) calculates the exact Z heights from the enclosure dimensions in `base_remote.json`:

- **Ink pause:** `floor_thickness` = 3.0mm — the top of the floor where trace channels are exposed
- **Component pause:** `floor_thickness + cavity_height - ceiling_thickness` = 3.0 + 12.0 − 0.5 = 14.5mm — just before the ceiling starts printing

### What Happens at Each Pause

**Pause 1 (ink, Z=3.0mm):**
1. Printer executes `M601` (filament change / pause)
2. Ironing moves on this layer have been pre-filtered by the post-processor (ironing would smear wet ink)
3. Conductive ink G-code (generated by `ink_traces.py`) is spliced into this layer
4. The ink toolpath traces follow the same paths the router computed, but at a specific Z height, extrusion width, and flow rate for conductive filament
5. Operator resumes → print continues upward with standard PLA

**Pause 2 (components, Z=14.5mm):**
1. Printer executes `M601`
2. All component pockets are now open cavities — walls printed, no ceiling yet
3. Operator inserts: battery holder (25×48mm pocket), ATmega328P (DIP-28 pocket with 56 pinholes), IR LED (top center pocket), tactile switches (each in their 6×6mm pocket with 4 pinholes)
4. DIP pins push through 0.7mm pinholes into the conductive trace pads below, creating electrical connections
5. Operator resumes → ceiling prints over the top, encapsulating all components

### Post-Processing Steps at Each Layer

The post-processor doesn't just insert pauses — it also:

- **Removes ironing** from the ink layer (ironing pass would drag through wet conductive traces)
- **Highlights trace regions** by adjusting extrusion parameters over trace channel areas (visual feedback)
- **Recalculates M73 progress** after adding/removing lines so the printer's progress bar remains accurate
- **Applies bed offset correction** for printers that need coordinate translation

---

## Why Parametric Design

### The Core Argument

ManufacturerAI is fundamentally a **parametric design system** — every physical output is generated from a small set of abstract parameters rather than being manually modeled. This is not an incidental implementation choice; it is the essential architectural decision that makes the entire system possible.

### What "Parametric" Means Here

In traditional CAD, a designer manually draws or sculpts each part. In parametric design, the geometry is **computed from parameters**. The user provides a few high-level inputs (outline shape, button positions, curve values), and the system derives everything else:

```
User parameters (6 values + polygon + button list + outline_type)
    │
    ▼
┌─────────────────────────────────────────────┐
│  Pipeline computes:                         │
│  • Shape normalization (ellipse/racetrack)  │
│  • Component positions (scoring algorithm)  │
│  • Electrical nets (pin assignment rules)   │
│  • Trace paths (A* pathfinding)             │
│  • Cutout geometries (from component dims)  │
│  • Shell body (polygon extrusion + fillets) │
│  • Battery hatch (from config dimensions)   │
│  • Pinholes (from component footprints)     │
│  • Print plate (STL merge)                  │
│  • G-code (slicing + post-processing)       │
│  • Conductive ink toolpaths                 │
│  • Binary G-code (for Core One)             │
│  • Arduino firmware (pin assignments)       │
└─────────────────────────────────────────────┘
    │
    ▼
Complete manufacturing package:
  STL + G-code + firmware (.ino)
```

A single outline polygon with 8 vertices and 4 button positions generates an enclosure with 100+ cutouts, 56+ pinholes, trace channels, a battery compartment with spring-latch hatch, and fillet profiles — all dimensionally correct and ready to print.

### Why Every Part Must Be Parametric

**1. Arbitrary outline shapes require computed geometry.**
The user can submit any valid polygon — an oval, a diamond, a T-shape, an asymmetric blob. None of these can be pre-modeled. The enclosure body, fillet layers, cutout positions, and component placements must all be derived from the polygon. If the shell were a fixed 3D model, it would only work for one specific shape. By generating OpenSCAD from parameters, the system handles infinite shape variation.

**2. Component placement depends on the shape.**
Where the battery and controller fit inside the outline depends on the outline's geometry. A long narrow remote needs the components stacked vertically; a short wide one might place them side by side. The placer's grid-scan algorithm evaluates every valid position per shape — this is only possible because placement is parametric (it takes the outline as input), not pre-determined.

**3. Trace routing depends on component positions.**
The PCB traces connect specific pins on components that can be anywhere inside the outline. The routes are computed by A* pathfinding on a grid shaped by the outline boundary and component bodies. Different shapes produce different grids produce different routes produce different trace channel cutouts in the enclosure. Every trace channel in the SCAD file is generated from the routing result — pure parametric geometry.

**4. The fillet profile must adapt to any outline.**
The edge rounding uses Shapely's `buffer(-inset)` to shrink the outline polygon inward at each fillet step. A circle shrinks uniformly; a star shape shrinks with collapsing points; a rectangle shrinks with migrating corners. This adaptive behavior is inherently parametric — the same formula (`1 − cos θ` profile with polygon inset) works for every shape because it operates on the abstract polygon, not on a specific mesh.

**5. Cutouts are derived, not placed.**
Button holes are positioned at the LLM-specified coordinates. Battery compartment geometry is derived from the battery's placed position and the hardware dimensions. Pinholes are generated at every pad position calculated from the component footprints. None of these can be pre-placed in a template because their positions depend on the upstream placement and routing steps — which depend on the outline shape — which comes from the user.

**6. The hatch is shape-independent because it's parametric.**
The battery hatch is a standalone component defined purely by config parameters (compartment width, clearance, spring dimensions). It doesn't need to know the outline shape — it's generated from the same hardware constants regardless of the enclosure design. This is parametric reuse: one module, infinite contexts.

**7. An LLM can only be the designer if the output is parametric.**
The LLM doesn't output STL meshes or 3D geometry. It outputs **parameters**: a polygon and button coordinates. This is only useful if there exists a system that can take those parameters and produce a complete physical product. The parametric pipeline is what makes AI-driven design feasible — the LLM operates in a low-dimensional parameter space (shape + positions + curves) and the pipeline expands that into high-dimensional manufacturing output (SCAD → STL → 3D print).

### The Parametric Stack

Each layer in the system is parametric — its output is fully determined by its inputs:

| Layer | Input | Output |
|---|---|---|
| **LLM** | User's natural language | `outline`, `button_positions`, `outline_type`, `curve_params` |
| **Validator** | outline, buttons | Pass/fail with error messages |
| **Shape normalizer** | outline, outline_type | Final polygon (ellipse/racetrack/smoothed/raw) |
| **Placer** | outline, buttons | `pcb_layout` (all component positions) |
| **Router** | pcb_layout | `routing_result` (all trace paths) |
| **Cutout builder** | pcb_layout, routing_result | `cutouts[]` (polygon + depth + z for each) |
| **SCAD generator** | outline, cutouts, curve_params | OpenSCAD source code (text) |
| **OpenSCAD** | SCAD source | STL mesh (binary) |
| **STL merger** | enclosure.stl, hatch.stl | print_plate.stl |
| **PrusaSlicer** | print_plate.stl, printer profile | ASCII G-code |
| **Post-processor** | ASCII G-code, routing_result, pause heights | Modified G-code with pauses + ink paths |
| **Binary converter** | ASCII G-code, STL (for thumbnail) | Binary .bgcode (Core One only) |
| **Firmware generator** | routing_result (pin assignments) | Arduino .ino source file |

No layer has hidden state. No layer remembers previous runs. Given the same inputs, every layer produces the same output. This means:
- The curve editor can re-run the SCAD/STL layers without re-running placement/routing
- A failed routing can be reported to the LLM which adjusts the outline/buttons and the pipeline re-runs from scratch
- Re-slicing for a different printer only re-runs the G-code layers (PrusaSlicer → post-processor → binary converter)
- Any future component (e.g., a speaker, an LED strip) can be added to the placer and cutout builder without changing the SCAD generator — cutouts are generic polygons

### What This Enables

- **Infinite design variety** — Any polygon the LLM can imagine (within printer limits) becomes a functional remote control
- **AI-driven iteration** — The LLM can generate, fail, adjust, and retry entirely within the parameter space, without needing to understand mesh geometry
- **Real-time edge tuning** — The curve editor updates 2 numbers and the entire enclosure recompiles, because the shell is parametrically defined by those numbers
- **End-to-end manufacturing** — From natural language to printer-ready G-code and compiled firmware, with zero manual CAD steps
- **Multi-printer support** — The same STL flows through different slicer profiles to produce correct G-code for MK3S, MK3S+, or Core One
- **Separation of concerns** — The LLM knows nothing about SCAD, OpenSCAD, pinholes, or trace routing. The SCAD generator knows nothing about the LLM. Each module is a pure function from parameters to output.
- **Reproducibility** — Every design can be recreated from its `pcb_layout.json` + `routing_result.json` + curve parameters. The output is deterministic.
- **Future extensibility** — Adding a new component type (e.g., a buzzer) requires: adding its footprint to `base_remote.json`, adding placement logic to `placer.py`, adding cutout logic to `cutouts.py`, and adding pad extraction to `routability.py`. No existing module changes structurally — they already operate on generic component lists and cutout lists.
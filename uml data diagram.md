# ManufacturerAI — Data Flow Diagram

> **What is this?**  
> A system that turns a sentence like *"make me a flashlight"* into a fully
> manufactured electronic device: 3D-printed enclosure, conductive ink traces
> as wiring, real electronic components, and working firmware.

---

## The Pipeline at a Glance

```
  "Make me a flashlight"
          │
          ▼
  ┌────────┐  ┌────────┐  ┌───────┐  ┌───────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌────────┐  ┌───────┐
  │1.DESIGN│─▶│2.CIRCUI│─▶│3.PLACE│─▶│4.ROUTE│─▶│5.BITM│─▶│6.SCAD│─▶│7.GCOD│─▶│8.FIRMWA│─▶│9.SETUP│
  │ (LLM)  │  │T (LLM) │  │(algo) │  │(algo) │  │(algo)│  │(algo)│  │(algo)│  │RE(algo)│  │ (LLM) │
  └────────┘  └────────┘  └───────┘  └───────┘  └──────┘  └──────┘  └──────┘  └────────┘  └───────┘
       │           │           │          │          │         │         │           │          │
   design     circuit     placement   routing    trace_    .scad     .gcode    firmware    sim_
    .json      .json       .json       .json    bitmap     + .stl              .ino +     config
                                                  .txt                        sim_config   .json
```

Each step **reads** the output of previous steps and **writes** one artifact.  
If a step changes, everything downstream is automatically invalidated.

---

## Step-by-Step: What Each Stage Reads, Does, and Writes

### 1. Design Agent (LLM chat)

**Reads:**
- User messages (iterative conversation)
- Component catalog (what parts exist, their sizes)

**Does:**
- Chats with user to shape the device
- Decides the physical shape (CSG booleans: circles, rectangles, unions)
- Sets enclosure height and surface style (dome, ridge, flat)
- Places user-facing parts (LEDs, buttons) on the device surface

**Writes → `design.json` + `outline.json`**
- Device name and description
- Shape (CSG tree of geometric primitives)
- Outline (polygon vertices with optional rounding and per-vertex height)
- Enclosure (height, top/bottom surface style, edge profiles)
- UI placements (e.g. LED at x=20 y=10, button at x=40 y=60)

---

### 2. Circuit Agent (LLM chat)

**Reads:**
- `design.json` (what the device looks like, what UI parts are placed)
- Component catalog (available internal parts)

**Does:**
- Picks internal components (MCU, resistors, battery holder)
- Defines electrical nets (which pins connect to what)
- Allocates MCU GPIO pins dynamically
- Validates voltage and current
- If the parts don't fit → sends **feedback to Design Agent** to resize the enclosure

**Writes → `circuit.json`**
- Components list (what parts to use, with catalog IDs and configs)
- Nets (e.g. VCC connects mcu:pin_8 → led:+, GND connects mcu:pin_11 → led:−)

---

### 3. Placer (deterministic algorithm)

**Reads:**
- `design.json` (outline polygon)
- `circuit.json` (what components need placing)
- Component catalog (physical body dimensions)

**Does:**
- Finds x/y positions for all internal components (MCU, resistors, battery)
- Grid search with constraint scoring:
  - Must fit inside outline
  - No overlapping
  - Leave space for routing channels
  - Cluster electrically connected parts together

**Writes → `placement.json`**
- Component positions (e.g. mcu at x=45 y=40, resistor at x=30 y=35)
- Rotation angles

---

### 4. Router (deterministic algorithm)

**Reads:**
- `design.json` (outline boundary)
- `circuit.json` (nets — which pins must connect)
- `placement.json` (where components sit)
- Component catalog (exact pin locations on each part)

**Does:**
- A* pathfinding on a 0.5mm grid to draw conductive trace paths
- Resolves MCU pin groups (e.g. "digital_pins") to actual physical pins
- Avoids crossing traces
- Supports **jumper wires** for traces that can't route without crossing
- Iterates with backtracking to improve solutions

**Writes → `routing.json`**
- Traces (point-to-point paths for each net)
- Final pin assignments (e.g. gpio_1 → physical pin 10)
- Jumper wires (if needed)

---

### 5. Bitmap (deterministic algorithm)

**Reads:**
- `routing.json` (trace paths)

**Does:**
- Converts vector trace paths into a 2D pixel grid
- Grid is aligned to the silver ink printer's sweep lines

**Writes → `trace_bitmap.txt`**
- 2D text grid (each cell = ink or no ink) used by the inkjet printer

---

### 6. SCAD Generator (deterministic algorithm)

**Reads:**
- `design.json` (shape, enclosure, surface style)
- `placement.json` (where components sit)
- `routing.json` (traces)
- Component catalog (body dimensions, scad patterns)

**Does:**
- Generates a 3D-printable enclosure in OpenSCAD code
- Hollows out component cavities (pockets shaped to each part)
- Drills pinholes (0.7mm holes connecting cavities to ink trace layer)
- Creates trace channel walls to guide conductive ink
- Sculpts top surface (dome/ridge) and button geometry (socket + stem + cap)
- Generates extras (battery hatch, holder clips)
- Compiles to STL via OpenSCAD CLI

**Writes → `enclosure.scad` + `enclosure.stl`**

---

### 7. G-code Pipeline (deterministic algorithm)

**Reads:**
- `enclosure.stl` (3D model)
- `routing.json` (trace bitmap for ink alignment)
- Printer config (bed size, nozzle)
- Filament config (PLA/PETG/ASA temps, fan speeds)

**Does:**
- Slices STL with PrusaSlicer
- Injects two M601 PAUSE commands at computed heights:
  - **PAUSE 1** @ ~2mm: iron surface flat, then print conductive ink
  - **PAUSE 2** @ h−2mm: insert components into cavities
- Aligns ink trace pattern to printer sweep lines

**Writes → `enclosure.gcode` + `print_job.json`**

---

### 8. Firmware Generator (deterministic algorithm)

**Reads:**
- `design.json` (device description)
- `circuit.json` (components, nets)
- `routing.json` (final pin assignments)
- Component catalog (pin functions)

**Does:**
- Builds a firmware context document (maps physical pins → Arduino pins)
- Validates pin assignments against ATmega328P capabilities (PWM, analog, etc.)
- Prepares context for the Setup Agent

**Writes → `firmware.ino` + `sim_config.json`**

---

### 9. Setup Agent (LLM chat)

**Reads:**
- Firmware context (from stage 8: pin mappings, component list)
- `circuit.json` (what the device should do)
- `routing.json` (final pin assignments)

**Does:**
- Generates an Arduino sketch for ATmega328P implementing the device logic
  (blink patterns, button handling, serial output, etc.)
- Compiles with arduino-cli (retries up to 3× on error)
- User can test in a live **WebSocket simulator**
  (press buttons, see LEDs light up, read serial output)

**Writes → `firmware.ino` (final) + `sim_config.json`**

---

## How the Physical Device Gets Built

```
   WHAT THE 3D PRINTER PRODUCES (cross-section side view):

   Z = 0 mm   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  ← Solid PLA floor (2mm thick)
               ─── PAUSE 1 ─────────  ← Printer pauses. User irons surface flat,
   Z = 2 mm   ════════════════════════    then runs silver ink printer (conductive traces)
               ┌────┐      ┌────┐
   Z = 3 mm   │    │      │    │     ← Air + component cavities
               │    │      │    │       (pockets shaped to each part)
               │MCU │      │bat.│
               │    │      │    │
   Z = h-2mm  │····│      │····│     ← Pinholes at bottom connect pins to ink layer
               ─── PAUSE 2 ─────────  ← Printer pauses. User drops components in.
               │▓▓▓▓│      │▓▓▓▓│       Component pins touch the conductive ink traces.
   Z = h mm   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  ← Solid PLA ceiling seals everything in
```

---

## Frontend ↔ Backend Communication

```
  ┌─────────────┐                              ┌─────────────┐
  │   BROWSER   │                              │   SERVER    │
  │  (Next.js)  │                              │  (FastAPI)  │
  └──────┬──────┘                              └──────┬──────┘
         │                                            │
         │──── POST /design { prompt } ──────────────▶│  Start design agent
         │◀─── SSE stream: thinking, text, ──────────│  Stream response back
         │     tool_call, design.json, done           │
         │                                            │
         │──── POST /circuit ────────────────────────▶│  Start circuit agent
         │◀─── SSE stream: text, circuit.json, ─────│  Stream response back
         │     design_feedback (if problem), done     │
         │                                            │
         │──── POST /manufacture/placement ──────────▶│  Start placer
         │──── GET  /manufacture/placement ──────────▶│  Poll every 2s...
         │◀─── { status: "done", result: {...} } ────│  Done!
         │                                            │
         │     (same poll pattern for routing,        │
         │      bitmap, scad, compile, gcode)         │
         │                                            │
         │──── POST /manufacture/bundle ─────────────▶│  Download all outputs
         │                                            │
         │──── WebSocket /setup/sim ─────────────────▶│  Firmware simulator
         │◀───▶ press button → pin changes, ─────────│  Real-time interaction
         │      serial output, LED states             │
         │                                            │
```

**Frontend tabs:** Design → Circuit → Manufacture → **Guide** → Setup

The **Guide** panel shows step-by-step assembly instructions with
component-specific guidance for each part type (controller, button, LED, etc.).

---

## What Lives Where (file structure)

```
  manufacturerAI/                           manufacturerAI-Frontend/
  ├── catalog/*.json  (component library)   ├── src/app/         (pages: /, /catalog, /debug)
  │   └── disabled/   (unused components)   ├── src/components/
  ├── src/                                  │   ├── chat/        (ChatLog, ChatInput, ChatMessage)
  │   ├── session.py  (state per project)   │   ├── pipeline/    (Design/Circuit/Mfg/Guide/Setup)
  │   ├── agent/                            │   ├── viewport/    (10 viewport components)
  │   │   ├── core.py    (all 3 agents)     │   │   ├── Scene3D, DesignViewport
  │   │   ├── tools.py   (tool defs)        │   │   ├── PlacementViewport, RoutingViewport
  │   │   ├── prompt.py  (system prompts)   │   │   ├── BitmapViewport, DeviceSimulator
  │   │   ├── messages.py                   │   │   └── ComponentPreview3D, OutlineSVG...
  │   │   └── config.py                     │   ├── ui/          (ColorPicker, ErrorWindow...)
  │   ├── pipeline/                         │   └── layout/      (Sidebar)
  │   │   ├── design/    (data models)      ├── src/contexts/    (Session, Pipeline, Theme, Error)
  │   │   ├── circuit/   (validation)       ├── src/hooks/
  │   │   ├── placer/    (algorithm)        │   ├── useDesignAgent, useCircuitAgent
  │   │   ├── router/    (algorithm)        │   ├── useSetupAgent, useManufacture
  │   │   ├── scad/      (algorithm)        │   ├── useCatalog, useSimulation
  │   │   ├── gcode/     (algorithm)        ├── src/lib/api/     (HTTP/SSE/WS clients)
  │   │   ├── firmware/  (code gen)         │   ├── sessions, design, circuit, setup
  │   │   └── config.py  (printer cfg)      │   ├── printers, catalog, debug
  │   ├── catalog/  (component loader)      │   └── pipeline/    (placement, routing, bitmap,
  │   └── web/      (FastAPI routes)        │                     scad, compile, gcode, bundle)
  └── outputs/sessions/{id}/                └── src/types/       (models.ts, events.ts)
      ├── design/      design.json + outline.json
      ├── circuit/     circuit.json
      ├── placement/   placement.json
      ├── routing/     routing.json
      ├── bitmap/      trace_bitmap.txt
      ├── scad/        enclosure.scad + enclosure.stl
      ├── gcode/       enclosure.gcode + print_job.json
      └── firmware/    firmware.ino + sim_config.json
```

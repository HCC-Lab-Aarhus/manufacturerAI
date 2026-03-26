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
    ┌───────────┐    ┌───────────┐    ┌────────┐    ┌────────┐    ┌──────┐    ┌───────┐    ┌───────┐
    │  1.DESIGN │───▶│ 2.CIRCUIT │───▶│3.PLACE │───▶│4.ROUTE │───▶│5.SCAD│───▶│6.GCODE│───▶│7.SETUP│
    │   (LLM)  │    │   (LLM)   │    │ (algo) │    │ (algo) │    │(algo)│    │(algo) │    │ (LLM) │
    └───────────┘    └───────────┘    └────────┘    └────────┘    └──────┘    └───────┘    └───────┘
          │                │               │             │            │           │             │
      design.json    circuit.json   placement.json  routing.json  .scad/.stl   .gcode     firmware.ino
```

Each step **reads** the output of previous steps and **writes** one artifact.  
If a step changes, everything downstream is automatically invalidated.

---

## Step-by-Step: What Each Stage Reads, Does, and Writes

### 1. Design Agent (LLM chat)

```
  READS                    DOES                              WRITES
  ─────                    ────                              ──────
  • User messages          • Chats with user iteratively     → design.json
  • Component catalog      • Decides the physical shape        ├─ device name & description
    (what parts exist)       (CSG booleans: circles,           ├─ shape (CSG tree)
                             rectangles, unions)                ├─ outline (polygon vertices)
                           • Sets enclosure height              ├─ enclosure (height, surface style)
                           • Places user-facing parts           └─ ui_placements
                             (LEDs, buttons) on the               (LED at x=20 y=10,
                             surface of the device                 button at x=40 y=60)
```

### 2. Circuit Agent (LLM chat)

```
  READS                    DOES                              WRITES
  ─────                    ────                              ──────
  • design.json            • Picks internal components       → circuit.json
  • Component catalog        (MCU, resistors, battery)         ├─ components
                           • Defines electrical nets             │   (what parts to use)
                             (which pins connect to what)      └─ nets
                           • Allocates MCU GPIO pins               (VCC connects pin 8 → LED+,
                           • Validates voltage/current              GND connects pin 11 → LED−)
                           
                           If the parts don't fit:
                           sends feedback → Design Agent
                           to make the enclosure bigger
```

### 3. Placer (deterministic algorithm)

```
  READS                    DOES                              WRITES
  ─────                    ────                              ──────
  • design.json            • Finds positions for the         → placement.json
    (outline shape)          internal components                ├─ component positions
  • circuit.json             (MCU, resistors, battery)           │   (mcu at x=45 y=40,
    (what components)      • Grid search with scoring:            │    resistor at x=30 y=35)
  • Component catalog        − Must fit inside outline           └─ rotation angles
    (physical sizes)         − No overlapping
                             − Leave routing channels
                             − Cluster connected parts
```

### 4. Router (deterministic algorithm)

```
  READS                    DOES                              WRITES
  ─────                    ────                              ──────
  • design.json            • Draws conductive trace paths    → routing.json
  • circuit.json             between connected pins             ├─ traces
    (nets)                 • A* pathfinding on 0.5mm grid        │   (list of point-to-point paths)
  • placement.json         • Resolves MCU pin groups             ├─ final pin assignments
    (positions)              to actual pin numbers                │   (gpio_1 → physical pin 10)
  • Component catalog      • Avoids crossing traces              └─ trace_bitmap.txt
    (pin locations)        • Iterates to improve                     (2D grid for ink printer)
```

### 5. SCAD Generator (deterministic algorithm)

```
  READS                    DOES                              WRITES
  ─────                    ────                              ──────
  • design.json            • Generates a 3D-printable        → enclosure.scad
    (shape, enclosure)       enclosure in OpenSCAD code         (OpenSCAD source)
  • placement.json         • Hollows out component           → enclosure.stl
    (positions)              cavities (pockets for parts)       (compiled 3D model)
  • routing.json           • Drills pinholes (0.7mm holes
    (traces)                 connecting cavities to ink
  • Component catalog        trace layer below)
    (body dimensions)      • Creates trace channel walls
                           • Sculpts top surface (dome/
                             ridge) and button geometry
```

### 6. G-code Pipeline (deterministic algorithm)

```
  READS                    DOES                              WRITES
  ─────                    ────                              ──────
  • enclosure.stl          • Slices STL with PrusaSlicer     → enclosure.gcode
  • routing.json           • Injects two PAUSE commands:        (3D printer instructions)
    (trace bitmap)           PAUSE 1 @ 2mm: iron surface,    → ink trace pattern
  • Printer config           print conductive ink               (for ink printer alignment)
  • Filament config        PAUSE 2 @ h-2mm: insert
                             components into cavities
                           • Aligns ink pattern to
                             printer sweep lines
```

### 7. Setup Agent (LLM chat)

```
  READS                    DOES                              WRITES
  ─────                    ────                              ──────
  • circuit.json           • Generates an Arduino sketch     → firmware.ino
    (components, nets)       for ATmega328P                    (compilable Arduino code)
  • routing.json           • Implements the device logic
    (pin assignments)        (blink patterns, button
  • Component catalog        handling, etc.)
    (pin functions)        • Compiles with arduino-cli
                           • User can test in a live
                             WebSocket simulator
                             (press buttons, see LEDs)
```

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
         │      scad, compile, gcode)                 │
         │                                            │
         │──── WebSocket /setup/simulate ────────────▶│  Firmware simulator
         │◀───▶ press button → pin changes, ─────────│  Real-time interaction
         │      serial output, LED states             │
         │                                            │
```

---

## What Lives Where (file structure)

```
  manufacturerAI/                           manufacturerAI-Frontend/
  ├── catalog/*.json  (component library)   ├── src/app/         (pages)
  ├── src/                                  ├── src/components/  (UI)
  │   ├── session.py  (state per project)   │   ├── chat/        (ChatLog, ChatInput)
  │   ├── agent/                            │   ├── pipeline/    (Design/Circuit/Mfg panels)
  │   │   ├── design.py   (LLM)            │   ├── viewport/    (2D, 3D, placement, routing)
  │   │   ├── circuit.py  (LLM)            │   └── ui/          (shared widgets)
  │   │   └── setup.py    (LLM)            ├── src/contexts/    (global React state)
  │   ├── pipeline/                         ├── src/hooks/       (agent & pipeline logic)
  │   │   ├── placer/     (algorithm)       └── src/lib/api/     (HTTP/SSE/WS clients)
  │   │   ├── router/     (algorithm)
  │   │   ├── scad/       (algorithm)       One hook per pipeline stage:
  │   │   └── gcode/      (algorithm)         useDesignAgent, useCircuitAgent,
  │   ├── catalog/  (component loader)        useManufacture, useSetupAgent,
  │   └── web/      (FastAPI routes)          useSimulation
  └── outputs/sessions/{id}/
      ├── design/design.json
      ├── circuit/circuit.json
      ├── placement/placement.json
      ├── routing/routing.json
      ├── scad/enclosure.scad + .stl
      ├── gcode/enclosure.gcode
      └── setup/firmware.ino
```

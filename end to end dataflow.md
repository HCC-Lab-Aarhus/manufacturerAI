# End-to-End Data Flow

The pipeline turns a user's sentence into a manufactured electronic device.
Each step reads files from previous steps and writes exactly one artifact
(plus conversation logs for the LLM steps).

```
 User: "Make me a flashlight"
   │
   ▼
 DESIGN ──▶ CIRCUIT ──▶ PLACEMENT ──▶ ROUTING ──▶ BITMAP ──▶ SCAD ──▶ COMPILE ──▶ GCODE ──▶ SETUP
  (LLM)      (LLM)       (algo)       (algo)      (algo)    (algo)    (tool)      (algo)    (LLM)
```

If any step changes, everything downstream is invalidated and must re-run.

---

## 1. Design Agent (LLM)

The user describes what they want. The agent iteratively designs the physical
shape, sets the enclosure height, and places user-visible parts (LEDs, buttons)
on the surface.

| Reads | Writes |
|-------|--------|
| User messages | `design.json` |
| Component catalog | |

`design.json` contains:
- Device name and description
- Shape (CSG tree: unions/intersections of rectangles, circles)
- Outline (polygon vertices with optional corner rounding and per-vertex height)
- Enclosure (height, top/bottom surface style, edge profiles)
- UI placements (which LEDs and buttons go where on the surface)

---

## 2. Circuit Agent (LLM)

Reads the design and picks the internal electronics: MCU, resistors, battery.
Defines which pins connect to what (nets). Validates voltage and current.

| Reads | Writes |
|-------|--------|
| `design.json` | `circuit.json` |
| Component catalog | |

`circuit.json` contains:
- Components (catalog ID, instance ID, config for each part)
- Nets (e.g. "VCC" connects mcu:pin_8 → led:anode, "GND" connects mcu:pin_11 → led:cathode)

If the chosen parts don't physically fit inside the enclosure, the Circuit Agent
sends **feedback back to the Design Agent** asking it to make the shape bigger.

---

## 3. Placement (algorithm)

Finds x/y positions for all internal components inside the outline polygon.
Grid search with scoring: must fit inside outline, no overlapping, leave space
for routing channels, cluster connected parts together.

| Reads | Writes |
|-------|--------|
| `design.json` | `placement.json` |
| `circuit.json` | |
| Component catalog | |

`placement.json` contains:
- Position and rotation for each component (e.g. MCU at x=45, y=40, rotation=0)

---

## 4. Routing (algorithm)

Draws conductive trace paths between all connected pins. Uses A* pathfinding
on a 0.5mm grid. Resolves MCU pin groups ("digital_pins") to actual physical
pin numbers. Supports jumper wires when traces can't avoid crossing.

| Reads | Writes |
|-------|--------|
| `design.json` | `routing.json` |
| `circuit.json` | |
| `placement.json` | |
| Component catalog | |

`routing.json` contains:
- Traces (point-to-point path for each net)
- Pin assignments (e.g. gpio_1 resolved to physical pin 10)
- Jumper wires (if any traces needed to cross)

---

## 5. Bitmap (algorithm)

Converts the vector trace paths into a pixel grid aligned to the silver ink
printer's sweep lines.

| Reads | Writes |
|-------|--------|
| `design.json` | `trace_bitmap.txt` |
| `routing.json` | |
| Printer config | |

`trace_bitmap.txt` contains:
- Rows of `0` and `1` characters — each cell is ink or no ink

---

## 6. SCAD Generation (algorithm)

Generates a 3D-printable enclosure as OpenSCAD source code. Creates component
cavities (pockets where parts sit), pinholes (0.7mm holes connecting cavities
to the ink trace layer), trace channel walls, button geometry, and battery
hatch/clips.

| Reads | Writes |
|-------|--------|
| `design.json` | `enclosure.scad` |
| `circuit.json` | |
| `placement.json` | |
| `routing.json` | |
| Component catalog | |

---

## 7. STL Compile (external tool)

Runs the OpenSCAD CLI to compile the `.scad` source into a 3D mesh.

| Reads | Writes |
|-------|--------|
| `enclosure.scad` | `enclosure.stl` |

External tool: **OpenSCAD CLI**

---

## 8. G-code (algorithm + external tool)

Slices the STL into 3D printer instructions. Injects two pause commands:

- **PAUSE 1** at ~2mm: user irons the surface flat, then runs the silver ink printer
- **PAUSE 2** at h−2mm: user drops components into their cavities

| Reads | Writes |
|-------|--------|
| `enclosure.stl` | `enclosure.gcode` |
| `design.json` | |
| `placement.json` | |
| `routing.json` | |
| Printer config | |
| Filament config | |

External tool: **PrusaSlicer CLI**

---

## 9. Setup Agent (LLM)

Generates an Arduino sketch for the ATmega328P microcontroller. Implements the
device logic (blink patterns, button handling, serial output). Compiles with
arduino-cli. The user can test the firmware in a live WebSocket simulator
(press buttons, see LEDs, read serial output).

| Reads | Writes |
|-------|--------|
| `design.json` | `firmware.ino` |
| `circuit.json` | `sim_config.json` |
| `routing.json` | |
| Component catalog | |

External tool: **arduino-cli** (compiles the sketch, retries up to 3× on error)

---

## Full dependency graph

Shows which artifacts each step needs before it can run:

```
  design.json ─────┬──────────────────────────────────────────────────────┐
                    │                                                      │
                    ▼                                                      │
              circuit.json ───┬────────────────────────────────┐          │
                              │                                │          │
                    ┌─────────┘                                │          │
                    ▼                                          │          │
              placement.json ─┬─────────────────┐             │          │
                              │                 │             │          │
                    ┌─────────┘                 │             │          │
                    ▼                           ▼             ▼          ▼
              routing.json ──────┬──────▶ enclosure.scad   firmware.ino
                    │            │              │
                    ▼            │              ▼
              trace_bitmap.txt   │        enclosure.stl
                                 │              │
                                 │              ▼
                                 └───────▶ enclosure.gcode
```

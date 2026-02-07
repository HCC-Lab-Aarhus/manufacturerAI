# PCB Router

Automatic PCB Routing Engine for single-layer, planar boards with hard non-crossing constraints.

## Features

- **Deterministic Routing**: Given identical inputs, produces identical results
- **A* Pathfinding**: Manhattan (orthogonal) routing with configurable grid resolution
- **Manufacturing Constraints**: Configurable trace width and clearance
- **Visualization**: Debug output showing grid, blocked cells, traces, and pads
- **Raster Output**: PNG masks for 3D-printed PCB fabrication

## Installation

```bash
npm install
```

## Usage

### As a Library

```typescript
import { routePCB, routeAndVisualize, RouterInput } from './src'

const input: RouterInput = {
  board: {
    boardWidth: 100,      // mm
    boardHeight: 80,      // mm
    gridResolution: 1.0   // mm per cell
  },
  manufacturing: {
    traceWidth: 1.2,      // mm - minimum for 3D printed PCB
    traceClearance: 1.5   // mm - minimum clearance
  },
  placement: {
    buttons: [
      { id: 'BTN1', x: 15, y: 20, signalNet: 'BTN1_SIG' }
    ],
    controllers: [
      {
        id: 'MCU',
        x: 80,
        y: 40,
        pins: {
          P1: 'BTN1_SIG',
          VCC: 'VCC',
          GND: 'GND'
        }
      }
    ]
  }
}

// Route and generate output files
const result = await routeAndVisualize(input, './output/pcb')
```

### CLI

```bash
npm run build
npm start
```

## Input Format

### Button Definition

```json
{
  "id": "BTN1",
  "x": 12.5,
  "y": 34.0,
  "signalNet": "BTN1_SIG"
}
```

### Controller Definition

```json
{
  "id": "MCU",
  "x": 60.0,
  "y": 40.0,
  "pins": {
    "P1": "BTN1_SIG",
    "P2": "BTN2_SIG",
    "VCC": "VCC",
    "GND": "GND"
  }
}
```

## Output Files

- `*_debug.png` - Visualization showing grid, traces, and pads
- `*_positive.png` - Positive mask (white = conductive)
- `*_negative.png` - Negative mask (white = void/insulating)

## Routing Algorithm

1. **Net Ordering**: GND → VCC → Signal nets
2. **Pathfinding**: A* search with Manhattan heuristic
3. **Trace Commitment**: Each routed trace blocks space for subsequent nets

## Constraints

- Single copper layer
- No vias
- No trace crossings
- Orthogonal (Manhattan) routing only
- Rectangular board outline

## Architecture

```
src/
├── types.ts       # Type definitions
├── grid.ts        # Grid management and occupancy
├── pathfinder.ts  # A* pathfinding algorithm
├── router.ts      # Main routing orchestration
├── output.ts      # PNG rasterization
├── visualizer.ts  # Debug visualization
└── index.ts       # Entry point and exports
```

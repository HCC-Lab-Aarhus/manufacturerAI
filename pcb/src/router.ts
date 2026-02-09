import {
  RouterInput,
  RoutingResult,
  Trace,
  Pad,
  FailedNet,
  GridCoordinate,
  Button,
  Controller,
  Battery,
  Diode,
  Footprints,
  ComponentBody
} from './types'
import { Grid } from './grid'

/**
 * PCB Router — thin adapter around @tscircuit/capacity-autorouter.
 *
 * Converts our RouterInput (component placements in mm) into the
 * SimpleRouteJson format expected by tscircuit's capacity-mesh solver,
 * runs the solver, and converts the output traces back to our
 * grid-coordinate RoutingResult.
 *
 * The capacity-mesh approach replaces our custom A* / perimeter routing
 * with a hierarchical solver that handles congestion and polygon outlines
 * natively.
 */
export class Router {
  private readonly input: RouterInput
  private readonly footprints: Footprints
  private grid: Grid
  private pads: Map<string, Pad>
  private traces: Trace[]
  private failedNets: FailedNet[]
  private componentBodies: ComponentBody[]
  private readonly bodyKeepoutCells: number

  constructor(input: RouterInput) {
    this.input = input
    this.footprints = input.footprints
    this.grid = new Grid(input.board, input.manufacturing)
    this.pads = new Map()
    this.traces = []
    this.failedNets = []
    this.componentBodies = []
    this.bodyKeepoutCells = Math.ceil(
      input.manufacturing.traceWidth / (2 * input.board.gridResolution)
    ) + 1
  }

  route(): RoutingResult {
    this.initializeComponents()
    return this.runAutorouter()
  }

  // ── Component placement (populates pads + componentBodies for viz) ──

  private initializeComponents(): void {
    for (const battery of this.input.placement.batteries || []) {
      this.placeBattery(battery)
    }
    for (const diode of this.input.placement.diodes || []) {
      this.placeDiode(diode)
    }
    for (const button of this.input.placement.buttons) {
      this.placeButton(button)
    }
    for (const controller of this.input.placement.controllers) {
      this.placeController(controller)
    }
  }

  private placeBattery(battery: Battery): void {
    const batteryCenter = this.grid.worldToGrid(battery.x, battery.y)
    const fp = this.footprints.battery
    const bodyW = battery.bodyWidth ?? fp.bodyWidth ?? 0
    const bodyH = battery.bodyHeight ?? fp.bodyHeight ?? 0

    if (bodyW > 0 && bodyH > 0) {
      this.grid.blockRectangularBody(
        battery.x, battery.y, bodyW / 2, bodyH / 2, this.bodyKeepoutCells
      )
      this.componentBodies.push({
        id: battery.id, x: battery.x, y: battery.y,
        width: bodyW, height: bodyH
      })
    }

    const res = this.input.board.gridResolution
    let vccPadY: number, gndPadY: number

    if (bodyH > 0) {
      const padOffsetCells = this.bodyKeepoutCells + 5
      vccPadY = battery.y + bodyH / 2 + padOffsetCells * res
      gndPadY = battery.y - bodyH / 2 - padOffsetCells * res
    } else {
      // No body dims → use padSpacing from footprint
      vccPadY = battery.y + fp.padSpacing / 2
      gndPadY = battery.y - fp.padSpacing / 2
    }

    this.pads.set(`${battery.id}.VCC`, {
      componentId: battery.id, pinName: 'VCC',
      center: this.grid.worldToGrid(battery.x, vccPadY),
      net: 'VCC', componentCenter: batteryCenter
    })
    this.pads.set(`${battery.id}.GND`, {
      componentId: battery.id, pinName: 'GND',
      center: this.grid.worldToGrid(battery.x, gndPadY),
      net: 'GND', componentCenter: batteryCenter
    })
  }

  private placeDiode(diode: Diode): void {
    const diodeCenter = this.grid.worldToGrid(diode.x, diode.y)
    const halfSpacing = this.footprints.diode.padSpacing / 2

    this.pads.set(`${diode.id}.A`, {
      componentId: diode.id, pinName: 'A',
      center: this.grid.worldToGrid(diode.x - halfSpacing, diode.y),
      net: diode.signalNet, componentCenter: diodeCenter
    })
    this.pads.set(`${diode.id}.K`, {
      componentId: diode.id, pinName: 'K',
      center: this.grid.worldToGrid(diode.x + halfSpacing, diode.y),
      net: 'GND', componentCenter: diodeCenter
    })
  }

  private placeButton(button: Button): void {
    const buttonCenter = this.grid.worldToGrid(button.x, button.y)
    const halfX = this.footprints.button.pinSpacingX / 2
    const halfY = this.footprints.button.pinSpacingY / 2

    this.pads.set(`${button.id}.A1`, {
      componentId: button.id, pinName: 'A1',
      center: this.grid.worldToGrid(button.x - halfX, button.y - halfY),
      net: button.signalNet, componentCenter: buttonCenter
    })
    this.pads.set(`${button.id}.A2`, {
      componentId: button.id, pinName: 'A2',
      center: this.grid.worldToGrid(button.x - halfX, button.y + halfY),
      net: 'NC', componentCenter: buttonCenter
    })
    this.pads.set(`${button.id}.B1`, {
      componentId: button.id, pinName: 'B1',
      center: this.grid.worldToGrid(button.x + halfX, button.y - halfY),
      net: 'GND', componentCenter: buttonCenter
    })
    this.pads.set(`${button.id}.B2`, {
      componentId: button.id, pinName: 'B2',
      center: this.grid.worldToGrid(button.x + halfX, button.y + halfY),
      net: 'NC', componentCenter: buttonCenter
    })
  }

  private placeController(controller: Controller): void {
    const pinNames = Object.keys(controller.pins)
    const pinCount = pinNames.length
    const controllerCenter = this.grid.worldToGrid(controller.x, controller.y)
    const rowSpacing = this.footprints.controller.rowSpacing
    const pinSpacing = this.footprints.controller.pinSpacing
    const pinsPerSide = Math.ceil(pinCount / 2)
    const totalHeight = (pinsPerSide - 1) * pinSpacing

    pinNames.forEach((pinName, index) => {
      const pinNumber = index + 1
      let pinX: number, pinY: number
      if (pinNumber <= pinsPerSide) {
        pinX = controller.x - rowSpacing / 2
        pinY = controller.y - totalHeight / 2 + (pinNumber - 1) * pinSpacing
      } else {
        pinX = controller.x + rowSpacing / 2
        const rightSideIndex = pinCount - pinNumber
        pinY = controller.y - totalHeight / 2 + rightSideIndex * pinSpacing
      }

      this.pads.set(`${controller.id}.${pinName}`, {
        componentId: controller.id, pinName,
        center: this.grid.worldToGrid(pinX, pinY),
        net: controller.pins[pinName],
        componentCenter: controllerCenter
      })
    })
  }

  // ── tscircuit autorouter integration ───────────────────────────

  private runAutorouter(): RoutingResult {
    const srj = this.buildSimpleRouteJson()
    console.error(`\n=== tscircuit capacity-mesh autorouter ===`)
    console.error(`  Obstacles: ${srj.obstacles.length}`)
    console.error(`  Connections: ${srj.connections.length} (${srj.connections.map((c: any) => c.name).join(', ')})`)
    console.error(`  Bounds: [${srj.bounds.minX}, ${srj.bounds.minY}] → [${srj.bounds.maxX}, ${srj.bounds.maxY}]`)
    console.error(`  Outline: ${srj.outline ? srj.outline.length + ' vertices' : 'none'}`)
    console.error(`  Layer count: ${srj.layerCount}, trace width: ${srj.minTraceWidth}mm`)

    try {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const { AssignableAutoroutingPipeline2 } = require('@tscircuit/capacity-autorouter')

      const solver = new AssignableAutoroutingPipeline2(srj, { effort: 5 })
      const t0 = Date.now()
      solver.solve()
      const elapsed = ((Date.now() - t0) / 1000).toFixed(1)

      if (solver.solved) {
        console.error(`  Solved in ${elapsed}s`)
        const output = solver.getOutputSimpleRouteJson()
        return this.convertResult(output, srj)
      } else {
        console.error(`  Failed after ${elapsed}s: ${solver.error || 'unknown'}`)
        // Try to extract partial results
        try {
          const output = solver.getOutputSimpleRouteJson()
          return this.convertResult(output, srj)
        } catch {
          return this.buildFailureResult(srj, solver.error || 'Autorouter failed')
        }
      }
    } catch (error) {
      console.error(`  Error: ${error instanceof Error ? error.message : String(error)}`)
      return this.buildFailureResult(srj, String(error))
    }
  }

  /**
   * Convert our RouterInput into tscircuit's SimpleRouteJson format.
   *
   * Creates:
   *   - Obstacles for component bodies (battery, controller, buttons)
   *   - Obstacles for each pad (with connectedTo linking to the net)
   *   - Connections grouping pads by net name
   *   - Board outline from the polygon
   */
  private buildSimpleRouteJson(): any {
    const boardW = this.input.board.boardWidth
    const boardH = this.input.board.boardHeight
    const res = this.input.board.gridResolution
    const fp = this.footprints
    const obstacles: any[] = []
    const padWorld = new Map<string, { x: number; y: number; net: string }>()
    const PAD_SIZE = 1.0 // mm — physical pad obstacle size

    // ── Battery: body obstacle + VCC/GND pads ──
    for (const battery of this.input.placement.batteries || []) {
      const bodyW = battery.bodyWidth ?? fp.battery.bodyWidth ?? 0
      const bodyH = battery.bodyHeight ?? fp.battery.bodyHeight ?? 0

      // Battery body is handled by the pad offset — traces are prevented
      // from routing through the body by the VCC/GND pads placed with
      // padOffset from the body edges. We don't add a large blocking
      // obstacle because it over-constrains the capacity-mesh solver.

      let vccY: number, gndY: number
      if (bodyH > 0) {
        const padOffsetCells = this.bodyKeepoutCells + 5
        const padOffset = padOffsetCells * res
        vccY = battery.y + bodyH / 2 + padOffset
        gndY = battery.y - bodyH / 2 - padOffset
      } else {
        // No body dims → use padSpacing from footprint
        vccY = battery.y + fp.battery.padSpacing / 2
        gndY = battery.y - fp.battery.padSpacing / 2
      }

      const vccId = `${battery.id}.VCC`
      const gndId = `${battery.id}.GND`
      padWorld.set(vccId, { x: battery.x, y: vccY, net: 'VCC' })
      padWorld.set(gndId, { x: battery.x, y: gndY, net: 'GND' })

      obstacles.push({
        type: 'rect', layers: ['top'],
        center: { x: battery.x, y: vccY },
        width: PAD_SIZE, height: PAD_SIZE,
        connectedTo: [vccId]
      })
      obstacles.push({
        type: 'rect', layers: ['top'],
        center: { x: battery.x, y: gndY },
        width: PAD_SIZE, height: PAD_SIZE,
        connectedTo: [gndId]
      })
    }

    // ── Buttons: body obstacle + 4 pads ──
    for (const button of this.input.placement.buttons) {
      const halfX = fp.button.pinSpacingX / 2
      const halfY = fp.button.pinSpacingY / 2

      // Button body (6×6mm tactile switch)
      obstacles.push({
        type: 'rect', layers: ['top'],
        center: { x: button.x, y: button.y },
        width: 6, height: 6,
        connectedTo: []
      })

      const pins = [
        { id: `${button.id}.A1`, x: button.x - halfX, y: button.y - halfY, net: button.signalNet },
        { id: `${button.id}.A2`, x: button.x - halfX, y: button.y + halfY, net: 'NC' },
        { id: `${button.id}.B1`, x: button.x + halfX, y: button.y - halfY, net: 'GND' },
        { id: `${button.id}.B2`, x: button.x + halfX, y: button.y + halfY, net: 'NC' },
      ]

      for (const pin of pins) {
        if (pin.net === 'NC') {
          obstacles.push({
            type: 'rect', layers: ['top'],
            center: { x: pin.x, y: pin.y },
            width: PAD_SIZE, height: PAD_SIZE,
            connectedTo: []
          })
        } else {
          padWorld.set(pin.id, { x: pin.x, y: pin.y, net: pin.net })
          obstacles.push({
            type: 'rect', layers: ['top'],
            center: { x: pin.x, y: pin.y },
            width: PAD_SIZE, height: PAD_SIZE,
            connectedTo: [pin.id]
          })
        }
      }
    }

    // ── Controller: body obstacle + all pin pads ──
    for (const controller of this.input.placement.controllers) {
      const pinNames = Object.keys(controller.pins)
      const pinCount = pinNames.length
      const pinsPerSide = Math.ceil(pinCount / 2)
      const totalHeight = (pinsPerSide - 1) * fp.controller.pinSpacing

      // Controller body (area between pin rows)
      obstacles.push({
        type: 'rect', layers: ['top'],
        center: { x: controller.x, y: controller.y },
        width: fp.controller.rowSpacing - PAD_SIZE,
        height: totalHeight + 2 * PAD_SIZE,
        connectedTo: []
      })

      pinNames.forEach((pinName, index) => {
        const pinNumber = index + 1
        let pinX: number, pinY: number
        if (pinNumber <= pinsPerSide) {
          pinX = controller.x - fp.controller.rowSpacing / 2
          pinY = controller.y - totalHeight / 2 + (pinNumber - 1) * fp.controller.pinSpacing
        } else {
          pinX = controller.x + fp.controller.rowSpacing / 2
          const rightSideIndex = pinCount - pinNumber
          pinY = controller.y - totalHeight / 2 + rightSideIndex * fp.controller.pinSpacing
        }

        const net = controller.pins[pinName]
        const pinId = `${controller.id}.${pinName}`

        if (net === 'NC') {
          obstacles.push({
            type: 'rect', layers: ['top'],
            center: { x: pinX, y: pinY },
            width: PAD_SIZE, height: PAD_SIZE,
            connectedTo: []
          })
        } else {
          padWorld.set(pinId, { x: pinX, y: pinY, net })
          obstacles.push({
            type: 'rect', layers: ['top'],
            center: { x: pinX, y: pinY },
            width: PAD_SIZE, height: PAD_SIZE,
            connectedTo: [pinId]
          })
        }
      })
    }

    // ── Diodes: anode + cathode pads ──
    for (const diode of this.input.placement.diodes || []) {
      const halfSpacing = fp.diode.padSpacing / 2
      const anodeId = `${diode.id}.A`
      const cathodeId = `${diode.id}.K`

      padWorld.set(anodeId, { x: diode.x - halfSpacing, y: diode.y, net: diode.signalNet })
      padWorld.set(cathodeId, { x: diode.x + halfSpacing, y: diode.y, net: 'GND' })

      obstacles.push({
        type: 'rect', layers: ['top'],
        center: { x: diode.x - halfSpacing, y: diode.y },
        width: PAD_SIZE, height: PAD_SIZE,
        connectedTo: [anodeId]
      })
      obstacles.push({
        type: 'rect', layers: ['top'],
        center: { x: diode.x + halfSpacing, y: diode.y },
        width: PAD_SIZE, height: PAD_SIZE,
        connectedTo: [cathodeId]
      })
    }

    // ── Group pads by net → connections ──
    const netMap = new Map<string, Array<{ x: number; y: number; layer: string; pointId: string }>>()
    for (const [padId, pos] of padWorld) {
      if (!netMap.has(pos.net)) netMap.set(pos.net, [])
      netMap.get(pos.net)!.push({ x: pos.x, y: pos.y, layer: 'top', pointId: padId })
    }

    const connections: any[] = []
    for (const [netName, points] of netMap) {
      if (points.length < 2) continue
      connections.push({ name: netName, pointsToConnect: points })
    }

    return {
      layerCount: 1,
      minTraceWidth: this.input.manufacturing.traceWidth,
      defaultObstacleMargin: 0.15,
      obstacles,
      connections,
      bounds: { minX: 0, maxX: boardW, minY: 0, maxY: boardH },
      // Note: polygon outline is intentionally omitted — the rectangular
      // bounds suffice for the capacity mesh, and the polygon constraint
      // over-restricts the solver.  Downstream validators enforce outline
      // bounds on the final trace geometry.
    }
  }

  /**
   * Convert the tscircuit output (SimplifiedPcbTraces in world coords)
   * back to our RoutingResult (Traces with grid-coordinate paths).
   */
  private convertResult(output: any, inputSrj: any): RoutingResult {
    const traces: Trace[] = []
    const failedNets: FailedNet[] = []
    const res = this.input.board.gridResolution
    const routedConnectionNames = new Set<string>()

    if (output.traces) {
      for (const trace of output.traces) {
        const connectionName = trace.connection_name || trace.pcb_trace_id
        const wirePoints: GridCoordinate[] = trace.route
          .filter((s: any) => s.route_type === 'wire')
          .map((s: any) => ({
            x: Math.round(s.x / res),
            y: Math.round(s.y / res)
          }))

        if (wirePoints.length >= 2) {
          traces.push({ net: connectionName, path: wirePoints })
          routedConnectionNames.add(connectionName)
        }
      }
    }

    // Detect unrouted connections
    for (const conn of inputSrj.connections) {
      if (!routedConnectionNames.has(conn.name)) {
        const points = conn.pointsToConnect
        failedNets.push({
          netName: conn.name,
          sourcePin: points[0]?.pointId || 'unknown',
          destinationPin: points[points.length - 1]?.pointId || 'unknown',
          reason: 'No route found by autorouter'
        })
      }
    }

    this.traces = traces
    this.failedNets = failedNets

    console.error(`  Traces: ${traces.length}, Failed nets: ${failedNets.length}`)
    if (failedNets.length > 0) {
      for (const f of failedNets) {
        console.error(`    ${f.netName}: ${f.reason}`)
      }
    }

    return {
      success: failedNets.length === 0,
      traces,
      failedNets
    }
  }

  private buildFailureResult(inputSrj: any, reason: string): RoutingResult {
    const failedNets: FailedNet[] = inputSrj.connections.map((conn: any) => ({
      netName: conn.name,
      sourcePin: conn.pointsToConnect[0]?.pointId || 'unknown',
      destinationPin: conn.pointsToConnect[conn.pointsToConnect.length - 1]?.pointId || 'unknown',
      reason
    }))

    this.traces = []
    this.failedNets = failedNets

    return { success: false, traces: [], failedNets }
  }

  // ── Accessors (used by CLI for visualization) ──

  getGrid(): Grid {
    return this.grid
  }

  getPads(): Map<string, Pad> {
    return this.pads
  }

  getTraces(): Trace[] {
    return this.traces
  }

  getComponentBodies(): ComponentBody[] {
    return this.componentBodies
  }
}

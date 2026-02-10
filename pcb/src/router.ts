import {
  RouterInput,
  RoutingResult,
  Trace,
  Net,
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
import { Pathfinder, CellCostFn } from './pathfinder'

export class Router {
  private readonly input: RouterInput
  private readonly tracePadding: number
  private readonly footprints: Footprints
  private grid: Grid
  private pads: Map<string, Pad>
  private nets: Net[]
  private traces: Trace[]
  private failedNets: FailedNet[]
  private componentBodies: ComponentBody[]
  private readonly maxRipupAttempts: number
  private readonly bodyKeepoutCells: number
  private traceBlockPadding: number
  private readonly normalTraceBlockPadding: number
  private readonly relaxedTraceBlockPadding: number

  constructor(input: RouterInput) {
    this.input = input
    this.footprints = input.footprints
    const clearanceCells = Math.ceil(input.manufacturing.traceClearance / input.board.gridResolution)
    this.tracePadding = clearanceCells
    // Keep-out radius around completed traces and unrelated pads.
    // Set to approximately the MC pin spacing so that traces maintain
    // a full pin-pitch clearance from other conductors.
    this.normalTraceBlockPadding = Math.round(
      input.footprints.controller.pinSpacing / input.board.gridResolution
    )        // round(2.54 / 0.5) = 5 cells = 2.5 mm ≈ pin pitch
    this.relaxedTraceBlockPadding = Math.round(
      input.footprints.controller.pinSpacing / input.board.gridResolution
    ) - 1    // 4 cells = 2.0 mm — tighter fallback
    this.traceBlockPadding = this.normalTraceBlockPadding
    this.maxRipupAttempts = input.maxAttempts ?? 50
    this.grid = new Grid(input.board, input.manufacturing)
    this.pads = new Map()
    this.nets = []
    this.traces = []
    this.failedNets = []
    this.componentBodies = []
    // Body keepout: keep traces one pin-pitch away from component edges
    this.bodyKeepoutCells = Math.round(
      input.footprints.controller.pinSpacing / input.board.gridResolution
    )        // round(2.54 / 0.5) = 5 cells = 2.5 mm ≈ pin pitch
  }

  route(): RoutingResult {
    this.initializeComponents()
    this.extractNets()
    this.routeNets()

    return {
      success: this.failedNets.length === 0,
      traces: this.traces,
      failedNets: this.failedNets
    }
  }

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

    // Block the full compartment body area so no traces route through it
    const bodyW = battery.bodyWidth ?? fp.bodyWidth
    const bodyH = battery.bodyHeight ?? fp.bodyHeight
    if (bodyW > 0 && bodyH > 0) {
      this.grid.blockRectangularBody(
        battery.x, battery.y, bodyW / 2, bodyH / 2, this.bodyKeepoutCells
      )
      this.componentBodies.push({
        id: battery.id,
        x: battery.x,
        y: battery.y,
        width: bodyW,
        height: bodyH
      })
    }

    // Place VCC and GND pads on the SAME side (below the body),
    // separated horizontally by padSpacing.  This keeps one side of
    // the battery completely free for trace routing.
    const res = this.input.board.gridResolution
    const padOffsetCells = this.bodyKeepoutCells + 5
    const padY = battery.y - bodyH / 2 - padOffsetCells * res
    const halfPadSpacing = this.footprints.battery.padSpacing / 2

    const vccPadCenter = this.grid.worldToGrid(battery.x - halfPadSpacing, padY)
    this.pads.set(`${battery.id}.VCC`, {
      componentId: battery.id,
      pinName: 'VCC',
      center: vccPadCenter,
      net: 'VCC',
      componentCenter: batteryCenter
    })

    const gndPadCenter = this.grid.worldToGrid(battery.x + halfPadSpacing, padY)
    this.pads.set(`${battery.id}.GND`, {
      componentId: battery.id,
      pinName: 'GND',
      center: gndPadCenter,
      net: 'GND',
      componentCenter: batteryCenter
    })
  }

  private placeDiode(diode: Diode): void {
    const diodeCenter = this.grid.worldToGrid(diode.x, diode.y)
    const halfSpacing = this.footprints.diode.padSpacing / 2

    const anodePadCenter = this.grid.worldToGrid(diode.x - halfSpacing, diode.y)
    this.pads.set(`${diode.id}.A`, {
      componentId: diode.id,
      pinName: 'A',
      center: anodePadCenter,
      net: diode.signalNet,
      componentCenter: diodeCenter
    })

    const cathodePadCenter = this.grid.worldToGrid(diode.x + halfSpacing, diode.y)
    this.pads.set(`${diode.id}.K`, {
      componentId: diode.id,
      pinName: 'K',
      center: cathodePadCenter,
      net: 'GND',
      componentCenter: diodeCenter
    })
  }

  private placeButton(button: Button): void {
    const buttonCenter = this.grid.worldToGrid(button.x, button.y)
    const halfX = this.footprints.button.pinSpacingX / 2
    const halfY = this.footprints.button.pinSpacingY / 2

    const pinA1Center = this.grid.worldToGrid(button.x - halfX, button.y - halfY)
    this.pads.set(`${button.id}.A1`, {
      componentId: button.id,
      pinName: 'A1',
      center: pinA1Center,
      net: button.signalNet,
      componentCenter: buttonCenter
    })

    const pinA2Center = this.grid.worldToGrid(button.x - halfX, button.y + halfY)
    this.pads.set(`${button.id}.A2`, {
      componentId: button.id,
      pinName: 'A2',
      center: pinA2Center,
      net: 'NC',
      componentCenter: buttonCenter
    })

    const pinB1Center = this.grid.worldToGrid(button.x + halfX, button.y - halfY)
    this.pads.set(`${button.id}.B1`, {
      componentId: button.id,
      pinName: 'B1',
      center: pinB1Center,
      net: 'GND',  // INPUT_PULLUP: button connects signal to GND when pressed
      componentCenter: buttonCenter
    })

    const pinB2Center = this.grid.worldToGrid(button.x + halfX, button.y + halfY)
    this.pads.set(`${button.id}.B2`, {
      componentId: button.id,
      pinName: 'B2',
      center: pinB2Center,
      net: 'NC',
      componentCenter: buttonCenter
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
    const rotated = (controller.rotation ?? 0) === 90

    pinNames.forEach((pinName, index) => {
      const pinNumber = index + 1
      let pinX: number
      let pinY: number

      if (rotated) {
        // 90°: rows run along Y, pins along X
        if (pinNumber <= pinsPerSide) {
          pinY = controller.y - rowSpacing / 2
          pinX = controller.x - totalHeight / 2 + (pinNumber - 1) * pinSpacing
        } else {
          pinY = controller.y + rowSpacing / 2
          const rightSideIndex = pinCount - pinNumber
          pinX = controller.x - totalHeight / 2 + rightSideIndex * pinSpacing
        }
      } else {
        // 0°: rows run along X, pins along Y (default DIP orientation)
        if (pinNumber <= pinsPerSide) {
          pinX = controller.x - rowSpacing / 2
          pinY = controller.y - totalHeight / 2 + (pinNumber - 1) * pinSpacing
        } else {
          pinX = controller.x + rowSpacing / 2
          const rightSideIndex = pinCount - pinNumber
          pinY = controller.y - totalHeight / 2 + rightSideIndex * pinSpacing
        }
      }

      const padCenter = this.grid.worldToGrid(pinX, pinY)
      const net = controller.pins[pinName]

      this.pads.set(`${controller.id}.${pinName}`, {
        componentId: controller.id,
        pinName,
        center: padCenter,
        net,
        componentCenter: controllerCenter
      })
    })
  }

  private extractNets(): void {
    const netMap = new Map<string, Pad[]>()

    for (const pad of this.pads.values()) {
      if (pad.net === 'NC') continue
      if (!netMap.has(pad.net)) {
        netMap.set(pad.net, [])
      }
      netMap.get(pad.net)!.push(pad)
    }

    for (const [netName, pads] of netMap) {
      if (pads.length < 2) continue

      const type = this.getNetType(netName)

      const mstEdges = this.computeMST(pads)
      for (const edge of mstEdges) {
        this.nets.push({
          name: netName,
          source: edge.from,
          sink: edge.to,
          type
        })
      }
    }

    this.nets.sort((a, b) => {
      const priority: Record<string, number> = { SIGNAL: 0, GND: 1, VCC: 2 }
      const typeDiff = priority[a.type] - priority[b.type]
      if (typeDiff !== 0) return typeDiff
      
      const distA = this.manhattanDistance(a.source, a.sink)
      const distB = this.manhattanDistance(b.source, b.sink)
      return distA - distB
    })
  }

  private getNetType(netName: string): 'GND' | 'VCC' | 'SIGNAL' {
    if (netName === 'GND') return 'GND'
    if (netName === 'VCC') return 'VCC'
    return 'SIGNAL'
  }

  private computeMST(pads: Pad[]): { from: GridCoordinate; to: GridCoordinate }[] {
    if (pads.length < 2) return []

    const edges: { from: number; to: number; weight: number }[] = []
    for (let i = 0; i < pads.length; i++) {
      for (let j = i + 1; j < pads.length; j++) {
        const weight = this.manhattanDistance(pads[i].center, pads[j].center)
        edges.push({ from: i, to: j, weight })
      }
    }

    edges.sort((a, b) => a.weight - b.weight)

    const parent = pads.map((_, i) => i)
    const find = (x: number): number => {
      if (parent[x] !== x) parent[x] = find(parent[x])
      return parent[x]
    }
    const union = (x: number, y: number): boolean => {
      const px = find(x)
      const py = find(y)
      if (px === py) return false
      parent[px] = py
      return true
    }

    const result: { from: GridCoordinate; to: GridCoordinate }[] = []
    for (const edge of edges) {
      if (union(edge.from, edge.to)) {
        result.push({
          from: pads[edge.from].center,
          to: pads[edge.to].center
        })
      }
      if (result.length === pads.length - 1) break
    }

    return result
  }

  private manhattanDistance(a: GridCoordinate, b: GridCoordinate): number {
    return Math.abs(a.x - b.x) + Math.abs(a.y - b.y)
  }

  // ── Main routing orchestrator ──────────────────────────────────
  //
  // Two-phase architecture:
  //   Phase 1 — Route power nets (GND, VCC) with a perimeter-biased
  //             cost function so they hug the board edge and keep the
  //             interior free for signals.
  //   Phase 2 — Route signal nets through the freed interior with
  //             simple ordering rip-up.
  //
  // This eliminates the fundamental problem of GND's spanning tree
  // partitioning the board and blocking signal crossings.

  private routeNets(): void {
    const netPads = this.buildNetPadsMap()
    const signalNets = [...netPads.keys()].filter(n => this.getNetType(n) === 'SIGNAL')

    // Try both power orderings and pick the one with fewest total failures
    const powerOrderings: string[][] = [
      ['GND', 'VCC'],
      ['VCC', 'GND'],
    ]

    let bestTotalFails = Infinity
    let bestTraces: Trace[] = []
    let bestFailed: FailedNet[] = []

    for (const powerOrder of powerOrderings) {
      console.error(`\n=== Trying power order: ${powerOrder.join(' → ')} ===`)

      // Phase 1: Route power nets along the perimeter
      this.resetGrid()
      const powerTraces = new Map<string, Set<string>>()
      const powerTraceList: Trace[] = []
      let powerFails = 0
      const powerFailedNets: FailedNet[] = []

      for (const netName of powerOrder) {
        const pads = netPads.get(netName)
        if (!pads || pads.length < 2) continue

        const result = this.routePowerNetPerimeter(netName, pads, powerTraces)
        if (result.success) {
          powerTraces.set(netName, result.routedCells!)
          powerTraceList.push(...result.traces!)
          console.error(`  ${netName}: OK (${result.traces!.length} segments)`)
        } else {
          console.error(`  ${netName}: perimeter failed, trying direct A*`)
          const fallback = this.routeNetDirect(netName, pads, powerTraces)
          if (fallback.success) {
            powerTraces.set(netName, fallback.routedCells!)
            powerTraceList.push(...fallback.traces!)
            console.error(`  ${netName}: direct fallback OK`)
          } else {
            powerTraces.set(netName, fallback.routedCells ?? new Set())
            if (fallback.traces) powerTraceList.push(...fallback.traces)
            for (const fp of fallback.failedPads ?? []) {
              powerFails++
              powerFailedNets.push({
                netName,
                sourcePin: this.findPinAtCoord(pads[0].center),
                destinationPin: this.findPinAtCoord(fp.center),
                reason: 'Power net could not reach all pads'
              })
            }
            console.error(`  ${netName}: FAILED (${fallback.failedPads?.length ?? 0} unreachable pads)`)
          }
        }
      }

      // Phase 2: Route signal nets through the interior
      console.error('\n  Signal routing...')
      const signalResult = this.routeSignalsWithRipup(signalNets, netPads, powerTraces)

      let signalFails = 0
      const signalFailedNets: FailedNet[] = []
      for (const failure of signalResult.failures) {
        for (const destPad of failure.destPads) {
          signalFails++
          signalFailedNets.push({
            netName: failure.netName,
            sourcePin: this.findPinAtCoord(failure.sourcePad.center),
            destinationPin: this.findPinAtCoord(destPad.center),
            reason: 'No path found for signal trace'
          })
        }
      }

      const totalFails = powerFails + signalFails
      console.error(`  Total failures: ${totalFails} (power: ${powerFails}, signal: ${signalFails})`)

      if (totalFails < bestTotalFails) {
        bestTotalFails = totalFails
        bestTraces = [...powerTraceList]
        for (const [_n, t] of signalResult.tracesByNet) bestTraces.push(...t)
        bestFailed = [...powerFailedNets, ...signalFailedNets]
      }

      if (totalFails === 0) {
        console.error('  Full routing succeeded!')
        break
      }
    }

    this.traces = bestTraces
    this.failedNets = bestFailed
  }

  private buildNetPadsMap(): Map<string, Pad[]> {
    const netPads = new Map<string, Pad[]>()
    for (const pad of this.pads.values()) {
      if (pad.net === 'NC') continue
      if (!netPads.has(pad.net)) {
        netPads.set(pad.net, [])
      }
      netPads.get(pad.net)!.push(pad)
    }
    return netPads
  }

  // ── Perimeter power routing ────────────────────────────────────

  /**
   * Route a power net (GND or VCC) along the board perimeter.
   *
   * Strategy: use A* with a cost function that strongly prefers cells
   * near the board edge.  Paths naturally follow the perimeter, keeping
   * the interior free for signal traces.  Each pad connects to the
   * existing routed tree via the cheapest (= most-peripheral) path.
   */
  private routePowerNetPerimeter(
    netName: string,
    pads: Pad[],
    existingTraces: Map<string, Set<string>>
  ): {
    success: boolean
    routedCells?: Set<string>
    traces?: Trace[]
    failedPads?: Pad[]
  } {
    const routedCells = new Set<string>()
    const traces: Trace[] = []
    const perimeterCost = this.buildPerimeterCostFn()

    const netPadCoords = new Set<string>()
    for (const pad of pads) {
      netPadCoords.add(`${pad.center.x},${pad.center.y}`)
    }

    // Start from the pad closest to the board edge
    let startIdx = 0
    let minEdgeDist = Infinity
    for (let i = 0; i < pads.length; i++) {
      const d = this.grid.distToEdge(pads[i].center.x, pads[i].center.y)
      if (d < minEdgeDist) {
        minEdgeDist = d
        startIdx = i
      }
    }

    const connected = new Set<number>()
    connected.add(startIdx)
    routedCells.add(`${pads[startIdx].center.x},${pads[startIdx].center.y}`)

    while (connected.size < pads.length) {
      let bestPath: GridCoordinate[] | null = null
      let bestPadIdx = -1
      let bestLength = Infinity

      for (let i = 0; i < pads.length; i++) {
        if (connected.has(i)) continue

        // Free own routed cells so the pathfinder can reach the tree
        for (const cellKey of routedCells) {
          const [x, y] = cellKey.split(',').map(Number)
          this.grid.freeCell(x, y)
        }
        this.blockUnrelatedPads(netPadCoords)
        this.blockUnrelatedTraces(netName, existingTraces, netPadCoords)
        this.freeApproachZones(pads, netPadCoords)

        const pathfinder = new Pathfinder(this.grid)
        const path = pathfinder.findPathToTree(pads[i].center, routedCells, perimeterCost)

        this.unblockAllPads()
        this.unblockTraceExclusions(netName, existingTraces)

        // Re-block own routed cells
        for (const cellKey of routedCells) {
          const [x, y] = cellKey.split(',').map(Number)
          this.grid.blockCell(x, y)
        }

        if (path && path.length < bestLength) {
          bestPath = path
          bestPadIdx = i
          bestLength = path.length
        }
      }

      if (!bestPath || bestPadIdx === -1) {
        const failedPads = pads.filter((_, i) => !connected.has(i))
        return { success: false, routedCells, traces, failedPads }
      }

      console.error(`    ${netName}: pad ${bestPadIdx} connected (len=${bestPath.length})`)
      connected.add(bestPadIdx)
      for (const cell of bestPath) {
        routedCells.add(`${cell.x},${cell.y}`)
        this.grid.blockCell(cell.x, cell.y)
      }
      traces.push({ net: netName, path: bestPath })
    }

    return { success: true, routedCells, traces }
  }

  /**
   * Build a cost function that penalises cells proportionally to
   * their distance from the board edge.  Cells on the perimeter ring
   * cost 0; cells in the deep interior cost up to maxPenalty extra.
   *
   * This biases A* to route power traces along the perimeter without
   * hard-blocking the interior (stubs can still cut across).
   */
  private buildPerimeterCostFn(): CellCostFn {
    const maxPenaltyDist = 12  // cells beyond this all get max penalty
    const maxPenalty = 8.0     // strong bias to keep power on the edge
    const cache = new Map<string, number>()

    return (x: number, y: number): number => {
      const key = `${x},${y}`
      let d = cache.get(key)
      if (d === undefined) {
        d = this.quickEdgeDist(x, y, maxPenaltyDist)
        cache.set(key, d)
      }
      if (d <= 1) return 0
      return Math.min(d / maxPenaltyDist, 1.0) * maxPenalty
    }
  }

  /**
   * Fast approximate distance from (x,y) to the nearest permanently-
   * blocked cell, capped at maxDist.  Scans expanding diamonds.
   */
  private quickEdgeDist(x: number, y: number, maxDist: number): number {
    for (let d = 1; d <= maxDist; d++) {
      for (let dx = -d; dx <= d; dx++) {
        const dy = d - Math.abs(dx)
        for (const sy of [dy, -dy]) {
          const nx = x + dx, ny = y + sy
          if (!this.grid.isInBounds(nx, ny) || this.grid.isPermanentlyBlocked(nx, ny)) {
            return d
          }
          if (sy === 0) break  // avoid checking (dx, 0) twice
        }
      }
    }
    return maxDist
  }

  // ── Direct A* routing (fallback for power, primary for signals) ─

  /**
   * Route a net by connecting pads via A* spanning tree (closest first).
   * No perimeter bias — routes through the interior freely.
   */
  private routeNetDirect(
    netName: string,
    pads: Pad[],
    existingTraces: Map<string, Set<string>>
  ): {
    success: boolean
    routedCells?: Set<string>
    traces?: Trace[]
    failedPads?: Pad[]
  } {
    const routedCells = new Set<string>()
    const traces: Trace[] = []
    const netPadCoords = new Set<string>()
    for (const pad of pads) {
      netPadCoords.add(`${pad.center.x},${pad.center.y}`)
    }

    const connected = new Set<number>()
    connected.add(0)
    routedCells.add(`${pads[0].center.x},${pads[0].center.y}`)

    while (connected.size < pads.length) {
      let bestPath: GridCoordinate[] | null = null
      let bestPadIdx = -1
      let bestLength = Infinity

      for (let i = 0; i < pads.length; i++) {
        if (connected.has(i)) continue

        for (const cellKey of routedCells) {
          const [x, y] = cellKey.split(',').map(Number)
          this.grid.freeCell(x, y)
        }
        this.blockUnrelatedPads(netPadCoords)
        this.blockUnrelatedTraces(netName, existingTraces, netPadCoords)
        this.freeApproachZones(pads, netPadCoords)

        const pathfinder = new Pathfinder(this.grid)
        const path = pathfinder.findPathToTree(pads[i].center, routedCells)

        this.unblockAllPads()
        this.unblockTraceExclusions(netName, existingTraces)

        for (const cellKey of routedCells) {
          const [x, y] = cellKey.split(',').map(Number)
          this.grid.blockCell(x, y)
        }

        if (path && path.length < bestLength) {
          bestPath = path
          bestPadIdx = i
          bestLength = path.length
        }
      }

      if (!bestPath || bestPadIdx === -1) {
        const failedPads = pads.filter((_, i) => !connected.has(i))
        return { success: false, routedCells, traces, failedPads }
      }

      console.error(`    ${netName}: pad ${bestPadIdx} connected (len=${bestPath.length})`)
      connected.add(bestPadIdx)
      for (const cell of bestPath) {
        routedCells.add(`${cell.x},${cell.y}`)
        this.grid.blockCell(cell.x, cell.y)
      }
      traces.push({ net: netName, path: bestPath })
    }

    return { success: true, routedCells, traces }
  }

  // ── Signal routing with rip-up ─────────────────────────────────

  /**
   * Route all signal nets with simple rip-up: try multiple orderings.
   * Power traces are already placed and treated as fixed obstacles.
   */
  private routeSignalsWithRipup(
    signalNets: string[],
    netPads: Map<string, Pad[]>,
    powerTraces: Map<string, Set<string>>
  ): {
    tracesByNet: Map<string, Trace[]>
    failures: { netName: string; sourcePad: Pad; destPads: Pad[] }[]
  } {
    const orderings = this.generateSignalOrderings(signalNets)

    let bestResult: {
      tracesByNet: Map<string, Trace[]>
      failures: { netName: string; sourcePad: Pad; destPads: Pad[] }[]
    } | null = null
    let bestFailCount = Infinity

    const budget = Math.min(this.maxRipupAttempts, orderings.length)

    for (let attempt = 0; attempt < budget; attempt++) {
      const order = orderings[attempt]
      console.error(`\n  Signal attempt ${attempt + 1}/${budget}: ${order.join(' → ')}`)

      this.resetGridWithPower(powerTraces)

      const completedTraces = new Map<string, Set<string>>()
      // Include power traces so signal routing respects them
      for (const [name, cells] of powerTraces) {
        completedTraces.set(name, cells)
      }
      const tracesByNet = new Map<string, Trace[]>()
      const failures: { netName: string; sourcePad: Pad; destPads: Pad[] }[] = []

      for (const netName of order) {
        const pads = netPads.get(netName)
        if (!pads || pads.length < 2) continue

        const result = this.routeNetDirect(netName, pads, completedTraces)
        if (result.success) {
          completedTraces.set(netName, result.routedCells!)
          tracesByNet.set(netName, result.traces!)
        } else {
          completedTraces.set(netName, result.routedCells ?? new Set())
          tracesByNet.set(netName, result.traces ?? [])
          failures.push({
            netName,
            sourcePad: pads[0],
            destPads: result.failedPads!
          })
        }
      }

      const failStr = failures.length > 0 ? ` (${failures.map(f => f.netName).join(', ')})` : ''
      console.error(`  Failures: ${failures.length}${failStr}`)

      if (failures.length === 0) {
        console.error('  Signal routing succeeded!')
        return { tracesByNet, failures }
      }

      if (failures.length < bestFailCount) {
        bestFailCount = failures.length
        bestResult = { tracesByNet, failures }
      }
    }

    // Retry with relaxed clearance
    if (this.relaxedTraceBlockPadding < this.normalTraceBlockPadding) {
      console.error('\n  --- Relaxing trace clearance ---')
      this.traceBlockPadding = this.relaxedTraceBlockPadding

      for (let attempt = 0; attempt < budget; attempt++) {
        const order = orderings[attempt % orderings.length]
        console.error(`\n  Relaxed attempt ${attempt + 1}: ${order.join(' → ')}`)

        this.resetGridWithPower(powerTraces)

        const completedTraces = new Map<string, Set<string>>()
        for (const [name, cells] of powerTraces) {
          completedTraces.set(name, cells)
        }
        const tracesByNet = new Map<string, Trace[]>()
        const failures: { netName: string; sourcePad: Pad; destPads: Pad[] }[] = []

        for (const netName of order) {
          const pads = netPads.get(netName)
          if (!pads || pads.length < 2) continue

          const result = this.routeNetDirect(netName, pads, completedTraces)
          if (result.success) {
            completedTraces.set(netName, result.routedCells!)
            tracesByNet.set(netName, result.traces!)
          } else {
            completedTraces.set(netName, result.routedCells ?? new Set())
            tracesByNet.set(netName, result.traces ?? [])
            failures.push({
              netName,
              sourcePad: pads[0],
              destPads: result.failedPads!
            })
          }
        }

        console.error(`  Failures: ${failures.length}`)

        if (failures.length === 0) {
          console.error('  Relaxed signal routing succeeded!')
          this.traceBlockPadding = this.normalTraceBlockPadding
          return { tracesByNet, failures }
        }

        if (failures.length < bestFailCount) {
          bestFailCount = failures.length
          bestResult = { tracesByNet, failures }
        }
      }

      this.traceBlockPadding = this.normalTraceBlockPadding
    }

    return bestResult ?? {
      tracesByNet: new Map(),
      failures: signalNets.map(n => ({
        netName: n,
        sourcePad: netPads.get(n)![0],
        destPads: netPads.get(n)!.slice(1)
      }))
    }
  }

  private generateSignalOrderings(signalNets: string[]): string[][] {
    const result: string[][] = []
    result.push([...signalNets])
    result.push([...signalNets].reverse())
    const sorted = [...signalNets].sort()
    result.push(sorted)
    result.push([...sorted].reverse())

    // Rotate each signal to front
    for (let i = 1; i < signalNets.length; i++) {
      result.push([...signalNets.slice(i), ...signalNets.slice(0, i)])
    }

    // De-duplicate
    const seen = new Set<string>()
    return result.filter(order => {
      const key = order.join(',')
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  }

  private resetGrid(): void {
    this.grid = new Grid(this.input.board, this.input.manufacturing)
    // Re-block component bodies that were established during initializeComponents
    for (const body of this.componentBodies) {
      this.grid.blockRectangularBody(
        body.x, body.y, body.width / 2, body.height / 2, this.bodyKeepoutCells
      )
    }
  }

  /**
   * Reset the grid and re-block power traces as permanent obstacles.
   * Used before each signal routing attempt — power traces are fixed.
   */
  private resetGridWithPower(powerTraces: Map<string, Set<string>>): void {
    this.resetGrid()
    const padding = this.traceBlockPadding
    for (const [_netName, cells] of powerTraces) {
      for (const cellKey of cells) {
        const [x, y] = cellKey.split(',').map(Number)
        for (let dy = -padding; dy <= padding; dy++) {
          for (let dx = -padding; dx <= padding; dx++) {
            this.grid.blockCell(x + dx, y + dy)
          }
        }
      }
    }
  }

  /**
   * Free the approach zone around each pad in the current net so the
   * pathfinder can reach pads even when adjacent blocking zones would
   * otherwise isolate them.  Cells that fall inside the keep-out radius
   * of an unrelated pad are NOT freed so traces never hug foreign pins.
   */
  private freeApproachZones(pads: Pad[], netPadCoords: Set<string>): void {
    const padApproach = this.tracePadding
    const keepout = this.traceBlockPadding

    // Collect centres of unrelated pads for fast proximity checks
    const unrelatedCentres: GridCoordinate[] = []
    for (const pad of this.pads.values()) {
      const key = `${pad.center.x},${pad.center.y}`
      if (!netPadCoords.has(key)) {
        unrelatedCentres.push(pad.center)
      }
    }

    for (const pad of pads) {
      for (let dy = -padApproach; dy <= padApproach; dy++) {
        for (let dx = -padApproach; dx <= padApproach; dx++) {
          const nx = pad.center.x + dx
          const ny = pad.center.y + dy
          // Skip if this cell is inside the keep-out zone of any unrelated pad
          let tooClose = false
          for (const uc of unrelatedCentres) {
            if (Math.abs(nx - uc.x) <= keepout && Math.abs(ny - uc.y) <= keepout) {
              tooClose = true
              break
            }
          }
          if (!tooClose) {
            this.grid.freeCell(nx, ny)
          }
        }
      }
    }
  }

  private blockUnrelatedPads(allowedPadCoords: Set<string>): void {
    const blockedCells = new Set<string>()
    // Every pin (including NC) gets the full trace-block keep-out so
    // that traces never route too close to any physical pin hole.
    const padding = this.traceBlockPadding
    
    for (const pad of this.pads.values()) {
      const key = `${pad.center.x},${pad.center.y}`
      if (!allowedPadCoords.has(key)) {
        for (let dy = -padding; dy <= padding; dy++) {
          for (let dx = -padding; dx <= padding; dx++) {
            const nx = pad.center.x + dx
            const ny = pad.center.y + dy
            const cellKey = `${nx},${ny}`
            if (!allowedPadCoords.has(cellKey) && !blockedCells.has(cellKey)) {
              this.grid.blockCell(nx, ny)
              blockedCells.add(cellKey)
            }
          }
        }
      }
    }
  }

  private unblockAllPads(): void {
    // Must use the LARGER of tracePadding and traceBlockPadding to ensure
    // every cell that blockUnrelatedPads might have blocked gets freed.
    const padding = Math.max(this.tracePadding, this.traceBlockPadding)
    for (const pad of this.pads.values()) {
      for (let dy = -padding; dy <= padding; dy++) {
        for (let dx = -padding; dx <= padding; dx++) {
          this.grid.freeCell(pad.center.x + dx, pad.center.y + dy)
        }
      }
    }
  }

  private blockUnrelatedTraces(
    currentNet: string,
    completedTraces: Map<string, Set<string>>,
    allowedCoords: Set<string>
  ): void {
    const padding = this.traceBlockPadding
    for (const [netName, traceCells] of completedTraces) {
      if (netName === currentNet) continue
      
      for (const cellKey of traceCells) {
        const [x, y] = cellKey.split(',').map(Number)
        for (let dy = -padding; dy <= padding; dy++) {
          for (let dx = -padding; dx <= padding; dx++) {
            const nx = x + dx
            const ny = y + dy
            const neighborKey = `${nx},${ny}`
            if (!allowedCoords.has(neighborKey)) {
              this.grid.blockCell(nx, ny)
            }
          }
        }
      }
    }
  }

  private unblockTraceExclusions(
    currentNet: string,
    completedTraces: Map<string, Set<string>>
  ): void {
    const padding = this.traceBlockPadding
    for (const [netName, traceCells] of completedTraces) {
      if (netName === currentNet) continue
      
      for (const cellKey of traceCells) {
        const [x, y] = cellKey.split(',').map(Number)
        for (let dy = -padding; dy <= padding; dy++) {
          for (let dx = -padding; dx <= padding; dx++) {
            if (dx === 0 && dy === 0) continue
            this.grid.freeCell(x + dx, y + dy)
          }
        }
      }
    }
  }

  private findPinAtCoord(coord: GridCoordinate): string {
    for (const [key, pad] of this.pads) {
      if (pad.center.x === coord.x && pad.center.y === coord.y) {
        return key
      }
    }
    return `(${coord.x}, ${coord.y})`
  }

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

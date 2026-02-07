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
  Footprints
} from './types'
import { Grid } from './grid'
import { Pathfinder } from './pathfinder'

export class Router {
  private readonly input: RouterInput
  private readonly tracePadding: number
  private readonly footprints: Footprints
  private grid: Grid
  private pads: Map<string, Pad>
  private nets: Net[]
  private traces: Trace[]
  private failedNets: FailedNet[]
  private readonly maxRipupAttempts = 15

  constructor(input: RouterInput) {
    this.input = input
    this.footprints = input.footprints
    const clearanceCells = Math.ceil(input.manufacturing.traceClearance / input.board.gridResolution)
    this.tracePadding = clearanceCells
    this.grid = new Grid(input.board, input.manufacturing)
    this.pads = new Map()
    this.nets = []
    this.traces = []
    this.failedNets = []
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
    const halfSpacing = this.footprints.battery.padSpacing / 2

    const vccPadCenter = this.grid.worldToGrid(battery.x, battery.y - halfSpacing)
    this.pads.set(`${battery.id}.VCC`, {
      componentId: battery.id,
      pinName: 'VCC',
      center: vccPadCenter,
      net: 'VCC',
      componentCenter: batteryCenter
    })

    const gndPadCenter = this.grid.worldToGrid(battery.x, battery.y + halfSpacing)
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

    pinNames.forEach((pinName, index) => {
      const pinNumber = index + 1
      let pinX: number
      let pinY: number

      if (pinNumber <= pinsPerSide) {
        pinX = controller.x - rowSpacing / 2
        pinY = controller.y - totalHeight / 2 + (pinNumber - 1) * pinSpacing
      } else {
        pinX = controller.x + rowSpacing / 2
        const rightSideIndex = pinCount - pinNumber
        pinY = controller.y - totalHeight / 2 + rightSideIndex * pinSpacing
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

  private routeNets(): void {
    const netPads = this.buildNetPadsMap()
    const netOrder = this.getInitialNetOrder(netPads)
    
    const result = this.attemptRouteWithOrder(netPads, netOrder)
    
    if (result.failures.length > 0) {
      console.error(`\nInitial routing failed for ${result.failures.length} nets. Attempting rip-up and reroute...`)
      this.ripUpAndReroute(netPads, netOrder, result)
    } else {
      this.applyRoutingResult(result)
    }
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

  private getInitialNetOrder(netPads: Map<string, Pad[]>): string[] {
    const netOrder: string[] = []
    for (const [netName, pads] of netPads) {
      if (pads.length >= 2) {
        netOrder.push(netName)
      }
    }
    netOrder.sort((a, b) => {
      const typeA = this.getNetType(a)
      const typeB = this.getNetType(b)
      const priority: Record<string, number> = { SIGNAL: 0, GND: 1, VCC: 2 }
      return priority[typeA] - priority[typeB]
    })
    return netOrder
  }

  private attemptRouteWithOrder(
    netPads: Map<string, Pad[]>,
    netOrder: string[]
  ): { 
    completedTraces: Map<string, Set<string>>
    tracesByNet: Map<string, Trace[]>
    failures: { netName: string; sourcePad: Pad; destPads: Pad[] }[]
  } {
    this.resetGrid()
    
    const completedTraces = new Map<string, Set<string>>()
    const tracesByNet = new Map<string, Trace[]>()
    const failures: { netName: string; sourcePad: Pad; destPads: Pad[] }[] = []

    for (const netName of netOrder) {
      const pads = netPads.get(netName)!
      const result = this.routeSingleNet(netName, pads, completedTraces)
      
      if (result.success) {
        completedTraces.set(netName, result.routedCells!)
        tracesByNet.set(netName, result.traces!)
      } else {
        failures.push({
          netName,
          sourcePad: pads[0],
          destPads: result.failedPads!
        })
      }
    }

    return { completedTraces, tracesByNet, failures }
  }

  private routeSingleNet(
    netName: string,
    pads: Pad[],
    completedTraces: Map<string, Set<string>>
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

    const connectedPads = new Set<number>()
    connectedPads.add(0)
    routedCells.add(`${pads[0].center.x},${pads[0].center.y}`)

    while (connectedPads.size < pads.length) {
      let bestPath: GridCoordinate[] | null = null
      let bestPadIdx = -1
      let bestLength = Infinity

      for (let i = 0; i < pads.length; i++) {
        if (connectedPads.has(i)) continue

        const targetPad = pads[i].center

        if (routedCells.size > 0) {
          for (const cellKey of routedCells) {
            const [x, y] = cellKey.split(',').map(Number)
            this.grid.freeCell(x, y)
          }
        }

        this.blockUnrelatedPads(netPadCoords)
        this.blockUnrelatedTraces(netName, completedTraces, netPadCoords)

        const pathfinder = new Pathfinder(this.grid)
        const path = pathfinder.findPathToTree(targetPad, routedCells)

        this.unblockAllPads()
        this.unblockTraceExclusions(netName, completedTraces)

        if (routedCells.size > 0) {
          for (const cellKey of routedCells) {
            const [x, y] = cellKey.split(',').map(Number)
            this.grid.blockCell(x, y)
          }
        }

        if (path && path.length < bestLength) {
          bestPath = path
          bestPadIdx = i
          bestLength = path.length
        }
      }

      if (bestPath === null || bestPadIdx === -1) {
        const failedPads = pads.filter((_, i) => !connectedPads.has(i))
        return { success: false, routedCells, traces, failedPads }
      }

      console.error(`OK: ${netName} to pad ${bestPadIdx}, length=${bestPath.length}`)

      connectedPads.add(bestPadIdx)
      for (const cell of bestPath) {
        routedCells.add(`${cell.x},${cell.y}`)
      }

      traces.push({ net: netName, path: bestPath })

      for (const cell of bestPath) {
        this.grid.blockCell(cell.x, cell.y)
      }
    }

    return { success: true, routedCells, traces }
  }

  private ripUpAndReroute(
    netPads: Map<string, Pad[]>,
    originalOrder: string[],
    initialResult: {
      completedTraces: Map<string, Set<string>>
      tracesByNet: Map<string, Trace[]>
      failures: { netName: string; sourcePad: Pad; destPads: Pad[] }[]
    }
  ): void {
    const failedNetNames = initialResult.failures.map(f => f.netName)
    let bestResult = initialResult
    
    const orderStrategies = this.generateOrderStrategies(originalOrder, failedNetNames)
    
    for (let attempt = 0; attempt < Math.min(this.maxRipupAttempts, orderStrategies.length); attempt++) {
      console.error(`\nRip-up attempt ${attempt + 1}/${this.maxRipupAttempts}`)
      
      const newOrder = orderStrategies[attempt]
      console.error(`Trying order: ${newOrder.join(' → ')}`)
      
      const newResult = this.attemptRouteWithOrder(netPads, newOrder)
      
      if (newResult.failures.length === 0) {
        console.error(`Rip-up and reroute succeeded with order: ${newOrder.join(' → ')}`)
        this.applyRoutingResult(newResult)
        return
      }
      
      console.error(`Failures: ${newResult.failures.length} (${newResult.failures.map(f => f.netName).join(', ')})`)
      
      if (newResult.failures.length < bestResult.failures.length) {
        console.error(`Progress: reduced failures from ${bestResult.failures.length} to ${newResult.failures.length}`)
        bestResult = newResult
      }
    }
    
    this.applyRoutingResult(bestResult)
    
    for (const failure of bestResult.failures) {
      for (const destPad of failure.destPads) {
        this.failedNets.push({
          netName: failure.netName,
          sourcePin: this.findPinAtCoord(failure.sourcePad.center),
          destinationPin: this.findPinAtCoord(destPad.center),
          reason: 'No valid path found after rip-up attempts'
        })
      }
    }
  }

  private generateOrderStrategies(originalOrder: string[], failedNets: string[]): string[][] {
    const strategies: string[][] = []
    const failedSet = new Set(failedNets)
    const otherNets = originalOrder.filter(n => !failedSet.has(n))
    
    strategies.push([...failedNets, ...otherNets])
    
    const gnd = originalOrder.filter(n => n === 'GND')
    const vcc = originalOrder.filter(n => n === 'VCC')
    const signals = originalOrder.filter(n => n !== 'GND' && n !== 'VCC')
    
    strategies.push([...gnd, ...vcc, ...signals])
    strategies.push([...vcc, ...gnd, ...signals])
    strategies.push([...signals, ...vcc, ...gnd])
    strategies.push([...signals, ...gnd, ...vcc])
    
    const interleaved1: string[] = []
    for (let i = 0; i < Math.max(signals.length, 2); i++) {
      if (i < signals.length) interleaved1.push(signals[i])
      if (i === 0 && vcc.length > 0) interleaved1.push(vcc[0])
      if (i === 1 && gnd.length > 0) interleaved1.push(gnd[0])
    }
    strategies.push(interleaved1.filter(n => n !== undefined))
    
    const interleaved2: string[] = []
    for (let i = 0; i < Math.max(signals.length, 2); i++) {
      if (i === 0 && gnd.length > 0) interleaved2.push(gnd[0])
      if (i < signals.length) interleaved2.push(signals[i])
      if (i === signals.length - 1 && vcc.length > 0) interleaved2.push(vcc[0])
    }
    strategies.push(interleaved2.filter(n => n !== undefined))
    
    if (signals.length > 1) {
      strategies.push([...signals.slice().reverse(), ...gnd, ...vcc])
      strategies.push([...vcc, ...signals.slice().reverse(), ...gnd])
    }
    
    if (signals.length >= 2) {
      const half = Math.ceil(signals.length / 2)
      const firstHalf = signals.slice(0, half)
      const secondHalf = signals.slice(half)
      strategies.push([...firstHalf, ...gnd, ...secondHalf, ...vcc])
      strategies.push([...firstHalf, ...vcc, ...secondHalf, ...gnd])
      strategies.push([...gnd, ...firstHalf, ...vcc, ...secondHalf])
    }
    
    strategies.push([...signals.slice().reverse(), ...vcc, ...gnd])
    
    return strategies
  }

  private applyRoutingResult(result: {
    completedTraces: Map<string, Set<string>>
    tracesByNet: Map<string, Trace[]>
    failures: { netName: string; sourcePad: Pad; destPads: Pad[] }[]
  }): void {
    this.traces = []
    for (const [_, netTraces] of result.tracesByNet) {
      this.traces.push(...netTraces)
    }
  }

  private resetGrid(): void {
    this.grid = new Grid(this.input.board, this.input.manufacturing)
  }

  private blockUnrelatedPads(allowedPadCoords: Set<string>): void {
    const blockedCells = new Set<string>()
    const padding = this.tracePadding
    
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
    const padding = this.tracePadding
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
    const padding = this.tracePadding
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
    const padding = this.tracePadding
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
}

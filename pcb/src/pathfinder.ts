import { GridCoordinate } from './types'
import { Grid } from './grid'

/**
 * Optional per-cell cost callback.
 * Returned value is added to the A* g-score for each expanded cell,
 * allowing the caller to bias routing toward specific board regions
 * (e.g. edges for power nets) without hard-blocking.
 */
export type CellCostFn = (x: number, y: number) => number

// ── Binary min-heap for A* open set ──────────────────────────────

class MinHeap {
  private heap: AStarNode[] = []

  get size(): number { return this.heap.length }

  push(node: AStarNode): void {
    this.heap.push(node)
    this.bubbleUp(this.heap.length - 1)
  }

  pop(): AStarNode | undefined {
    const heap = this.heap
    if (heap.length === 0) return undefined
    const top = heap[0]
    const last = heap.pop()!
    if (heap.length > 0) {
      heap[0] = last
      this.sinkDown(0)
    }
    return top
  }

  private bubbleUp(i: number): void {
    const heap = this.heap
    while (i > 0) {
      const parent = (i - 1) >> 1
      if (heap[i].f >= heap[parent].f) break
      ;[heap[i], heap[parent]] = [heap[parent], heap[i]]
      i = parent
    }
  }

  private sinkDown(i: number): void {
    const heap = this.heap
    const n = heap.length
    while (true) {
      let smallest = i
      const l = 2 * i + 1
      const r = 2 * i + 2
      if (l < n && heap[l].f < heap[smallest].f) smallest = l
      if (r < n && heap[r].f < heap[smallest].f) smallest = r
      if (smallest === i) break
      ;[heap[i], heap[smallest]] = [heap[smallest], heap[i]]
      i = smallest
    }
  }
}

// ── Directions (shared constant) ─────────────────────────────────

const DIRS: readonly GridCoordinate[] = [
  { x: 1, y: 0 },
  { x: -1, y: 0 },
  { x: 0, y: 1 },
  { x: 0, y: -1 }
]

export class Pathfinder {
  private readonly grid: Grid

  constructor(grid: Grid) {
    this.grid = grid
  }

  findPath(
    source: GridCoordinate,
    sink: GridCoordinate,
    _preferOutward: 'above' | 'below' | 'none' = 'none'
  ): GridCoordinate[] | null {
    if (!this.grid.isInBounds(source.x, source.y) ||
        !this.grid.isInBounds(sink.x, sink.y)) {
      return null
    }

    if (source.x === sink.x && source.y === sink.y) {
      return [source]
    }

    const path = this.tryLShapedRoute(source, sink)
    if (path) return path

    return this.aStarFallback(source, sink)
  }

  private tryLShapedRoute(source: GridCoordinate, sink: GridCoordinate): GridCoordinate[] | null {
    const horizontalFirst = this.tryHorizontalThenVertical(source, sink)
    if (horizontalFirst) return horizontalFirst

    const verticalFirst = this.tryVerticalThenHorizontal(source, sink)
    if (verticalFirst) return verticalFirst

    return null
  }

  private tryHorizontalThenVertical(source: GridCoordinate, sink: GridCoordinate): GridCoordinate[] | null {
    const path: GridCoordinate[] = []
    const dx = sink.x > source.x ? 1 : -1
    const dy = sink.y > source.y ? 1 : -1

    let x = source.x
    let y = source.y
    path.push({ x, y })

    while (x !== sink.x) {
      x += dx
      if (!this.grid.isFree(x, y)) return null
      path.push({ x, y })
    }

    while (y !== sink.y) {
      y += dy
      if (!this.grid.isFree(x, y)) return null
      path.push({ x, y })
    }

    return path
  }

  private tryVerticalThenHorizontal(source: GridCoordinate, sink: GridCoordinate): GridCoordinate[] | null {
    const path: GridCoordinate[] = []
    const dx = sink.x > source.x ? 1 : -1
    const dy = sink.y > source.y ? 1 : -1

    let x = source.x
    let y = source.y
    path.push({ x, y })

    while (y !== sink.y) {
      y += dy
      if (!this.grid.isFree(x, y)) return null
      path.push({ x, y })
    }

    while (x !== sink.x) {
      x += dx
      if (!this.grid.isFree(x, y)) return null
      path.push({ x, y })
    }

    return path
  }

  private aStarFallback(source: GridCoordinate, sink: GridCoordinate): GridCoordinate[] | null {
    const openSet = new MinHeap()
    const closedSet = new Set<number>()
    const gScores = new Map<number, number>()
    const W = this.grid.gridWidth

    const encode = (x: number, y: number): number => y * W + x

    const startNode: AStarNode = {
      x: source.x,
      y: source.y,
      g: 0,
      h: Math.abs(source.x - sink.x) + Math.abs(source.y - sink.y),
      f: Math.abs(source.x - sink.x) + Math.abs(source.y - sink.y),
      parent: null,
      direction: -1
    }

    openSet.push(startNode)
    gScores.set(encode(source.x, source.y), 0)

    while (openSet.size > 0) {
      const current = openSet.pop()!
      const currentKey = encode(current.x, current.y)

      if (current.x === sink.x && current.y === sink.y) {
        return this.reconstructPath(current)
      }

      if (closedSet.has(currentKey)) continue
      closedSet.add(currentKey)

      for (let d = 0; d < 4; d++) {
        const nx = current.x + DIRS[d].x
        const ny = current.y + DIRS[d].y

        if (!this.grid.isInBounds(nx, ny)) continue
        const neighborKey = encode(nx, ny)
        if (closedSet.has(neighborKey)) continue
        if (!this.grid.isFree(nx, ny) && !(nx === sink.x && ny === sink.y)) continue

        const isTurn = current.direction !== -1 && current.direction !== d
        const turnPenalty = isTurn ? 10 : 0

        const tentativeG = current.g + 1 + turnPenalty
        const existingG = gScores.get(neighborKey)

        if (existingG === undefined || tentativeG < existingG) {
          const h = Math.abs(nx - sink.x) + Math.abs(ny - sink.y)
          openSet.push({
            x: nx, y: ny, g: tentativeG, h, f: tentativeG + h,
            parent: current, direction: d
          })
          gScores.set(neighborKey, tentativeG)
        }
      }
    }

    return null
  }

  findPathToTree(
    source: GridCoordinate,
    treeSet: Set<string>,
    cellCost?: CellCostFn
  ): GridCoordinate[] | null {
    return this.findPathToTreeInternal(source, treeSet, cellCost, false)
  }

  /**
   * Find a path to the tree that is allowed to cross blocked cells
   * (existing traces).  Crossing cells incur a heavy penalty but are
   * not forbidden.  Returns the path AND the set of cell keys that
   * were blocked (i.e. cells where it crosses existing traces).
   */
  findPathMinCrossings(
    source: GridCoordinate,
    treeSet: Set<string>,
    cellCost?: CellCostFn
  ): { path: GridCoordinate[]; crossedCells: Set<string> } | null {
    const result = this.findPathToTreeInternal(source, treeSet, cellCost, true)
    if (!result) return null

    // Identify which cells in the path were blocked (= crossings)
    const crossedCells = new Set<string>()
    for (const cell of result) {
      const key = `${cell.x},${cell.y}`
      if (!treeSet.has(key) && !this.grid.isFree(cell.x, cell.y)) {
        crossedCells.add(key)
      }
    }
    return { path: result, crossedCells }
  }

  private findPathToTreeInternal(
    source: GridCoordinate,
    treeSet: Set<string>,
    cellCost: CellCostFn | undefined,
    allowCrossings: boolean
  ): GridCoordinate[] | null {
    const W = this.grid.gridWidth
    const encode = (x: number, y: number): number => y * W + x

    if (treeSet.has(`${source.x},${source.y}`)) {
      return [source]
    }

    // Convert string treeSet to numeric set + coordinate arrays for speed
    const treeNumeric = new Set<number>()
    const treeXs: Int16Array = new Int16Array(treeSet.size)
    const treeYs: Int16Array = new Int16Array(treeSet.size)
    let ti = 0
    for (const key of treeSet) {
      const comma = key.indexOf(',')
      const tx = parseInt(key.substring(0, comma))
      const ty = parseInt(key.substring(comma + 1))
      treeXs[ti] = tx
      treeYs[ti] = ty
      treeNumeric.add(ty * W + tx)
      ti++
    }
    const treeCount = ti

    const minH = (x: number, y: number): number => {
      let best = Infinity
      for (let i = 0; i < treeCount; i++) {
        const d = Math.abs(x - treeXs[i]) + Math.abs(y - treeYs[i])
        if (d < best) best = d
        if (d === 0) return 0
      }
      return best
    }

    const openSet = new MinHeap()
    const closedSet = new Set<number>()
    const gScores = new Map<number, number>()

    const h0 = minH(source.x, source.y)
    const startKey = encode(source.x, source.y)
    openSet.push({
      x: source.x, y: source.y,
      g: 0, h: h0, f: h0,
      parent: null, direction: -1
    })
    gScores.set(startKey, 0)

    while (openSet.size > 0) {
      const current = openSet.pop()!
      const currentKey = encode(current.x, current.y)

      if (treeNumeric.has(currentKey)) {
        return this.reconstructPath(current)
      }

      if (closedSet.has(currentKey)) continue
      closedSet.add(currentKey)

      for (let d = 0; d < 4; d++) {
        const nx = current.x + DIRS[d].x
        const ny = current.y + DIRS[d].y

        if (!this.grid.isInBounds(nx, ny)) continue
        const neighborKey = encode(nx, ny)
        if (closedSet.has(neighborKey)) continue

        const isTreeCell = treeNumeric.has(neighborKey)
        const cellFree = this.grid.isFree(nx, ny)

        if (!cellFree && !isTreeCell) {
          if (!allowCrossings || this.grid.isPermanentlyBlocked(nx, ny)) continue
        }

        const isTurn = current.direction !== -1 && current.direction !== d
        const turnPenalty = isTurn ? 5 : 0
        const extraCost = cellCost ? cellCost(nx, ny) : 0
        const crossingPenalty = (!cellFree && !isTreeCell) ? 500 : 0

        const tentativeG = current.g + 1 + turnPenalty + extraCost + crossingPenalty
        const existingG = gScores.get(neighborKey)

        if (existingG === undefined || tentativeG < existingG) {
          const h = minH(nx, ny)
          openSet.push({
            x: nx, y: ny, g: tentativeG, h, f: tentativeG + h,
            parent: current, direction: d
          })
          gScores.set(neighborKey, tentativeG)
        }
      }
    }

    return null
  }

  private reconstructPath(node: AStarNode): GridCoordinate[] {
    const path: GridCoordinate[] = []
    let current: AStarNode | null = node
    while (current !== null) {
      path.unshift({ x: current.x, y: current.y })
      current = current.parent
    }
    return path
  }
}

interface AStarNode {
  x: number
  y: number
  g: number
  h: number
  f: number
  parent: AStarNode | null
  direction: number  // index into DIRS, or -1 for start
}

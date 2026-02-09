import { GridCoordinate } from './types'
import { Grid } from './grid'

/**
 * Optional per-cell cost callback.
 * Returned value is added to the A* g-score for each expanded cell,
 * allowing the caller to bias routing toward specific board regions
 * (e.g. edges for power nets) without hard-blocking.
 */
export type CellCostFn = (x: number, y: number) => number

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
    const openSet: AStarNode[] = []
    const closedSet = new Set<string>()
    const gScores = new Map<string, number>()

    const startNode: AStarNode = {
      x: source.x,
      y: source.y,
      g: 0,
      h: this.manhattanDistance(source, sink),
      f: this.manhattanDistance(source, sink),
      parent: null,
      direction: null
    }

    openSet.push(startNode)
    gScores.set(this.coordKey(source.x, source.y), 0)

    const directions: GridCoordinate[] = [
      { x: 1, y: 0 },
      { x: -1, y: 0 },
      { x: 0, y: 1 },
      { x: 0, y: -1 }
    ]

    while (openSet.length > 0) {
      openSet.sort((a, b) => a.f - b.f)
      const current = openSet.shift()!
      const currentKey = this.coordKey(current.x, current.y)

      if (current.x === sink.x && current.y === sink.y) {
        return this.reconstructPath(current)
      }

      if (closedSet.has(currentKey)) continue
      closedSet.add(currentKey)

      for (const dir of directions) {
        const nx = current.x + dir.x
        const ny = current.y + dir.y
        const neighborKey = this.coordKey(nx, ny)

        if (!this.grid.isInBounds(nx, ny)) continue
        if (!this.grid.isFree(nx, ny) && !(nx === sink.x && ny === sink.y)) continue
        if (closedSet.has(neighborKey)) continue

        const newDir = `${dir.x},${dir.y}`
        const isTurn = current.direction !== null && current.direction !== newDir
        const turnPenalty = isTurn ? 10 : 0

        const tentativeG = current.g + 1 + turnPenalty
        const existingG = gScores.get(neighborKey)

        if (existingG === undefined || tentativeG < existingG) {
          const h = this.manhattanDistance({ x: nx, y: ny }, sink)
          const neighbor: AStarNode = {
            x: nx,
            y: ny,
            g: tentativeG,
            h,
            f: tentativeG + h,
            parent: current,
            direction: newDir
          }

          gScores.set(neighborKey, tentativeG)
          openSet.push(neighbor)
        }
      }
    }

    return null
  }

  private manhattanDistance(a: GridCoordinate, b: GridCoordinate): number {
    return Math.abs(a.x - b.x) + Math.abs(a.y - b.y)
  }

  private minManhattanToSet(point: GridCoordinate, targets: Set<string>): number {
    let minDist = Infinity
    for (const key of targets) {
      const [tx, ty] = key.split(',').map(Number)
      const dist = Math.abs(point.x - tx) + Math.abs(point.y - ty)
      if (dist < minDist) minDist = dist
    }
    return minDist
  }

  findPathToTree(
    source: GridCoordinate,
    treeSet: Set<string>,
    cellCost?: CellCostFn
  ): GridCoordinate[] | null {
    if (treeSet.has(`${source.x},${source.y}`)) {
      return [source]
    }

    const openSet: AStarNode[] = []
    const closedSet = new Set<string>()
    const gScores = new Map<string, number>()

    const startNode: AStarNode = {
      x: source.x,
      y: source.y,
      g: 0,
      h: this.minManhattanToSet(source, treeSet),
      f: this.minManhattanToSet(source, treeSet),
      parent: null,
      direction: null
    }

    openSet.push(startNode)
    gScores.set(this.coordKey(source.x, source.y), 0)

    const directions: GridCoordinate[] = [
      { x: 1, y: 0 },
      { x: -1, y: 0 },
      { x: 0, y: 1 },
      { x: 0, y: -1 }
    ]

    while (openSet.length > 0) {
      openSet.sort((a, b) => a.f - b.f)
      const current = openSet.shift()!
      const currentKey = this.coordKey(current.x, current.y)

      if (treeSet.has(currentKey)) {
        return this.reconstructPath(current)
      }

      if (closedSet.has(currentKey)) continue
      closedSet.add(currentKey)

      for (const dir of directions) {
        const nx = current.x + dir.x
        const ny = current.y + dir.y
        const neighborKey = this.coordKey(nx, ny)

        if (!this.grid.isInBounds(nx, ny)) continue
        if (closedSet.has(neighborKey)) continue
        
        const isTreeCell = treeSet.has(neighborKey)
        if (!this.grid.isFree(nx, ny) && !isTreeCell) continue

        const newDir = `${dir.x},${dir.y}`
        const isTurn = current.direction !== null && current.direction !== newDir
        const turnPenalty = isTurn ? 5 : 0
        const extraCost = cellCost ? cellCost(nx, ny) : 0

        const tentativeG = current.g + 1 + turnPenalty + extraCost
        const existingG = gScores.get(neighborKey)

        if (existingG === undefined || tentativeG < existingG) {
          const h = this.minManhattanToSet({ x: nx, y: ny }, treeSet)
          const neighbor: AStarNode = {
            x: nx,
            y: ny,
            g: tentativeG,
            h,
            f: tentativeG + h,
            parent: current,
            direction: newDir
          }

          gScores.set(neighborKey, tentativeG)
          openSet.push(neighbor)
        }
      }
    }

    return null
  }

  private coordKey(x: number, y: number): string {
    return `${x},${y}`
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
  direction: string | null
}

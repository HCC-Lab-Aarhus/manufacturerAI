import {
  BoardParameters,
  ManufacturingConstraints,
  CellState,
  GridCoordinate,
  Pad
} from './types'

export class Grid {
  private readonly width: number
  private readonly height: number
  private readonly cells: CellState[][]
  private readonly permanentlyBlocked: boolean[][]
  private readonly gridResolution: number
  private readonly blockedRadius: number

  constructor(
    board: BoardParameters,
    manufacturing: ManufacturingConstraints
  ) {
    this.gridResolution = board.gridResolution
    this.width = Math.ceil(board.boardWidth / board.gridResolution)
    this.height = Math.ceil(board.boardHeight / board.gridResolution)
    this.blockedRadius = Math.ceil(
      (manufacturing.traceWidth / 2 + manufacturing.traceClearance) / board.gridResolution
    )

    this.cells = Array.from({ length: this.height }, () =>
      Array.from({ length: this.width }, () => CellState.FREE)
    )
    this.permanentlyBlocked = Array.from({ length: this.height }, () =>
      Array.from({ length: this.width }, () => false)
    )

    if (board.boardOutline && board.boardOutline.length >= 3) {
      this.blockOutsidePolygon(board.boardOutline)
    } else {
      this.blockBoardEdges()
    }
  }

  get gridWidth(): number {
    return this.width
  }

  get gridHeight(): number {
    return this.height
  }

  get resolution(): number {
    return this.gridResolution
  }

  get effectiveBlockedRadius(): number {
    return this.blockedRadius
  }

  /**
   * Block all grid cells whose world-space center falls outside the polygon.
   * Uses ray-casting point-in-polygon test.
   */
  private blockOutsidePolygon(outline: number[][]): void {
    const n = outline.length
    for (let gy = 0; gy < this.height; gy++) {
      for (let gx = 0; gx < this.width; gx++) {
        const wx = (gx + 0.5) * this.gridResolution
        const wy = (gy + 0.5) * this.gridResolution
        if (!this.pointInPolygon(wx, wy, outline, n)) {
          this.cells[gy][gx] = CellState.BLOCKED
          this.permanentlyBlocked[gy][gx] = true
        }
      }
    }
    // Also block cells within blockedRadius of the polygon edges
    for (let gy = 0; gy < this.height; gy++) {
      for (let gx = 0; gx < this.width; gx++) {
        if (this.cells[gy][gx] === CellState.BLOCKED) continue
        const wx = (gx + 0.5) * this.gridResolution
        const wy = (gy + 0.5) * this.gridResolution
        const dist = this.distToPolygonEdge(wx, wy, outline, n)
        if (dist < this.blockedRadius * this.gridResolution) {
          this.cells[gy][gx] = CellState.BLOCKED
          this.permanentlyBlocked[gy][gx] = true
        }
      }
    }
  }

  private pointInPolygon(x: number, y: number, poly: number[][], n: number): boolean {
    let inside = false
    for (let i = 0, j = n - 1; i < n; j = i++) {
      const xi = poly[i][0], yi = poly[i][1]
      const xj = poly[j][0], yj = poly[j][1]
      if (((yi > y) !== (yj > y)) &&
          (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) {
        inside = !inside
      }
    }
    return inside
  }

  private distToPolygonEdge(px: number, py: number, poly: number[][], n: number): number {
    let minDist = Infinity
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n
      const ax = poly[i][0], ay = poly[i][1]
      const bx = poly[j][0], by = poly[j][1]
      const dx = bx - ax, dy = by - ay
      const len2 = dx * dx + dy * dy
      let t = len2 === 0 ? 0 : ((px - ax) * dx + (py - ay) * dy) / len2
      t = Math.max(0, Math.min(1, t))
      const cx = ax + t * dx, cy = ay + t * dy
      const d = Math.sqrt((px - cx) * (px - cx) + (py - cy) * (py - cy))
      if (d < minDist) minDist = d
    }
    return minDist
  }

  private blockBoardEdges(): void {
    for (let x = 0; x < this.width; x++) {
      for (let r = 0; r < this.blockedRadius; r++) {
        if (r < this.height) { this.cells[r][x] = CellState.BLOCKED; this.permanentlyBlocked[r][x] = true }
        if (this.height - 1 - r >= 0) { this.cells[this.height - 1 - r][x] = CellState.BLOCKED; this.permanentlyBlocked[this.height - 1 - r][x] = true }
      }
    }
    for (let y = 0; y < this.height; y++) {
      for (let r = 0; r < this.blockedRadius; r++) {
        if (r < this.width) { this.cells[y][r] = CellState.BLOCKED; this.permanentlyBlocked[y][r] = true }
        if (this.width - 1 - r >= 0) { this.cells[y][this.width - 1 - r] = CellState.BLOCKED; this.permanentlyBlocked[y][this.width - 1 - r] = true }
      }
    }
  }

  worldToGrid(worldX: number, worldY: number): GridCoordinate {
    return {
      x: Math.floor(worldX / this.gridResolution),
      y: Math.floor(worldY / this.gridResolution)
    }
  }

  gridToWorld(gridX: number, gridY: number): { x: number; y: number } {
    return {
      x: (gridX + 0.5) * this.gridResolution,
      y: (gridY + 0.5) * this.gridResolution
    }
  }

  isInBounds(x: number, y: number): boolean {
    return x >= 0 && x < this.width && y >= 0 && y < this.height
  }

  isFree(x: number, y: number): boolean {
    if (!this.isInBounds(x, y)) return false
    return this.cells[y][x] === CellState.FREE
  }

  isBlocked(x: number, y: number): boolean {
    if (!this.isInBounds(x, y)) return true
    return this.cells[y][x] === CellState.BLOCKED
  }

  getState(x: number, y: number): CellState {
    if (!this.isInBounds(x, y)) return CellState.BLOCKED
    return this.cells[y][x]
  }

  blockCell(x: number, y: number): void {
    if (this.isInBounds(x, y)) {
      this.cells[y][x] = CellState.BLOCKED
    }
  }

  freeCell(x: number, y: number): void {
    if (this.isInBounds(x, y) && !this.permanentlyBlocked[y][x]) {
      this.cells[y][x] = CellState.FREE
    }
  }

  blockArea(centerX: number, centerY: number, radius: number): void {
    for (let dy = -radius; dy <= radius; dy++) {
      for (let dx = -radius; dx <= radius; dx++) {
        this.blockCell(centerX + dx, centerY + dy)
      }
    }
  }

  blockCircularArea(centerX: number, centerY: number, radius: number): void {
    for (let dy = -radius; dy <= radius; dy++) {
      for (let dx = -radius; dx <= radius; dx++) {
        if (dx * dx + dy * dy <= radius * radius) {
          this.blockCell(centerX + dx, centerY + dy)
        }
      }
    }
  }

  blockComponentBody(worldX: number, worldY: number, bodyRadius: number): void {
    const center = this.worldToGrid(worldX, worldY)
    const gridRadius = Math.ceil(bodyRadius / this.gridResolution) + this.blockedRadius
    this.blockArea(center.x, center.y, gridRadius)
  }

  blockPad(pad: Pad, padRadius: number): void {
    const gridRadius = Math.ceil(padRadius / this.gridResolution) + this.blockedRadius
    this.blockArea(pad.center.x, pad.center.y, gridRadius)
  }

  blockTrace(path: GridCoordinate[]): void {
    for (const coord of path) {
      this.blockArea(coord.x, coord.y, this.blockedRadius)
    }
  }

  temporarilyFreePads(pads: Pad[]): void {
    for (const pad of pads) {
      this.freeCell(pad.center.x, pad.center.y)
    }
  }

  clone(): Grid {
    const cloned = Object.create(Grid.prototype) as Grid
    Object.assign(cloned, {
      width: this.width,
      height: this.height,
      gridResolution: this.gridResolution,
      blockedRadius: this.blockedRadius,
      cells: this.cells.map(row => [...row]),
      permanentlyBlocked: this.permanentlyBlocked.map(row => [...row])
    })
    return cloned
  }

  getBlockedCells(): GridCoordinate[] {
    const blocked: GridCoordinate[] = []
    for (let y = 0; y < this.height; y++) {
      for (let x = 0; x < this.width; x++) {
        if (this.cells[y][x] === CellState.BLOCKED) {
          blocked.push({ x, y })
        }
      }
    }
    return blocked
  }

  getFreeCells(): GridCoordinate[] {
    const free: GridCoordinate[] = []
    for (let y = 0; y < this.height; y++) {
      for (let x = 0; x < this.width; x++) {
        if (this.cells[y][x] === CellState.FREE) {
          free.push({ x, y })
        }
      }
    }
    return free
  }

  getCellArray(): CellState[][] {
    return this.cells.map(row => [...row])
  }
}

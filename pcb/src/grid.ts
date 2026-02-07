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

    this.blockBoardEdges()
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

  private blockBoardEdges(): void {
    for (let x = 0; x < this.width; x++) {
      for (let r = 0; r < this.blockedRadius; r++) {
        if (r < this.height) this.cells[r][x] = CellState.BLOCKED
        if (this.height - 1 - r >= 0) this.cells[this.height - 1 - r][x] = CellState.BLOCKED
      }
    }
    for (let y = 0; y < this.height; y++) {
      for (let r = 0; r < this.blockedRadius; r++) {
        if (r < this.width) this.cells[y][r] = CellState.BLOCKED
        if (this.width - 1 - r >= 0) this.cells[y][this.width - 1 - r] = CellState.BLOCKED
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
    if (this.isInBounds(x, y)) {
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
      cells: this.cells.map(row => [...row])
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

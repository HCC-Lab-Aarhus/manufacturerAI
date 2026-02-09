import sharp from 'sharp'
import {
  Trace,
  Pad,
  VisualizationOptions,
  CellState,
  GridCoordinate,
  ComponentBody
} from './types'
import { Grid } from './grid'

interface Color {
  r: number
  g: number
  b: number
  a: number
}

const COLORS = {
  background: { r: 40, g: 40, b: 40, a: 255 },
  gridLine: { r: 60, g: 60, b: 60, a: 255 },
  blocked: { r: 100, g: 50, b: 50, a: 255 },
  free: { r: 50, g: 80, b: 50, a: 255 },
  pad: { r: 255, g: 215, b: 0, a: 255 },
  padOutline: { r: 200, g: 170, b: 0, a: 255 },
  text: { r: 255, g: 255, b: 255, a: 255 },
  textShadow: { r: 0, g: 0, b: 0, a: 200 },
  traceColors: [
    { r: 0, g: 255, b: 100, a: 255 },
    { r: 100, g: 200, b: 255, a: 255 },
    { r: 255, g: 100, b: 100, a: 255 },
    { r: 255, g: 200, b: 50, a: 255 },
    { r: 200, g: 100, b: 255, a: 255 },
    { r: 100, g: 255, b: 200, a: 255 }
  ],
  highlight: { r: 255, g: 255, b: 0, a: 255 },
  componentBody: { r: 255, g: 140, b: 0, a: 255 }
} as const

const FONT: Record<string, number[]> = {
  'A': [0b01110, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001],
  'B': [0b11110, 0b10001, 0b10001, 0b11110, 0b10001, 0b10001, 0b11110],
  'C': [0b01110, 0b10001, 0b10000, 0b10000, 0b10000, 0b10001, 0b01110],
  'D': [0b11110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b11110],
  'E': [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b11111],
  'F': [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b10000],
  'G': [0b01110, 0b10001, 0b10000, 0b10111, 0b10001, 0b10001, 0b01110],
  'H': [0b10001, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001],
  'I': [0b01110, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
  'J': [0b00111, 0b00010, 0b00010, 0b00010, 0b00010, 0b10010, 0b01100],
  'K': [0b10001, 0b10010, 0b10100, 0b11000, 0b10100, 0b10010, 0b10001],
  'L': [0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b11111],
  'M': [0b10001, 0b11011, 0b10101, 0b10101, 0b10001, 0b10001, 0b10001],
  'N': [0b10001, 0b10001, 0b11001, 0b10101, 0b10011, 0b10001, 0b10001],
  'O': [0b01110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110],
  'P': [0b11110, 0b10001, 0b10001, 0b11110, 0b10000, 0b10000, 0b10000],
  'Q': [0b01110, 0b10001, 0b10001, 0b10001, 0b10101, 0b10010, 0b01101],
  'R': [0b11110, 0b10001, 0b10001, 0b11110, 0b10100, 0b10010, 0b10001],
  'S': [0b01110, 0b10001, 0b10000, 0b01110, 0b00001, 0b10001, 0b01110],
  'T': [0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100],
  'U': [0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110],
  'V': [0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01010, 0b00100],
  'W': [0b10001, 0b10001, 0b10001, 0b10101, 0b10101, 0b10101, 0b01010],
  'X': [0b10001, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001, 0b10001],
  'Y': [0b10001, 0b10001, 0b01010, 0b00100, 0b00100, 0b00100, 0b00100],
  'Z': [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b10000, 0b11111],
  '0': [0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110],
  '1': [0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
  '2': [0b01110, 0b10001, 0b00001, 0b00110, 0b01000, 0b10000, 0b11111],
  '3': [0b01110, 0b10001, 0b00001, 0b00110, 0b00001, 0b10001, 0b01110],
  '4': [0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010],
  '5': [0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110],
  '6': [0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110],
  '7': [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000],
  '8': [0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110],
  '9': [0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100],
  '.': [0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b01100, 0b01100],
  '_': [0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b11111],
  '-': [0b00000, 0b00000, 0b00000, 0b11111, 0b00000, 0b00000, 0b00000],
  ' ': [0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000],
}

const CHAR_WIDTH = 5
const CHAR_HEIGHT = 7
const CHAR_SPACING = 1

export class Visualizer {
  private readonly grid: Grid
  private readonly cellSize: number
  private readonly width: number
  private readonly height: number

  constructor(grid: Grid, cellSize: number = 8) {
    this.grid = grid
    this.cellSize = cellSize
    this.width = grid.gridWidth * cellSize
    this.height = grid.gridHeight * cellSize
  }

  async renderGrid(options: Partial<VisualizationOptions> = {}): Promise<Buffer> {
    const opts: VisualizationOptions = {
      showGrid: true,
      showBlockedCells: true,
      showTraces: false,
      showPads: false,
      ...options
    }

    const pixels = new Uint8ClampedArray(this.width * this.height * 4)
    this.fillBackground(pixels)

    if (opts.showBlockedCells) {
      this.drawCells(pixels)
    }

    if (opts.showGrid) {
      this.drawGridLines(pixels)
    }

    return this.toBuffer(pixels)
  }

  async renderComplete(
    traces: Trace[],
    pads: Map<string, Pad>,
    options: Partial<VisualizationOptions> = {},
    componentBodies: ComponentBody[] = []
  ): Promise<Buffer> {
    const opts: VisualizationOptions = {
      showGrid: true,
      showBlockedCells: true,
      showTraces: true,
      showPads: true,
      ...options
    }

    const pixels = new Uint8ClampedArray(this.width * this.height * 4)
    this.fillBackground(pixels)

    if (opts.showBlockedCells) {
      this.drawCells(pixels)
    }

    if (opts.showTraces) {
      this.drawTraceLines(pixels, traces, opts.highlightNet)
    }

    if (opts.showPads) {
      this.drawPadCircles(pixels, pads)
    }

    if (componentBodies.length > 0) {
      this.drawComponentBodies(pixels, componentBodies)
    }

    if (opts.showGrid) {
      this.drawGridLines(pixels)
    }

    if (opts.showTraces) {
      this.drawTraceLabels(pixels, traces, opts.highlightNet)
    }

    if (opts.showPads) {
      this.drawPadLabels(pixels, pads)
    }

    return this.toBuffer(pixels)
  }

  private fillBackground(pixels: Uint8ClampedArray): void {
    for (let i = 0; i < pixels.length; i += 4) {
      pixels[i] = COLORS.background.r
      pixels[i + 1] = COLORS.background.g
      pixels[i + 2] = COLORS.background.b
      pixels[i + 3] = COLORS.background.a
    }
  }

  private drawCells(pixels: Uint8ClampedArray): void {
    for (let gy = 0; gy < this.grid.gridHeight; gy++) {
      for (let gx = 0; gx < this.grid.gridWidth; gx++) {
        const state = this.grid.getState(gx, gy)
        const color = state === CellState.BLOCKED ? COLORS.blocked : COLORS.free

        this.fillCell(pixels, gx, gy, color)
      }
    }
  }

  private drawGridLines(pixels: Uint8ClampedArray): void {
    for (let gx = 0; gx <= this.grid.gridWidth; gx++) {
      const px = gx * this.cellSize
      if (px < this.width) {
        for (let py = 0; py < this.height; py++) {
          this.setPixel(pixels, px, py, COLORS.gridLine)
        }
      }
    }

    for (let gy = 0; gy <= this.grid.gridHeight; gy++) {
      const py = gy * this.cellSize
      if (py < this.height) {
        for (let px = 0; px < this.width; px++) {
          this.setPixel(pixels, px, py, COLORS.gridLine)
        }
      }
    }
  }

  private drawTraceLines(
    pixels: Uint8ClampedArray,
    traces: Trace[],
    highlightNet?: string
  ): void {
    const netColorMap = new Map<string, Color>()
    let colorIndex = 0
    for (const trace of traces) {
      if (!netColorMap.has(trace.net)) {
        netColorMap.set(trace.net, COLORS.traceColors[colorIndex % COLORS.traceColors.length])
        colorIndex++
      }
    }

    traces.forEach((trace) => {
      const color = trace.net === highlightNet
        ? COLORS.highlight
        : netColorMap.get(trace.net)!

      for (let i = 0; i < trace.path.length - 1; i++) {
        this.drawLine(pixels, trace.path[i], trace.path[i + 1], color)
      }

      for (const coord of trace.path) {
        this.drawTraceNode(pixels, coord, color)
      }
    })
  }

  private drawTraceLabels(
    pixels: Uint8ClampedArray,
    traces: Trace[],
    highlightNet?: string
  ): void {
    const netTraces = new Map<string, Trace[]>()
    traces.forEach((trace, index) => {
      if (!netTraces.has(trace.net)) {
        netTraces.set(trace.net, [])
      }
      netTraces.get(trace.net)!.push(trace)
    })

    let colorIndex = 0
    for (const [netName, netTraceList] of netTraces) {
      const color = netName === highlightNet
        ? COLORS.highlight
        : COLORS.traceColors[colorIndex % COLORS.traceColors.length]

      const longestTrace = netTraceList.reduce((longest, current) =>
        current.path.length > longest.path.length ? current : longest
      )

      this.drawTraceLabel(pixels, longestTrace, color)
      colorIndex++
    }
  }

  private drawTraceLabel(pixels: Uint8ClampedArray, trace: Trace, color: Color): void {
    if (trace.path.length < 2) return

    let longestSegmentStart = 0
    let longestSegmentLength = 0
    let currentSegmentStart = 0
    let currentSegmentLength = 1
    let currentIsHorizontal = trace.path[1].y === trace.path[0].y

    for (let i = 1; i < trace.path.length; i++) {
      const isHorizontal = trace.path[i].y === trace.path[i - 1].y

      if (isHorizontal === currentIsHorizontal) {
        currentSegmentLength++
      } else {
        if (currentSegmentLength > longestSegmentLength) {
          longestSegmentLength = currentSegmentLength
          longestSegmentStart = currentSegmentStart
        }
        currentSegmentStart = i - 1
        currentSegmentLength = 2
        currentIsHorizontal = isHorizontal
      }
    }

    if (currentSegmentLength > longestSegmentLength) {
      longestSegmentLength = currentSegmentLength
      longestSegmentStart = currentSegmentStart
    }

    const segmentMidIndex = longestSegmentStart + Math.floor(longestSegmentLength / 2)
    const midPoint = trace.path[Math.min(segmentMidIndex, trace.path.length - 1)]
    const nextPoint = trace.path[Math.min(segmentMidIndex + 1, trace.path.length - 1)]

    const cx = midPoint.x * this.cellSize + this.cellSize / 2
    const cy = midPoint.y * this.cellSize + this.cellSize / 2

    const isHorizontal = nextPoint.y === midPoint.y

    let labelX: number, labelY: number
    if (isHorizontal) {
      labelX = cx - (trace.net.length * (CHAR_WIDTH + CHAR_SPACING)) / 2
      labelY = cy - CHAR_HEIGHT - 4
    } else {
      labelX = cx + 5
      labelY = cy - CHAR_HEIGHT / 2
    }

    this.drawText(pixels, trace.net, labelX, labelY, color, COLORS.textShadow)
  }

  private drawLine(
    pixels: Uint8ClampedArray,
    from: GridCoordinate,
    to: GridCoordinate,
    color: Color
  ): void {
    const x1 = from.x * this.cellSize + this.cellSize / 2
    const y1 = from.y * this.cellSize + this.cellSize / 2
    const x2 = to.x * this.cellSize + this.cellSize / 2
    const y2 = to.y * this.cellSize + this.cellSize / 2

    const dx = Math.abs(x2 - x1)
    const dy = Math.abs(y2 - y1)
    const sx = x1 < x2 ? 1 : -1
    const sy = y1 < y2 ? 1 : -1
    let err = dx - dy

    let x = x1
    let y = y1

    while (true) {
      for (let offset = -1; offset <= 1; offset++) {
        if (dx > dy) {
          this.setPixel(pixels, x, y + offset, color)
        } else {
          this.setPixel(pixels, x + offset, y, color)
        }
      }

      if (x === x2 && y === y2) break
      const e2 = 2 * err
      if (e2 > -dy) {
        err -= dy
        x += sx
      }
      if (e2 < dx) {
        err += dx
        y += sy
      }
    }
  }

  private drawTraceNode(
    pixels: Uint8ClampedArray,
    coord: GridCoordinate,
    color: Color
  ): void {
    const cx = coord.x * this.cellSize + this.cellSize / 2
    const cy = coord.y * this.cellSize + this.cellSize / 2
    const radius = 2

    for (let dy = -radius; dy <= radius; dy++) {
      for (let dx = -radius; dx <= radius; dx++) {
        if (dx * dx + dy * dy <= radius * radius) {
          this.setPixel(pixels, cx + dx, cy + dy, color)
        }
      }
    }
  }

  private drawPadCircles(pixels: Uint8ClampedArray, pads: Map<string, Pad>): void {
    for (const [id, pad] of pads) {
      const cx = pad.center.x * this.cellSize + this.cellSize / 2
      const cy = pad.center.y * this.cellSize + this.cellSize / 2
      const radius = Math.floor(this.cellSize / 2) - 1

      for (let dy = -radius - 1; dy <= radius + 1; dy++) {
        for (let dx = -radius - 1; dx <= radius + 1; dx++) {
          const dist = dx * dx + dy * dy
          if (dist <= (radius + 1) * (radius + 1) && dist > radius * radius) {
            this.setPixel(pixels, cx + dx, cy + dy, COLORS.padOutline)
          }
        }
      }

      for (let dy = -radius; dy <= radius; dy++) {
        for (let dx = -radius; dx <= radius; dx++) {
          if (dx * dx + dy * dy <= radius * radius) {
            this.setPixel(pixels, cx + dx, cy + dy, COLORS.pad)
          }
        }
      }
    }
  }

  private drawPadLabels(pixels: Uint8ClampedArray, pads: Map<string, Pad>): void {
    const componentPads = new Map<string, { pads: Pad[], minX: number, minY: number, maxX: number, maxY: number }>()

    for (const [id, pad] of pads) {
      const cx = pad.center.x * this.cellSize + this.cellSize / 2
      const cy = pad.center.y * this.cellSize + this.cellSize / 2

      if (!componentPads.has(pad.componentId)) {
        componentPads.set(pad.componentId, { pads: [], minX: cx, minY: cy, maxX: cx, maxY: cy })
      }
      const comp = componentPads.get(pad.componentId)!
      comp.pads.push(pad)
      comp.minX = Math.min(comp.minX, cx)
      comp.minY = Math.min(comp.minY, cy)
      comp.maxX = Math.max(comp.maxX, cx)
      comp.maxY = Math.max(comp.maxY, cy)
    }

    for (const [componentId, comp] of componentPads) {
      const labelX = comp.minX - 4
      const labelY = comp.minY - CHAR_HEIGHT - 4
      this.drawText(pixels, componentId, labelX, labelY, COLORS.text, COLORS.textShadow)

      for (const pad of comp.pads) {
        const cx = pad.center.x * this.cellSize + this.cellSize / 2
        const cy = pad.center.y * this.cellSize + this.cellSize / 2
        const radius = Math.floor(this.cellSize / 2) - 1
        const pinLabelX = Math.round(cx + radius + 3)
        const pinLabelY = Math.round(cy - CHAR_HEIGHT / 2)
        this.drawText(pixels, pad.pinName, pinLabelX, pinLabelY, COLORS.text, COLORS.textShadow)
      }
    }
  }

  private drawText(
    pixels: Uint8ClampedArray,
    text: string,
    x: number,
    y: number,
    color: Color,
    shadowColor?: Color
  ): void {
    const upperText = text.toUpperCase()
    let offsetX = 0

    for (const char of upperText) {
      const glyph = FONT[char]
      if (glyph) {
        if (shadowColor) {
          this.drawChar(pixels, glyph, x + offsetX + 1, y + 1, shadowColor)
        }
        this.drawChar(pixels, glyph, x + offsetX, y, color)
      }
      offsetX += CHAR_WIDTH + CHAR_SPACING
    }
  }

  private drawChar(
    pixels: Uint8ClampedArray,
    glyph: number[],
    x: number,
    y: number,
    color: Color
  ): void {
    for (let row = 0; row < CHAR_HEIGHT; row++) {
      const rowBits = glyph[row] || 0
      for (let col = 0; col < CHAR_WIDTH; col++) {
        const bit = (rowBits >> (CHAR_WIDTH - 1 - col)) & 1
        if (bit) {
          this.setPixel(pixels, x + col, y + row, color)
        }
      }
    }
  }

  private drawComponentBodies(
    pixels: Uint8ClampedArray,
    bodies: ComponentBody[]
  ): void {
    const color = COLORS.componentBody
    const res = this.grid.resolution

    for (const body of bodies) {
      // Convert world mm to pixel coordinates
      const left = Math.round((body.x - body.width / 2) / res * this.cellSize)
      const right = Math.round((body.x + body.width / 2) / res * this.cellSize)
      const top = Math.round((body.y - body.height / 2) / res * this.cellSize)
      const bottom = Math.round((body.y + body.height / 2) / res * this.cellSize)

      // Draw rectangle outline (2px thick)
      for (let t = 0; t < 2; t++) {
        // Top edge
        for (let px = left; px <= right; px++) {
          this.setPixel(pixels, px, top + t, color)
        }
        // Bottom edge
        for (let px = left; px <= right; px++) {
          this.setPixel(pixels, px, bottom - t, color)
        }
        // Left edge
        for (let py = top; py <= bottom; py++) {
          this.setPixel(pixels, left + t, py, color)
        }
        // Right edge
        for (let py = top; py <= bottom; py++) {
          this.setPixel(pixels, right - t, py, color)
        }
      }

      // Draw cross lines through center for visibility
      const cx = Math.round(body.x / res * this.cellSize)
      const cy = Math.round(body.y / res * this.cellSize)
      const halfCross = 6
      for (let d = -halfCross; d <= halfCross; d++) {
        this.setPixel(pixels, cx + d, cy, color)
        this.setPixel(pixels, cx, cy + d, color)
      }

      // Draw label above the rectangle
      const labelText = body.id
      const labelX = left + 2
      const labelY = top - CHAR_HEIGHT - 3
      this.drawText(pixels, labelText, labelX, labelY, color, COLORS.textShadow)
    }
  }

  private fillCell(
    pixels: Uint8ClampedArray,
    gx: number,
    gy: number,
    color: Color
  ): void {
    const startX = gx * this.cellSize + 1
    const startY = gy * this.cellSize + 1
    const endX = startX + this.cellSize - 2
    const endY = startY + this.cellSize - 2

    for (let py = startY; py < endY; py++) {
      for (let px = startX; px < endX; px++) {
        this.setPixel(pixels, px, py, color)
      }
    }
  }

  private setPixel(
    pixels: Uint8ClampedArray,
    x: number,
    y: number,
    color: Color
  ): void {
    if (x < 0 || x >= this.width || y < 0 || y >= this.height) return
    const i = (y * this.width + x) * 4
    pixels[i] = color.r
    pixels[i + 1] = color.g
    pixels[i + 2] = color.b
    pixels[i + 3] = color.a
  }

  private async toBuffer(pixels: Uint8ClampedArray): Promise<Buffer> {
    return sharp(Buffer.from(pixels.buffer), {
      raw: {
        width: this.width,
        height: this.height,
        channels: 4
      }
    })
      .png()
      .toBuffer()
  }

  async saveToFile(
    path: string,
    traces: Trace[] = [],
    pads: Map<string, Pad> = new Map(),
    options: Partial<VisualizationOptions> = {},
    componentBodies: ComponentBody[] = []
  ): Promise<void> {
    const buffer = traces.length > 0 || pads.size > 0
      ? await this.renderComplete(traces, pads, options, componentBodies)
      : await this.renderGrid(options)

    await sharp(buffer).toFile(path)
  }
}

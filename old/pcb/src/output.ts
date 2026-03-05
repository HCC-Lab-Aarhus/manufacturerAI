import sharp from 'sharp'
import {
  Trace,
  Pad,
  Point,
  Polygon,
  OutputGeometry,
  RasterOutput,
  BoardParameters,
  ManufacturingConstraints
} from './types'
import { Grid } from './grid'

export class OutputGenerator {
  private readonly grid: Grid
  private readonly board: BoardParameters
  private readonly manufacturing: ManufacturingConstraints
  private readonly traces: Trace[]
  private readonly pads: Map<string, Pad>
  private readonly dpi: number

  constructor(
    grid: Grid,
    board: BoardParameters,
    manufacturing: ManufacturingConstraints,
    traces: Trace[],
    pads: Map<string, Pad>,
    dpi: number = 300,
  ) {
    this.grid = grid
    this.board = board
    this.manufacturing = manufacturing
    this.traces = traces
    this.pads = pads
    this.dpi = dpi
  }

  generateGeometry(): OutputGeometry {
    const boardOutline = this.createBoardOutline()
    const tracePolygons = this.createTracePolygons()
    const padPolygons = this.createPadPolygons()

    return {
      boardOutline,
      tracePolygons,
      padPolygons
    }
  }

  private createBoardOutline(): Polygon {
    if (this.board.boardOutline && this.board.boardOutline.length >= 3) {
      return this.board.boardOutline.map(p => ({ x: p[0], y: p[1] }))
    }
    return [
      { x: 0, y: 0 },
      { x: this.board.boardWidth, y: 0 },
      { x: this.board.boardWidth, y: this.board.boardHeight },
      { x: 0, y: this.board.boardHeight }
    ]
  }

  private createTracePolygons(): Polygon[] {
    const polygons: Polygon[] = []
    const halfWidth = this.manufacturing.traceWidth / 2

    for (const trace of this.traces) {
      for (let i = 0; i < trace.path.length - 1; i++) {
        const start = this.grid.gridToWorld(trace.path[i].x, trace.path[i].y)
        const end = this.grid.gridToWorld(trace.path[i + 1].x, trace.path[i + 1].y)

        const segment = this.createTraceSegment(start, end, halfWidth)
        polygons.push(segment)
      }

      for (const coord of trace.path) {
        const center = this.grid.gridToWorld(coord.x, coord.y)
        const pad = this.createCirclePolygon(center, halfWidth, 8)
        polygons.push(pad)
      }
    }

    return polygons
  }

  private createTraceSegment(
    start: { x: number; y: number },
    end: { x: number; y: number },
    halfWidth: number
  ): Polygon {
    const dx = end.x - start.x
    const dy = end.y - start.y

    let perpX: number, perpY: number

    if (Math.abs(dx) > Math.abs(dy)) {
      perpX = 0
      perpY = halfWidth
    } else {
      perpX = halfWidth
      perpY = 0
    }

    return [
      { x: start.x - perpX, y: start.y - perpY },
      { x: start.x + perpX, y: start.y + perpY },
      { x: end.x + perpX, y: end.y + perpY },
      { x: end.x - perpX, y: end.y - perpY }
    ]
  }

  private createPadPolygons(): Polygon[] {
    const polygons: Polygon[] = []
    const padRadius = 1.0

    for (const pad of this.pads.values()) {
      const center = this.grid.gridToWorld(pad.center.x, pad.center.y)
      const polygon = this.createCirclePolygon(center, padRadius, 16)
      polygons.push(polygon)
    }

    return polygons
  }

  private createCirclePolygon(
    center: { x: number; y: number },
    radius: number,
    segments: number
  ): Polygon {
    const points: Point[] = []

    for (let i = 0; i < segments; i++) {
      const angle = (2 * Math.PI * i) / segments
      points.push({
        x: center.x + radius * Math.cos(angle),
        y: center.y + radius * Math.sin(angle)
      })
    }

    return points
  }

  async generateRasterOutput(): Promise<RasterOutput> {
    // Target dimensions required by ink deposition equipment
    const targetWidth = 1536
    const targetHeight = 1383

    // Render at native DPI-based resolution (preserves original scale)
    const nativeWidth = Math.ceil((this.board.boardWidth / 25.4) * this.dpi)
    const nativeHeight = Math.ceil((this.board.boardHeight / 25.4) * this.dpi)

    const geometry = this.generateGeometry()

    const positiveMask = await this.renderMaskPadded(geometry, nativeWidth, nativeHeight, targetWidth, targetHeight, false)
    const negativeMask = await this.renderMaskPadded(geometry, nativeWidth, nativeHeight, targetWidth, targetHeight, true)

    return {
      positiveMask,
      negativeMask,
      width: targetWidth,
      height: targetHeight
    }
  }

  /**
   * Render at native resolution, then centre the result on a
   * target-sized canvas padded with white (negative) or black (positive).
   */
  private async renderMaskPadded(
    geometry: OutputGeometry,
    nativeW: number,
    nativeH: number,
    targetW: number,
    targetH: number,
    inverted: boolean,
  ): Promise<Buffer> {
    // Clamp native size so it never exceeds the target
    const renderW = Math.min(nativeW, targetW)
    const renderH = Math.min(nativeH, targetH)

    // Render the board image at native resolution
    const nativeBuf = await this.renderMask(geometry, renderW, renderH, inverted)

    if (renderW === targetW && renderH === targetH) {
      return nativeBuf
    }

    // Pad: white for negative (inverted), black for positive
    const padColor = inverted ? { r: 255, g: 255, b: 255 } : { r: 0, g: 0, b: 0 }

    // Centre the native image on the target canvas
    const padLeft = Math.floor((targetW - renderW) / 2)
    const padTop = Math.floor((targetH - renderH) / 2)

    return sharp(nativeBuf)
      .extend({
        top: padTop,
        bottom: targetH - renderH - padTop,
        left: padLeft,
        right: targetW - renderW - padLeft,
        background: padColor,
      })
      .png()
      .toBuffer()
  }

  private async renderMask(
    geometry: OutputGeometry,
    width: number,
    height: number,
    inverted: boolean
  ): Promise<Buffer> {
    const pixels = new Uint8Array(width * height)
    pixels.fill(inverted ? 255 : 0)

    const fillValue = inverted ? 0 : 255

    for (const polygon of geometry.tracePolygons) {
      this.fillPolygon(pixels, width, height, polygon, fillValue)
    }

    for (const polygon of geometry.padPolygons) {
      this.fillPolygon(pixels, width, height, polygon, fillValue)
    }

    return sharp(Buffer.from(pixels), {
      raw: {
        width,
        height,
        channels: 1
      }
    })
      .png()
      .toBuffer()
  }

  private worldToPixel(worldX: number, worldY: number, width: number, height: number): { px: number; py: number } {
    // Flip both X and Y so mask orientation matches the debug image
    // (the debug image applies a 180° rotation which flips both axes)
    const px = Math.floor((1 - worldX / this.board.boardWidth) * width)
    const py = Math.floor((1 - worldY / this.board.boardHeight) * height)
    return { px, py }
  }

  private fillPolygon(
    pixels: Uint8Array,
    width: number,
    height: number,
    polygon: Polygon,
    value: number
  ): void {
    if (polygon.length < 3) return

    const pixelPolygon = polygon.map(p => this.worldToPixel(p.x, p.y, width, height))

    let minY = Infinity, maxY = -Infinity
    for (const p of pixelPolygon) {
      minY = Math.min(minY, p.py)
      maxY = Math.max(maxY, p.py)
    }

    minY = Math.max(0, Math.floor(minY))
    maxY = Math.min(height - 1, Math.ceil(maxY))

    for (let y = minY; y <= maxY; y++) {
      const intersections: number[] = []

      for (let i = 0; i < pixelPolygon.length; i++) {
        const p1 = pixelPolygon[i]
        const p2 = pixelPolygon[(i + 1) % pixelPolygon.length]

        if ((p1.py <= y && p2.py > y) || (p2.py <= y && p1.py > y)) {
          const x = p1.px + ((y - p1.py) / (p2.py - p1.py)) * (p2.px - p1.px)
          intersections.push(x)
        }
      }

      intersections.sort((a, b) => a - b)

      for (let i = 0; i < intersections.length - 1; i += 2) {
        const xStart = Math.max(0, Math.floor(intersections[i]))
        const xEnd = Math.min(width - 1, Math.ceil(intersections[i + 1]))

        for (let x = xStart; x <= xEnd; x++) {
          pixels[y * width + x] = value
        }
      }
    }
  }

  async saveToFiles(basePath: string): Promise<void> {
    const output = await this.generateRasterOutput()

    await sharp(output.positiveMask).toFile(`${basePath}_positive.png`)
    await sharp(output.negativeMask).toFile(`${basePath}_negative.png`)
  }
}

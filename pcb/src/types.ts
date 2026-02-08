export interface BoardParameters {
  boardWidth: number
  boardHeight: number
  gridResolution: number
  /** Optional polygon outline (list of [x,y] vertices). If provided, cells outside the polygon are blocked. */
  boardOutline?: number[][]
}

export interface ManufacturingConstraints {
  traceWidth: number
  traceClearance: number
}

export interface ButtonFootprint {
  pinSpacingX: number
  pinSpacingY: number
}

export interface ControllerFootprint {
  pinSpacing: number
  rowSpacing: number
}

export interface BatteryFootprint {
  padSpacing: number
}

export interface DiodeFootprint {
  padSpacing: number
}

export interface Footprints {
  button: ButtonFootprint
  controller: ControllerFootprint
  battery: BatteryFootprint
  diode: DiodeFootprint
}

export interface Button {
  id: string
  x: number
  y: number
  signalNet: string
}

export interface Battery {
  id: string
  x: number
  y: number
}

export interface Diode {
  id: string
  x: number
  y: number
  signalNet: string
}

export interface ControllerPins {
  [pinName: string]: string
}

export interface Controller {
  id: string
  x: number
  y: number
  pins: ControllerPins
}

export interface ComponentPlacement {
  buttons: Button[]
  controllers: Controller[]
  batteries?: Battery[]
  diodes?: Diode[]
}

export interface RouterInput {
  board: BoardParameters
  manufacturing: ManufacturingConstraints
  footprints: Footprints
  placement: ComponentPlacement
}

export enum CellState {
  FREE = 0,
  BLOCKED = 1
}

export interface GridCell {
  x: number
  y: number
  state: CellState
}

export interface GridCoordinate {
  x: number
  y: number
}

export interface Net {
  name: string
  source: GridCoordinate
  sink: GridCoordinate
  type: 'GND' | 'VCC' | 'SIGNAL'
}

export interface Trace {
  net: string
  path: GridCoordinate[]
}

export interface Pad {
  componentId: string
  pinName: string
  center: GridCoordinate
  net: string
  componentCenter?: GridCoordinate
}

export interface RoutingResult {
  success: boolean
  traces: Trace[]
  failedNets: FailedNet[]
}

export interface FailedNet {
  netName: string
  sourcePin: string
  destinationPin: string
  reason: string
}

export interface OutputGeometry {
  boardOutline: Polygon
  tracePolygons: Polygon[]
  padPolygons: Polygon[]
}

export interface Point {
  x: number
  y: number
}

export type Polygon = Point[]

export interface RasterOutput {
  positiveMask: Buffer
  negativeMask: Buffer
  width: number
  height: number
}

export interface VisualizationOptions {
  showGrid: boolean
  showBlockedCells: boolean
  showTraces: boolean
  showPads: boolean
  highlightNet?: string
}

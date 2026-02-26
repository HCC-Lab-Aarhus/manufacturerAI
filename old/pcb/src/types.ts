export interface BoardParameters {
  boardWidth: number
  boardHeight: number
  gridResolution: number
  /** Optional polygon outline (list of [x,y] vertices). If provided, cells outside the polygon are blocked. */
  boardOutline?: number[][]
  /** Extra clearance (mm) from the board/polygon edge where no traces may be routed. */
  edgeClearance?: number
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
  /** Default compartment body width (mm) if not specified per-battery. */
  bodyWidth: number
  /** Default compartment body height (mm) if not specified per-battery. */
  bodyHeight: number
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
  /** Full compartment body width in mm (traces blocked across this area). */
  bodyWidth?: number
  /** Full compartment body height in mm (traces blocked across this area). */
  bodyHeight?: number
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
  /** Rotation in degrees (0 or 90). When 90, rows run along Y and pins along X. */
  rotation?: number
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
  /** Maximum total rip-up/reroute attempts across all phases.
   *  Use a low value (e.g. 8) for fast screening, higher (25+) for thorough routing. */
  maxAttempts?: number
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

/** A rectangular component body to be blocked and drawn on the debug image. */
export interface ComponentBody {
  id: string
  /** World-space center X (mm). */
  x: number
  /** World-space center Y (mm). */
  y: number
  /** Full width (mm). */
  width: number
  /** Full height (mm). */
  height: number
}

export interface VisualizationOptions {
  showGrid: boolean
  showBlockedCells: boolean
  showTraces: boolean
  showPads: boolean
  highlightNet?: string
}

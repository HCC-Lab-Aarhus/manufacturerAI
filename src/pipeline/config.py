"""Shared physical constants for the manufacturing pipeline.

These values describe the physical properties of conductive-ink traces,
pin holes, and board edges.  Both the **placer** (which reserves routing
channels between components) and the **router** (which lays down actual
traces) derive their clearance parameters from this single source of truth.

Change a value here and both stages will stay in sync automatically.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

log = logging.getLogger(__name__)


# ── Printhead hardware definition ──────────────────────────────────

@dataclass(frozen=True)
class PrintheadConfig:
    """Physical parameters of the inkjet printhead (Xaar 128).

    Only geometry is stored here — timing / speed / serial protocol
    are execution concerns handled entirely by silver3dprinter.
    """

    nozzle_count: int = 128
    """Number of nozzles in the linear array."""

    nozzle_pitch_mm: float = 0.1371
    """Centre-to-centre distance between adjacent nozzles (137.1 µm, ~185 DPI)."""

    lane_step_nozzles: int = 32
    """How many nozzles the head advances between sweep lanes.
    The overlap is (nozzle_count - lane_step_nozzles) nozzles."""

    @property
    def printhead_width_mm(self) -> float:
        return self.nozzle_count * self.nozzle_pitch_mm

    @property
    def lane_width_mm(self) -> float:
        return self.lane_step_nozzles * self.nozzle_pitch_mm

    @property
    def pixel_size_mm(self) -> float:
        """Square pixel size — equal to nozzle pitch for 1:1 mapping."""
        return self.nozzle_pitch_mm


PRINTHEAD = PrintheadConfig()



@dataclass(frozen=True)
class TraceRules:
    """Physical design rules for conductive-ink traces.

    All distances are in millimetres.
    """

    trace_width_mm: float = 1.0
    """Width of a single conductive-ink trace."""

    trace_clearance_mm: float = 1.0
    """Minimum edge-to-edge gap between two traces (or a trace and
    another net's clearance zone)."""

    pin_clearance_mm: float = 1.5
    """Minimum gap from a trace edge to a foreign pin centre."""

    edge_clearance_mm: float = 1.5
    """Minimum distance from a trace to the outline edge."""

    grid_resolution_mm: float = 0.5
    """Routing-grid cell size (mm).  Independent of the bitmap resolution;
    the bitmap is rendered from world-mm trace coordinates after routing."""

    # ── Derived helpers ────────────────────────────────────────────

    @property
    def routing_channel_mm(self) -> float:
        """Width needed per trace channel between components.

        One channel = trace_width + trace_clearance (the gap the router
        enforces on each side is already half the clearance, so one full
        clearance between two traces is correct).
        """
        return self.trace_width_mm + self.trace_clearance_mm

    @property
    def min_pin_clearance_mm(self) -> float:
        """Minimum centre-to-centre distance between pin holes of
        different components.

        Ensures a trace (with its clearance envelope) can pass between
        two pins without violating pin_clearance on either side.
        Equals the largest common hole diameter (1.2 mm) + 2× pin_clearance.
        """
        return 1.2 + 2 * self.pin_clearance_mm

    @property
    def min_edge_clearance_mm(self) -> float:
        """Hard minimum body-to-outline distance for the placer.

        Matches the router edge clearance so traces at the body perimeter
        can still reach the outline-inset boundary.
        """
        return self.edge_clearance_mm


# Module-level singleton — importable everywhere.
TRACE_RULES = TraceRules()


# ── Sweep grid ────────────────────────────────────────────────────
#
# rasp_main.py adds 3 × 32-pixel padding strips before the bitmap
# data in its sliding-window slicing.  Bitmap column 0 maps to
# physical X = X_START + 3 × lane_width, NOT X_START.

_PADDING_STRIPS: int = 3


@dataclass(frozen=True)
class SweepGrid:
    """Derived sweep-grid geometry for a specific printer + printhead.

    Consumers use ``data_cols``, ``data_rows``, ``pixel_size_mm``,
    and ``bed_to_bitmap()`` to produce bitmaps.  Internal sweep
    parameters (lane count, offsets, etc.) are baked into the
    coordinate transform and don't need to be accessed directly.
    """

    data_cols: int
    data_rows: int
    pixel_size_mm: float

    _data_x_start_mm: float
    _y_start_mm: float
    _inkjet_offset_x: float
    _inkjet_offset_y: float
    _calibration_offset_x: float
    _calibration_offset_y: float

    def bed_to_bitmap(self, bed_x: float, bed_y: float) -> tuple[float, float]:
        """Convert absolute bed coordinates to bitmap-local coordinates (mm)."""
        bx = (bed_x
              - self._data_x_start_mm
              - self._inkjet_offset_x
              + self._calibration_offset_x)
        by = (bed_y
              - self._y_start_mm
              - self._inkjet_offset_y
              + self._calibration_offset_y)
        return bx, by


def sweep_grid(pdef: PrinterDef, printhead: PrintheadConfig = PRINTHEAD) -> SweepGrid:
    """Compute the sweep grid for a printer + printhead combination.

    X_START = abs(inkjet_offset_x)  — first lane where nozzle 0 reaches bed X=0
    Y_START = abs(inkjet_offset_y)  — Y position where the nozzle array starts
    X_END   = nominal_bed_width     — last reachable X position
    Y_END   = nominal_bed_depth     — last reachable Y position
    """
    x_start = abs(pdef.inkjet_offset_x)
    y_start = abs(pdef.inkjet_offset_y)
    x_end = pdef.nominal_bed_width
    increment = printhead.lane_width_mm
    pixel = printhead.pixel_size_mm
    step = printhead.lane_step_nozzles

    num_lanes = 1 + int((x_end - x_start + 1e-9) / increment)
    data_cols = (num_lanes - _PADDING_STRIPS) * step
    data_rows = math.ceil((pdef.nominal_bed_depth - y_start) / pixel)
    data_x_start = x_start + _PADDING_STRIPS * step * pixel

    return SweepGrid(
        data_cols=data_cols,
        data_rows=data_rows,
        pixel_size_mm=pixel,
        _data_x_start_mm=data_x_start,
        _y_start_mm=y_start,
        _inkjet_offset_x=pdef.inkjet_offset_x,
        _inkjet_offset_y=pdef.inkjet_offset_y,
        _calibration_offset_x=pdef.calibration_offset_x,
        _calibration_offset_y=pdef.calibration_offset_y,
    )



# ── Enclosure Z-layer constants (mm) ──────────────────────────────
#
# The enclosure is a vertical stack of zones.  Every stage that
# needs a Z-height (scad cutouts, pause-point computation, design
# validation) must reference these constants so they stay in sync.
#
#   0 ─── build plate
#   │  FLOOR_MM (2)          solid printed floor (ironed top surface)
#   │  FLOOR_MM              trace zone begins (conductive ink on ironed surface)
#   │  FLOOR_MM + TRACE_H    trace zone top (shallow channels, 0.4 mm)
#   │  CAVITY_START_MM (3)   component zone begins (= FLOOR_MM + COMP_OFFSET)
#   │  ... component pockets / pin shafts ...
#   │  CEIL_START            = total_height - CEILING_MM
#   │  CEILING_MM (2)        solid printed ceiling
#   └── total_height

FLOOR_MM: float = 2.0
TRACE_HEIGHT_MM: float = 0.4
COMPONENT_OFFSET_MM: float = 1.0
CAVITY_START_MM: float = FLOOR_MM + COMPONENT_OFFSET_MM
CEILING_MM: float = 2.0

# ── Pause-group thresholds ────────────────────────────────────────
#
# Components are assigned to insertion-pause groups based on their
# total standing height (tip-to-top when seated in the cavity).
# Group 1 (early) gets the shortest components, Group 2 (mid) gets
# medium-height ones, and everything else waits for the late pause.

PAUSE_NOZZLE_CLEARANCE_MM: float = 2.0
"""Minimum gap between the tallest component in a pause group and the
Z height at which the nozzle resumes printing."""

EARLY_GROUP_MAX_TOTAL_HEIGHT_MM: float = 5.0
"""Components with total_height ≤ this go to Group 1 (early pause).
ATmega (1.45 mm body above pins), resistors (2.4 mm body)."""

MID_GROUP_MAX_TOTAL_HEIGHT_MM: float = 10.0
"""Components with total_height ≤ this go to Group 2 (mid pause).
Buttons (7.9 mm total)."""


# ── Printer definitions ────────────────────────────────────────────

@dataclass(frozen=True)
class PrinterDef:
    """Static definition of a supported 3D printer.

    ``nominal_bed_width/depth`` are the physical bed dimensions matching
    PrusaSlicer's ``bed_shape``.  ``inkjet_offset_x/y`` describe the
    mechanical offset from the PLA nozzle to the inkjet nozzle array
    centre.  The usable area (``bed_width/depth``) is derived —
    existing code continues to work unchanged.
    """
    id: str
    label: str
    nominal_bed_width: float   # mm — full bed (matches PrusaSlicer bed_shape)
    nominal_bed_depth: float   # mm
    inkjet_offset_x: float     # mm — PLA nozzle → inkjet array centre, +X = right
    inkjet_offset_y: float     # mm — PLA nozzle → inkjet array centre, +Y = back
    calibration_offset_x: float = 0.0  # mm — residual X correction from calibration prints
    calibration_offset_y: float = 0.0  # mm — residual Y correction from calibration prints
    max_z_mm: float = 210.0    # mm — maximum build height
    profile_filename: str = ""
    native_printer: str | None = None
    native_print: str | None = None
    native_material: str | None = None
    thumbnails: str | None = None

    @property
    def bed_width(self) -> float:
        """Usable print area width (nominal minus inkjet X offset)."""
        return self.nominal_bed_width - abs(self.inkjet_offset_x)

    @property
    def bed_depth(self) -> float:
        """Usable print area depth (nominal minus inkjet Y offset)."""
        return self.nominal_bed_depth - abs(self.inkjet_offset_y)


PRINTERS: dict[str, PrinterDef] = {
    "mk3s": PrinterDef(
        id="mk3s",
        label="Prusa MK3S",
        nominal_bed_width=250.0,
        nominal_bed_depth=210.0,
        inkjet_offset_x=-57.6,
        inkjet_offset_y=-32.0,
        calibration_offset_x=-1.8,
        calibration_offset_y=2.7,
        max_z_mm=210.0,
        profile_filename="slicer_profile_mk3s.ini",
    ),
    "mk3s_plus": PrinterDef(
        id="mk3s_plus",
        label="Prusa i3 MK3S+",
        nominal_bed_width=250.0,
        nominal_bed_depth=210.0,
        inkjet_offset_x=-57.6,
        inkjet_offset_y=-32.0,
        calibration_offset_x=-1.8,
        calibration_offset_y=2.7,
        max_z_mm=210.0,
        profile_filename="slicer_profile_mk3s_plus.ini",
    ),
    "coreone": PrinterDef(
        id="coreone",
        label="Prusa Core One+",
        nominal_bed_width=250.0,
        nominal_bed_depth=250.0,
        inkjet_offset_x=-57.6,
        inkjet_offset_y=-32.0,
        calibration_offset_x=-1.8,
        calibration_offset_y=2.7,
        max_z_mm=220.0,
        profile_filename="slicer_profile_coreone.ini",
        native_printer="Prusa CORE One HF0.4 nozzle",
        native_print="0.20mm BALANCED @COREONE HF0.4",
        native_material="Prusament PLA @COREONE HF0.4",
        thumbnails="16x16/PNG,220x124/PNG",
    ),
}

DEFAULT_PRINTER = "coreone"


def get_printer(printer_id: str | None = None) -> PrinterDef:
    """Return the *PrinterDef* for *printer_id* (falls back to default)."""
    pid = (printer_id or DEFAULT_PRINTER).lower().strip()
    if pid not in PRINTERS:
        log.warning("Unknown printer '%s' — falling back to %s", pid, DEFAULT_PRINTER)
        pid = DEFAULT_PRINTER
    return PRINTERS[pid]

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

    All sweep timing, bitmap resolution, and pixel sizing are derived
    from these hardware-given constants.
    """

    nozzle_count: int = 128
    """Number of nozzles in the linear array."""

    nozzle_pitch_mm: float = 0.13625
    """Centre-to-centre distance between adjacent nozzles (~185 DPI)."""

    lane_step_nozzles: int = 32
    """How many nozzles the head advances between sweep lanes.
    The overlap is (nozzle_count - lane_step_nozzles) nozzles."""

    max_fire_rate_hz: float = 5500.0
    """Maximum nozzle firing frequency (limited by READY cycle)."""

    serial_baud: int = 115200
    """Baud rate of the Pi→Arduino bitmap serial link."""

    serial_packet_bytes: int = 17
    """Bytes per row packet (1 header + 16 data)."""

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

    @property
    def max_serial_row_rate_hz(self) -> float:
        bits_per_byte = 10  # 8 data + 1 start + 1 stop
        return self.serial_baud / (self.serial_packet_bytes * bits_per_byte)

    @property
    def bottleneck_hz(self) -> float:
        return min(self.max_fire_rate_hz, self.max_serial_row_rate_hz)

    @property
    def max_sweep_speed_mm_per_s(self) -> float:
        return self.bottleneck_hz * self.pixel_size_mm

    def sweep_speed_for_row_rate(self, row_rate_hz: float) -> float:
        """Return sweep speed (mm/s) for a given row fire rate."""
        return row_rate_hz * self.pixel_size_mm

    def row_rate_for_speed(self, speed_mm_per_s: float) -> float:
        """Return the required row fire rate (Hz) for a given sweep speed."""
        return speed_mm_per_s / self.pixel_size_mm

    def bitmap_dims_for_part(
        self, part_width_mm: float, part_depth_mm: float,
    ) -> tuple[int, int]:
        """Return (cols, rows) of the *internal* rasterization grid.

        Internally:  cols = X extent (width),  rows = Y extent (depth).
        The text-file output is transposed (see bitmap.py) so that
        text-lines = X positions (sweep) and characters = Y positions
        (nozzle array).  The caller should use ``bitmap_output_dims``
        to get the file-level dimensions.
        """
        cols = math.ceil(part_width_mm / self.pixel_size_mm)
        rows = math.ceil(part_depth_mm / self.pixel_size_mm)
        return cols, rows

    def bitmap_output_dims(
        self, part_width_mm: float, part_depth_mm: float,
    ) -> tuple[int, int]:
        """Return (out_cols, out_rows) as they appear in the text file.

        The nozzle array is parallel to Y, so:
          - out_cols = Y extent (depth)  — characters per line = nozzle axis
          - out_rows = X extent (width)  — lines in file = sweep axis
        """
        internal_cols, internal_rows = self.bitmap_dims_for_part(
            part_width_mm, part_depth_mm,
        )
        return internal_rows, internal_cols


PRINTHEAD = PrintheadConfig()


@dataclass(frozen=True)
class BitmapConfig:
    """Resolution of the conductive-ink trace bitmap.

    Now dynamically sized to match the printhead's nozzle-native pixel
    pitch.  ``cols`` and ``rows`` are computed from the part bounding box
    and the printhead's pixel_size_mm.
    """

    cols: int = 1536
    rows: int = 1383

    def rows_for_bed(self, bed_width: float, bed_depth: float) -> int:
        """Return the fixed row count (kept for API compatibility)."""
        return self.rows

    @staticmethod
    def for_part(
        part_width_mm: float,
        part_depth_mm: float,
        printhead: PrintheadConfig = PRINTHEAD,
    ) -> "BitmapConfig":
        """Create a BitmapConfig sized to a specific part at nozzle-native resolution."""
        cols, rows = printhead.bitmap_dims_for_part(part_width_mm, part_depth_mm)
        return BitmapConfig(cols=cols, rows=rows)


BITMAP_CONFIG = BitmapConfig()


@dataclass(frozen=True)
class BitmapCalibration:
    """Projection-to-print alignment offsets (LEGACY).

    These empirical fudge factors exist for backward compatibility with
    the old fixed-resolution bitmap.  New code should use geometric
    alignment via the print-job manifest and set all offsets to zero.
    """

    offset_x: float = 0.0
    offset_y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0


BITMAP_CALIBRATION = BitmapCalibration()


@dataclass(frozen=True)
class TraceRules:
    """Physical design rules for conductive-ink traces.

    All distances are in millimetres.
    """

    trace_width_mm: float = 0.5
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
    max_z_mm: float            # mm — maximum build height
    profile_filename: str
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
        inkjet_offset_x=31.0,
        inkjet_offset_y=32.0,
        max_z_mm=210.0,
        profile_filename="slicer_profile_mk3s.ini",
    ),
    "mk3s_plus": PrinterDef(
        id="mk3s_plus",
        label="Prusa i3 MK3S+",
        nominal_bed_width=250.0,
        nominal_bed_depth=210.0,
        inkjet_offset_x=31.0,
        inkjet_offset_y=32.0,
        max_z_mm=210.0,
        profile_filename="slicer_profile_mk3s_plus.ini",
    ),
    "coreone": PrinterDef(
        id="coreone",
        label="Prusa Core One+",
        nominal_bed_width=250.0,
        nominal_bed_depth=250.0,
        inkjet_offset_x=31.0,
        inkjet_offset_y=32.0,
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

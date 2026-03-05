"""Shared physical constants for the manufacturing pipeline.

These values describe the physical properties of conductive-ink traces,
pin holes, and board edges.  Both the **placer** (which reserves routing
channels between components) and the **router** (which lays down actual
traces) derive their clearance parameters from this single source of truth.

Change a value here and both stages will stay in sync automatically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BitmapConfig:
    """Resolution of the conductive-ink trace bitmap.

    The bitmap is a fixed-size text grid produced by the router.
    It stretches over the printer's bed area; cell sizes are derived
    from the printer dimensions at render time.
    """

    cols: int = 1536
    rows: int = 1383


BITMAP_CONFIG = BitmapConfig()


@dataclass(frozen=True)
class TraceRules:
    """Physical design rules for conductive-ink traces.

    All distances are in millimetres.
    """

    trace_width_mm: float = 1.0
    """Width of a single conductive-ink trace."""

    trace_clearance_mm: float = 1.5
    """Minimum edge-to-edge gap between two traces (or a trace and
    another net's clearance zone).  1.5 mm gives a 2 mm physical gap
    between trace edges, providing a safe margin for conductive-ink
    deposition tolerances and preventing crosstalk / accidental shorts."""

    pin_clearance_mm: float = 2.0
    """Minimum gap from a trace edge to a foreign pin centre.
    1.0 mm — keeps well clear of adjacent DIP-28 pins (2.54 mm pitch)
    without adjacent blocked zones overlapping on the 0.5 mm grid."""

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
#   │  FLOOR_MM          solid printed floor
#   │  CAVITY_START_MM   cavity / trace zone begins
#   │  ... component pockets, traces ...
#   │  CEIL_START         = total_height - CEILING_MM
#   │  CEILING_MM         solid printed ceiling
#   └── total_height

FLOOR_MM: float = 2.0
CAVITY_START_MM: float = 3.0
CEILING_MM: float = 2.0


# ── Printer definitions ────────────────────────────────────────────

@dataclass(frozen=True)
class PrinterDef:
    """Static definition of a supported 3D printer."""
    id: str
    label: str
    bed_width: float      # mm
    bed_depth: float      # mm
    max_z_mm: float       # mm — maximum build height
    profile_filename: str
    native_printer: str | None = None
    native_print: str | None = None
    native_material: str | None = None
    thumbnails: str | None = None


PRINTERS: dict[str, PrinterDef] = {
    "mk3s": PrinterDef(
        id="mk3s",
        label="Prusa MK3S",
        bed_width=250.0,
        bed_depth=210.0,
        max_z_mm=210.0,
        profile_filename="slicer_profile.ini",
    ),
    "mk3s_plus": PrinterDef(
        id="mk3s_plus",
        label="Prusa i3 MK3S+",
        bed_width=250.0,
        bed_depth=210.0,
        max_z_mm=210.0,
        profile_filename="slicer_profile_mk3s_plus.ini",
    ),
    "coreone": PrinterDef(
        id="coreone",
        label="Prusa Core One+",
        bed_width=250.0,
        bed_depth=220.0,
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

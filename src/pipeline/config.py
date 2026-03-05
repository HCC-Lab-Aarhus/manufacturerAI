"""Shared physical constants for the manufacturing pipeline.

These values describe the physical properties of conductive-ink traces,
pin holes, and board edges.  Both the **placer** (which reserves routing
channels between components) and the **router** (which lays down actual
traces) derive their clearance parameters from this single source of truth.

Change a value here and both stages will stay in sync automatically.
"""

from __future__ import annotations

from dataclasses import dataclass


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
    """Routing-grid cell size."""

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

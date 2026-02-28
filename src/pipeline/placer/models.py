"""Placer output dataclasses and configuration constants."""

from __future__ import annotations

from dataclasses import dataclass

from src.pipeline.design.models import Outline, Net


# ── Output dataclasses ─────────────────────────────────────────────


@dataclass
class PlacedComponent:
    """A component with a resolved world position and rotation."""

    instance_id: str
    catalog_id: str
    x_mm: float
    y_mm: float
    rotation_deg: int   # 0, 90, 180, 270


@dataclass
class FullPlacement:
    """Complete placement of all components, ready for the router."""

    components: list[PlacedComponent]
    outline: Outline
    nets: list[Net]


class PlacementError(Exception):
    """Raised when a component cannot be placed inside the outline."""

    def __init__(self, instance_id: str, catalog_id: str, reason: str) -> None:
        self.instance_id = instance_id
        self.catalog_id = catalog_id
        self.reason = reason
        super().__init__(f"Cannot place '{instance_id}' ({catalog_id}): {reason}")


# ── Configuration ──────────────────────────────────────────────────

GRID_STEP_MM = 1.0          # grid scan resolution (mm)
VALID_ROTATIONS = (0, 90, 180, 270)

# Hard minimum distance (mm) from the component body edge to the
# outline polygon.  Positions with less clearance are rejected.
MIN_EDGE_CLEARANCE_MM = 1.5

# Routing-channel sizing — the gap between two components must
# leave room for all traces that need to pass between them.
# Each trace channel needs trace_width + clearance on each side.
# These match the router defaults (TRACE_WIDTH_MM=1.0,
# TRACE_CLEARANCE_MM=2.0).
ROUTING_CHANNEL_MM = 3.0     # width needed per trace channel (mm)

# Minimum centre-to-centre distance between pin holes of different
# components.  Prevents pad overlaps and ensures the router can
# address each pad independently.  Set to the largest common hole
# diameter (1.2 mm) plus one trace clearance (2.0 mm).
MIN_PIN_CLEARANCE_MM = 3.2

# Scoring weights — higher absolute value = more influence.
W_NET_PROXIMITY = 5.0       # MAIN driver: connected components close
W_EDGE_CLEARANCE = 0.5      # prefer safe distance from outline
W_COMPACTNESS = 0.3          # weakly prefer compact layouts
W_CLEARANCE_UNIFORM = 1.0   # prefer uniform gaps between components
W_BOTTOM_PREFERENCE = 0.08  # bottom-mount components prefer low Y
W_CROSSING = 50.0            # heavy penalty per inter-net crossing
W_PIN_COLLOCATION = 40.0     # heavy penalty per near-colliding pin pair

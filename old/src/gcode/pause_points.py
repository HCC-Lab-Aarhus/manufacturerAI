"""
Compute pause Z-heights from the enclosure geometry and cutout data.

Analyses the known Z-layer stack and returns the two critical heights
where the print must be paused:

1. **Ink layer** — the top of the solid floor, where traces start.
   The printer irons this surface, then conductive ink is deposited.

2. **Component insertion** — the Z where all component pockets
   (diode, switches, ATmega328P) are open and components can be
   dropped in before the ceiling closes over them.

These heights are expressed in mm from Z=0 (build plate) and are
snapped to the nearest layer boundary for the configured layer height.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.config.hardware import hw
from src.scad.shell import DEFAULT_HEIGHT_MM


@dataclass
class PausePoints:
    """Z-heights (mm) where the print must pause."""

    ink_layer_z: float
    """Top of the solid floor — iron this, then deposit ink."""

    component_insert_z: float
    """Top of component pockets — insert diode, switches, ATmega."""

    total_height: float
    """Overall enclosure height."""

    layer_height: float
    """Configured layer height (for snapping)."""

    ink_layer_number: int
    """Layer number (0-based) for the ink pause."""

    component_layer_number: int
    """Layer number (0-based) for the component insertion pause."""


def _snap_to_layer(z: float, layer_h: float) -> float:
    """Round *z* down to the nearest layer boundary."""
    return math.floor(z / layer_h) * layer_h


def compute_pause_points(
    shell_height: float | None = None,
    layer_height: float = 0.2,
) -> PausePoints:
    """Determine the two pause Z-heights for the multi-stage print.

    Parameters
    ----------
    shell_height : float, optional
        Total enclosure height.  Defaults to ``DEFAULT_HEIGHT_MM``.
    layer_height : float
        Slicer layer height in mm.  Default ``0.2``.

    Returns
    -------
    PausePoints
    """
    h = shell_height or DEFAULT_HEIGHT_MM

    # Z-layer constants — must match cutouts.py
    FLOOR = 2.0
    CAVITY_START = 3.0
    TOP_SOLID = 2.0
    CAVITY_END = h - TOP_SOLID

    # 1. Ink pause: top of the solid floor = CAVITY_START.
    #    We want to iron the last floor layer, then pause for ink.
    ink_z = _snap_to_layer(CAVITY_START, layer_height)

    # 2. Component insertion: just before the ceiling closes.
    #    CAVITY_END is where the solid ceiling starts.  Components
    #    must be in place before we print past this height.
    comp_z = _snap_to_layer(CAVITY_END, layer_height)

    ink_layer = round(ink_z / layer_height)
    comp_layer = round(comp_z / layer_height)

    return PausePoints(
        ink_layer_z=ink_z,
        component_insert_z=comp_z,
        total_height=h,
        layer_height=layer_height,
        ink_layer_number=ink_layer,
        component_layer_number=comp_layer,
    )

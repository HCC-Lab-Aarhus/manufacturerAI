"""
Compute pause Z-heights from the enclosure geometry and component data.

The print has multiple pause stages:

1. **Ink layer** — the top of the solid floor, where traces start.
   The printer irons this surface, then conductive ink is deposited.

2. **Component insertion pauses** — one or more pauses at increasing
   Z-heights where groups of components are inserted.  Short
   components go in early (low walls, easy access); tall components
   wait for later pauses.

Heights are in mm from Z=0 (build plate) and snapped to the nearest
layer boundary for the configured layer height.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from src.pipeline.config import (
    FLOOR_MM,
    CAVITY_START_MM,
    CEILING_MM,
    PAUSE_NOZZLE_CLEARANCE_MM,
)

DEFAULT_SHELL_HEIGHT_MM = 19.0  # typical: 15.0 cavity + 2.0 floor + 2.0 ceiling


@dataclass
class ComponentPauseInfo:
    """Minimal data needed for pause-point grouping."""

    instance_id: str
    body_height_mm: float
    pause_z_mm: float | None = None  # explicit override from catalog


@dataclass
class PausePoint:
    """A single pause in the multi-stage print."""

    z: float
    layer_number: int
    label: str
    components: list[str] = field(default_factory=list)


@dataclass
class PausePoints:
    """All pause points for a multi-stage print."""

    pauses: list[PausePoint]
    total_height: float
    layer_height: float

    # ── Convenience properties (backward-compat) ──────────────

    @property
    def ink_layer_z(self) -> float:
        return self.pauses[0].z

    @property
    def ink_layer_number(self) -> int:
        return self.pauses[0].layer_number

    @property
    def component_pauses(self) -> list[PausePoint]:
        """All non-ink pauses (component insertion stages)."""
        return [p for p in self.pauses if p.label != "ink"]


def _snap_to_layer(z: float, layer_h: float) -> float:
    """Round *z* down to the nearest layer boundary."""
    return math.floor(z / layer_h) * layer_h


def _fallback_pause_z(
    body_height_mm: float,
    shell_height: float,
    layer_height: float,
) -> float:
    """Compute a pause Z when no explicit pause_z_mm is set."""
    ceil_start = shell_height - CEILING_MM
    z = CAVITY_START_MM + body_height_mm + PAUSE_NOZZLE_CLEARANCE_MM
    return _snap_to_layer(min(z, ceil_start), layer_height)


def pause_z_for_component(
    body_height_mm: float,
    all_components: list[ComponentPauseInfo],
    shell_height: float,
    layer_height: float = 0.2,
    pause_z_mm: float | None = None,
) -> float:
    """Return the pause Z at which a component is inserted.

    If *pause_z_mm* is provided (from catalog), it is snapped to the
    nearest layer boundary and used directly.  Otherwise falls back to
    a computed value from body height + nozzle clearance.
    """
    if pause_z_mm is not None:
        return _snap_to_layer(pause_z_mm, layer_height)
    return _fallback_pause_z(body_height_mm, shell_height, layer_height)


def compute_pause_points(
    shell_height: float | None = None,
    layer_height: float = 0.2,
    components: list[ComponentPauseInfo] | None = None,
) -> PausePoints:
    """Determine pause Z-heights for the multi-stage print.

    Components with an explicit ``pause_z_mm`` are grouped by that
    value.  Components without one get a computed Z from their body
    height.  Duplicate Z values are merged into a single pause.

    Parameters
    ----------
    shell_height : float, optional
        Total enclosure height.  Defaults to ``DEFAULT_SHELL_HEIGHT_MM``.
    layer_height : float
        Slicer layer height in mm.  Default ``0.2``.
    components : list[ComponentPauseInfo], optional
        Placed components for multi-stage grouping.  When *None* the
        function falls back to a single component pause at ceil_start.

    Returns
    -------
    PausePoints
    """
    h = shell_height or DEFAULT_SHELL_HEIGHT_MM
    ceil_start = h - CEILING_MM

    ink_z = _snap_to_layer(FLOOR_MM, layer_height)
    ink_layer = round(ink_z / layer_height)

    pauses: list[PausePoint] = [
        PausePoint(z=ink_z, layer_number=ink_layer, label="ink"),
    ]

    if not components:
        # Backward compat: single component pause at ceil_start
        comp_z = _snap_to_layer(ceil_start, layer_height)
        comp_layer = round(comp_z / layer_height)
        pauses.append(PausePoint(
            z=comp_z, layer_number=comp_layer,
            label="components",
        ))
    else:
        # Group components by their resolved pause Z
        z_groups: dict[float, list[str]] = {}
        for c in components:
            z = pause_z_for_component(
                c.body_height_mm, components, h, layer_height, c.pause_z_mm,
            )
            z_groups.setdefault(z, []).append(c.instance_id)

        for z in sorted(z_groups):
            layer_num = round(z / layer_height)
            pauses.append(PausePoint(
                z=z, layer_number=layer_num,
                label="components",
                components=z_groups[z],
            ))

    return PausePoints(
        pauses=pauses,
        total_height=h,
        layer_height=layer_height,
    )

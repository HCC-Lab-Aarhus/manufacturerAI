"""Design spec dataclasses — the agent's output structure."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ComponentInstance:
    catalog_id: str
    instance_id: str
    config: dict | None = None
    mounting_style: str | None = None       # override from allowed_styles


@dataclass
class Net:
    id: str
    pins: list[str]     # "instance_id:pin_id" or "instance_id:group_id" for dynamic


@dataclass
class OutlineVertex:
    """A single vertex with optional corner easing.

    ease_in:  mm along the incoming edge (from prev vertex) where the
              curve begins.  0 = no easing on that side.
    ease_out: mm along the outgoing edge (to next vertex) where the
              curve ends.    0 = no easing on that side.

    If both are 0 the corner is sharp.  If only one is provided at
    parse time, the other defaults to the same value (symmetric).
    """
    x: float
    y: float
    ease_in: float = 0
    ease_out: float = 0


@dataclass
class Outline:
    """Device outline as a list of vertices, each with its own corner easing."""
    points: list[OutlineVertex]

    @property
    def vertices(self) -> list[tuple[float, float]]:
        """List of (x, y) tuples for polygon operations."""
        return [(p.x, p.y) for p in self.points]


@dataclass
class UIPlacement:
    instance_id: str
    x_mm: float
    y_mm: float
    edge_index: int | None = None       # side-mount: which outline edge (0-based)


@dataclass
class DesignSpec:
    components: list[ComponentInstance]
    nets: list[Net]
    outline: Outline
    ui_placements: list[UIPlacement]

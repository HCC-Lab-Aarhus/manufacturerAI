"""3D design spec dataclasses — arbitrary CSG mesh + surface-placed components."""

from __future__ import annotations

from dataclasses import dataclass, field
from .models import ComponentInstance, Net


@dataclass
class CSGNode:
    """A node in the CSG tree: either a primitive leaf or a boolean operation.

    Primitives have ``type`` set (box / cylinder / sphere).
    Operations have ``op`` set (union/difference/intersection) plus ``children``.

    Every shape can optionally taper along its axis:
    - **box**: add ``size_top`` to taper → wedge / prism / pyramid.
    - **cylinder**: add ``radius_top`` to taper → cone / frustum.
    - **sphere**: no taper (standalone).

    Round shapes accept ``radius`` as a scalar (uniform) or per-axis tuple
    (ellipsoid / oval cross-section).
    """

    # Primitive fields (leaf nodes)
    type: str | None = None                 # "box" | "cylinder" | "sphere"
    center: tuple[float, float, float] = (0.0, 0.0, 0.0)

    # Box dimensions
    size: tuple[float, float, float] | None = None         # extents (x, y, z)
    size_top: tuple[float, float, float] | None = None     # tapered box top-end extents

    # Round shape dimensions (scalar = uniform, tuple = per-axis)
    radius: float | None = None                             # uniform radius
    radii: tuple[float, ...] | None = None                  # sphere: (rx,ry,rz) / cylinder: (ra,rb)
    radius_top: float | None = None                         # tapered cylinder top radius
    radii_top: tuple[float, ...] | None = None              # tapered cylinder oval top: (ra,rb)

    height: float | None = None                             # cylinder
    axis: str = "z"                                         # cylinder / tapered box

    # Boolean operation fields (branch nodes)
    op: str | None = None                   # "union" | "difference" | "intersection"
    children: list[CSGNode] = field(default_factory=list)

    # Optional transform applied to the entire subtree result
    rotate: tuple[float, float, float] | None = None    # Euler XYZ degrees

    @property
    def is_primitive(self) -> bool:
        return self.type is not None

    @property
    def is_operation(self) -> bool:
        return self.op is not None


@dataclass
class SurfacePlacement:
    """A component placed on the mesh surface."""
    instance_id: str
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)  # 3D world position (or resolved from offset)
    face_hint: str | None = None            # "top"|"bottom"|"front"|"back"|"left"|"right"
    rotation_deg: float = 0.0               # rotation around the surface normal
    offset_mm: tuple[float, float] | None = None  # 2D offset from face zone center

    # Computed after snapping (populated by the surface snapper, not the agent)
    snapped_position: tuple[float, float, float] | None = None
    surface_normal: tuple[float, float, float] | None = None
    face_id: int | None = None


@dataclass
class DesignSpec3D:
    """A complete 3D device design with arbitrary mesh shape."""
    components: list[ComponentInstance]
    nets: list[Net]
    shape: CSGNode
    surface_placements: list[SurfacePlacement]

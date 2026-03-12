"""3D design spec dataclasses — arbitrary CSG mesh + surface-placed components."""

from __future__ import annotations

from dataclasses import dataclass, field
from .models import ComponentInstance, Net


@dataclass
class FitMarker:
    """Marks a dimension as adaptive — resolved at mesh-build time by
    ray-casting each vertex inward until it hits the nearest surface.

    ``cap`` limits the maximum extent (``None`` = derive from context bbox).
    """
    cap: float | None = None


@dataclass
class CSGNode:
    """A node in the CSG tree: either a primitive leaf or a boolean operation.

    Primitives have ``type`` set (box / cylinder / sphere).
    Operations have ``op`` set (union/difference/intersection) plus ``children``.

    Tapered shapes span along ``axis`` from ``center[axis] - height/2`` (the
    **−axis end**) to ``center[axis] + height/2`` (the **+axis end**):

    - **box**: ``size`` defines the −axis end cross-section.
      Add ``size_end`` to taper toward the +axis end.
    - **cylinder**: ``radius`` defines the −axis end cross-section.
      Add ``radius_end`` to taper toward the +axis end.
    - **sphere**: no taper (standalone).

    Round shapes accept ``radius`` as a scalar (uniform) or per-axis tuple
    (ellipsoid / oval cross-section).
    """

    # Primitive fields (leaf nodes)
    type: str | None = None                 # "box" | "cylinder" | "sphere"
    center: tuple[float, float, float] = (0.0, 0.0, 0.0)

    # Box dimensions
    size: tuple[float, float, float] | None = None         # extents (x, y, z)
    size_end: tuple[float, float, float] | None = None      # +axis end extents

    # Round shape dimensions (scalar = uniform, tuple = per-axis)
    radius: float | None = None                             # uniform radius
    radii: tuple[float, ...] | None = None                  # sphere: (rx,ry,rz) / cylinder: (ra,rb)
    radius_end: float | None = None                         # +axis end radius
    radii_end: tuple[float, ...] | None = None              # +axis end oval: (ra,rb)

    height: float | None = None                             # cylinder
    axis: str = "z"                                         # cylinder / tapered box

    # Boolean operation fields (branch nodes)
    op: str | None = None                   # "union" | "difference" | "intersection"
    children: list[CSGNode] = field(default_factory=list)

    # Optional transform applied to the entire subtree result
    rotate: tuple[float, float, float] | None = None    # Euler XYZ degrees

    # Adaptive dimensions — keys are field names, values are FitMarkers.
    # When present the numeric field is left None; the mesh builder resolves
    # the actual geometry by ray-casting against the accumulated context mesh.
    fit: dict[str, FitMarker] = field(default_factory=dict)

    @property
    def is_primitive(self) -> bool:
        return self.type is not None

    @property
    def is_operation(self) -> bool:
        return self.op is not None


@dataclass
class SurfacePlacement:
    """A component placed on the mesh surface.

    The agent specifies ``face`` and ``at``.
    ``face`` selects which surface to project onto (top/bottom/front/back/left/right).
    ``at`` is an approximate [x, y, z] aim-point in the same coordinate system
    as the CSG shapes — the depth axis (perpendicular to ``face``) is ignored
    and resolved automatically by ray-casting to the actual surface.
    """
    instance_id: str
    face: str = "top"                       # "top"|"bottom"|"front"|"back"|"left"|"right"
    at: tuple[float, float, float] = (0.0, 0.0, 0.0)  # aim point (depth axis auto-projected)
    rotation_deg: float = 0.0               # rotation around the surface normal

    # Computed after projection (populated by the surface projector, not the agent)
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

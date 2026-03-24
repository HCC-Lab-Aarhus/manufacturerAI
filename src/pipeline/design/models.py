"""Design spec dataclasses — the agent's output structure."""

from __future__ import annotations

from dataclasses import dataclass, field


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

    If both ease values are 0 the corner is sharp.  If only one is
    provided at parse time, the other defaults to the same value
    (symmetric).
    """
    x: float
    y: float
    ease_in: float = 0
    ease_out: float = 0

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict, omitting default/None fields."""
        d: dict = {"x": self.x, "y": self.y}
        if self.ease_in:
            d["ease_in"] = self.ease_in
        if self.ease_out:
            d["ease_out"] = self.ease_out
        return d


@dataclass
class Outline:
    """Device outline as a list of vertices, each with its own corner easing.

    holes: optional interior cutout rings.  Each hole is a closed polygon
    that is subtracted from the outer boundary, creating a through-hole in
    the enclosure (e.g. between tree branches, decorative openings).
    """
    points: list[OutlineVertex]
    holes: list[list[OutlineVertex]] = field(default_factory=list)

    @property
    def vertices(self) -> list[tuple[float, float]]:
        """List of (x, y) tuples for the outer ring."""
        return [(p.x, p.y) for p in self.points]

    @property
    def hole_vertices(self) -> list[list[tuple[float, float]]]:
        """List of (x, y) tuple lists for each interior hole."""
        return [[(p.x, p.y) for p in hole] for hole in self.holes]


@dataclass
class EdgeProfile:
    """Profile applied to the top or bottom edge of the enclosure wall.

    type:
      "none"    — a sharp right-angle edge (default).
      "chamfer" — a flat 45° bevel: size_mm wide and size_mm tall.
      "fillet"  — a smooth quarter-circle arc of radius size_mm.

    size_mm: size of the chamfer width / fillet radius in mm.  Defaults to
             2.0 mm.  Automatically clamped to at most 45% of the local
             wall height to prevent the top and bottom profiles overlapping.
    """
    type: str = "none"       # "none" | "chamfer" | "fillet"
    size_mm: float = 2.0

    def to_dict(self) -> dict:
        d: dict = {"type": self.type}
        if self.type != "none":
            d["size_mm"] = self.size_mm
        return d


@dataclass
class Enclosure:
    """Top-level enclosure shape descriptor.

    height_mm:   uniform ceiling height (mm).
    edge_top:    profile applied to the top edge of the wall (wall-to-lid
                 junction).  A chamfer creates a bevelled shoulder; a fillet
                 gives a smooth rounded rim.
    edge_bottom: profile applied to the bottom edge of the wall (wall-to-
                 floor junction).
    """
    height_mm: float = 25.0
    edge_top: EdgeProfile = field(default_factory=EdgeProfile)
    edge_bottom: EdgeProfile = field(default_factory=EdgeProfile)

    def to_dict(self) -> dict:
        d: dict = {"height_mm": self.height_mm}
        if self.edge_top.type != "none":
            d["edge_top"] = self.edge_top.to_dict()
        if self.edge_bottom.type != "none":
            d["edge_bottom"] = self.edge_bottom.to_dict()
        return d


# ── Placement / layout ─────────────────────────────────────────────────────────


@dataclass
class UIPlacement:
    instance_id: str
    x_mm: float
    y_mm: float
    catalog_id: str | None = None       # which catalog component
    edge_index: int | None = None       # side-mount: which outline edge (0-based)
    conform_to_surface: bool = True     # angle cutout to follow local surface normal
    mounting_style: str | None = None   # override from allowed_styles
    button_outline: list[list[float]] | None = None  # custom button shape [[x,y], ...]


@dataclass
class PhysicalDesign:
    """What design.json stores — the physical shape and UI component placements.

    This is the output of the design agent. No electrical components or nets.
    """
    outline: Outline
    enclosure: Enclosure = field(default_factory=Enclosure)
    ui_placements: list[UIPlacement] = field(default_factory=list)
    device_description: str = ""
    name: str = ""


@dataclass
class CircuitDesign:
    """What circuit.json stores — component instances and electrical nets.

    This is the output of the circuit agent.
    """
    components: list[ComponentInstance] = field(default_factory=list)
    nets: list[Net] = field(default_factory=list)


@dataclass
class DesignSpec:
    """Full merged design — physical + circuit combined.

    Constructed via build_design_spec(physical, circuit) for downstream
    pipeline steps (placer, router, validator) that need everything.
    """
    components: list[ComponentInstance]
    nets: list[Net]
    outline: Outline
    ui_placements: list[UIPlacement]
    enclosure: Enclosure = field(default_factory=Enclosure)

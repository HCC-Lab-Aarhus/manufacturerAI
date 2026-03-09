"""Catalog dataclasses — typed representations of catalog/*.json entries."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Body:
    shape: str                          # "rect" | "circle"
    height_mm: float
    width_mm: float | None = None       # rect only
    length_mm: float | None = None      # rect only
    diameter_mm: float | None = None    # circle only


@dataclass
class Cap:
    diameter_mm: float
    height_mm: float
    hole_clearance_mm: float


@dataclass
class Hatch:
    enabled: bool
    clearance_mm: float
    thickness_mm: float


@dataclass
class Mounting:
    style: str                          # "top" | "side" | "internal" | "bottom"
    allowed_styles: list[str]
    blocks_routing: bool
    keepout_margin_mm: float
    cap: Cap | None = None
    hatch: Hatch | None = None


@dataclass
class PinShape:
    """Optional non-circular pin geometry.

    type:
      "circle" — default round hole (uses Pin.hole_diameter_mm).
      "rect"   — rectangular pad / contact area.
      "slot"   — elongated slot (width × length, rounded ends).
    """
    type: str = "circle"                # "circle" | "rect" | "slot"
    width_mm: float | None = None       # rect / slot width
    length_mm: float | None = None      # rect / slot length


@dataclass
class Pin:
    id: str
    label: str
    position_mm: tuple[float, float]
    direction: str                      # "in" | "out" | "bidirectional"
    hole_diameter_mm: float
    description: str
    voltage_v: float | None = None
    current_max_ma: float | None = None
    shape: PinShape | None = None       # None → default circle from hole_diameter_mm


@dataclass
class PinGroup:
    id: str
    pin_ids: list[str]
    description: str = ""
    fixed_net: str | None = None
    allocatable: bool = False
    capabilities: list[str] | None = None


@dataclass
class ScadPattern:
    """Repeat pattern for a scad feature (e.g. grid of sound holes)."""
    type: str                           # "grid"
    spacing_mm: float
    clip_to_body: bool = True


@dataclass
class ScadFeature:
    """Additional cutout feature described in catalog JSON."""
    shape: str                          # "rect" | "circle"
    label: str
    position_mm: tuple[float, float]    # relative to component center
    width_mm: float | None = None       # rect
    length_mm: float | None = None      # rect
    diameter_mm: float | None = None    # circle
    depth_mm: float | None = None       # override; else uses cavity_depth
    z_anchor: str = "cavity_start"      # "cavity_start" | "floor" | "ceil_start"
    through_surface: bool = False       # cut through dome (e.g. shaft hole)
    pattern: ScadPattern | None = None  # repeat pattern (e.g. grid of holes)


@dataclass
class Component:
    id: str
    name: str
    description: str
    ui_placement: bool
    body: Body
    mounting: Mounting
    pins: list[Pin]
    internal_nets: list[list[str]] = field(default_factory=list)
    pin_groups: list[PinGroup] | None = None
    configurable: dict | None = None
    scad_features: list[ScadFeature] = field(default_factory=list)
    source_file: str = ""               # path of the JSON file (for error reporting)


@dataclass
class ValidationError:
    component_id: str
    field: str
    message: str

    def __str__(self) -> str:
        return f"[{self.component_id}] {self.field}: {self.message}"


@dataclass
class CatalogResult:
    """Result of loading the catalog — components + any validation errors."""
    components: list[Component]
    errors: list[ValidationError]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

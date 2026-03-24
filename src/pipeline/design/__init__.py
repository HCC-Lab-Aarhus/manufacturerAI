"""Design spec — dataclasses, parsing, validation, and serialization."""

from .models import (
    ComponentInstance, Net, OutlineVertex, Outline, UIPlacement, DesignSpec,
    Enclosure,
    PhysicalDesign, CircuitDesign,
)
from .parsing import parse_design, parse_physical_design, parse_circuit, build_design_spec
from .validation import validate_design, validate_physical_design
from .serialization import design_to_dict
from .shape2d import tessellate_shape, validate_shape
from .height_field import (
    blended_height, sample_height_grid,
    surface_normal_at,
)

__all__ = [
    # Models
    "ComponentInstance", "Net", "OutlineVertex", "Outline",
    "UIPlacement", "DesignSpec", "Enclosure",
    "PhysicalDesign", "CircuitDesign",
    # Parsing / Validation / Serialization
    "parse_design", "parse_physical_design", "parse_circuit", "build_design_spec",
    "validate_design", "validate_physical_design",
    "design_to_dict",
    # Shape2D
    "tessellate_shape", "validate_shape",
    # Height field
    "blended_height", "sample_height_grid",
    "surface_normal_at",
]

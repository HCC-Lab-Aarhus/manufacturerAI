"""Design spec — dataclasses, parsing, validation, and serialization."""

from .models import (
    ComponentInstance, Net, OutlineVertex, Outline, UIPlacement, DesignSpec,
    TopSurface, Enclosure,
)
from .parsing import parse_design
from .validation import validate_design
from .serialization import design_to_dict
from .height_field import (
    blended_height, sample_height_grid, surface_normal_at,
)

from .models3d import CSGNode, SurfacePlacement, DesignSpec3D
from .parsing3d import parse_design_3d
from .validation3d import validate_design_3d
from .serialization3d import design3d_to_dict

__all__ = [
    # Models (2D)
    "ComponentInstance", "Net", "OutlineVertex", "Outline",
    "UIPlacement", "DesignSpec", "TopSurface", "Enclosure",
    # Models (3D)
    "CSGNode", "SurfacePlacement", "DesignSpec3D",
    # Parsing / Validation / Serialization (2D)
    "parse_design", "validate_design", "design_to_dict",
    # Parsing / Validation / Serialization (3D)
    "parse_design_3d", "validate_design_3d", "design3d_to_dict",
    # Height field
    "blended_height", "sample_height_grid", "surface_normal_at",
]

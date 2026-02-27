"""Design spec — dataclasses, parsing, validation, and serialization."""

from .models import (
    ComponentInstance, Net, OutlineVertex, Outline, UIPlacement, DesignSpec,
)
from .parsing import parse_design
from .validation import validate_design
from .serialization import design_to_dict

__all__ = [
    # Models
    "ComponentInstance", "Net", "OutlineVertex", "Outline",
    "UIPlacement", "DesignSpec",
    # Parsing / Validation / Serialization
    "parse_design", "validate_design", "design_to_dict",
]

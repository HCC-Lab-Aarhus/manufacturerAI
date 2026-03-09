"""Per-component SCAD resolver infrastructure.

The resolver registry maps catalog IDs to resolver classes.  When no
specific resolver is registered for a component, the generic resolver
is used (derives geometry from body.shape + mounting.style).

Usage::

    from src.pipeline.scad.resolvers import resolve_component
    fragments = resolve_component(placed, catalog_comp, ctx)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.catalog.models import Component
    from src.pipeline.placer.models import PlacedComponent
    from .base import ResolverContext

from ..fragment import ScadFragment
from .base import BaseResolver, ResolverContext
from .generic import GenericResolver

_REGISTRY: dict[str, type[BaseResolver]] = {}


def register(catalog_id: str, resolver_cls: type[BaseResolver]) -> None:
    _REGISTRY[catalog_id] = resolver_cls


def resolve_component(
    placed: PlacedComponent,
    catalog: Component,
    ctx: ResolverContext,
) -> list[ScadFragment]:
    """Resolve SCAD fragments for a placed component.

    Looks up a specific resolver by catalog_id; falls back to GenericResolver.
    """
    cls = _REGISTRY.get(catalog.id, GenericResolver)
    resolver = cls(placed, catalog, ctx)
    return resolver.resolve()


# Auto-register component-specific resolvers on import
from . import battery_holder  # noqa: F401, E402
from . import led             # noqa: F401, E402
from . import button          # noqa: F401, E402

__all__ = ["resolve_component", "register", "ResolverContext"]

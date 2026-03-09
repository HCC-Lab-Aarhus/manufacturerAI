"""Base resolver class and shared context for SCAD fragment generation."""

from __future__ import annotations

from dataclasses import dataclass

from src.catalog.models import Component
from src.pipeline.design.models import Outline, Enclosure
from src.pipeline.placer.models import PlacedComponent
from typing import Callable

from src.pipeline.config import FLOOR_MM, CAVITY_START_MM

from ..fragment import (
    ScadFragment, RectGeometry, CylinderGeometry, PolygonGeometry,
    rotated_polygon, rotate_point,
)


COMPONENT_MARGIN: float = 1.5
SURFACE_OVERSHOOT: float = 1.0

PINHOLE_CLEARANCE: float = 0.15
PINHOLE_TAPER_D: float = 1.4
PINHOLE_TAPER_DEPTH: float = 0.5


@dataclass
class ResolverContext:
    """Shared data available to all resolvers."""
    outline: Outline
    enclosure: Enclosure
    base_h: float              # enclosure.height_mm
    ceil_start: float          # base_h - CEILING_MM
    cavity_depth: float        # ceil_start - CAVITY_START_MM
    blended_height_fn: Callable[..., float]


class BaseResolver:
    """Base class for component SCAD resolvers.

    Subclasses override ``resolve()`` to produce component-specific
    fragments.  Shared helpers for pinholes and body pockets live here.
    """

    def __init__(
        self,
        placed: PlacedComponent,
        catalog: Component,
        ctx: ResolverContext,
    ) -> None:
        self.placed = placed
        self.catalog = catalog
        self.ctx = ctx
        self.cx = placed.x_mm
        self.cy = placed.y_mm
        self.rot = placed.rotation_deg
        self.cid = placed.instance_id

    def resolve(self) -> list[ScadFragment]:
        raise NotImplementedError

    # ── Shared helpers ─────────────────────────────────────────────

    def dome_z(self) -> float:
        fn = self.ctx.blended_height_fn
        return fn(self.cx, self.cy, self.ctx.outline, self.ctx.enclosure)

    def surface_depth(self) -> float:
        return self.dome_z() - self.ctx.ceil_start + SURFACE_OVERSHOOT

    def pinhole_fragments(self) -> list[ScadFragment]:
        """Press-fit shaft + taper pinholes for every pin.

        Pins extend from FLOOR_MM (trace bottom) up to ceil_start
        (component pocket top), with a taper at the bottom.
        """
        frags: list[ScadFragment] = []
        shaft_h = (self.ctx.ceil_start - FLOOR_MM) - PINHOLE_TAPER_DEPTH

        for pin in self.catalog.pins:
            px_rel, py_rel = float(pin.position_mm[0]), float(pin.position_mm[1])
            if self.rot:
                px_rel, py_rel = rotate_point(px_rel, py_rel, self.rot)
            px = self.cx + px_rel
            py = self.cy + py_rel

            pin_d = pin.hole_diameter_mm + PINHOLE_CLEARANCE

            if pin.shape and pin.shape.type == "rect":
                w = pin.shape.width_mm or pin_d
                h = pin.shape.length_mm or pin_d
                shaft_geom = RectGeometry(px, py, w + PINHOLE_CLEARANCE, h + PINHOLE_CLEARANCE)
            else:
                shaft_geom = RectGeometry(px, py, pin_d, pin_d)

            frags.append(ScadFragment(
                type="cutout",
                geometry=shaft_geom,
                z_base=FLOOR_MM + PINHOLE_TAPER_DEPTH,
                depth=shaft_h,
                label=f"pin {self.cid}:{pin.id}",
            ))

            taper_d = max(PINHOLE_TAPER_D, pin_d + 0.4)
            frags.append(ScadFragment(
                type="cutout",
                geometry=RectGeometry(px, py, taper_d, taper_d),
                z_base=FLOOR_MM,
                depth=PINHOLE_TAPER_DEPTH,
                label=f"pin taper {self.cid}:{pin.id}",
            ))

        return frags

    def body_pocket_rect(self, margin: float = COMPONENT_MARGIN) -> ScadFragment:
        """Standard rectangular body pocket in the cavity zone."""
        body = self.catalog.body
        w = (body.width_mm or 5.0) + 2 * margin
        h = (body.length_mm or 5.0) + 2 * margin

        if self.rot:
            pts = RectGeometry(self.cx, self.cy, w, h).to_polygon()
            pts = rotated_polygon(pts, self.rot, self.cx, self.cy)
            geom = PolygonGeometry(pts)
        else:
            geom = RectGeometry(self.cx, self.cy, w, h)

        return ScadFragment(
            type="cutout",
            geometry=geom,
            z_base=CAVITY_START_MM,
            depth=self.ctx.cavity_depth,
            label=f"body pocket — {self.cid}",
        )

    def body_pocket_circle(self, margin: float = COMPONENT_MARGIN) -> ScadFragment:
        """Standard circular body pocket in the cavity zone."""
        body = self.catalog.body
        r = (body.diameter_mm or 5.0) / 2 + margin
        return ScadFragment(
            type="cutout",
            geometry=CylinderGeometry(self.cx, self.cy, r),
            z_base=CAVITY_START_MM,
            depth=self.ctx.cavity_depth,
            label=f"body pocket — {self.cid}",
        )

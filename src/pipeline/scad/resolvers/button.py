"""Button resolver — cap hole + body pocket for tactile buttons."""

from __future__ import annotations

from src.pipeline.config import CAVITY_START_MM

from ..fragment import (
    ScadFragment, RectGeometry, CylinderGeometry, PolygonGeometry,
    rotated_polygon,
)
from .base import BaseResolver, COMPONENT_MARGIN
from . import register


class ButtonResolver(BaseResolver):

    def resolve(self) -> list[ScadFragment]:
        frags: list[ScadFragment] = []
        body = self.catalog.body
        mounting = self.catalog.mounting
        s_depth = max(self.surface_depth(), 1.0)

        if mounting.cap is not None:
            cap = mounting.cap
            cap_r = (cap.diameter_mm + 2 * cap.hole_clearance_mm) / 2

            frags.append(ScadFragment(
                type="cutout",
                geometry=CylinderGeometry(self.cx, self.cy, cap_r),
                z_base=self.ctx.ceil_start,
                depth=s_depth,
                label=f"cap hole — {self.cid}",
            ))

            bw = (body.width_mm or 6.0) + 2 * COMPONENT_MARGIN
            bh = (body.length_mm or 6.0) + 2 * COMPONENT_MARGIN
            if self.rot:
                pts = RectGeometry(self.cx, self.cy, bw, bh).to_polygon()
                pts = rotated_polygon(pts, self.rot, self.cx, self.cy)
                geom = PolygonGeometry(pts)
            else:
                geom = RectGeometry(self.cx, self.cy, bw, bh)

            frags.append(ScadFragment(
                type="cutout",
                geometry=geom,
                z_base=CAVITY_START_MM,
                depth=self.ctx.cavity_depth,
                label=f"button body — {self.cid}",
            ))
        else:
            frags.append(self.body_pocket_rect())

        frags.extend(self.pinhole_fragments())
        return frags


register("tactile_button_6x6", ButtonResolver)

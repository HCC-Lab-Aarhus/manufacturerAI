"""Generic resolver — fallback for components without a specific resolver.

Derives geometry from body.shape + mounting.style, producing the same
output as the old cutouts.py dispatch.
"""

from __future__ import annotations

from src.pipeline.config import CAVITY_START_MM

from ..fragment import (
    ScadFragment, RectGeometry, CylinderGeometry, PolygonGeometry,
    rotated_polygon,
)
from .base import BaseResolver, COMPONENT_MARGIN


class GenericResolver(BaseResolver):

    def resolve(self) -> list[ScadFragment]:
        style = self.catalog.mounting.style
        if style == "top":
            return self._top_mount()
        elif style == "bottom":
            return self._bottom_mount()
        elif style == "side":
            return self._side_mount()
        else:
            return self._internal_mount()

    def _top_mount(self) -> list[ScadFragment]:
        frags: list[ScadFragment] = []
        body = self.catalog.body
        s_depth = max(self.surface_depth(), 1.0)

        if body.shape == "circle":
            body_r = (body.diameter_mm or 5.0) / 2
            frags.append(ScadFragment(
                type="cutout",
                geometry=CylinderGeometry(self.cx, self.cy, body_r + 0.3),
                z_base=self.ctx.ceil_start,
                depth=s_depth,
                label=f"top surface hole — {self.cid}",
            ))
            frags.append(self.body_pocket_circle())
        else:
            w = (body.width_mm or 5.0) + COMPONENT_MARGIN
            h = (body.length_mm or 5.0) + COMPONENT_MARGIN
            if self.rot:
                pts = RectGeometry(self.cx, self.cy, w, h).to_polygon()
                pts = rotated_polygon(pts, self.rot, self.cx, self.cy)
                geom = PolygonGeometry(pts)
            else:
                geom = RectGeometry(self.cx, self.cy, w, h)

            frags.append(ScadFragment(
                type="cutout",
                geometry=geom,
                z_base=self.ctx.ceil_start,
                depth=s_depth,
                label=f"top surface hole — {self.cid}",
            ))
            frags.append(ScadFragment(
                type="cutout",
                geometry=RectGeometry(
                    self.cx, self.cy,
                    w + 2 * COMPONENT_MARGIN, h + 2 * COMPONENT_MARGIN,
                ),
                z_base=CAVITY_START_MM,
                depth=self.ctx.cavity_depth,
                label=f"top body pocket — {self.cid}",
            ))

        frags.extend(self.pinhole_fragments())
        return frags

    def _bottom_mount(self) -> list[ScadFragment]:
        frags: list[ScadFragment] = []
        body = self.catalog.body
        margin = COMPONENT_MARGIN

        bw = (body.width_mm or 25.0) + 2 * margin
        bh = (body.length_mm or 48.0) + 2 * margin
        body_h = body.height_mm

        pocket_depth = min(body_h + margin, self.ctx.ceil_start - CAVITY_START_MM)
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
            depth=pocket_depth,
            label=f"bottom-mount body — {self.cid}",
        ))

        frags.extend(self.pinhole_fragments())
        return frags

    def _side_mount(self) -> list[ScadFragment]:
        frags: list[ScadFragment] = []
        body = self.catalog.body
        margin = COMPONENT_MARGIN
        body_h = body.height_mm or 4.0
        slot_z = min(body_h + margin, self.ctx.ceil_start - CAVITY_START_MM)

        if body.shape == "circle":
            r = (body.diameter_mm or 5.0) / 2 + margin
            frags.append(ScadFragment(
                type="cutout",
                geometry=CylinderGeometry(self.cx, self.cy, r),
                z_base=CAVITY_START_MM,
                depth=slot_z,
                label=f"side slot — {self.cid}",
            ))
        else:
            w = (body.width_mm or 5.0) + 2 * margin
            h = (body.length_mm or 5.0) + 2 * margin
            if self.rot:
                pts = RectGeometry(self.cx, self.cy, w, h).to_polygon()
                pts = rotated_polygon(pts, self.rot, self.cx, self.cy)
                geom = PolygonGeometry(pts)
            else:
                geom = RectGeometry(self.cx, self.cy, w, h)

            frags.append(ScadFragment(
                type="cutout",
                geometry=geom,
                z_base=CAVITY_START_MM,
                depth=slot_z,
                label=f"side slot — {self.cid}",
            ))

        frags.extend(self.pinhole_fragments())
        return frags

    def _internal_mount(self) -> list[ScadFragment]:
        frags: list[ScadFragment] = []
        body = self.catalog.body

        if body.shape == "circle":
            frags.append(self.body_pocket_circle())
        else:
            frags.append(self.body_pocket_rect())

        frags.extend(self.pinhole_fragments())
        return frags

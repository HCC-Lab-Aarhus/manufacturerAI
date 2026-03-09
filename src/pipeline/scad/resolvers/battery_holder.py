"""Battery holder resolver — hatch, ledges, pin bridges, body pocket."""

from __future__ import annotations

import math

from src.pipeline.config import CAVITY_START_MM

from ..fragment import (
    ScadFragment, RectGeometry, PolygonGeometry, SegmentGeometry,
    rotated_polygon, rotate_point,
)
from .base import BaseResolver, COMPONENT_MARGIN
from . import register

TRACE_WIDTH: float = 1.2


class BatteryHolderResolver(BaseResolver):

    def resolve(self) -> list[ScadFragment]:
        frags: list[ScadFragment] = []
        body = self.catalog.body
        mounting = self.catalog.mounting
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

        if mounting.hatch and mounting.hatch.enabled:
            frags.extend(self._hatch_fragments())

        frags.extend(self._pin_bridge_fragments(bw, bh))
        frags.extend(self.pinhole_fragments())
        return frags

    def _hatch_fragments(self) -> list[ScadFragment]:
        frags: list[ScadFragment] = []
        body = self.catalog.body
        hatch = self.catalog.mounting.hatch
        assert hatch is not None
        hatch_clr = hatch.clearance_mm

        hw2 = (body.width_mm or 25.0) / 2 - hatch_clr
        hh2 = (body.length_mm or 48.0) / 2 - hatch_clr
        hatch_depth = CAVITY_START_MM + 1.0

        hatch_pts = [
            [self.cx - hw2, self.cy - hh2],
            [self.cx + hw2, self.cy - hh2],
            [self.cx + hw2, self.cy + hh2],
            [self.cx - hw2, self.cy + hh2],
        ]
        if self.rot:
            hatch_pts = rotated_polygon(hatch_pts, self.rot, self.cx, self.cy)

        frags.append(ScadFragment(
            type="cutout",
            geometry=PolygonGeometry(hatch_pts),
            z_base=-1.0,
            depth=hatch_depth,
            label=f"battery floor opening — {self.cid}",
        ))

        hatch_thick = hatch.thickness_mm  # type: ignore[union-attr]
        ledge_w = 2.5
        ledge_d = hatch_thick + 0.3
        half_bw = (body.width_mm or 25.0) / 2 - hatch_clr
        ledge_len = (body.length_mm or 48.0) - hatch_clr * 2

        for side in (-1, 1):
            ledge_pts = RectGeometry(
                self.cx + side * (half_bw - ledge_w / 2), self.cy,
                ledge_w, ledge_len,
            ).to_polygon()
            if self.rot:
                ledge_pts = rotated_polygon(ledge_pts, self.rot, self.cx, self.cy)
            frags.append(ScadFragment(
                type="cutout",
                geometry=PolygonGeometry(ledge_pts),
                z_base=-0.5,
                depth=ledge_d + 0.5,
                label=f"battery ledge — {self.cid}",
            ))

        return frags

    def _pin_bridge_fragments(
        self, pocket_w: float, pocket_h: float,
    ) -> list[ScadFragment]:
        frags: list[ScadFragment] = []
        channel_depth = self.ctx.ceil_start - CAVITY_START_MM
        hw = pocket_w / 2
        hh = pocket_h / 2

        for pin in self.catalog.pins:
            px_rel = float(pin.position_mm[0])
            py_rel = float(pin.position_mm[1])

            outside_x = abs(px_rel) - hw
            outside_y = abs(py_rel) - hh
            if outside_x <= 0 and outside_y <= 0:
                continue

            if self.rot:
                rx, ry = rotate_point(px_rel, py_rel, self.rot)
            else:
                rx, ry = px_rel, py_rel
            pin_wx = self.cx + rx
            pin_wy = self.cy + ry

            if outside_x >= outside_y:
                face_x = math.copysign(hw, px_rel)
                face_y = py_rel
            else:
                face_x = px_rel
                face_y = math.copysign(hh, py_rel)

            if self.rot:
                frx, fry = rotate_point(face_x, face_y, self.rot)
            else:
                frx, fry = face_x, face_y
            face_wx = self.cx + frx
            face_wy = self.cy + fry

            frags.append(ScadFragment(
                type="cutout",
                geometry=SegmentGeometry(
                    pin_wx, pin_wy, face_wx, face_wy, TRACE_WIDTH,
                ),
                z_base=CAVITY_START_MM,
                depth=channel_depth,
                label=f"pin bridge {self.cid}:{pin.id}",
            ))

        return frags


register("battery_holder_2xAAA", BatteryHolderResolver)
register("battery_holder_9v", BatteryHolderResolver)

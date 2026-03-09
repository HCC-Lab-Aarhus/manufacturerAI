"""LED resolver — surface hole + body pocket for top-mounted LEDs."""

from __future__ import annotations

from src.pipeline.config import CAVITY_START_MM

from ..fragment import ScadFragment, CylinderGeometry
from .base import BaseResolver, COMPONENT_MARGIN
from . import register


class LedResolver(BaseResolver):

    def resolve(self) -> list[ScadFragment]:
        frags: list[ScadFragment] = []
        body = self.catalog.body
        body_r = (body.diameter_mm or 5.0) / 2
        s_depth = max(self.surface_depth(), 1.0)

        frags.append(ScadFragment(
            type="cutout",
            geometry=CylinderGeometry(self.cx, self.cy, body_r + 0.3),
            z_base=self.ctx.ceil_start,
            depth=s_depth,
            label=f"LED hole — {self.cid}",
        ))

        frags.append(ScadFragment(
            type="cutout",
            geometry=CylinderGeometry(self.cx, self.cy, body_r + COMPONENT_MARGIN),
            z_base=CAVITY_START_MM,
            depth=self.ctx.cavity_depth,
            label=f"LED body — {self.cid}",
        ))

        frags.extend(self.pinhole_fragments())
        return frags


register("led_5mm", LedResolver)

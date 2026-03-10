"""SCAD fragment resolver — turns placed components into cutout geometry.

All component-specific behaviour is driven by catalog data (body.shape,
mounting.style, mounting.cap, mounting.hatch, pin positions).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from src.catalog.models import Component
from src.pipeline.config import CAVITY_START_MM, FLOOR_MM
from src.pipeline.design.models import Outline, Enclosure
from src.pipeline.placer.models import PlacedComponent

from .fragment import (
    ScadFragment, RectGeometry, CylinderGeometry, PolygonGeometry,
    SegmentGeometry, rotated_polygon, rotate_point,
)

SURFACE_OVERSHOOT: float = 1.0
PINHOLE_CLEARANCE: float = 0.15
PINHOLE_TAPER_D: float = 1.4
PINHOLE_TAPER_DEPTH: float = 0.5
PIN_BRIDGE_WIDTH: float = 1.2
HATCH_LEDGE_WIDTH: float = 2.5


@dataclass
class ResolverContext:
    """Shared data available to the resolver."""
    outline: Outline
    enclosure: Enclosure
    base_h: float
    ceil_start: float
    cavity_depth: float
    blended_height_fn: Callable[..., float]


class ComponentResolver:
    """Resolves a single placed component into SCAD cutout fragments.

    Dispatches by ``mounting.style`` (top / bottom / side / internal)
    and uses catalog fields (cap, hatch, body shape, pin positions) to
    derive all geometry.
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
        style = self.catalog.mounting.style
        if style == "top":
            frags = self._top_mount()
        elif style == "bottom":
            frags = self._bottom_mount()
        elif style == "side":
            frags = self._side_mount()
        else:
            frags = self._internal_mount()

        frags.extend(self._pin_bridge_fragments())
        frags.extend(self._pinhole_fragments())
        frags.extend(self._scad_feature_fragments())
        return frags

    # ── Mounting-style handlers ────────────────────────────────────

    def _top_mount(self) -> list[ScadFragment]:
        frags: list[ScadFragment] = []
        body = self.catalog.body
        mounting = self.catalog.mounting
        s_depth = max(self._surface_depth(), 1.0)

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
        elif body.shape == "circle":
            frags.append(ScadFragment(
                type="cutout",
                geometry=CylinderGeometry(self.cx, self.cy, body.diameter_mm / 2),
                z_base=self.ctx.ceil_start,
                depth=s_depth,
                label=f"top surface hole — {self.cid}",
            ))
        else:
            frags.append(ScadFragment(
                type="cutout",
                geometry=self._rect_geom(body.width_mm, body.length_mm),
                z_base=self.ctx.ceil_start,
                depth=s_depth,
                label=f"top surface hole — {self.cid}",
            ))

        frags.append(self._body_pocket())
        return frags

    def _bottom_mount(self) -> list[ScadFragment]:
        frags: list[ScadFragment] = []
        body = self.catalog.body
        mounting = self.catalog.mounting

        pocket_depth = min(body.height_mm, self.ctx.cavity_depth)

        if body.shape == "circle":
            geom = CylinderGeometry(self.cx, self.cy, body.diameter_mm / 2)
        else:
            geom = self._rect_geom(body.width_mm, body.length_mm)

        frags.append(ScadFragment(
            type="cutout",
            geometry=geom,
            z_base=CAVITY_START_MM,
            depth=pocket_depth,
            label=f"bottom-mount body — {self.cid}",
        ))

        if mounting.hatch and mounting.hatch.enabled:
            frags.extend(self._hatch_fragments())

        return frags

    def _side_mount(self) -> list[ScadFragment]:
        body = self.catalog.body
        slot_depth = min(body.height_mm, self.ctx.cavity_depth)

        if body.shape == "circle":
            geom = CylinderGeometry(self.cx, self.cy, body.diameter_mm / 2)
        else:
            geom = self._rect_geom(body.width_mm, body.length_mm)

        return [ScadFragment(
            type="cutout",
            geometry=geom,
            z_base=CAVITY_START_MM,
            depth=slot_depth,
            label=f"side slot — {self.cid}",
        )]

    def _internal_mount(self) -> list[ScadFragment]:
        return [self._body_pocket()]

    # ── Geometry helpers ───────────────────────────────────────────

    def _dome_z(self) -> float:
        fn = self.ctx.blended_height_fn
        return fn(self.cx, self.cy, self.ctx.outline, self.ctx.enclosure)

    def _surface_depth(self) -> float:
        return self._dome_z() - self.ctx.ceil_start + SURFACE_OVERSHOOT

    def _rect_geom(self, w: float, h: float):
        if self.rot:
            pts = RectGeometry(self.cx, self.cy, w, h).to_polygon()
            pts = rotated_polygon(pts, self.rot, self.cx, self.cy)
            return PolygonGeometry(pts)
        return RectGeometry(self.cx, self.cy, w, h)

    def _body_pocket(self) -> ScadFragment:
        body = self.catalog.body
        if body.shape == "circle":
            geom = CylinderGeometry(self.cx, self.cy, body.diameter_mm / 2)
        else:
            geom = self._rect_geom(body.width_mm, body.length_mm)
        return ScadFragment(
            type="cutout",
            geometry=geom,
            z_base=CAVITY_START_MM,
            depth=self.ctx.cavity_depth,
            label=f"body pocket — {self.cid}",
        )

    # ── Pinholes ───────────────────────────────────────────────────

    def _pinhole_fragments(self) -> list[ScadFragment]:
        """Press-fit shaft + taper pinholes for every pin.

        Pins extend from FLOOR_MM (trace bottom) up to ceil_start
        (component pocket top), with a taper at the bottom.
        """
        frags: list[ScadFragment] = []
        shaft_h = (self.ctx.ceil_start - FLOOR_MM) - PINHOLE_TAPER_DEPTH

        for pin in self.catalog.pins:
            pos = self.placed.pin_positions.get(pin.id)
            if pos is not None:
                px, py = pos[0], pos[1]
            else:
                px_rel, py_rel = float(pin.position_mm[0]), float(pin.position_mm[1])
                if self.rot:
                    px_rel, py_rel = rotate_point(px_rel, py_rel, self.rot)
                px = self.cx + px_rel
                py = self.cy + py_rel

            pin_d = pin.hole_diameter_mm + PINHOLE_CLEARANCE

            if pin.shape and pin.shape.type in ("rect", "slot"):
                w = (pin.shape.width_mm or pin_d) + PINHOLE_CLEARANCE
                h = (pin.shape.length_mm or pin_d) + PINHOLE_CLEARANCE
                shaft_geom = RectGeometry(px, py, w, h)
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

    # ── Hatch (bottom-mount) ───────────────────────────────────────

    def _hatch_fragments(self) -> list[ScadFragment]:
        frags: list[ScadFragment] = []
        body = self.catalog.body
        hatch = self.catalog.mounting.hatch
        assert hatch is not None
        hatch_clr = hatch.clearance_mm

        hw2 = body.width_mm / 2 - hatch_clr
        hh2 = body.length_mm / 2 - hatch_clr
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
            label=f"floor opening — {self.cid}",
        ))

        hatch_thick = hatch.thickness_mm
        ledge_d = hatch_thick + 0.3
        half_bw = body.width_mm / 2 - hatch_clr
        ledge_len = body.length_mm - hatch_clr * 2

        for side in (-1, 1):
            ledge_pts = RectGeometry(
                self.cx + side * (half_bw - HATCH_LEDGE_WIDTH / 2), self.cy,
                HATCH_LEDGE_WIDTH, ledge_len,
            ).to_polygon()
            if self.rot:
                ledge_pts = rotated_polygon(ledge_pts, self.rot, self.cx, self.cy)
            frags.append(ScadFragment(
                type="cutout",
                geometry=PolygonGeometry(ledge_pts),
                z_base=-0.5,
                depth=ledge_d + 0.5,
                label=f"hatch ledge — {self.cid}",
            ))

        return frags

    # ── Pin bridges ──────────────────────────────────────────────────

    def _pin_bridge_fragments(self) -> list[ScadFragment]:
        """Bridge channels for pins that fall outside the body pocket."""
        frags: list[ScadFragment] = []
        body = self.catalog.body
        channel_depth = self.ctx.ceil_start - CAVITY_START_MM

        for pin in self.catalog.pins:
            pos = self.placed.pin_positions.get(pin.id)
            if pos is not None:
                pin_wx, pin_wy = pos[0], pos[1]
                rx, ry = pin_wx - self.cx, pin_wy - self.cy
                if self.rot:
                    px_rel, py_rel = rotate_point(rx, ry, -self.rot)
                else:
                    px_rel, py_rel = rx, ry
            else:
                px_rel = float(pin.position_mm[0])
                py_rel = float(pin.position_mm[1])
                if self.rot:
                    rx, ry = rotate_point(px_rel, py_rel, self.rot)
                else:
                    rx, ry = px_rel, py_rel
                pin_wx = self.cx + rx
                pin_wy = self.cy + ry

            if body.shape == "circle":
                r = body.diameter_mm / 2
                dist = math.hypot(px_rel, py_rel)
                if dist <= r:
                    continue
            else:
                hw = body.width_mm / 2
                hh = body.length_mm / 2
                if abs(px_rel) <= hw and abs(py_rel) <= hh:
                    continue

            if body.shape == "circle":
                face_x = px_rel * r / dist
                face_y = py_rel * r / dist
            else:
                outside_x = abs(px_rel) - hw
                outside_y = abs(py_rel) - hh
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
                    pin_wx, pin_wy, face_wx, face_wy, PIN_BRIDGE_WIDTH,
                ),
                z_base=CAVITY_START_MM,
                depth=channel_depth,
                label=f"pin bridge {self.cid}:{pin.id}",
            ))

        return frags

    # ── Catalog scad_features ──────────────────────────────────────

    def _scad_feature_fragments(self) -> list[ScadFragment]:
        frags: list[ScadFragment] = []
        for feat in self.catalog.scad_features:
            fx, fy = float(feat.position_mm[0]), float(feat.position_mm[1])
            if self.rot:
                fx, fy = rotate_point(fx, fy, self.rot)
            wx, wy = self.cx + fx, self.cy + fy

            if feat.z_anchor == "floor":
                z_base = FLOOR_MM
            elif feat.z_anchor == "ceil_start":
                z_base = self.ctx.ceil_start
            else:
                z_base = CAVITY_START_MM

            if feat.through_surface:
                depth = max(self._surface_depth(), 1.0)
            elif feat.depth_mm:
                depth = feat.depth_mm
            else:
                depth = self.ctx.cavity_depth

            if feat.pattern and feat.pattern.type == "grid":
                frags.extend(self._grid_pattern_fragments(
                    feat, wx, wy, z_base, depth,
                ))
            else:
                if feat.shape == "circle":
                    geom = CylinderGeometry(wx, wy, (feat.diameter_mm or 1.0) / 2)
                else:
                    w = feat.width_mm or 1.0
                    h = feat.length_mm or 1.0
                    geom = self._rect_geom_at(wx, wy, w, h)

                frags.append(ScadFragment(
                    type="cutout",
                    geometry=geom,
                    z_base=z_base,
                    depth=depth,
                    label=f"{feat.label} — {self.cid}",
                ))
        return frags

    def _grid_pattern_fragments(
        self, feat, cx: float, cy: float, z_base: float, depth: float,
    ) -> list[ScadFragment]:
        """Expand a single feature with a grid pattern into multiple fragments."""
        frags: list[ScadFragment] = []
        spacing = feat.pattern.spacing_mm
        body = self.catalog.body

        if feat.pattern.clip_to_body:
            if body.shape == "circle":
                limit_r = body.diameter_mm / 2 - spacing / 2
            else:
                limit_r = min(body.width_mm, body.length_mm) / 2 - spacing / 2
        else:
            limit_r = 1000.0

        n = int(limit_r / spacing)
        for ix in range(-n, n + 1):
            for iy in range(-n, n + 1):
                dx, dy = ix * spacing, iy * spacing
                if math.hypot(dx, dy) > limit_r:
                    continue
                if self.rot:
                    dx, dy = rotate_point(dx, dy, self.rot)

                if feat.shape == "circle":
                    geom = CylinderGeometry(
                        cx + dx, cy + dy, (feat.diameter_mm or 1.0) / 2,
                    )
                else:
                    w = feat.width_mm or 1.0
                    h = feat.length_mm or 1.0
                    geom = self._rect_geom_at(cx + dx, cy + dy, w, h)

                frags.append(ScadFragment(
                    type="cutout",
                    geometry=geom,
                    z_base=z_base,
                    depth=depth,
                    label=f"{feat.label} — {self.cid}",
                ))
        return frags

    def _rect_geom_at(self, cx: float, cy: float, w: float, h: float):
        """Rotated rect geometry centered at an absolute position."""
        if self.rot:
            pts = RectGeometry(cx, cy, w, h).to_polygon()
            pts = rotated_polygon(pts, self.rot, self.cx, self.cy)
            return PolygonGeometry(pts)
        return RectGeometry(cx, cy, w, h)


def resolve_component(
    placed: PlacedComponent,
    catalog: Component,
    ctx: ResolverContext,
) -> list[ScadFragment]:
    """Resolve SCAD fragments for a placed component."""
    return ComponentResolver(placed, catalog, ctx).resolve()

"""SCAD fragment resolver — turns placed components into cutout geometry.

All component-specific behaviour is driven by catalog data (body.shape,
mounting.style, mounting.cap, mounting.hatch, pin positions).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from src.catalog.models import Component
from src.pipeline.config import CAVITY_START_MM, FLOOR_MM, component_z_range
from src.pipeline.design.models import Outline, Enclosure
from src.pipeline.placer.models import PlacedComponent

from .fragment import (
    ScadFragment, RectGeometry, CylinderGeometry,
    PolygonGeometry, SegmentGeometry, rotated_polygon, rotate_point,
)

SURFACE_OVERSHOOT: float = 1.0
PINHOLE_CLEARANCE: float = 1.0
PINHOLE_TAPER_EXTRA: float = 1.0   # extra width on each side for the funnel mouth
PINHOLE_TAPER_DEPTH: float = 1.5   # total height of the graduated funnel zone (mm)
PIN_BRIDGE_WIDTH: float = 1.6
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
    pause_z: float | None = None  # per-component insertion pause Z (caps pin grooves)


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
        style = self.placed.mounting_style or self.catalog.mounting.style
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

        # Custom button outline: the ceiling hole matches the cap outline
        # (+ clearance) so the button top slides freely.
        if self.placed.button_outline is not None and mounting.cap is not None and mounting.cap.actuator is not None:
            from .buttons import _offset_polygon, BUTTON_CLEARANCE_MM
            hole = _offset_polygon(self.placed.button_outline, BUTTON_CLEARANCE_MM)
            # Translate to world position
            world_pts = [[p[0] + self.cx, p[1] + self.cy] for p in hole]
            frags.append(ScadFragment(
                type="cutout",
                geometry=PolygonGeometry(world_pts),
                z_base=self.ctx.ceil_start,
                depth=s_depth,
                label=f"button hole — {self.cid}",
            ))
        elif mounting.cap is not None:
            cap = mounting.cap
            cap_r = (cap.diameter_mm + 2 * cap.hole_clearance_mm) / 2
            # When an actuator is defined but no custom outline, the hole
            # matches the default cap circle so the button top slides freely.
            if cap.actuator is not None:
                from .buttons import _offset_polygon, BUTTON_CLEARANCE_MM
                cap_r = cap.diameter_mm / 2 + BUTTON_CLEARANCE_MM
                frags.append(ScadFragment(
                    type="cutout",
                    geometry=CylinderGeometry(self.cx, self.cy, cap_r),
                    z_base=self.ctx.ceil_start,
                    depth=s_depth,
                    label=f"button hole — {self.cid}",
                ))
            else:
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

        _, body_top = self._z_range()
        gap = self.ctx.ceil_start - body_top
        if gap > 0:
            if body.shape == "circle":
                geom = CylinderGeometry(self.cx, self.cy, body.diameter_mm / 2)
            else:
                geom = self._rect_geom(body.width_mm, body.length_mm)
            frags.append(ScadFragment(
                type="cutout",
                geometry=geom,
                z_base=body_top,
                depth=gap,
                label=f"top-mount upper cavity — {self.cid}",
            ))

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
        is_reoriented = self.catalog.mounting.style != "side"

        if is_reoriented:
            if body.shape == "circle":
                geom = CylinderGeometry(self.cx, self.cy, body.diameter_mm / 2)
                z_ext = min(body.diameter_mm, self.ctx.cavity_depth)
                return [ScadFragment(
                    type="cutout",
                    geometry=geom,
                    z_base=CAVITY_START_MM,
                    depth=z_ext,
                    tilt_deg=90,
                    tilt_length=body.height_mm,
                    rotation_deg=self.rot + 90,
                    label=f"side slot — {self.cid}",
                )]
            else:
                geom = self._rect_geom(body.width_mm, body.height_mm)
                z_ext = min(body.length_mm, self.ctx.cavity_depth)
                return [ScadFragment(
                    type="cutout",
                    geometry=geom,
                    z_base=CAVITY_START_MM,
                    depth=z_ext,
                    label=f"side slot — {self.cid}",
                )]
        else:
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
        body_floor, body_top = self._z_range()
        pocket_depth = body_top - body_floor
        if body.shape == "circle":
            geom = CylinderGeometry(self.cx, self.cy, body.diameter_mm / 2)
        else:
            geom = self._rect_geom(body.width_mm, body.length_mm)
        return ScadFragment(
            type="cutout",
            geometry=geom,
            z_base=body_floor,
            depth=pocket_depth,
            label=f"body pocket — {self.cid}",
        )

    def _z_range(self) -> tuple[float, float]:
        """Return (body_floor_z, body_top_z) for this component."""
        style = self.placed.mounting_style or self.catalog.mounting.style
        return component_z_range(
            style,
            self.catalog.body.height_mm,
            self.catalog.pin_length_mm,
            self.ctx.ceil_start,
        )

    def _component_z_top(self) -> float:
        """Z where this component's body cutout ends."""
        style = self.placed.mounting_style or self.catalog.mounting.style
        if style == "side":
            body = self.catalog.body
            is_reoriented = self.catalog.mounting.style != "side"
            if is_reoriented:
                if body.shape == "circle":
                    z_ext = body.diameter_mm
                else:
                    z_ext = body.length_mm
                z_ext = min(z_ext, self.ctx.cavity_depth)
            else:
                z_ext = min(body.height_mm, self.ctx.cavity_depth)
            return CAVITY_START_MM + z_ext
        _, body_top = self._z_range()
        return body_top

    # ── Pinholes ───────────────────────────────────────────────────

    def _pinhole_fragments(self) -> list[ScadFragment]:
        """Pin shafts with smooth tapered funnel just below the body floor.

        Each pin gets up to two vertical zones:

          1. **Shaft** (FLOOR_MM → funnel_bottom): straight hole from
             the trace surface up to the start of the funnel.
          2. **Tapered funnel** (funnel_bottom → body_floor_z): smooth
             cone/pyramid widening downward for easy pin insertion.
             The funnel sits directly below the body pocket so the
             narrow end feeds into the cavity.  The funnel is clamped
             to the body pocket width so it does not cut into adjacent
             walls.

        Pin holes stop at body_floor_z because the body pocket itself
        provides the opening above that level.
        """
        frags: list[ScadFragment] = []
        body = self.catalog.body
        body_floor, _ = self._z_range()

        funnel_top = body_floor
        funnel_bottom = max(funnel_top - PINHOLE_TAPER_DEPTH, FLOOR_MM)
        actual_taper = funnel_top - funnel_bottom

        shaft_bottom = FLOOR_MM
        shaft_h = max(funnel_bottom - shaft_bottom, 0.0)

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
                shaft_w = (pin.shape.width_mm or pin_d) + PINHOLE_CLEARANCE
                shaft_h_dim = (pin.shape.length_mm or pin_d) + PINHOLE_CLEARANCE
            else:
                shaft_w = pin_d
                shaft_h_dim = pin_d

            if shaft_h > 0:
                frags.append(ScadFragment(
                    type="cutout",
                    geometry=RectGeometry(px, py, shaft_w, shaft_h_dim),
                    z_base=shaft_bottom,
                    depth=shaft_h,
                    label=f"pin {self.cid}:{pin.id}",
                ))

            if actual_taper > 0:
                extra = PINHOLE_TAPER_EXTRA
                scale_x = (shaft_w + extra) / shaft_w
                scale_y = (shaft_h_dim + extra) / shaft_h_dim
                taper = max(scale_x, scale_y)
                frags.append(ScadFragment(
                    type="cutout",
                    geometry=RectGeometry(px, py, shaft_w, shaft_h_dim),
                    z_base=funnel_bottom,
                    depth=actual_taper,
                    taper_scale=taper,
                    label=f"pin funnel {self.cid}:{pin.id}",
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
        body_floor, body_top = self._z_range()
        channel_depth = body_top - body_floor

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
                z_base=body_floor,
                depth=channel_depth,
                label=f"pin bridge {self.cid}:{pin.id}",
            ))

        return frags

    # ── Catalog scad_features ──────────────────────────────────────

    def _scad_feature_fragments(self) -> list[ScadFragment]:
        style = self.placed.mounting_style or self.catalog.mounting.style
        if style != self.catalog.mounting.style:
            return []
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
            pts = rotated_polygon(pts, self.rot, cx, cy)
            return PolygonGeometry(pts)
        return RectGeometry(cx, cy, w, h)


def resolve_component(
    placed: PlacedComponent,
    catalog: Component,
    ctx: ResolverContext,
) -> list[ScadFragment]:
    """Resolve SCAD fragments for a placed component."""
    return ComponentResolver(placed, catalog, ctx).resolve()

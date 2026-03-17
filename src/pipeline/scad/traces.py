"""Trace channel and standalone pinhole fragment builders.

Extracted from the old cutouts.py — these produce ScadFragment objects
for trace channels (one per routed segment) and are independent of
any particular component resolver.
"""

from __future__ import annotations

import math

from src.pipeline.config import FLOOR_MM, TRACE_HEIGHT_MM, CAVITY_START_MM
from src.pipeline.router.models import RoutingResult

from .fragment import ScadFragment, SegmentGeometry, RectGeometry, CapsuleGeometry

TRACE_WIDTH: float = 1.2
JUMPER_PAD_SIZE: float = 1.0
JUMPER_PAD_RADIUS: float = JUMPER_PAD_SIZE / 2
JUMPER_WIRE_WIDTH: float = 1.0


def build_trace_fragments(
    routing: RoutingResult,
    ceil_start: float,
) -> list[ScadFragment]:
    """Build trace channel fragments for every segment in every routed trace.

    Channels span the trace zone (FLOOR_MM → FLOOR_MM + TRACE_HEIGHT_MM).
    """
    channel_depth = TRACE_HEIGHT_MM
    frags: list[ScadFragment] = []

    for trace in routing.traces:
        path = trace.path
        if len(path) < 2:
            continue

        for i in range(len(path) - 1):
            x1, y1 = float(path[i][0]), float(path[i][1])
            x2, y2 = float(path[i + 1][0]), float(path[i + 1][1])

            seg_len = math.hypot(x2 - x1, y2 - y1)
            if seg_len < 1e-6:
                continue

            overshoot = TRACE_WIDTH / 2
            ux, uy = (x2 - x1) / seg_len, (y2 - y1) / seg_len
            ex1 = x1 - ux * overshoot
            ey1 = y1 - uy * overshoot
            ex2 = x2 + ux * overshoot
            ey2 = y2 + uy * overshoot

            frags.append(ScadFragment(
                type="cutout",
                geometry=SegmentGeometry(ex1, ey1, ex2, ey2, TRACE_WIDTH),
                z_base=FLOOR_MM,
                depth=channel_depth,
                label=f"trace {trace.net_id}",
            ))

    return frags


def build_jumper_fragments(
    routing: RoutingResult,
    ceil_start: float,
) -> list[ScadFragment]:
    """Build jumper wire cutouts: endpoint pinholes + wire channel.

    Endpoint pinholes run from FLOOR_MM up to ceil_start (full depth).
    The wire channel sits between the trace roof and the component floor
    (the jumper layer), giving the physical wire a place to lie flat.
    """
    shaft_h = ceil_start - FLOOR_MM
    trace_roof = FLOOR_MM + TRACE_HEIGHT_MM
    jumper_layer_depth = CAVITY_START_MM - trace_roof
    frags: list[ScadFragment] = []

    for j in routing.jumpers:
        # Endpoint pinholes
        for label, ep in [("start", j.start), ("end", j.end)]:
            if ep.pin_center is not None:
                frags.append(ScadFragment(
                    type="cutout",
                    geometry=CapsuleGeometry(
                        x1=ep.pin_center[0], y1=ep.pin_center[1],
                        r1=ep.pin_radius_mm,
                        x2=ep.x, y2=ep.y,
                        r2=JUMPER_PAD_RADIUS,
                    ),
                    z_base=FLOOR_MM,
                    depth=shaft_h,
                    label=f"jumper {j.net_id} {label} (capsule)",
                ))
            else:
                frags.append(ScadFragment(
                    type="cutout",
                    geometry=RectGeometry(ep.x, ep.y, JUMPER_PAD_SIZE, JUMPER_PAD_SIZE),
                    z_base=FLOOR_MM,
                    depth=shaft_h,
                    label=f"jumper {j.net_id} {label}",
                ))

        # Wire channel between trace roof and component floor
        sx, sy = j.start.x, j.start.y
        ex, ey = j.end.x, j.end.y
        seg_len = math.hypot(ex - sx, ey - sy)
        if seg_len > 1e-6 and jumper_layer_depth > 0:
            frags.append(ScadFragment(
                type="cutout",
                geometry=SegmentGeometry(sx, sy, ex, ey, JUMPER_WIRE_WIDTH),
                z_base=trace_roof,
                depth=jumper_layer_depth,
                label=f"jumper {j.net_id} wire channel",
            ))

    return frags

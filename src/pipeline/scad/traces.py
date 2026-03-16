"""Trace channel and standalone pinhole fragment builders.

Extracted from the old cutouts.py — these produce ScadFragment objects
for trace channels (one per routed segment) and are independent of
any particular component resolver.
"""

from __future__ import annotations

import math

from src.pipeline.config import FLOOR_MM, TRACE_HEIGHT_MM
from src.pipeline.router.models import RoutingResult

from .fragment import ScadFragment, SegmentGeometry, RectGeometry

TRACE_WIDTH: float = 1.2
JUMPER_PAD_SIZE: float = 1.0


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
    """Build pinhole cutouts at each jumper wire endpoint.

    Each endpoint gets a square shaft from FLOOR_MM up to ceil_start,
    matching the pin shaft depth of regular components.
    """
    shaft_h = ceil_start - FLOOR_MM
    frags: list[ScadFragment] = []

    for j in routing.jumpers:
        for label, (px, py) in [("start", j.start), ("end", j.end)]:
            frags.append(ScadFragment(
                type="cutout",
                geometry=RectGeometry(px, py, JUMPER_PAD_SIZE, JUMPER_PAD_SIZE),
                z_base=FLOOR_MM,
                depth=shaft_h,
                label=f"jumper {j.net_id} {label}",
            ))

    return frags

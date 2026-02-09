"""
Routability scoring — estimates how likely a placement is to route
successfully before invoking the (slow) A* trace router.

The main insight: on a single-layer PCB, two nets can only cross if
there is enough horizontal space at the crossing Y-level.  By scanning
vertical cross-sections we can identify bottleneck bands and predict
failures.

Usage
-----
    from src.pcb.routability import score_placement

    score, bottlenecks = score_placement(layout, outline)
    # score > 0  → likely routable
    # score <= 0 → bottlenecks list shows where and why
"""

from __future__ import annotations

import math
from typing import NamedTuple

from src.config.hardware import hw
from src.geometry.polygon import polygon_bounds, ensure_ccw, point_in_polygon


# ── Public types ────────────────────────────────────────────────────

class Bottleneck(NamedTuple):
    """A vertical band where the board is too narrow for the required traces."""
    y_mm: float
    board_width_mm: float
    body_width_mm: float
    available_mm: float
    required_mm: float
    crossing_nets: list[str]
    shortfall_mm: float         # available - required (negative = too narrow)


class PadInfo(NamedTuple):
    """A pad's world position and net assignment."""
    x: float
    y: float
    net: str
    component_id: str
    pin: str


# ── Internal helpers ────────────────────────────────────────────────

def _polygon_width_at_y(polygon: list[list[float]], y: float) -> float:
    """Horizontal span of *polygon* at scanline *y*."""
    xs: list[float] = []
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if y1 == y2:
            continue
        if min(y1, y2) <= y <= max(y1, y2):
            t = (y - y1) / (y2 - y1)
            xs.append(x1 + t * (x2 - x1))
    if len(xs) < 2:
        return 0.0
    return max(xs) - min(xs)


def _body_width_at_y(
    components: list[dict], y: float
) -> float:
    """Total horizontal body extent that blocks traces at scanline *y*."""
    total = 0.0
    for comp in components:
        ctype = comp.get("type")
        cx, cy = comp["center"]

        if ctype == "battery":
            bw = comp.get("body_width_mm", 0)
            bh = comp.get("body_height_mm", 0)
            if bw > 0 and bh > 0:
                # Body keepout extends a margin around the body
                keepout = (
                    math.ceil(hw.trace_width / (2 * hw.grid_resolution)) + 1
                ) * hw.grid_resolution
                if abs(y - cy) <= bh / 2 + keepout:
                    total += bw + 2 * keepout

        elif ctype == "controller":
            ctrl = hw.controller
            cw = ctrl["body_width_mm"]
            ch = ctrl["body_height_mm"]
            keepout = (
                math.ceil(hw.trace_width / (2 * hw.grid_resolution)) + 1
            ) * hw.grid_resolution
            if abs(y - cy) <= ch / 2 + keepout:
                total += cw + 2 * keepout

    return total


def _extract_pads(layout: dict) -> list[PadInfo]:
    """
    Replicate the TS router's pad extraction in Python so we can predict
    net topologies without calling the router.
    """
    pads: list[PadInfo] = []
    components = layout.get("components", [])

    buttons = [c for c in components if c.get("type") == "button"]
    diodes = [c for c in components if c.get("type") == "diode"]

    # Get pin assignments
    pin_map = hw.pin_assignments(len(buttons), len(diodes))
    # Rename generic SWn / LEDn to actual component IDs
    cp = hw.controller_pins
    btn_idx = 0
    diode_idx = 0
    for pin in cp["digital_order"]:
        net = pin_map.get(pin, "")
        if net.startswith("SW") and net.endswith("_SIG"):
            if btn_idx < len(buttons):
                pin_map[pin] = f"{buttons[btn_idx]['id']}_SIG"
                btn_idx += 1
        elif net.startswith("LED") and net.endswith("_SIG"):
            if diode_idx < len(diodes):
                pin_map[pin] = f"{diodes[diode_idx]['id']}_SIG"
                diode_idx += 1

    for comp in components:
        ctype = comp.get("type")
        cx, cy = comp["center"]
        cid = comp["id"]

        if ctype == "button":
            hx = hw.button["pin_spacing_x_mm"] / 2
            hy = hw.button["pin_spacing_y_mm"] / 2
            pads.append(PadInfo(cx - hx, cy - hy, f"{cid}_SIG", cid, "A1"))
            pads.append(PadInfo(cx + hx, cy - hy, "GND", cid, "B1"))
            # A2 and B2 are NC — skip

        elif ctype == "battery":
            bw = comp.get("body_width_mm", hw.battery["compartment_width_mm"])
            bh = comp.get("body_height_mm", hw.battery["compartment_height_mm"])
            body_keepout_cells = (
                math.ceil(hw.trace_width / (2 * hw.grid_resolution)) + 1
            )
            pad_offset = (body_keepout_cells + 5) * hw.grid_resolution
            pads.append(PadInfo(cx, cy + bh / 2 + pad_offset, "VCC", cid, "VCC"))
            pads.append(PadInfo(cx, cy - bh / 2 - pad_offset, "GND", cid, "GND"))

        elif ctype == "controller":
            row_spacing = hw.controller["row_spacing_mm"]
            pin_spacing = hw.controller["pin_spacing_mm"]
            pin_names = list(pin_map.keys())
            pin_count = len(pin_names)
            pins_per_side = math.ceil(pin_count / 2)
            total_height = (pins_per_side - 1) * pin_spacing
            for idx, pin_name in enumerate(pin_names):
                pin_number = idx + 1
                if pin_number <= pins_per_side:
                    px = cx - row_spacing / 2
                    py = cy - total_height / 2 + (pin_number - 1) * pin_spacing
                else:
                    px = cx + row_spacing / 2
                    right_idx = pin_count - pin_number
                    py = cy - total_height / 2 + right_idx * pin_spacing
                net = pin_map.get(pin_name, "NC")
                if net != "NC":
                    pads.append(PadInfo(px, py, net, cid, pin_name))

        elif ctype == "diode":
            hs = hw.diode["pad_spacing_mm"] / 2
            pads.append(PadInfo(cx - hs, cy, f"{cid}_SIG", cid, "A"))
            pads.append(PadInfo(cx + hs, cy, "GND", cid, "K"))

    return pads


def _group_by_net(pads: list[PadInfo]) -> dict[str, list[PadInfo]]:
    """Group pads by net name, ignoring NC."""
    nets: dict[str, list[PadInfo]] = {}
    for p in pads:
        if p.net == "NC":
            continue
        nets.setdefault(p.net, []).append(p)
    return nets


def _segments_cross(
    ax1: float, ay1: float, ax2: float, ay2: float,
    bx1: float, by1: float, bx2: float, by2: float,
) -> bool:
    """Check if segment (a1→a2) and segment (b1→b2) properly cross."""
    def cross(o: tuple, a: tuple, b: tuple) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    d1 = cross((bx1, by1), (bx2, by2), (ax1, ay1))
    d2 = cross((bx1, by1), (bx2, by2), (ax2, ay2))
    d3 = cross((ax1, ay1), (ax2, ay2), (bx1, by1))
    d4 = cross((ax1, ay1), (ax2, ay2), (bx2, by2))

    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
       ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True
    return False


def _crossing_y(
    ax1: float, ay1: float, ax2: float, ay2: float,
    bx1: float, by1: float, bx2: float, by2: float,
) -> float:
    """Y coordinate of the intersection of two segments (assuming they cross)."""
    dax = ax2 - ax1
    day = ay2 - ay1
    dbx = bx2 - bx1
    dby = by2 - by1
    denom = dax * dby - day * dbx
    if abs(denom) < 1e-12:
        return (ay1 + ay2 + by1 + by2) / 4
    t = ((bx1 - ax1) * dby - (by1 - ay1) * dbx) / denom
    return ay1 + t * day


# ── MST edges (Kruskal) ────────────────────────────────────────────

def _mst_edges(pads: list[PadInfo]) -> list[tuple[PadInfo, PadInfo]]:
    """Compute MST of pad positions (same algorithm the TS router uses)."""
    if len(pads) < 2:
        return []
    n = len(pads)
    edges: list[tuple[float, int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            d = abs(pads[i].x - pads[j].x) + abs(pads[i].y - pads[j].y)
            edges.append((d, i, j))
    edges.sort()
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    result: list[tuple[PadInfo, PadInfo]] = []
    for _, i, j in edges:
        pi, pj = find(i), find(j)
        if pi != pj:
            parent[pi] = pj
            result.append((pads[i], pads[j]))
            if len(result) == n - 1:
                break
    return result


# ── Public API ──────────────────────────────────────────────────────

def detect_crossings(layout: dict) -> list[dict]:
    """
    Detect net-pair crossings in the straight-line MST topology.

    Returns a list of crossing dicts:
        {"net_a", "net_b", "y_mm"}
    """
    pads = _extract_pads(layout)
    net_groups = _group_by_net(pads)

    # Build MST edges per net
    net_edges: dict[str, list[tuple[PadInfo, PadInfo]]] = {}
    for net_name, net_pads in net_groups.items():
        if len(net_pads) >= 2:
            net_edges[net_name] = _mst_edges(net_pads)

    crossings: list[dict] = []
    net_names = list(net_edges.keys())
    for i in range(len(net_names)):
        for j in range(i + 1, len(net_names)):
            na, nb = net_names[i], net_names[j]
            for ea in net_edges[na]:
                for eb in net_edges[nb]:
                    if _segments_cross(
                        ea[0].x, ea[0].y, ea[1].x, ea[1].y,
                        eb[0].x, eb[0].y, eb[1].x, eb[1].y,
                    ):
                        y = _crossing_y(
                            ea[0].x, ea[0].y, ea[1].x, ea[1].y,
                            eb[0].x, eb[0].y, eb[1].x, eb[1].y,
                        )
                        crossings.append({
                            "net_a": na,
                            "net_b": nb,
                            "y_mm": round(y, 1),
                        })
    return crossings


def score_placement(
    layout: dict,
    outline: list[list[float]],
) -> tuple[float, list[Bottleneck]]:
    """
    Estimate the routability of *layout* on *outline*.

    Returns ``(score, bottlenecks)`` where:

    * **score > 0** → placement is likely routable.
    * **score ≤ 0** → one or more bottleneck bands are too narrow.
    * **bottlenecks** lists every band where the available width falls
      short of the minimum required for the crossing traces.

    Algorithm
    ---------
    Divide the board into horizontal bands every ``band_step`` mm.
    For each band, count how many distinct nets need routing channels
    that pass through (i.e., have pads both above and below the band).
    Subtract the horizontal extent of component bodies.  The remaining
    width must accommodate the required trace corridors.
    """
    ccw = ensure_ccw(outline)
    _, min_y, _, max_y = polygon_bounds(ccw)

    pads = _extract_pads(layout)
    net_groups = _group_by_net(pads)
    components = layout.get("components", [])

    edge_clearance = hw.edge_clearance
    trace_corridor = hw.trace_width + hw.trace_clearance  # 3.5 mm per trace

    band_step = 2.0  # mm between scan lines
    bottlenecks: list[Bottleneck] = []

    # For each net, precompute its Y extent (min pad Y, max pad Y).
    net_y_extents: dict[str, tuple[float, float]] = {}
    for net_name, net_pads in net_groups.items():
        if len(net_pads) < 2:
            continue
        ys = [p.y for p in net_pads]
        net_y_extents[net_name] = (min(ys), max(ys))

    # Scan from bottom to top
    min_margin = float("inf")
    y = min_y + band_step
    while y < max_y - band_step:
        board_w = _polygon_width_at_y(ccw, y)
        body_w = _body_width_at_y(components, y)
        available = board_w - 2 * edge_clearance - body_w

        # Count nets that need to cross this Y band
        crossing: list[str] = []
        for net_name, (ny_min, ny_max) in net_y_extents.items():
            if ny_min < y - band_step and ny_max > y + band_step:
                crossing.append(net_name)

        if not crossing:
            y += band_step
            continue

        required = len(crossing) * trace_corridor
        margin = available - required
        if margin < min_margin:
            min_margin = margin

        if margin < 0:
            bottlenecks.append(Bottleneck(
                y_mm=round(y, 1),
                board_width_mm=round(board_w, 1),
                body_width_mm=round(body_w, 1),
                available_mm=round(available, 1),
                required_mm=round(required, 1),
                crossing_nets=crossing,
                shortfall_mm=round(margin, 1),
            ))

        y += band_step

    # Overall score: the tightest margin across all bands.
    # Positive = likely routable, negative = bottleneck.
    score = min_margin if min_margin != float("inf") else 100.0

    # Deduplicate bottlenecks — keep only the worst per 10 mm band
    if bottlenecks:
        bottlenecks.sort(key=lambda b: b.shortfall_mm)
        deduped: list[Bottleneck] = []
        used_bands: set[int] = set()
        for b in bottlenecks:
            band_key = int(b.y_mm // 10)
            if band_key not in used_bands:
                deduped.append(b)
                used_bands.add(band_key)
        bottlenecks = deduped[:5]  # top 5 worst

    # Add crossing penalty — each crossing pair makes routing harder
    crossings = detect_crossings(layout)
    crossing_penalty = len(crossings) * 2.0
    score -= crossing_penalty

    return (round(score, 2), bottlenecks)


def format_feedback(
    bottlenecks: list[Bottleneck],
    crossings: list[dict] | None = None,
    tried_placements: int = 1,
    best_routed: int = 0,
    total_nets: int = 0,
) -> dict:
    """
    Build a structured feedback dict that the LLM can act on.

    Returns machine-readable + human-readable descriptions of exactly
    what is wrong and what the designer should change.
    """
    problems: list[dict] = []

    for b in bottlenecks:
        problems.append({
            "type": "narrow_section",
            "y_mm": b.y_mm,
            "board_width_mm": b.board_width_mm,
            "body_width_mm": b.body_width_mm,
            "available_width_mm": b.available_mm,
            "traces_needed": len(b.crossing_nets),
            "width_needed_mm": b.required_mm,
            "shortfall_mm": abs(b.shortfall_mm),
            "nets": b.crossing_nets,
            "description": (
                f"At y={b.y_mm:.0f}mm the board is {b.board_width_mm:.0f}mm wide "
                f"but {len(b.crossing_nets)} traces need {b.required_mm:.0f}mm. "
                f"Widen by at least {abs(b.shortfall_mm):.0f}mm at this height."
            ),
        })

    if crossings:
        for c in crossings[:3]:
            problems.append({
                "type": "net_crossing",
                "net_a": c["net_a"],
                "net_b": c["net_b"],
                "y_mm": c["y_mm"],
                "description": (
                    f"Nets {c['net_a']} and {c['net_b']} must cross near "
                    f"y={c['y_mm']:.0f}mm — extra routing space needed there."
                ),
            })

    suggestion_parts: list[str] = []
    if bottlenecks:
        worst = min(bottlenecks, key=lambda b: b.shortfall_mm)
        suggestion_parts.append(
            f"The outline is too narrow at y≈{worst.y_mm:.0f}mm. "
            f"Widen it by at least {abs(worst.shortfall_mm):.0f}mm there."
        )
    if crossings and len(crossings) > 3:
        suggestion_parts.append(
            f"There are {len(crossings)} net crossings — consider repositioning "
            f"buttons so their signal traces don't cross GND connections."
        )
    if not suggestion_parts:
        suggestion_parts.append(
            "The board shape may be too narrow for the required components and traces."
        )

    return {
        "tried_placements": tried_placements,
        "best_routed": best_routed,
        "total_nets": total_nets,
        "bottlenecks": [
            {
                "y_mm": b.y_mm,
                "board_width_mm": b.board_width_mm,
                "available_width_mm": b.available_mm,
                "traces_needed": len(b.crossing_nets),
                "width_needed_mm": b.required_mm,
                "shortfall_mm": abs(b.shortfall_mm),
            }
            for b in bottlenecks[:3]
        ],
        "problems": problems,
        "suggestion": " ".join(suggestion_parts),
    }

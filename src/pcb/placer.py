"""
Component placer — places battery, controller, diode inside an arbitrary polygon.

Buttons are placed by the LLM designer; this module places everything else
by maximising the minimum clearance to all polygon edges AND all occupied
components.  A 1 mm grid scan evaluates every valid position and picks the
one with the most breathing room.
"""

from __future__ import annotations
import logging
import math
from typing import Optional

log = logging.getLogger("manufacturerAI.placer")

from src.config.hardware import hw
from src.geometry.polygon import (
    point_in_polygon,
    ensure_ccw,
    inset_polygon,
    polygon_bounds,
)


class PlacementError(Exception):
    """Raised when a component cannot be placed inside the outline.

    Attributes:
        component:  which component failed (e.g. "battery", "controller").
        dimensions: dict with the component's required footprint.
        available:  dict with the board's usable extents.
        occupied:   list of already-placed occupied rectangles.
        suggestion: human-readable hint for the designer LLM.
    """

    def __init__(
        self,
        component: str,
        dimensions: dict,
        available: dict,
        occupied: list[dict],
        suggestion: str,
    ) -> None:
        self.component = component
        self.dimensions = dimensions
        self.available = available
        self.occupied = occupied
        self.suggestion = suggestion
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        return (
            f"Cannot place {self.component} "
            f"({self.dimensions['width_mm']:.1f} x {self.dimensions['height_mm']:.1f} mm) "
            f"inside usable area "
            f"({self.available['width_mm']:.1f} x {self.available['height_mm']:.1f} mm). "
            f"{self.suggestion}"
        )

    def to_dict(self) -> dict:
        """Serialisable summary the pipeline can return to the LLM."""
        return {
            "component": self.component,
            "dimensions": self.dimensions,
            "available": self.available,
            "occupied_count": len(self.occupied),
            "suggestion": self.suggestion,
        }


# ── Main entry point ───────────────────────────────────────────────


def place_components(
    outline: list[list[float]],
    button_positions: list[dict],
    battery_type: str = "2xAAA",
) -> dict:
    """
    Place battery, controller, and diode inside *outline*, avoiding
    *button_positions*.

    Returns a pcb_layout dict with board + components.
    """
    ccw = ensure_ccw(outline)
    board_inset = inset_polygon(ccw, hw.wall_clearance)
    min_x, min_y, max_x, max_y = polygon_bounds(board_inset)
    board_width = max_x - min_x
    board_height = max_y - min_y

    components: list[dict] = []
    occupied: list[dict] = []  # {"cx","cy","hw","hh"} half-width/half-height

    margin = hw.component_margin

    # ── 1. Buttons (fixed from designer) ────────────────────────────
    for btn in button_positions:
        comp = {
            "id": btn["id"],
            "ref": btn["id"],
            "type": "button",
            "footprint": hw.button["switch_type"],
            "center": [btn["x"], btn["y"]],
            "rotation_deg": 0,
            "keepout": {
                "type": "circle",
                "radius_mm": hw.button["cap_diameter_mm"] / 2 + hw.button["keepout_padding_mm"],
            },
        }
        components.append(comp)
        occupied.append({
            "cx": btn["x"], "cy": btn["y"],
            "hw": hw.button["pin_spacing_x_mm"] / 2,
            "hh": hw.button["pin_spacing_y_mm"] / 2,
        })

    # ── 2. Battery compartment ──────────────────────────────────────
    bat_fp = hw.battery
    bat_w = bat_fp["compartment_width_mm"]
    bat_h = bat_fp["compartment_height_mm"]

    # Try placing in the bottom 40% first — the battery belongs at
    # the bottom of the remote.  Use a low bottleneck channel (3 mm)
    # because traces route above the battery, not alongside it.
    bat_pos, _ = _place_rect(
        board_inset, occupied,
        bat_w, bat_h, margin, prefer="bottom",
        prefer_weight=0.15,
        clearance_cap=3.0,
        y_zone=(0.0, 0.40),
        bottleneck_channel=3.0,
    )
    # Fallback: full board area if bottom 40% is too narrow
    if bat_pos is None:
        bat_pos, _ = _place_rect(
            board_inset, occupied,
            bat_w, bat_h, margin, prefer="bottom",
            prefer_weight=0.15,
            clearance_cap=3.0,
            bottleneck_channel=3.0,
        )
    if bat_pos is None:
        raise PlacementError(
            component="battery",
            dimensions={"width_mm": bat_w, "height_mm": bat_h},
            available={"width_mm": board_width, "height_mm": board_height},
            occupied=list(occupied),
            suggestion=(
                "The battery compartment requires a clear area of "
                f"{bat_w:.0f} x {bat_h:.0f} mm. "
                "Widen the outline or move buttons further apart "
                "to free enough contiguous space."
            ),
        )

    bx, by = bat_pos
    components.append({
        "id": "BAT1",
        "ref": "battery",
        "type": "battery",
        "footprint": battery_type,
        "center": [bx, by],
        "rotation_deg": 0,
        "body_width_mm": bat_w,
        "body_height_mm": bat_h,
        "keepout": {
            "type": "rectangle",
            "width_mm": bat_w,
            "height_mm": bat_h,
        },
    })
    occupied.append({"cx": bx, "cy": by, "hw": bat_w / 2, "hh": bat_h / 2})

    # ── 3. Controller ──────────────────────────────────────────────
    #      Smart placement: avoid putting the MC in the button Y-band
    #      and try both orientations (0° and 90°) to find the best fit.
    ctrl = hw.controller
    ctrl_w = ctrl["body_width_mm"]   # 10 mm (narrow side)
    ctrl_h = ctrl["body_height_mm"]  # 36 mm (long side)
    ctrl_pad = ctrl["keepout_padding_mm"]

    # Compute the button Y-band so we can penalise placement inside it.
    btn_band = _button_y_band(button_positions, margin)

    # Try both orientations: 0° (w×h) and 90° (h×w), keep best scoring.
    ctrl_pos, ctrl_rot = _place_rect_with_rotation(
        board_inset, occupied,
        ctrl_w, ctrl_h, margin,
        prefer="center",
        avoid_y_band=btn_band,
    )
    if ctrl_pos is None:
        raise PlacementError(
            component="controller",
            dimensions={"width_mm": ctrl_w, "height_mm": ctrl_h},
            available={"width_mm": board_width, "height_mm": board_height},
            occupied=list(occupied),
            suggestion=(
                "The micro-controller requires a clear area of "
                f"{ctrl_w:.0f} x {ctrl_h:.0f} mm. "
                "Widen the outline, make it taller, or reposition "
                "the buttons so there is an unobstructed strip "
                "beside them."
            ),
        )

    cx, cy = ctrl_pos
    # When rotated 90°, the keepout dimensions swap.
    if ctrl_rot == 90:
        ko_w = ctrl_h + ctrl_pad
        ko_h = ctrl_w + ctrl_pad
        occ_hw, occ_hh = ctrl_h / 2, ctrl_w / 2
    else:
        ko_w = ctrl_w + ctrl_pad
        ko_h = ctrl_h + ctrl_pad
        occ_hw, occ_hh = ctrl_w / 2, ctrl_h / 2

    components.append({
        "id": "U1",
        "ref": "controller",
        "type": "controller",
        "footprint": ctrl["type"],
        "center": [cx, cy],
        "rotation_deg": ctrl_rot,
        "keepout": {
            "type": "rectangle",
            "width_mm": ko_w,
            "height_mm": ko_h,
        },
    })
    occupied.append({"cx": cx, "cy": cy, "hw": occ_hw, "hh": occ_hh})

    # ── 4. Diode (IR LED) — at top center, facing outward ─────────
    d_diam = hw.diode["diameter_mm"]
    d_r = d_diam / 2 + 1.0  # keepout radius

    # Place at top center — the diode must face outward through the
    # end wall.  We scan downward from max_y until both pads have
    # sufficient perpendicular distance to the polygon boundary so
    # the router won't block them.  This handles capsule / rounded
    # outlines where the polygon narrows dramatically near the top.
    diode_x = (min_x + max_x) / 2
    pad_half = hw.diode.get("pad_spacing_mm", 5.0) / 2  # pad offset from center
    required_clearance = hw.edge_clearance + 2.5  # router blocks < edge_clearance; add margin
    diode_y = max_y - hw.edge_clearance  # starting guess
    # Walk downward in 0.5mm steps until both pads are safely inside
    for _step in range(200):
        pad_l_dist = _dist_to_polygon(diode_x - pad_half, diode_y, board_inset)
        pad_r_dist = _dist_to_polygon(diode_x + pad_half, diode_y, board_inset)
        if min(pad_l_dist, pad_r_dist) >= required_clearance:
            break
        diode_y -= 0.5
    diode_pos = (diode_x, diode_y)

    dx, dy = diode_pos
    components.append({
        "id": "D1",
        "ref": "DIODE",
        "type": "diode",
        "footprint": hw.diode["type"],
        "center": [dx, dy],
        "rotation_deg": 0,
        "keepout": {
            "type": "circle",
            "radius_mm": d_r,
        },
    })

    # ── Assemble layout ────────────────────────────────────────────
    return {
        "board": {
            "outline_polygon": [[v[0], v[1]] for v in board_inset],
            "thickness_mm": hw.pcb_thickness,
            "origin": "bottom_left",
        },
        "components": components,
        "keepout_regions": [],
        "metadata": {},
    }


# ── Placement core ─────────────────────────────────────────────────


def _point_seg_dist(
    px: float, py: float,
    x1: float, y1: float,
    x2: float, y2: float,
) -> float:
    """Distance from point (px, py) to line segment (x1,y1)-(x2,y2)."""
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _dist_to_polygon(px: float, py: float, polygon: list[list[float]]) -> float:
    """Minimum distance from a point to the polygon boundary."""
    n = len(polygon)
    return min(
        _point_seg_dist(
            px, py,
            polygon[i][0], polygon[i][1],
            polygon[(i + 1) % n][0], polygon[(i + 1) % n][1],
        )
        for i in range(n)
    )


def _rect_edge_clearance(
    cx: float, cy: float,
    hw2: float, hh2: float,
    polygon: list[list[float]],
) -> float:
    """Min distance from rect perimeter (corners + edge midpoints) to polygon boundary."""
    samples = [
        (cx - hw2, cy - hh2), (cx + hw2, cy - hh2),
        (cx + hw2, cy + hh2), (cx - hw2, cy + hh2),
        (cx, cy - hh2), (cx + hw2, cy),
        (cx, cy + hh2), (cx - hw2, cy),
    ]
    return min(_dist_to_polygon(px, py, polygon) for px, py in samples)


def _button_y_band(
    button_positions: list[dict],
    margin: float,
) -> tuple[float, float] | None:
    """Return (y_min, y_max) of the button band, expanded by margin.

    Returns None if there are no buttons.
    """
    if not button_positions:
        return None
    ys = [b["y"] for b in button_positions]
    # Include button keepout radius so band covers the full button area
    r = hw.button["cap_diameter_mm"] / 2 + hw.button["keepout_padding_mm"]
    return (min(ys) - r - margin, max(ys) + r + margin)


def _outline_width_at_y(polygon: list[list[float]], y: float) -> float:
    """X-span of the polygon at a given Y level via ray-casting."""
    n = len(polygon)
    xs: list[float] = []
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if y1 == y2:
            continue
        if (y1 <= y < y2) or (y2 <= y < y1):
            t = (y - y1) / (y2 - y1)
            xs.append(x1 + t * (x2 - x1))
    if len(xs) < 2:
        return 0.0
    return max(xs) - min(xs)


def _bottleneck_penalty(
    polygon: list[list[float]],
    cx: float, cy: float,
    hw2: float, hh2: float,
    min_channel: float = 10.0,
) -> float:
    """Penalise positions where the outline narrows around the component.

    Scans the outline width across the component's Y span and computes
    the minimum routing channel on either side, accounting for the
    router's edge clearance zone.  If the narrowest usable channel is
    less than *min_channel* mm, returns a penalty proportional to the
    deficit (2 pts per mm).

    This drives large components away from indents and bottlenecks,
    leaving routing channels open for traces to pass.
    """
    edge_clr = hw.edge_clearance  # router blocks this zone near walls
    comp_w = hw2 * 2
    worst_channel = float("inf")
    y = cy - hh2
    while y <= cy + hh2 + 0.01:
        outline_w = _outline_width_at_y(polygon, y)
        if outline_w > 0:
            usable_w = outline_w - 2 * edge_clr
            channel = (usable_w - comp_w) / 2
            if channel < worst_channel:
                worst_channel = channel
        y += 2.0
    if worst_channel < min_channel:
        return (min_channel - worst_channel) * 2.0
    return 0.0


def _y_overlap(cy: float, hh: float, band: tuple[float, float] | None) -> float:
    """How many mm of the rect [cy-hh, cy+hh] overlap with *band*.

    Returns 0 if band is None or there is no overlap.
    """
    if band is None:
        return 0.0
    lo = max(cy - hh, band[0])
    hi = min(cy + hh, band[1])
    return max(0.0, hi - lo)


def _place_rect_with_rotation(
    polygon: list[list[float]],
    occupied: list[dict],
    width: float,
    height: float,
    margin: float,
    prefer: str = "center",
    avoid_y_band: tuple[float, float] | None = None,
) -> tuple[tuple[float, float] | None, int]:
    """
    Try both 0° and 90° orientations and return the best position
    and rotation (0 or 90).  The *avoid_y_band* area is penalised.
    """
    best_pos: tuple[float, float] | None = None
    best_score = -1e18
    best_rot = 0

    for rot, w, h in [(0, width, height), (90, height, width)]:
        pos, score = _place_rect(
            polygon, occupied, w, h, margin,
            prefer=prefer, avoid_y_band=avoid_y_band,
        )
        if pos is not None and score > best_score:
            best_pos = pos
            best_score = score
            best_rot = rot

    return best_pos, best_rot


def _place_rect(
    polygon: list[list[float]],
    occupied: list[dict],
    width: float,
    height: float,
    margin: float,
    prefer: str = "center",
    step: float = 1.0,
    y_zone: tuple[float, float] | None = None,
    avoid_y_band: tuple[float, float] | None = None,
    prefer_weight: float = 0.01,
    clearance_cap: float | None = None,
    bottleneck_channel: float = 10.0,
) -> tuple[tuple[float, float] | None, float]:
    """
    Find the best position for a *width* × *height* rectangle inside
    *polygon*, maximising the minimum clearance to **both** polygon
    edges and occupied components.

    Parameters
    ----------
    polygon : inset polygon (the PCB boundary).
    occupied : list of {"cx","cy","hw","hh"} already-placed rects.
    width, height : component footprint size in mm.
    margin : minimum gap to any occupied rect.
    prefer : "bottom" / "top" / "center" — weak tiebreaker only;
             never overrides clearance.
    step : grid resolution in mm (default 1 mm).
    y_zone : optional (lo_frac, hi_frac) to restrict the Y scan range
             as a fraction of the polygon height, e.g. (0.75, 1.0)
             for the top quarter.  Falls through to None on no fit.
    avoid_y_band : optional (y_lo, y_hi) — penalise any position whose
             Y extent overlaps this band (used to keep the controller
             out of the button row).
    prefer_weight : strength of the directional preference (default
             0.01 = very weak tiebreaker; use 0.05+ to make the
             component strongly prefer the given direction).
    clearance_cap : if set, edge clearance beyond this threshold
             yields only 10% of its value.  This prevents large
             clearance at the center from overriding directional
             preference.
    bottleneck_channel : minimum routing channel (mm) beside the
             component before a penalty applies (default 10.0).
             Use a lower value for components that don't need
             side channels (e.g. battery = 3.0).

    Returns
    -------
    (best_position, best_score) — position is None if no fit found.
    """
    ccw = ensure_ccw(polygon)
    min_x, min_y, max_x, max_y = polygon_bounds(ccw)
    hw2, hh2 = width / 2, height / 2

    scan_y_min = min_y + hh2
    scan_y_max = max_y - hh2
    if y_zone is not None:
        range_y = max_y - min_y
        scan_y_min = max(scan_y_min, min_y + range_y * y_zone[0])
        scan_y_max = min(scan_y_max, min_y + range_y * y_zone[1])

    if scan_y_min > scan_y_max:
        return None, -1e18

    best: tuple[float, float] | None = None
    best_score = -1e18

    cx = min_x + hw2
    while cx <= max_x - hw2 + 0.01:
        cy = scan_y_min
        while cy <= scan_y_max + 0.01:
            # All four corners must be inside the polygon
            if not all(
                point_in_polygon(cx + dx, cy + dy, ccw)
                for dx in (-hw2, hw2)
                for dy in (-hh2, hh2)
            ):
                cy += step
                continue

            # No overlap with any occupied component (with margin)
            if any(
                abs(cx - o["cx"]) < hw2 + o["hw"] + margin
                and abs(cy - o["cy"]) < hh2 + o["hh"] + margin
                for o in occupied
            ):
                cy += step
                continue

            # ── Score: minimum clearance to edges AND components ───
            poly_dist = _rect_edge_clearance(cx, cy, hw2, hh2, ccw)

            if occupied:
                occ_dist = min(
                    max(
                        abs(cx - o["cx"]) - hw2 - o["hw"],
                        abs(cy - o["cy"]) - hh2 - o["hh"],
                    )
                    for o in occupied
                )
                score = min(poly_dist, occ_dist)
            else:
                score = poly_dist

            # Cap excessive edge clearance so it doesn't overwhelm
            # the directional preference.  Beyond the cap, extra
            # clearance only counts at 10%.
            if clearance_cap is not None and score > clearance_cap:
                score = clearance_cap + (score - clearance_cap) * 0.1

            # Penalise overlap with the button Y-band (strong).
            # Each mm of overlap costs 1.0 points — much stronger
            # than the directional tiebreaker so the placer will
            # strongly prefer positions outside the button band
            # but can still use it as a last resort.
            overlap = _y_overlap(cy, hh2, avoid_y_band)
            if overlap > 0:
                score -= overlap * 1.0

            # Penalise bottleneck positions — if the outline narrows
            # across the component's Y span, traces can't pass on the
            # sides.  Drives large components away from indents.
            bp = _bottleneck_penalty(ccw, cx, cy, hw2, hh2,
                                     min_channel=bottleneck_channel)
            score -= bp

            # Directional preference
            if prefer == "bottom":
                score -= (cy - min_y) * prefer_weight
            elif prefer == "top":
                score += (cy - min_y) * prefer_weight
            elif prefer == "center":
                center_x = (min_x + max_x) / 2
                center_y = (min_y + max_y) / 2
                score -= (abs(cy - center_y) + abs(cx - center_x)) * prefer_weight

            if score > best_score:
                best_score = score
                best = (cx, cy)

            cy += step
        cx += step

    return best, best_score


# ── Reporting ──────────────────────────────────────────────────────


def build_optimization_report(
    pcb_layout: dict,
    routing_result: dict | None,
    outline: list[list[float]],
) -> dict:
    """
    Build the optimization report sent back to the designer agent
    when components don't fit or routing fails.
    """
    problems: list[dict] = []
    placed: list[dict] = []
    ccw = ensure_ccw(outline)

    for comp in pcb_layout.get("components", []):
        cx, cy = comp["center"]
        inside = point_in_polygon(cx, cy, ccw)
        status = "placed" if inside else "failed"
        placed.append({
            "id": comp["id"],
            "type": comp.get("type", ""),
            "center": [cx, cy],
            "status": status,
        })
        if not inside:
            problems.append({
                "type": "component_outside_outline",
                "component_id": comp["id"],
                "description": f"{comp['id']} at ({cx:.1f}, {cy:.1f}) is outside the outline.",
                "suggestion": f"Widen the outline near y={cy:.0f}mm.",
            })

    routing_summary = {"total_nets": 0, "routed_nets": 0, "failed_nets": []}
    if routing_result:
        traces = routing_result.get("traces", [])
        failed = routing_result.get("failed_nets", [])
        routing_summary["total_nets"] = len(traces) + len(failed)
        routing_summary["routed_nets"] = len(traces)
        routing_summary["failed_nets"] = [f.get("netName", str(f)) if isinstance(f, dict) else str(f) for f in failed]
        for f in failed:
            net_name = f.get("netName", str(f)) if isinstance(f, dict) else str(f)
            problems.append({
                "type": "trace_failed",
                "component_id": net_name,
                "description": f"Failed to route net {net_name}.",
                "suggestion": "Widen the outline or adjust button positions to leave more routing space.",
            })

    feasible = len(problems) == 0
    return {
        "feasible": feasible,
        "problems": problems,
        "placed_components": placed,
        "routing_summary": routing_summary,
    }


# ── Multi-placement candidate generation ──────────────────────────


def generate_placement_candidates(
    outline: list[list[float]],
    button_positions: list[dict],
    battery_type: str = "2xAAA",
    max_candidates: int = 8,
) -> list[dict]:
    """
    Generate multiple placement layouts by trying different preference
    combinations for battery and controller placement.

    Each candidate uses a different directional preference, yielding
    varied positions that all maximise edge + component clearance.
    """
    ccw = ensure_ccw(outline)
    board_inset = inset_polygon(ccw, hw.wall_clearance)
    margin = hw.component_margin

    # Shared button components
    components_base: list[dict] = []
    occupied_base: list[dict] = []
    for btn in button_positions:
        components_base.append({
            "id": btn["id"],
            "ref": btn["id"],
            "type": "button",
            "footprint": hw.button["switch_type"],
            "center": [btn["x"], btn["y"]],
            "rotation_deg": 0,
            "keepout": {
                "type": "circle",
                "radius_mm": hw.button["cap_diameter_mm"] / 2 + hw.button["keepout_padding_mm"],
            },
        })
        occupied_base.append({
            "cx": btn["x"], "cy": btn["y"],
            "hw": hw.button["pin_spacing_x_mm"] / 2,
            "hh": hw.button["pin_spacing_y_mm"] / 2,
        })

    bat_fp = hw.battery
    bat_w = bat_fp["compartment_width_mm"]
    bat_h = bat_fp["compartment_height_mm"]
    ctrl = hw.controller
    ctrl_w = ctrl["body_width_mm"]
    ctrl_h = ctrl["body_height_mm"]
    ctrl_pad = ctrl["keepout_padding_mm"]

    d_diam = hw.diode["diameter_mm"]
    d_r = d_diam / 2 + 1.0

    battery_prefs = ["bottom", "center", "top"]
    controller_prefs = ["center", "bottom", "top"]

    btn_band = _button_y_band(button_positions, margin)

    candidates: list[dict] = []
    seen: set[tuple[int, int, int, int, int]] = set()

    for bpref in battery_prefs:
        bat_pos, _ = _place_rect(
            board_inset, occupied_base,
            bat_w, bat_h, margin, prefer=bpref,
        )
        if bat_pos is None:
            continue
        bx, by = bat_pos

        occupied_with_bat = list(occupied_base)
        occupied_with_bat.append({"cx": bx, "cy": by, "hw": bat_w / 2, "hh": bat_h / 2})

        for cpref in controller_prefs:
            # Try both orientations via _place_rect_with_rotation
            ctrl_pos, ctrl_rot = _place_rect_with_rotation(
                board_inset, occupied_with_bat,
                ctrl_w, ctrl_h, margin, prefer=cpref,
                avoid_y_band=btn_band,
            )
            if ctrl_pos is None:
                continue
            cx, cy = ctrl_pos

            if ctrl_rot == 90:
                occ_hw, occ_hh = ctrl_h / 2, ctrl_w / 2
                ko_w = ctrl_h + ctrl_pad
                ko_h = ctrl_w + ctrl_pad
            else:
                occ_hw, occ_hh = ctrl_w / 2, ctrl_h / 2
                ko_w = ctrl_w + ctrl_pad
                ko_h = ctrl_h + ctrl_pad

            # Dedup on 2mm grid (include rotation)
            key = (int(bx / 2), int(by / 2), int(cx / 2), int(cy / 2), ctrl_rot)
            if key in seen:
                continue
            seen.add(key)

            occupied_all = list(occupied_with_bat)
            occupied_all.append({"cx": cx, "cy": cy, "hw": occ_hw, "hh": occ_hh})

            # Diode — at top center; scan down until pads clear edge zone
            dmin_x, _, dmax_x, dmax_y = polygon_bounds(board_inset)
            diode_cx = (dmin_x + dmax_x) / 2
            d_pad_half = hw.diode.get("pad_spacing_mm", 5.0) / 2
            d_req_clr = hw.edge_clearance + 2.5
            ddy = dmax_y - hw.edge_clearance
            for _ds in range(200):
                dl = _dist_to_polygon(diode_cx - d_pad_half, ddy, board_inset)
                dr = _dist_to_polygon(diode_cx + d_pad_half, ddy, board_inset)
                if min(dl, dr) >= d_req_clr:
                    break
                ddy -= 0.5
            ddx = diode_cx

            comps = list(components_base)
            comps.append({
                "id": "BAT1", "ref": "battery", "type": "battery",
                "footprint": battery_type,
                "center": [bx, by], "rotation_deg": 0,
                "body_width_mm": bat_w, "body_height_mm": bat_h,
                "keepout": {"type": "rectangle", "width_mm": bat_w, "height_mm": bat_h},
            })
            comps.append({
                "id": "U1", "ref": "controller", "type": "controller",
                "footprint": ctrl["type"],
                "center": [cx, cy], "rotation_deg": ctrl_rot,
                "keepout": {
                    "type": "rectangle",
                    "width_mm": ko_w,
                    "height_mm": ko_h,
                },
            })
            comps.append({
                "id": "D1", "ref": "DIODE", "type": "diode",
                "footprint": hw.diode["type"],
                "center": [ddx, ddy], "rotation_deg": 0,
                "keepout": {"type": "circle", "radius_mm": d_r},
            })

            layout = {
                "board": {
                    "outline_polygon": [[v[0], v[1]] for v in board_inset],
                    "thickness_mm": hw.pcb_thickness,
                    "origin": "bottom_left",
                },
                "components": comps,
                "keepout_regions": [],
                "metadata": {
                    "battery_prefer": bpref,
                    "controller_prefer": cpref,
                    "controller_rotation": ctrl_rot,
                },
            }
            candidates.append(layout)
            if len(candidates) >= max_candidates:
                return candidates

    return candidates


# ── Optimal placement (maximize spacing) ───────────────────────────


def _component_half_extents(comp: dict) -> tuple[float, float]:
    """Return (half_width, half_height) based on the component's keepout."""
    ko = comp.get("keepout", {})
    if ko.get("type") == "rectangle":
        return ko["width_mm"] / 2, ko["height_mm"] / 2
    elif ko.get("type") == "circle":
        r = ko["radius_mm"]
        return r, r
    return 2.0, 2.0


def _score_layout_spacing(
    layout: dict,
    button_positions: list[dict] | None = None,
) -> tuple[float, float]:
    """
    Score a layout by how well-spaced the components are.

    Computes the gap between every component and the polygon boundary,
    and between every pair of components.  Returns
    ``(min_gap, mean_gap, mc_button_bonus)``.

    *  Higher ``min_gap``  → components are further from the tightest
       constraint (edge or neighbour).
    *  Higher ``mean_gap`` (as tiebreaker) → gaps are more uniformly
       distributed, meaning no single component is squeezed.
    *  Higher ``mc_button_bonus`` (final tiebreaker) → the controller
       is closer to the buttons than the battery is, which is
       preferred for shorter traces.  Computed as the difference
       ``battery_dist − controller_dist`` so that layouts where the
       MC is closer to the buttons score higher.
    """
    polygon = layout["board"]["outline_polygon"]
    components = layout["components"]
    gaps: list[float] = []

    # Component-to-edge gaps
    for comp in components:
        cx, cy = comp["center"]
        hw2, hh2 = _component_half_extents(comp)
        edge_gap = _rect_edge_clearance(cx, cy, hw2, hh2, polygon)
        gaps.append(edge_gap)

    # Pairwise component-to-component gaps
    for i in range(len(components)):
        a = components[i]
        ax, ay = a["center"]
        a_hw, a_hh = _component_half_extents(a)
        for j in range(i + 1, len(components)):
            b = components[j]
            bx, by = b["center"]
            b_hw, b_hh = _component_half_extents(b)
            gap = max(
                abs(ax - bx) - a_hw - b_hw,
                abs(ay - by) - a_hh - b_hh,
            )
            gaps.append(gap)

    min_gap = min(gaps) if gaps else 0.0
    mean_gap = sum(gaps) / len(gaps) if gaps else 0.0

    # MC-closer-to-buttons bonus — folded into mean_gap so it can
    # tip the balance between candidates with similar spacing.
    # Weight of 0.3 means each mm of MC-closer-than-battery advantage
    # is worth 0.3 mm of mean_gap.  This is enough to prefer MC-near-
    # buttons when spacing is comparable, but won't override a layout
    # that has genuinely better clearance.
    mc_bonus = 0.0
    if button_positions:
        btn_cx = sum(b["x"] for b in button_positions) / len(button_positions)
        btn_cy = sum(b["y"] for b in button_positions) / len(button_positions)

        ctrl_dist = None
        bat_dist = None
        for comp in components:
            cx, cy = comp["center"]
            if comp.get("type") == "controller":
                ctrl_dist = math.hypot(cx - btn_cx, cy - btn_cy)
            elif comp.get("type") == "battery":
                bat_dist = math.hypot(cx - btn_cx, cy - btn_cy)

        if ctrl_dist is not None and bat_dist is not None:
            # Positive when battery is further from buttons than MC
            mc_bonus = bat_dist - ctrl_dist

    # min_gap stays untouched — never sacrifice worst-case clearance.
    # MC proximity only boosts mean_gap (secondary sort) with a strong
    # additive bonus so it can differentiate between candidates that
    # have similar min_gap values.  2mm bonus per mm of advantage.
    MC_PROXIMITY_WEIGHT = 2.0
    adjusted_mean = mean_gap + mc_bonus * MC_PROXIMITY_WEIGHT

    return min_gap, adjusted_mean


def place_components_optimal(
    outline: list[list[float]],
    button_positions: list[dict],
    battery_type: str = "2xAAA",
) -> dict | None:
    """
    Find the single placement that maximizes the minimum gap between
    all components and the polygon boundary, optimizing for the most
    equal spacing throughout.

    Generates varied placement candidates (different battery/controller
    position preferences) and picks the one with the best global
    spacing score.  This is fast — pure geometry, no routing.
    """
    candidates = generate_placement_candidates(
        outline, button_positions,
        battery_type=battery_type,
        max_candidates=50,
    )

    if not candidates:
        # Fallback to greedy sequential placement
        try:
            return place_components(outline, button_positions, battery_type)
        except PlacementError:
            return None

    best_layout = None
    best_score = (-1e18, -1e18)

    for layout in candidates:
        score = _score_layout_spacing(layout, button_positions)
        if score > best_score:
            best_score = score
            best_layout = layout

    log.info(
        "Optimal placement: min_gap=%.1f mm, adjusted_mean=%.1f mm "
        "(from %d candidates)",
        best_score[0], best_score[1], len(candidates),
    )
    return best_layout

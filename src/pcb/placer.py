"""
Component placer — places battery, controller, diode inside an arbitrary polygon.

Buttons are placed by the LLM designer; this module places everything else
by maximising the minimum clearance to all polygon edges AND all occupied
components.  A 1 mm grid scan evaluates every valid position and picks the
one with the most breathing room.
"""

from __future__ import annotations
import math
from typing import Optional

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

    bat_pos, _ = _place_rect(
        board_inset, occupied,
        bat_w, bat_h, margin, prefer="bottom",
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

    # ── 4. Diode (IR LED) — prefer top, but respect clearances ────
    d_diam = hw.diode["diameter_mm"]
    d_r = d_diam / 2 + 1.0  # keepout radius

    # Try placing in the top 25% first, fall back to full board
    diode_pos, _ = _place_rect(
        board_inset, occupied,
        d_diam, d_diam, margin, prefer="top", y_zone=(0.75, 1.0),
    )
    if diode_pos is None:
        diode_pos, _ = _place_rect(
            board_inset, occupied,
            d_diam, d_diam, margin, prefer="top",
        )
    if diode_pos is None:
        # Last resort: polygon center
        diode_pos = ((min_x + max_x) / 2, (min_y + max_y) / 2)

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

            # Penalise overlap with the button Y-band (strong).
            # Each mm of overlap costs 1.0 points — much stronger
            # than the directional tiebreaker (0.01/mm) so the
            # placer will strongly prefer positions outside the
            # button band but can still use it as a last resort.
            overlap = _y_overlap(cy, hh2, avoid_y_band)
            if overlap > 0:
                score -= overlap * 1.0

            # Weak directional tiebreaker (never overrides clearance)
            if prefer == "bottom":
                score -= (cy - min_y) * 0.01
            elif prefer == "top":
                score += (cy - min_y) * 0.01
            elif prefer == "center":
                center_x = (min_x + max_x) / 2
                center_y = (min_y + max_y) / 2
                score -= (abs(cy - center_y) + abs(cx - center_x)) * 0.01

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

            # Diode — prefer top zone, then full board
            diode_pos, _ = _place_rect(
                board_inset, occupied_all,
                d_diam, d_diam, margin, prefer="top", y_zone=(0.75, 1.0),
            )
            if diode_pos is None:
                diode_pos, _ = _place_rect(
                    board_inset, occupied_all,
                    d_diam, d_diam, margin, prefer="top",
                )
            if diode_pos is None:
                min_x, min_y, max_x, max_y = polygon_bounds(board_inset)
                diode_pos = ((min_x + max_x) / 2, (min_y + max_y) / 2)

            ddx, ddy = diode_pos

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

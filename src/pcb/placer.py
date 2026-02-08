"""
Component placer — places battery, controller, diode inside an arbitrary polygon.

Buttons are placed by the LLM designer; this module places everything else
using a grid-scan approach that respects the polygon boundary and avoids
button positions.
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


def place_components(
    outline: list[list[float]],
    button_positions: list[dict],
    battery_type: str = "2xAAA",
) -> dict:
    """
    Place battery, controller, and diode inside *outline*, avoiding *button_positions*.

    Returns a pcb_layout dict with board + components.
    """
    ccw = ensure_ccw(outline)
    board_inset = inset_polygon(ccw, hw.wall_clearance)
    min_x, min_y, max_x, max_y = polygon_bounds(board_inset)
    board_width = max_x - min_x
    board_height = max_y - min_y

    components: list[dict] = []
    occupied: list[dict] = []  # {"cx","cy","hw","hh"} half-width/half-height rects

    margin = hw.component_margin  # clearance between components (mm)

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
        # Occupied rect = actual pin extents (no extra padding; the
        # shared `margin` in _grid_scan_best handles clearance).
        occupied.append({
            "cx": btn["x"], "cy": btn["y"],
            "hw": hw.button["pin_spacing_x_mm"] / 2,
            "hh": hw.button["pin_spacing_y_mm"] / 2,
        })

    # ── 2. Battery ──────────────────────────────────────────────────
    bat_fp = hw.battery
    bat_w = bat_fp["holder_width_mm"]
    bat_h = bat_fp["holder_height_mm"]
    bat_pad = bat_fp["holder_padding_mm"]

    # Use body half-size + component margin for scan (not holder_padding)
    bat_scan_hw = bat_w / 2 + margin
    bat_scan_hh = bat_h / 2 + margin

    bat_pos = _grid_scan_best(
        board_inset, occupied,
        half_w=bat_scan_hw, half_h=bat_scan_hh,
        prefer="bottom", step=2.0, margin=margin,
    )
    if not bat_pos:
        # Retry with finer step and tighter margin
        bat_pos = _grid_scan_best(
            board_inset, occupied,
            half_w=bat_w / 2, half_h=bat_h / 2,
            prefer="bottom", step=1.0, margin=0.5,
        )
    if bat_pos:
        bx, by = bat_pos
    else:
        raise PlacementError(
            component="battery",
            dimensions={"width_mm": bat_w, "height_mm": bat_h},
            available={"width_mm": board_width, "height_mm": board_height},
            occupied=list(occupied),
            suggestion=(
                "The battery holder requires a clear area of "
                f"{bat_w:.0f} x {bat_h:.0f} mm. "
                "Widen the outline or move buttons further apart "
                "to free enough contiguous space."
            ),
        )

    components.append({
        "id": "BAT1",
        "ref": "battery",
        "type": "battery",
        "footprint": battery_type,
        "center": [bx, by],
        "rotation_deg": 0,
        "keepout": {
            "type": "rectangle",
            "width_mm": bat_w + bat_pad * 2,
            "height_mm": bat_h + bat_pad * 2,
        },
    })
    occupied.append({"cx": bx, "cy": by, "hw": bat_w / 2 + bat_pad, "hh": bat_h / 2 + bat_pad})

    # ── 3. Controller ──────────────────────────────────────────────
    ctrl = hw.controller
    ctrl_w = ctrl["body_width_mm"]
    ctrl_h = ctrl["body_height_mm"]
    ctrl_pad = ctrl["keepout_padding_mm"]

    # Use body half-size + component margin (not the full keepout_padding
    # which double-counts when combined with occupied rects' own extents).
    ctrl_scan_hw = ctrl_w / 2 + margin
    ctrl_scan_hh = ctrl_h / 2 + margin

    ctrl_pos = _grid_scan_best(
        board_inset, occupied,
        half_w=ctrl_scan_hw, half_h=ctrl_scan_hh,
        prefer="center", step=2.0, margin=margin,
    )
    if not ctrl_pos:
        # Retry with finer step and tighter margin
        ctrl_pos = _grid_scan_best(
            board_inset, occupied,
            half_w=ctrl_w / 2, half_h=ctrl_h / 2,
            prefer="center", step=1.0, margin=0.5,
        )
    if ctrl_pos:
        cx, cy = ctrl_pos
    else:
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

    components.append({
        "id": "U1",
        "ref": "controller",
        "type": "controller",
        "footprint": ctrl["type"],
        "center": [cx, cy],
        "rotation_deg": 0,
        "keepout": {
            "type": "rectangle",
            "width_mm": ctrl_w + ctrl_pad,
            "height_mm": ctrl_h + ctrl_pad,
        },
    })
    # Occupied entry: body half-size + margin (sufficient for overlap detection).
    occupied.append({"cx": cx, "cy": cy, "hw": ctrl_scan_hw, "hh": ctrl_scan_hh})

    # ── 4. Diode (IR LED) at top edge ──────────────────────────────
    d_diam = hw.diode["diameter_mm"]
    d_pad_spacing = hw.diode["pad_spacing_mm"]
    shell_clearance = 5.0  # diode bounding box must be ≥5 mm inside the shell wall

    # Half-extents of the rectangle that must stay fully inside the outline.
    # X: half the diameter (body) + clearance.
    # Y: half the pad spacing (pads extend ±pad_spacing/2 from center) + clearance.
    diode_hw = d_diam / 2 + shell_clearance
    diode_hh = d_pad_spacing / 2 + shell_clearance

    diode_pos = _find_top_edge_center(
        ccw,  # original outline = shell wall
        half_w=diode_hw,
        half_h=diode_hh,
    )
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
            "radius_mm": d_diam / 2 + 1.0,
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


# ── Placement helpers ──────────────────────────────────────────────


def _grid_scan_best(
    polygon: list[list[float]],
    occupied: list[dict],
    half_w: float,
    half_h: float,
    prefer: str = "center",
    step: float = 2.0,
    margin: float = 1.0,
) -> tuple[float, float] | None:
    """
    Scan a coarse grid inside *polygon*, find best position for a component
    of size 2*half_w × 2*half_h that does not overlap *occupied* rects.

    *margin* is the required clearance (mm) between two components'
    footprint extents.  Using a single shared margin avoids double-
    counting when both the new component and existing occupied entries
    already include padding.
    """
    min_x, min_y, max_x, max_y = polygon_bounds(polygon)
    ccw = ensure_ccw(polygon)
    best: tuple[float, float] | None = None
    best_score = -1e18

    x = min_x + half_w
    while x <= max_x - half_w:
        y = min_y + half_h
        while y <= max_y - half_h:
            # all four corners inside polygon?
            corners_ok = all(
                point_in_polygon(x + dx, y + dy, ccw)
                for dx in (-half_w, half_w)
                for dy in (-half_h, half_h)
            )
            if not corners_ok:
                y += step
                continue

            # no overlap with occupied (using shared margin)
            overlaps = False
            for occ in occupied:
                if (abs(x - occ["cx"]) < half_w + occ["hw"] + margin and
                        abs(y - occ["cy"]) < half_h + occ["hh"] + margin):
                    overlaps = True
                    break
            if overlaps:
                y += step
                continue

            # scoring
            score = 0.0
            if prefer == "bottom":
                score = -(y - min_y)  # minimize y (lower is better)
            elif prefer == "center":
                mid_x = (min_x + max_x) / 2
                mid_y = (min_y + max_y) / 2
                score = -(abs(x - mid_x) + abs(y - mid_y))

            # bonus: distance from occupied
            min_dist = min(
                (math.hypot(x - occ["cx"], y - occ["cy"]) for occ in occupied),
                default=100,
            )
            score += min_dist * 0.5

            if score > best_score:
                best_score = score
                best = (x, y)

            y += step
        x += step

    return best


def _polygon_width_at_y(polygon: list[list[float]], y: float) -> float:
    """Return the horizontal span of *polygon* at a given *y* level.

    Finds all edge intersections at the scanline and returns
    max_x − min_x.  Returns 0 if no intersections are found.
    """
    xs: list[float] = []
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if y1 == y2:
            # Horizontal edge — skip (or both endpoints contribute)
            continue
        if min(y1, y2) <= y <= max(y1, y2):
            t = (y - y1) / (y2 - y1)
            xs.append(x1 + t * (x2 - x1))
    if len(xs) < 2:
        return 0.0
    return max(xs) - min(xs)


def _find_top_edge_center(
    polygon: list[list[float]],
    half_w: float = 7.5,
    half_h: float = 7.5,
) -> tuple[float, float]:
    """Find the highest point near the top-center where a rectangle fits.

    Places a rectangle of size ``2*half_w × 2*half_h`` centered on the
    polygon's horizontal midpoint, as high as possible while keeping all
    four corners strictly inside *polygon*.

    This guarantees the diode (plus its clearance rectangle) never
    intersects the outline edges, even for pointed or curved shapes.
    """
    ccw = ensure_ccw(polygon)
    max_y = max(v[1] for v in ccw)
    min_y = min(v[1] for v in ccw)

    # Horizontal center: average X of vertices near the top
    top_verts = [v for v in ccw if v[1] >= max_y - 1.0]
    center_x = sum(v[0] for v in top_verts) / len(top_verts)

    # Scan downward from 1 mm below the top until the full rectangle fits
    y = max_y - half_h - 1.0  # start with top edge of rect 1mm below apex
    while y > min_y + half_h:
        # Check all four corners
        if all(
            point_in_polygon(center_x + dx, y + dy, ccw)
            for dx in (-half_w, half_w)
            for dy in (-half_h, half_h)
        ):
            return (center_x, y)
        y -= 0.5

    # Last resort: centroid
    cx = sum(v[0] for v in ccw) / len(ccw)
    cy = sum(v[1] for v in ccw) / len(ccw)
    return (cx, cy)


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

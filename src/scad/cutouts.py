"""
Build ``Cutout`` polygons from PCB layout and routing data.

Converts component keepouts, trace paths, and pad positions into
generic polygon cutouts that ``shell.generate_enclosure_scad`` subtracts
from the solid enclosure body.

Cross-section (bottom to top):
  0 – 2 mm          solid floor (no cuts)
  2 – 3 mm          pinholes only
  3 – (h − 2) mm    pinholes + traces + component pockets
  (h − 2) – h mm    solid ceiling, except circular button holes

Coordinate note:
  The router normalises coords by subtracting the pcb_layout outline
  minimum.  When converting grid positions back to mm we must add that
  offset so the cutouts align with the SCAD outline (which uses the
  pipeline's own normalised outline — identical to pcb_layout coords).

No ``hull()``, no cylinders — just ``polygon()`` + ``linear_extrude``.
"""

from __future__ import annotations

import math
from typing import Sequence

from src.config.hardware import hw
from src.scad.shell import Cutout, DEFAULT_HEIGHT_MM
from src.geometry.polygon import polygon_bounds


# ── geometry helpers ────────────────────────────────────────────────


def _rect(cx: float, cy: float, w: float, h: float) -> list[list[float]]:
    """CCW rectangle centred on *(cx, cy)*."""
    hw2, hh = w / 2, h / 2
    return [
        [cx - hw2, cy - hh],
        [cx + hw2, cy - hh],
        [cx + hw2, cy + hh],
        [cx - hw2, cy + hh],
    ]


def _circle_poly(cx: float, cy: float, r: float, n: int = 16) -> list[list[float]]:
    """Approximate a circle as an *n*-gon (CCW)."""
    return [
        [cx + r * math.cos(2 * math.pi * i / n),
         cy + r * math.sin(2 * math.pi * i / n)]
        for i in range(n)
    ]


def _simplify_path(path: list[dict]) -> list[dict]:
    """Collapse grid-step path into corners only."""
    if len(path) <= 2:
        return list(path)
    out = [path[0]]
    for i in range(1, len(path) - 1):
        dx1 = path[i]["x"] - path[i - 1]["x"]
        dy1 = path[i]["y"] - path[i - 1]["y"]
        dx2 = path[i + 1]["x"] - path[i]["x"]
        dy2 = path[i + 1]["y"] - path[i]["y"]
        if dx1 != dx2 or dy1 != dy2:
            out.append(path[i])
    out.append(path[-1])
    return out


# ── public API ──────────────────────────────────────────────────────


def build_cutouts(
    pcb_layout: dict,
    routing_result: dict | None = None,
    *,
    shell_height: float | None = None,
) -> list[Cutout]:
    """Build cutout list from PCB layout and (optionally) routing result.

    Parameters
    ----------
    pcb_layout : dict
        The ``pcb_layout.json`` produced by the placer.
    routing_result : dict, optional
        The routing result from the TS router (contains ``traces``).
    shell_height : float, optional
        Total enclosure height in mm.  Defaults to the hardware config
        value (floor + cavity + ceiling).

    Returns
    -------
    list[Cutout]
        Ready to pass straight into ``generate_enclosure_scad(cutouts=…)``.
    """
    h = shell_height or DEFAULT_HEIGHT_MM
    margin = hw.component_margin
    cuts: list[Cutout] = []

    # ── Z-layer constants ──────────────────────────────────────────
    FLOOR = 2.0              # solid bottom
    PINHOLE_TOP = 3.0        # pinholes: 2 → 3 mm
    CAVITY_START = 3.0       # traces + components start
    TOP_SOLID = 2.0          # solid ceiling
    CAVITY_END = h - TOP_SOLID

    pin_depth = PINHOLE_TOP - FLOOR          # 1 mm
    pocket_depth = CAVITY_END - CAVITY_START  # full cavity zone
    trace_depth = hw.trace_channel_depth      # 0.4 mm (shallow channel)

    # ── Router → layout offset ─────────────────────────────────────
    # The TS router works in a coordinate system shifted so the board
    # outline starts at (0, 0).  We need to add the pcb_layout outline
    # minimum back to convert grid positions to layout (= SCAD) space.
    board_outline = pcb_layout.get("board", {}).get("outline_polygon", [])
    if board_outline:
        o_min_x, o_min_y, _, _ = polygon_bounds(board_outline)
    else:
        o_min_x = o_min_y = 0.0

    grid = hw.grid_resolution  # mm per grid unit

    def _grid_to_mm(gx: int | float, gy: int | float) -> tuple[float, float]:
        """Convert router grid coords to layout/SCAD mm."""
        return gx * grid + o_min_x, gy * grid + o_min_y

    # ── 1. Components ──────────────────────────────────────────────
    for comp in pcb_layout.get("components", []):
        cx, cy = comp["center"]
        ctype = comp.get("type", "")
        cid = comp.get("id", ctype)
        keepout = comp.get("keepout", {})

        if ctype == "button":
            # a) Circular cylinder for button cap press-fit (8.3 mm deep from top)
            cap_d = hw.button["min_hole_diameter_mm"]
            cap_depth = 8.3
            cap_poly = _circle_poly(cx, cy, cap_d / 2)
            cuts.append(Cutout(
                polygon=cap_poly,
                depth=cap_depth,
                z_base=h - cap_depth,
                label=f"button hole {cid}",
            ))

            # b) Rectangular body pocket from 3 mm to h-2 mm
            #    Sized to fit the actual 12×12mm switch body, not pin spacing.
            body_w = 12.0 + 2 * margin
            body_h = 12.0 + 2 * margin
            body_poly = _rect(cx, cy, body_w, body_h)
            cuts.append(Cutout(
                polygon=body_poly,
                depth=pocket_depth,
                z_base=CAVITY_START,
                label=f"button body {cid}",
            ))
            continue

        if ctype == "diode":
            # a) Body pocket in cavity zone (space for the LED body)
            d_diam = hw.diode["diameter_mm"]
            d_clr = hw.diode["hole_clearance_mm"]
            hole_d = d_diam + d_clr               # 6.0 mm
            body_w = d_diam + 2 * margin
            body_poly = _rect(cx, cy, body_w, body_w)
            cuts.append(Cutout(
                polygon=body_poly,
                depth=pocket_depth,
                z_base=CAVITY_START,
                label=f"diode body {cid}",
            ))

            # b) Wall-through slot for IR transmission.
            #    A rectangle extending from inside the board past the
            #    max-Y wall so the IR LED can shine outward.
            #    board_outline here is the board_inset polygon (2mm
            #    inside the actual outline).  The shell wall extends
            #    wall_clearance + wall_thickness beyond that, so we
            #    add enough margin to punch fully through.
            _, _, _, outline_max_y = polygon_bounds(board_outline)
            wall_extra = hw.wall_clearance + hw.wall_thickness + 1.0
            wall_poly = [
                [cx - hole_d / 2, cy - d_diam / 2],
                [cx + hole_d / 2, cy - d_diam / 2],
                [cx + hole_d / 2, outline_max_y + wall_extra],
                [cx - hole_d / 2, outline_max_y + wall_extra],
            ]
            cuts.append(Cutout(
                polygon=wall_poly,
                depth=hole_d,
                z_base=CAVITY_START,
                label=f"diode wall hole {cid}",
            ))
            continue

        if ctype == "battery":
            # Battery compartment → hatch opening in the floor.
            #
            # Creates a stepped cutout from the bottom:
            #   1. Main through-hole (full floor depth, narrower for ledges)
            #   2. Side ledge recesses on long edges (hatch panel rests here)
            #   3. Back notch dent (for hatch latch hook)
            bat_w = comp.get("body_width_mm", keepout.get("width_mm", 25.0))
            bat_h = comp.get("body_height_mm", keepout.get("height_mm", 48.0))
            enc = hw.enclosure
            hatch_thickness = enc["battery_hatch_thickness_mm"]   # 1.5
            ledge_width = 2.5         # ledge on each long side
            ledge_depth = hatch_thickness + 0.3  # recess for panel

            # a) Main through-hole (narrower by ledge on each side, full height)
            hole_w = bat_w - 2 * ledge_width
            cuts.append(Cutout(
                polygon=_rect(cx, cy, hole_w, bat_h),
                depth=CAVITY_START + 1.0, # cut through full 3mm floor + overlap
                z_base=-0.5,              # start below z=0 for clean boolean
                label=f"battery through-hole {cid}",
            ))

            # b) Left ledge recess
            cuts.append(Cutout(
                polygon=_rect(cx - bat_w / 2 + ledge_width / 2, cy,
                              ledge_width, bat_h),
                depth=ledge_depth,
                z_base=0.0,
                label=f"battery left ledge {cid}",
            ))

            # c) Right ledge recess
            cuts.append(Cutout(
                polygon=_rect(cx + bat_w / 2 - ledge_width / 2, cy,
                              ledge_width, bat_h),
                depth=ledge_depth,
                z_base=0.0,
                label=f"battery right ledge {cid}",
            ))

            # d) Front notch dent (for spring latch hook, at -Y end)
            notch_w = 8.0
            notch_d = 2.3       # depth into shell
            notch_h = 1.8       # height of notch
            cuts.append(Cutout(
                polygon=_rect(cx, cy - bat_h / 2 + notch_d / 2,
                              notch_w, notch_d),
                depth=notch_h,
                z_base=ledge_depth,
                label=f"battery hook notch {cid}",
            ))

            # d2) Back ledge notch slot (for hatch ledge tab, at +Y end)
            #     The hatch has an 8×2×1.5mm tab on top at its far end;
            #     this slot lets it hook under the shell floor.
            tab_w = 8.0
            tab_d = 2.0         # matches hatch cube depth
            tab_h = 1.5 + 0.3   # tab height + clearance
            cuts.append(Cutout(
                polygon=_rect(cx, cy + bat_h / 2 - tab_d / 2,
                              tab_w, tab_d),
                depth=tab_h,
                z_base=ledge_depth,
                label=f"battery ledge slot {cid}",
            ))

            # e) Cavity pocket above the floor (same as other components)
            poly = _rect(cx, cy, bat_w + 2 * margin, bat_h + 2 * margin)
            cuts.append(Cutout(
                polygon=poly,
                depth=pocket_depth,
                z_base=CAVITY_START,
                label=f"battery pocket {cid}",
            ))
            continue

        # Non-button, non-diode, non-battery component → rectangular pocket
        if keepout.get("type") == "rectangle":
            w = keepout["width_mm"] + 2 * margin
            ht = keepout["height_mm"] + 2 * margin
        elif keepout.get("type") == "circle":
            r = keepout.get("radius_mm", 5.0) + margin
            w = ht = 2 * r
        else:
            w = ht = 10.0 + 2 * margin

        poly = _rect(cx, cy, w, ht)
        cuts.append(Cutout(
            polygon=poly,
            depth=pocket_depth,
            z_base=CAVITY_START,
            label=f"{ctype} {cid}",
        ))

    # ── 2. Trace channels (shallow, in floor of cavity zone) ──────
    if routing_result and routing_result.get("traces"):
        tw = hw.trace_width
        half = tw / 2
        # Traces are shallow channels carved at the bottom of the
        # cavity zone (z = CAVITY_START, depth = trace_channel_depth).
        trace_z = CAVITY_START

        for trace in routing_result["traces"]:
            path = trace.get("path", [])
            if len(path) < 2:
                continue
            net = trace.get("net", "trace")
            simplified = _simplify_path(path)

            for i in range(len(simplified) - 1):
                x1, y1 = _grid_to_mm(simplified[i]["x"], simplified[i]["y"])
                x2, y2 = _grid_to_mm(simplified[i + 1]["x"], simplified[i + 1]["y"])

                poly = [
                    [min(x1, x2) - half, min(y1, y2) - half],
                    [max(x1, x2) + half, min(y1, y2) - half],
                    [max(x1, x2) + half, max(y1, y2) + half],
                    [min(x1, x2) - half, max(y1, y2) + half],
                ]
                cuts.append(Cutout(
                    polygon=poly,
                    depth=trace_depth,
                    z_base=trace_z,
                    label=f"trace {net}",
                ))

    # ── 3. Pinholes (all component pads) ───────────────────────────
    #    Pinholes go from 2 mm to 3 mm (1 mm deep), sitting just
    #    below the trace/component cavity so conductive filament
    #    can bridge from the trace channel into the pin hole.
    _add_pad_pinholes(pcb_layout, cuts, pin_depth, FLOOR, grid, o_min_x, o_min_y)

    return cuts


def _add_pad_pinholes(
    pcb_layout: dict,
    cuts: list[Cutout],
    depth: float,
    z_base: float,
    grid: float,
    o_min_x: float,
    o_min_y: float,
) -> None:
    """Add square pinholes at every component pad position.

    Pad positions are computed from the footprint geometry in
    ``base_remote.json``, so they match the actual hardware.
    """
    ps = hw.pinhole_diameter  # side length of square pinhole

    for comp in pcb_layout.get("components", []):
        cx, cy = comp["center"]
        ctype = comp.get("type", "")
        cid = comp.get("id", ctype)

        if ctype == "button":
            # 4 pins at corners of pin_spacing rectangle
            psx = hw.button["pin_spacing_x_mm"] / 2
            psy = hw.button["pin_spacing_y_mm"] / 2
            for dx, dy in [(-psx, -psy), (psx, -psy), (-psx, psy), (psx, psy)]:
                poly = _rect(cx + dx, cy + dy, ps, ps)
                cuts.append(Cutout(polygon=poly, depth=depth, z_base=z_base,
                                   label=f"pin {cid}"))

        elif ctype == "controller":
            # DIP-28: 2 rows of 14 pins
            pins_per_side = hw.controller["pins_per_side"]
            pin_spacing = hw.controller["pin_spacing_mm"]
            row_spacing = hw.controller["row_spacing_mm"]
            total_h = (pins_per_side - 1) * pin_spacing
            rotated = comp.get("rotation_deg", 0) == 90
            for i in range(pins_per_side):
                for side in (-1, 1):
                    if rotated:
                        # 90°: rows along Y, pins along X
                        x = cx - total_h / 2 + i * pin_spacing
                        y = cy + side * row_spacing / 2
                    else:
                        # 0°: rows along X, pins along Y
                        x = cx + side * row_spacing / 2
                        y = cy - total_h / 2 + i * pin_spacing
                    poly = _rect(x, y, ps, ps)
                    cuts.append(Cutout(polygon=poly, depth=depth, z_base=z_base,
                                       label=f"pin {cid}"))

        elif ctype == "battery":
            # 2 pads along Y axis
            pad_sp = hw.battery["pad_spacing_mm"]
            for dy in (-pad_sp / 2, pad_sp / 2):
                poly = _rect(cx, cy + dy, ps, ps)
                cuts.append(Cutout(polygon=poly, depth=depth, z_base=z_base,
                                   label=f"pin {cid}"))

        elif ctype == "diode":
            # 2 pads along X axis
            pad_sp = hw.diode["pad_spacing_mm"]
            for dx in (-pad_sp / 2, pad_sp / 2):
                poly = _rect(cx + dx, cy, ps, ps)
                cuts.append(Cutout(polygon=poly, depth=depth, z_base=z_base,
                                   label=f"pin {cid}"))


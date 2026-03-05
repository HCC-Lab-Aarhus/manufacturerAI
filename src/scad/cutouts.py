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


def _offset_polygon(
    shape: list[list[float]],
    offset: float,
    cx: float = 0.0,
    cy: float = 0.0,
) -> list[list[float]]:
    """Offset a polygon outward by *offset* mm using Shapely, then translate.

    *shape* is centered at origin. The result is translated to *(cx, cy)*.
    Falls back to a simple scale if Shapely buffer produces unexpected geometry.
    """
    from shapely.geometry import Polygon as _SPoly
    try:
        poly = _SPoly(shape)
        if not poly.is_valid:
            poly = poly.buffer(0)
        buffered = poly.buffer(offset, join_style=2)  # mitre join
        if buffered.is_empty:
            return [[x + cx, y + cy] for x, y in shape]
        # Take the exterior of the (possibly multi) polygon
        exterior = (
            buffered.exterior if hasattr(buffered, "exterior")
            else list(buffered.geoms)[0].exterior
        )
        coords = list(exterior.coords)[:-1]  # drop closing duplicate
        return [[x + cx, y + cy] for x, y in coords]
    except Exception:
        # Fallback: naive uniform scale from centroid
        return [[x + cx, y + cy] for x, y in shape]


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

    # ── Minimum height check for battery compartment ───────────────
    #    AA batteries are ~14.5 mm diameter.  The cavity must be at
    #    least as tall as the battery + conductor plate spring depth
    #    to avoid the compartment punching through the shell top.
    bat_cfg = hw.battery
    plate_cfg = bat_cfg.get("conductor_plate", {})
    neg_spring_depth = plate_cfg.get("negative_spring_depth_mm", 10.5)
    # Minimum cavity = battery diameter (approximated by compartment_width / 2
    # for 2-cell side-by-side, but really the AA diameter is 14.5 mm)
    aa_diameter = 14.5  # AA battery diameter in mm
    min_cavity = aa_diameter
    min_h = CAVITY_START + min_cavity + TOP_SOLID
    if h < min_h:
        import warnings
        warnings.warn(
            f"Shell height {h:.1f} mm is below the minimum {min_h:.1f} mm "
            f"required for the battery compartment (AA Ø{aa_diameter} mm). "
            f"Increasing to {min_h:.1f} mm."
        )
        h = min_h
        CAVITY_END = h - TOP_SOLID

    pin_depth = hw.pinhole_depth             # 2.5 mm (from config)
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
            shape_outline = comp.get("shape_outline")
            cap_depth = 8.3
            overshoot = 0.5

            if shape_outline and len(shape_outline) >= 3:
                # Custom shape — offset polygon by hole_clearance and translate
                # to button center. Shape vertices are relative to (0,0).
                clr = hw.button_cap.get("hole_clearance_mm", 0.3)
                cap_poly = _offset_polygon(shape_outline, clr, cx, cy)
            else:
                # Default circular cap hole — 13mm Ø
                cap_d = hw.button["min_hole_diameter_mm"]
                cap_poly = _circle_poly(cx, cy, cap_d / 2)

            # a) Cap hole from top (8.3 mm deep from top)
            #    Extend 0.5 mm ABOVE shell top to ensure a clean boolean cut
            #    through the rounded fillet surface.
            cuts.append(Cutout(
                polygon=cap_poly,
                depth=cap_depth + overshoot,
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
                z_base=CAVITY_END - hole_d,
                label=f"diode wall hole {cid}",
            ))
            continue

        if ctype == "battery":
            # Battery compartment — rectangular box in the cavity zone.
            #
            # Cross-section at one narrow end (looking along Y toward wall):
            #
            #       narrow wall (shell material)
            #       │  0.3mm plate slot (open from bottom for insertion)
            #       │  │
            #       │  ├──┐  latch support  ┌──┤  ← cavity zone only
            #       │  │  │   1×1mm         │  │    (holds plate once
            #       │  │  │  (batteries     │  │     seated)
            #       │  │  │   touch plate   │  │
            #       │  │  │   in centre)    │  │
            #       │  │  │                 │  │
            #       │  ├──┘                 └──┤
            #       │  │                       │  ← floor zone: open
            #       │  │  (plate slides in     │    slot, no supports
            #       │  │   from below)         │
            #       ───┘                       └───  shell bottom
            #
            # Geometry:
            #   a) Hatch ledge recesses on long sides (shallow shelf
            #      where the hatch cover rests).
            #   b) Centre through-hole (narrower by ledge_width, through
            #      the floor so hatch can be accessed from below).
            #   c) Cavity pocket — split into 3 cuts to leave 1×1 mm
            #      latch support blocks at each narrow-end corner.
            #   d) Plate slots — 0.3 mm channels carved into each
            #      narrow wall, extending from the shell bottom all
            #      the way up through the cavity.  Plates slide in
            #      from below after printing and seat flush with the
            #      compartment inner surface.  The latch supports
            #      from (c) hold the plate from the battery side.
            #   e) Hatch ledge-tab dent.
            bat_w = comp.get("body_width_mm", keepout.get("width_mm", 27.0))
            bat_h = comp.get("body_height_mm", keepout.get("height_mm", 50.0))
            enc = hw.enclosure
            hatch_clr = enc["battery_hatch_clearance_mm"]   # 0.3
            hatch_thickness = enc["battery_hatch_thickness_mm"]   # 1.5
            ledge_width = 2.5         # ledge on each long side
            ledge_depth = hatch_thickness + 0.3  # recess depth (1.8 mm)

            plate_cfg_loc = hw.battery.get("conductor_plate", {})
            plate_thick = plate_cfg_loc.get("thickness_mm", 0.3)
            plate_slot_w = bat_w + 1.0  # 28 mm — 1 mm wider than compartment for easy insertion
            support_x = 2.0   # support width along narrow wall (X) — extends into compartment centre
            support_y = 2.0   # support depth into compartment (Y) — 2 mm gap from narrow wall

            # ── a) Hatch ledge recesses (long sides) ──────────────
            cuts.append(Cutout(
                polygon=_rect(cx - bat_w / 2 + ledge_width / 2,
                              cy,
                              ledge_width, bat_h),
                depth=ledge_depth + 1.0,
                z_base=-1.0,
                label=f"battery left ledge {cid}",
            ))
            cuts.append(Cutout(
                polygon=_rect(cx + bat_w / 2 - ledge_width / 2,
                              cy,
                              ledge_width, bat_h),
                depth=ledge_depth + 1.0,
                z_base=-1.0,
                label=f"battery right ledge {cid}",
            ))

            # ── b) Centre through-hole (floor punch) ──────────────
            hole_w = bat_w - 2 * ledge_width   # 22 mm
            cuts.append(Cutout(
                polygon=_rect(cx, cy, hole_w, bat_h),
                depth=CAVITY_START + 1.0,      # through entire floor
                z_base=-0.5,
                label=f"battery through-hole {cid}",
            ))

            # ── c) Cavity pocket (split for supports) ─────────────
            #    Full pocket: (bat_w + 2m) × (bat_h + 2m).
            #    Split into 3 rectangles to leave solid 2×2 mm support
            #    blocks at each narrow-end corner.
            #
            #    Top-down (XY) of -Y narrow end inside the pocket:
            #
            #      cx-14.5          cx-11.5     cx+11.5          cx+14.5
            #         │  ████████████  │          │  ████████████  │
            #   cy-27 │  █ SUPPORT █  │  cut 23mm │  █ SUPPORT █  │ cy-27
            #         │  █  2×2mm  █  │  (open)   │  █  2×2mm  █  │
            #   cy-25 ├───────────────┴────────────┴───────────────┤ cy-25
            #         │           central band (29mm wide)          │
            #
            #    Each support is 2 mm wide (X) × 2 mm deep (Y).
            #    2 mm gap between narrow wall and the support.

            central_h = bat_h - 2 * support_y  # 48 mm
            cuts.append(Cutout(
                polygon=_rect(cx, cy,
                              bat_w + 2 * margin, central_h),
                depth=pocket_depth,
                z_base=CAVITY_START,
                label=f"battery pocket central {cid}",
            ))

            end_strip_w = bat_w - 2 * support_x   # 25 mm (between supports)
            end_strip_h = support_y + margin       # 2 mm
            for end_sign in (-1, +1):
                strip_cy = cy + end_sign * (bat_h / 2 + margin - end_strip_h / 2)
                cuts.append(Cutout(
                    polygon=_rect(cx, strip_cy,
                                  end_strip_w, end_strip_h),
                    depth=pocket_depth,
                    z_base=CAVITY_START,
                    label=f"battery pocket end {cid}",
                ))

            # ── d) Plate slots (through floor for bottom insertion) ────
            #    The conductor plate (27 × 12 mm, 0.3 mm thick) slides
            #    up from the shell bottom into this 0.3 mm channel.
            #    The slot is 28 mm wide (1 mm wider than compartment)
            #    for easier insertion.  It runs from z = −0.5 (below
            #    shell) through the full cavity height.  Once the plate
            #    is pushed up past the floor zone, the 2×2 mm latch
            #    supports from (c) press against it from the battery
            #    side and hold it flush against the narrow wall.
            slot_depth = CAVITY_END + 0.5   # from below shell to cavity top
            for end_sign in (-1, +1):
                pocket_cy = (cy + end_sign * (bat_h / 2 + plate_thick / 2))
                cuts.append(Cutout(
                    polygon=_rect(cx, pocket_cy,
                                  plate_slot_w, plate_thick),
                    depth=slot_depth,
                    z_base=-0.5,
                    label=f"battery plate slot {cid}",
                ))

            # ── e) Dent for hatch ledge tab (back +Y end) ─────────
            dent_w = 8.0
            dent_d = 2.0 + 0.3          # tab depth + clearance
            dent_h = 1.5 + 0.3          # tab height + clearance
            dent_span = dent_d + 1.0    # 3.3 mm total
            dent_cy = cy + bat_h / 2 - dent_d + dent_span / 2
            cuts.append(Cutout(
                polygon=_rect(cx, dent_cy, dent_w, dent_span),
                depth=dent_h + 1.0,
                z_base=ledge_depth - 0.5,
                label=f"battery ledge dent {cid}",
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

    # ── 2. Trace channels (full cavity height) ───────────────────
    #    Traces are carved through the entire cavity zone (same
    #    height as component pockets) so conductive filament fills
    #    the full depth and makes reliable contact.
    if routing_result and routing_result.get("traces"):
        tw = hw.trace_width
        half = tw / 2
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
                    depth=pocket_depth,
                    z_base=trace_z,
                    label=f"trace {net}",
                ))

    # ── 3. Pinholes (all component pads) ───────────────────────────
    #    Round pinholes start at the bottom of the cavity zone and
    #    extend downward into the floor.  A wider taper/funnel cone
    #    at the entry guides pin insertion and provides a press-fit.
    #    Pin depth from config (2.5 mm); z_base sits the hole so its
    #    top aligns with CAVITY_START (traces can bridge into the hole).
    pin_z_base = CAVITY_START - pin_depth
    _add_pad_pinholes(pcb_layout, cuts, pin_depth, pin_z_base, grid, o_min_x, o_min_y)

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
    """Add square pinholes with entry taper at every component pad.

    Each pinhole is a **stepped square channel**:

      1. **Main shaft** (lower portion) — tight 0.7 mm square hole.
         A DIP pin is 0.46 mm square, so this gives ~0.12 mm clearance
         per side.  FDM printers shrink holes slightly (material
         expansion) so the printed hole will be even tighter, producing
         a press-fit that holds pins securely.

      2. **Entry taper** (top 0.5 mm) — wider 1.2 mm square opening
         that guides pin insertion and lets conductive filament bridge
         from the trace channel into the hole.

    Both layers are simple 4-vertex rectangles to keep the OpenSCAD
    CSG tree lightweight (minimising compile time).
    """
    # Default pinhole sizes (ATmega DIP pins: 0.46 mm square)
    default_hole_d = hw.pinhole_diameter       # 0.7 mm — press-fit for DIP
    taper_d = hw.pinhole_taper_diameter        # 1.2 mm — entry funnel
    taper_depth = hw.pinhole_taper_depth       # 0.5 mm

    # Button pins are thicker (~1.0 mm Ø legs on tactile switches)
    button_hole_d = hw.button.get("pinhole_diameter_mm", default_hole_d)

    # Pre-compute shaft depth (everything below the taper)
    shaft_depth = depth - taper_depth
    taper_z = z_base + shaft_depth

    def _add_pin(x: float, y: float, label: str,
                 pin_d: float = default_hole_d) -> None:
        """Append shaft + taper cutouts for one pin position."""
        # Shaft sized per component (lower portion)
        cuts.append(Cutout(
            polygon=_rect(x, y, pin_d, pin_d),
            depth=shaft_depth,
            z_base=z_base,
            label=label,
        ))
        # Wide entry taper (top portion) — always uses taper_d
        effective_taper = max(taper_d, pin_d + 0.4)
        cuts.append(Cutout(
            polygon=_rect(x, y, effective_taper, effective_taper),
            depth=taper_depth,
            z_base=taper_z,
            label=f"{label} taper",
        ))

    for comp in pcb_layout.get("components", []):
        cx, cy = comp["center"]
        ctype = comp.get("type", "")
        cid = comp.get("id", ctype)

        if ctype == "button":
            # 4 pins at corners of pin_spacing rectangle
            # Button tactile switch legs are ~1.0 mm — use wider holes
            psx = hw.button["pin_spacing_x_mm"] / 2
            psy = hw.button["pin_spacing_y_mm"] / 2
            for dx, dy in [(-psx, -psy), (psx, -psy), (-psx, psy), (psx, psy)]:
                _add_pin(cx + dx, cy + dy, f"pin {cid}",
                         pin_d=button_hole_d)

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
                        x = cx - total_h / 2 + i * pin_spacing
                        y = cy + side * row_spacing / 2
                    else:
                        x = cx + side * row_spacing / 2
                        y = cy - total_h / 2 + i * pin_spacing
                    _add_pin(x, y, f"pin {cid}")

        elif ctype == "battery":
            # 2 pads along Y axis
            pad_sp = hw.battery["pad_spacing_mm"]
            for dy in (-pad_sp / 2, pad_sp / 2):
                _add_pin(cx, cy + dy, f"pin {cid}")

        elif ctype == "diode":
            # 2 pads along X axis
            pad_sp = hw.diode["pad_spacing_mm"]
            for dx in (-pad_sp / 2, pad_sp / 2):
                _add_pin(cx + dx, cy, f"pin {cid}")


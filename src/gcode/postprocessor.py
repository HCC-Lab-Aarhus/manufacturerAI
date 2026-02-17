"""
G-code post-processor — splits a slicer G-code file at pause points
and injects ironing, ink deposition, and component-insertion pauses.

PrusaSlicer emits layer-change markers as comments:

    ;LAYER_CHANGE
    ;Z:3.200
    ;HEIGHT:0.2

The post-processor walks through the G-code line by line, watches for
these markers, and inserts custom blocks at the correct Z-heights.

Print stages (bottom to top):
  1. Print floor layers (Z = 0 → ink_z)
  2. Iron the ink layer surface (skipping trace channels)
  3. Extrude single-width trace highlight pass (optional color change)
  4. Pause — deposit conductive ink
  5. Resume printing cavity walls (ink_z → component_z)
  6. Pause — insert diode, switches, ATmega328P
  7. Resume and print ceiling to completion

The MK3S firmware supports ``M601`` for filament-change pause (LCD
prompt, beep, wait for user) — we use this for pauses.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("manufacturerAI.gcode.postprocessor")

# Regex for PrusaSlicer layer-change Z comment
_Z_RE = re.compile(r"^;Z:([\d.]+)")
# Regex to extract X/Y from a G0/G1 move
_MOVE_RE = re.compile(
    r"^G[01]\s+"
    r"(?:.*?X(?P<x>[\d.]+))?"
    r"(?:.*?Y(?P<y>[\d.]+))?",
)

# ── Extrusion constants ───────────────────────────────────────────

NOZZLE_DIA = 0.4           # mm
LAYER_HEIGHT = 0.2         # mm
FILAMENT_DIA = 1.75        # mm
EXTRUSION_WIDTH = 0.45     # mm — single line width (≈ nozzle + small overlap)
TRACE_BUFFER = 0.6         # mm — half-width exclusion around each trace segment
TRACE_EXTRUDE_SPEED = 600  # mm/min — slow extrusion for the highlight pass
TRACE_TRAVEL_SPEED = 3000  # mm/min

# Cross-section area of extruded bead
_BEAD_AREA = EXTRUSION_WIDTH * LAYER_HEIGHT          # mm²
# Filament cross-section
_FILAMENT_AREA = math.pi * (FILAMENT_DIA / 2) ** 2   # mm²
# E distance per 1 mm of XY travel
E_PER_MM = _BEAD_AREA / _FILAMENT_AREA               # ≈ 0.0374


@dataclass
class PostProcessResult:
    """Output of the post-processing step."""

    output_path: Path
    total_layers: int
    ink_layer: int
    component_layer: int
    stages: list[str] = field(default_factory=list)


# ── Bed-offset detection ─────────────────────────────────────────


def _stl_bbox_center(stl_path: Path) -> tuple[float, float]:
    """Read an STL (binary or ASCII) and return ``(center_x, center_y)``."""
    import struct

    data = stl_path.read_bytes()
    is_ascii = data.lstrip()[:6].lower() == b"solid " and b"facet" in data[:1000]

    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    if is_ascii:
        _VERTEX_RE = re.compile(
            r"vertex\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)"
        )
        for m in _VERTEX_RE.finditer(data.decode("utf-8", errors="replace")):
            x, y = float(m.group(1)), float(m.group(2))
            if x < min_x: min_x = x
            if x > max_x: max_x = x
            if y < min_y: min_y = y
            if y > max_y: max_y = y
    else:
        import io
        f = io.BytesIO(data)
        f.read(80)  # header
        (num_tri,) = struct.unpack("<I", f.read(4))
        for _ in range(num_tri):
            f.read(12)  # normal vector
            for _v in range(3):
                x, y, _z = struct.unpack("<fff", f.read(12))
                if x < min_x: min_x = x
                if x > max_x: max_x = x
                if y < min_y: min_y = y
                if y > max_y: max_y = y
            f.read(2)  # attribute byte count

    return ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0)


def _compute_bed_offset(
    stl_path: Path,
    bed_size: tuple[float, float],
) -> tuple[float, float]:
    """Compute offset from model-local coords to bed coords.

    PrusaSlicer auto-centres the STL on the build plate.  The centre
    of the model's bounding box lands on the centre of the bed:

        offset = bed_centre − stl_bbox_centre

    Parameters
    ----------
    stl_path : Path
        The STL file that PrusaSlicer is slicing (``enclosure.stl``
        or ``print_plate.stl``).
    bed_size : tuple
        ``(width, height)`` of the bed in mm.

    Returns ``(offset_x, offset_y)`` in mm.
    """
    model_cx, model_cy = _stl_bbox_center(stl_path)

    bed_cx = bed_size[0] / 2.0
    bed_cy = bed_size[1] / 2.0

    offset_x = bed_cx - model_cx
    offset_y = bed_cy - model_cy

    log.info(
        "Bed offset: STL bbox centre (%.3f, %.3f) → bed centre (%.1f, %.1f) "
        "⇒ offset (%.3f, %.3f)  [%s]",
        model_cx, model_cy, bed_cx, bed_cy, offset_x, offset_y,
        stl_path.name,
    )
    return offset_x, offset_y


def _offset_segments(
    segs: list[tuple[float, float, float, float]],
    dx: float,
    dy: float,
) -> list[tuple[float, float, float, float]]:
    """Translate all segments by (dx, dy)."""
    return [(x1 + dx, y1 + dy, x2 + dx, y2 + dy) for x1, y1, x2, y2 in segs]


def _offset_ink_gcode(
    lines: list[str],
    dx: float,
    dy: float,
) -> list[str]:
    """Shift X/Y coordinates in ink G-code lines by (dx, dy)."""
    result: list[str] = []
    for line in lines:
        if line.startswith(("G0 ", "G1 ")) and ("X" in line or "Y" in line):
            def _shift_coord(m: re.Match) -> str:
                axis = m.group(1)
                val = float(m.group(2))
                offset = dx if axis == "X" else dy
                return f"{axis}{val + offset:.3f}"
            line = re.sub(r"([XY])([\d.]+)", _shift_coord, line)
        result.append(line)
    return result


def _ironing_block(z: float) -> list[str]:
    """Emit a comment block marking the ironing pass location."""
    return [
        "",
        "; " + "-" * 40,
        "; IRONING PASS — ink layer surface (generated by slicer)",
        f"; Z = {z:.2f} mm",
        "; Ironing moves that cross trace channels have been removed.",
        "; " + "-" * 40,
        "",
    ]


# ── Geometry helpers — point-to-segment distance ─────────────────

def _point_to_segment_dist(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
) -> float:
    """Minimum distance from point (px, py) to segment (ax, ay)→(bx, by)."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def _segment_near_traces(
    x1: float, y1: float,
    x2: float, y2: float,
    trace_segs: list[tuple[float, float, float, float]],
    buffer: float = TRACE_BUFFER,
) -> bool:
    """Return True if the move (x1,y1)→(x2,y2) passes near any trace.

    We sample points along the move and check distance to every trace
    segment.  A move is "near" if any sample point is within *buffer*
    mm of any trace segment.
    """
    if not trace_segs:
        return False
    length = math.hypot(x2 - x1, y2 - y1)
    steps = max(2, int(length / (buffer * 0.5)))
    for i in range(steps + 1):
        t = i / steps
        px = x1 + t * (x2 - x1)
        py = y1 + t * (y2 - y1)
        for seg in trace_segs:
            if _point_to_segment_dist(px, py, *seg) < buffer:
                return True
    return False


# ── Ironing filter — remove ironing moves over traces ────────────

def _filter_ironing_at_ink_layer(
    lines: list[str],
    start: int,
    trace_segs: list[tuple[float, float, float, float]],
    iron_z: float = 3.0,
) -> tuple[list[str], int, int]:
    """Process a ``; TYPE:Ironing`` section, removing moves over traces.

    Instead of deleting moves and inserting complex reposition
    sequences (which corrupt E-counter continuity and confuse the
    G-code viewer), we simply convert ironing *extrusion* moves that
    cross trace channels into *travel* moves (G0).  The nozzle follows
    the same path but doesn't extrude, leaving the trace channels
    un-ironed while keeping the layer structure clean.

    Parameters
    ----------
    lines : list[str]
        Full G-code lines list.
    start : int
        Index of the ``;TYPE:Ironing`` line.
    trace_segs : list
        Trace segments in mm.
    iron_z : float
        The Z-height of the ironing layer.

    Returns
    -------
    (filtered_lines, end_index, removed_count)
    """
    filtered: list[str] = []
    removed = 0
    cur_x, cur_y = 0.0, 0.0
    i = start

    while i < len(lines):
        line = lines[i]

        # End of ironing section: another ;TYPE: or ;LAYER_CHANGE
        if i > start and (line.startswith(";TYPE:") or line.startswith(";LAYER_CHANGE")):
            break

        m = _MOVE_RE.match(line)
        if m and (m.group("x") or m.group("y")):
            nx = float(m.group("x")) if m.group("x") else cur_x
            ny = float(m.group("y")) if m.group("y") else cur_y

            if _segment_near_traces(cur_x, cur_y, nx, ny, trace_segs):
                # Convert extrusion move to travel — nozzle follows
                # the same path without extruding.
                coords = ""
                if m.group("x"):
                    coords += f" X{m.group('x')}"
                if m.group("y"):
                    coords += f" Y{m.group('y')}"
                filtered.append(f"G0{coords} ; ironing suppressed over trace")
                removed += 1
            else:
                filtered.append(line)

            cur_x, cur_y = nx, ny
        else:
            filtered.append(line)
            if m and m.group("x"):
                cur_x = float(m.group("x"))
            if m and m.group("y"):
                cur_y = float(m.group("y"))

        i += 1

    return filtered, i, removed


# ── Trace highlight extrusion pass ────────────────────────────────

def _trace_highlight_block(
    z: float,
    trace_segs: list[tuple[float, float, float, float]],
) -> list[str]:
    """Generate a single-extrusion-width pass along every trace path.

    This prints a thin line of filament directly over the trace
    channels so they're visually marked.  Precede this with an M600
    filament change (commented out by default) to use a
    contrasting color.

    Parameters
    ----------
    z : float
        Print Z-height (top of floor).
    trace_segs : list
        Trace segments as ``(x1, y1, x2, y2)`` in mm.
    """
    if not trace_segs:
        return ["; TRACE HIGHLIGHT: no trace segments"]

    lines = [
        "",
        "; " + "=" * 50,
        "; TRACE HIGHLIGHT PASS — single extrusion width",
        f"; Z = {z:.2f} mm — {len(trace_segs)} segments",
        "; Prints a thin filament line over each trace channel.",
        "; " + "=" * 50,
        "",
        "; ┌──────────────────────────────────────────────────┐",
        "; │  COLOR CHANGE — uncomment the line below to      │",
        "; │  switch to a contrasting filament color before    │",
        "; │  the trace highlight pass.                        │",
        "; └──────────────────────────────────────────────────┘",
        "; M600 ; filament change — swap to trace highlight color",
        "",
    ]

    # Group consecutive segments that share endpoints into polylines
    # to reduce travel moves.
    polylines = _segments_to_polylines(trace_segs)

    # NOTE: PrusaSlicer Core One uses M83 (relative E distances).
    # Each G1 E value must be the *per-move delta*, not cumulative.
    lines.append(f"G0 Z{z:.3f} F1000")

    for poly in polylines:
        if len(poly) < 2:
            continue

        # Travel to start
        sx, sy = poly[0]
        lines.append(f"G0 X{sx:.3f} Y{sy:.3f} F{TRACE_TRAVEL_SPEED}")
        lines.append(f"G0 Z{z:.3f} F1000")

        # Extrude along path — emit per-move E delta (M83 relative)
        prev = poly[0]
        for pt in poly[1:]:
            dist = math.hypot(pt[0] - prev[0], pt[1] - prev[1])
            e_delta = dist * E_PER_MM
            lines.append(
                f"G1 X{pt[0]:.3f} Y{pt[1]:.3f} E{e_delta:.5f} F{TRACE_EXTRUDE_SPEED}"
            )
            prev = pt

    # Retract after trace highlight (M83 relative: negative = retract)
    lines.extend([
        "",
        "; ┌──────────────────────────────────────────────────┐",
        "; │  Uncomment below to switch back to the main      │",
        "; │  filament color after the trace highlight pass.   │",
        "; └──────────────────────────────────────────────────┘",
        "; M600 ; filament change — swap back to main color",
        "",
        "G1 E-0.80000 F2100 ; retract 0.8 mm",
        "",
        "; " + "=" * 50,
        "; END TRACE HIGHLIGHT",
        "; " + "=" * 50,
        "",
    ])

    return lines


def _segments_to_polylines(
    segs: list[tuple[float, float, float, float]],
) -> list[list[tuple[float, float]]]:
    """Chain connected segments into polylines to minimize travel.

    Two segments are chained if one's endpoint equals another's
    start point (within tolerance).
    """
    if not segs:
        return []

    TOL = 0.01  # mm

    remaining = [(s[0], s[1], s[2], s[3]) for s in segs]
    polylines: list[list[tuple[float, float]]] = []

    while remaining:
        seg = remaining.pop(0)
        chain: list[tuple[float, float]] = [(seg[0], seg[1]), (seg[2], seg[3])]

        changed = True
        while changed:
            changed = False
            for j in range(len(remaining) - 1, -1, -1):
                s = remaining[j]
                ex, ey = chain[-1]
                sx, sy = chain[0]

                # Append: chain end == segment start
                if abs(ex - s[0]) < TOL and abs(ey - s[1]) < TOL:
                    chain.append((s[2], s[3]))
                    remaining.pop(j)
                    changed = True
                # Append reversed: chain end == segment end
                elif abs(ex - s[2]) < TOL and abs(ey - s[3]) < TOL:
                    chain.append((s[0], s[1]))
                    remaining.pop(j)
                    changed = True
                # Prepend: chain start == segment end
                elif abs(sx - s[2]) < TOL and abs(sy - s[3]) < TOL:
                    chain.insert(0, (s[0], s[1]))
                    remaining.pop(j)
                    changed = True
                # Prepend reversed: chain start == segment start
                elif abs(sx - s[0]) < TOL and abs(sy - s[1]) < TOL:
                    chain.insert(0, (s[2], s[3]))
                    remaining.pop(j)
                    changed = True

        polylines.append(chain)

    return polylines


def _pause_block(label: str, z: float, instructions: list[str]) -> list[str]:
    """Generate a firmware pause block (M601) with user instructions.

    ``M601`` on the MK3S:
    - Retracts filament
    - Parks the head
    - Beeps and shows LCD prompt
    - Waits for user to press the knob
    - Resumes print
    """
    lines = [
        "",
        "; " + "=" * 50,
        f"; PAUSE: {label}",
        f"; Z = {z:.2f} mm",
    ]
    for instr in instructions:
        lines.append(f"; >> {instr}")
    lines.extend([
        "; " + "=" * 50,
        "",
        "; Park head and wait for user",
        "M601 ; pause print — press knob to resume",
        "",
    ])
    return lines


def postprocess_gcode(
    gcode_path: Path,
    output_path: Path | None,
    ink_z: float,
    component_z: float,
    ink_gcode_lines: list[str] | None = None,
    trace_segments: list[tuple[float, float, float, float]] | None = None,
    bed_offset: tuple[float, float] | None = None,
) -> PostProcessResult:
    """Read slicer G-code, inject pauses and ink, write result.

    Parameters
    ----------
    gcode_path : Path
        Input ``.gcode`` from PrusaSlicer.
    output_path : Path or None
        Where to write the modified G-code.  Defaults to
        ``<input>_staged.gcode``.
    ink_z : float
        Z-height for the ink layer (top of floor).
    component_z : float
        Z-height for component insertion (top of cavity).
    ink_gcode_lines : list[str] or None
        Pre-generated ink deposition G-code (from ``ink_traces``).
        If *None*, only a pause is inserted (manual ink application).
    trace_segments : list or None
        Trace path segments as ``(x1, y1, x2, y2)`` in mm.  Used to
        filter ironing moves over trace channels and to generate the
        trace highlight extrusion pass.
    bed_offset : tuple or None
        ``(dx, dy)`` offset from model-local coords to bed coords.
        Computed from ``_compute_bed_offset(outline_polygon)``.

    Returns
    -------
    PostProcessResult
    """
    if output_path is None:
        output_path = gcode_path.with_name(
            gcode_path.stem + "_staged" + gcode_path.suffix
        )

    trace_segs = trace_segments or []

    raw_lines = gcode_path.read_text(encoding="utf-8").splitlines()

    # ── Apply bed offset ─────────────────────────────────────────
    # PrusaSlicer auto-centres the model on the bed.  Trace/ink
    # coordinates are in model-local space, so we need to shift them.
    offset_x, offset_y = bed_offset if bed_offset else (0.0, 0.0)

    if trace_segs and (offset_x or offset_y):
        trace_segs = _offset_segments(trace_segs, offset_x, offset_y)
        log.info("Trace segments shifted by (%.3f, %.3f) to match bed", offset_x, offset_y)

    if ink_gcode_lines and (offset_x or offset_y):
        ink_gcode_lines = _offset_ink_gcode(ink_gcode_lines, offset_x, offset_y)
        log.info("Ink G-code shifted by (%.3f, %.3f) to match bed", offset_x, offset_y)

    out: list[str] = []
    total_layers = 0
    ink_injected = False
    component_injected = False
    ink_layer_num = -1
    comp_layer_num = -1
    ironing_moves_removed = 0
    ironing_layers_stripped = 0
    ironing_lines_stripped = 0
    highlight_z = ink_z + LAYER_HEIGHT
    trace_highlight_pending = False  # armed at ink layer
    trace_highlight_armed = False    # ready to inject after next ;TYPE:
    current_z = 0.0                  # track Z for ironing filtering


    # Track whether we're inside the ink layer (between ink_z and the
    # next layer change) so we can filter ironing in that range.
    in_ink_layer = False

    stages = []

    # Track nozzle XY so we can restore position after trace highlight
    track_x, track_y = 0.0, 0.0

    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]

        # Track nozzle position from G0/G1 moves
        m_pos = _MOVE_RE.match(line)
        if m_pos:
            if m_pos.group("x"):
                track_x = float(m_pos.group("x"))
            if m_pos.group("y"):
                track_y = float(m_pos.group("y"))

        # Detect layer change
        z_match = _Z_RE.match(line)
        if z_match:
            z_val = float(z_match.group(1))
            total_layers += 1

            current_z = z_val

            # Leaving the ink layer
            if in_ink_layer and z_val > ink_z + 0.01:
                in_ink_layer = False

            # ── Ink layer pause (first Z >= ink_z) ─────────────
            if not ink_injected and z_val >= ink_z - 0.001:
                ink_injected = True
                ink_layer_num = total_layers
                in_ink_layer = True

                # Insert ironing marker
                out.extend(_ironing_block(ink_z))

                if ironing_moves_removed:
                    stages.append(
                        f"Removed {ironing_moves_removed} ironing moves over trace channels"
                    )

                # Queue trace highlight for the next layer
                if trace_segs:
                    trace_highlight_pending = True
                    stages.append(
                        f"Trace highlight pass: {len(trace_segs)} segments "
                        f"(single {EXTRUSION_WIDTH}mm width) at Z={highlight_z:.2f}"
                    )

                # Insert ink pause
                out.extend(_pause_block(
                    "DEPOSIT CONDUCTIVE INK",
                    ink_z,
                    [
                        "The floor surface has been ironed.",
                        "Deposit conductive ink along the trace channels.",
                        "Press the knob when done to resume printing.",
                    ],
                ))

                # Insert ink G-code if provided
                if ink_gcode_lines:
                    out.extend(ink_gcode_lines)
                    stages.append(f"Ink G-code injected at Z={ink_z:.2f} ({len(ink_gcode_lines)} lines)")
                    # Second pause after ink to let it dry / cure
                    out.extend(_pause_block(
                        "INK CURING",
                        ink_z,
                        [
                            "Conductive ink has been deposited.",
                            "Allow ink to dry / cure if needed.",
                            "Press the knob to resume printing.",
                        ],
                    ))
                else:
                    stages.append(f"Manual ink pause at Z={ink_z:.2f}")

                stages.append(f"Ink layer: {ink_layer_num}")

            # ── Component insertion pause (first Z >= component_z) ──
            if not component_injected and z_val >= component_z - 0.001:
                component_injected = True
                comp_layer_num = total_layers

                out.extend(_pause_block(
                    "INSERT COMPONENTS",
                    component_z,
                    [
                        "Insert the following components into their pockets:",
                        "  1. IR diode (LED) — into the round hole near the top edge",
                        "  2. Tactile switches — into the square button pockets",
                        "  3. ATmega328P — into the DIP-28 pocket",
                        "Ensure all pins seat fully into their pin holes.",
                        "Press the knob when done to resume printing.",
                    ],
                ))
                stages.append(f"Component insertion pause at Z={component_z:.2f}")

        # ── Strip ironing from non-ink layers ─────────────
        # We only need ironing at the ink layer for a smooth trace
        # surface.  Ironing the outer shell / ceiling wastes time.
        #
        # PrusaSlicer emits a travel preamble before each ironing
        # section (retract → G92 E0 → lift → travel → lower →
        # unretract → ;TYPE:Ironing) and a retract postamble after
        # it.  When we strip the ironing moves, we must also:
        #  a) remove the preamble (already in `out`)
        #  b) if another print section follows, keep the travel from
        #     the ironing postamble to the next section — but replace
        #     the ironing retract (whose E value is invalid after
        #     stripping) with a clean retract.
        if line.strip() == ';TYPE:Ironing' and abs(current_z - ink_z) > 0.05:
            # Collect all lines in the ironing section
            section: list[str] = []
            i += 1
            while i < len(raw_lines):
                nxt = raw_lines[i].strip()
                if nxt.startswith(';TYPE:') or nxt.startswith(';LAYER_CHANGE'):
                    break
                section.append(raw_lines[i])
                i += 1

            # ── Remove preamble from `out` ──
            # Walk backwards to find the G92 E0 that precedes the
            # retract → travel → unretract leading into this ironing.
            preamble_start = None
            for k in range(len(out) - 1, max(0, len(out) - 20), -1):
                if out[k].strip() == 'G92 E0':
                    # The retract is one line before.  If the line
                    # before G92 E0 looks like a retract (G1 E… F…
                    # with no X/Y), include it.
                    preamble_start = k
                    if k > 0 and re.match(
                        r'^G1\s+E[\d.]+\s+F\d+', out[k - 1].strip()
                    ):
                        preamble_start = k - 1
                    break

            preamble_removed = 0
            if preamble_start is not None:
                preamble_removed = len(out) - preamble_start
                del out[preamble_start:]

            # ── Determine what follows ──
            next_is_print_type = (
                i < len(raw_lines)
                and raw_lines[i].strip().startswith(';TYPE:')
                and not raw_lines[i].strip().startswith(';TYPE:Custom')
            )

            skipped = len(section)
            if next_is_print_type and section:
                # Another print section follows — keep the travel
                # from the ironing postamble to the next section.
                # Find the last G92 E0 in the section (before travel).
                g92_idx = None
                for k in range(len(section) - 1, -1, -1):
                    if section[k].strip() == 'G92 E0':
                        g92_idx = k
                        break

                if g92_idx is not None:
                    # Keep from G92 E0 onward (travel + unretract).
                    # Skip section[g92_idx - 1] which is the ironing
                    # retract with a stale E value from stripped moves.
                    kept = section[g92_idx:]
                    skipped = g92_idx
                    for kl in kept:
                        out.append(kl)
                        m_k = _MOVE_RE.match(kl)
                        if m_k:
                            if m_k.group("x"):
                                track_x = float(m_k.group("x"))
                            if m_k.group("y"):
                                track_y = float(m_k.group("y"))

            ironing_layers_stripped += 1
            ironing_lines_stripped += skipped + preamble_removed
            log.debug(
                "Stripped ironing at Z=%.2f (%d ironing + %d preamble stripped, %d kept)",
                current_z, skipped, preamble_removed,
                len(section) - skipped,
            )
            continue  # don't append the ;TYPE:Ironing line itself

        # Append the current line
        out.append(line)

        # ── Deferred trace highlight injection ─────────────────
        # Arm when we reach the Z layer above the ink surface.
        if trace_highlight_pending and z_match:
            z_val = float(z_match.group(1))
            if z_val >= highlight_z - 0.001:
                trace_highlight_armed = True
                trace_highlight_pending = False

        # Inject AFTER the first ;TYPE: of that layer so the viewer
        # assigns the highlight to the correct layer.
        if trace_highlight_armed and line.strip().startswith(';TYPE:'):
            # Remember the slicer's nozzle position (set by its
            # travel moves between ;Z: and ;TYPE:).
            resume_x, resume_y = track_x, track_y
            out.extend(_trace_highlight_block(highlight_z, trace_segs))
            # Restore nozzle to slicer's expected position and
            # unretract so E-state matches what the slicer assumes.
            out.append(f"G0 X{resume_x:.3f} Y{resume_y:.3f} F9000 ; return to layer start")
            out.append("G1 E0.80000 F2100 ; unretract to match slicer state")
            trace_highlight_armed = False

        i += 1

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(out) + "\n", encoding="utf-8")

    log.info(
        "Post-processed G-code: %d layers, ink@L%d (Z=%.2f), components@L%d (Z=%.2f) → %s",
        total_layers, ink_layer_num, ink_z, comp_layer_num, component_z, output_path,
    )
    if ironing_moves_removed:
        log.info("  Removed %d ironing moves over trace channels", ironing_moves_removed)
    if ironing_layers_stripped:
        log.info(
            "  Stripped ironing from %d non-ink layers (%d lines removed)",
            ironing_layers_stripped, ironing_lines_stripped,
        )
        stages.append(
            f"Stripped ironing from {ironing_layers_stripped} non-ink layers "
            f"({ironing_lines_stripped} G-code lines removed)"
        )
    if trace_segs:
        log.info("  Trace highlight: %d segments", len(trace_segs))

    return PostProcessResult(
        output_path=output_path,
        total_layers=total_layers,
        ink_layer=ink_layer_num,
        component_layer=comp_layer_num,
        stages=stages,
    )

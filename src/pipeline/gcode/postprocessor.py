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
  3. Pause — deposit conductive ink
  4. Resume printing cavity walls (ink_z → component_z)
  5. Pause — insert diode, switches, ATmega328P
  6. Resume and print ceiling to completion

The MK3S firmware supports ``M601`` for filament-change pause (LCD
prompt, beep, wait for user) — we use this for pauses.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Regex for PrusaSlicer layer-change Z comment
_Z_RE = re.compile(r"^;Z:([\d.]+)")
# Regex to extract X/Y from a G0/G1 move
_MOVE_RE = re.compile(
    r"^G[01]\s+"
    r"(?:.*?X(?P<x>[\d.]+))?"
    r"(?:.*?Y(?P<y>[\d.]+))?",
)

# ── Trace constants ───────────────────────────────────────────────────────

TRACE_BUFFER = 0.6         # mm — half-width exclusion around each trace segment


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


def compute_bed_offset(
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
    """Emit a comment block noting the floor was ironed."""
    return [
        "",
        "; " + "-" * 40,
        f"; Floor surface was ironed at Z = {z:.2f} mm",
        "; Surface ready for conductive ink deposition.",
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



def _ink_pause_block(
    label: str,
    z: float,
    instructions: list[str],
    display_msg: str | None = None,
) -> list[str]:
    """Generate an M0 pause for ink deposition — head stays in place.

    ``M0`` (Unconditional Stop) halts the printer immediately without
    parking or retracting, so the nozzle remains directly over the last
    print position.  This is intentional for ink work: the operator can
    see exactly where the traces are and the head is out of the way only
    if the slicer positioned it there.

    Unlike M601, M0 does NOT move the head to the park position, which
    means the silver-ink channels stay visible and accessible.
    """
    lines = [
        "",
        "; " + "=" * 50,
        f"; PAUSE: {label}",
        f"; Z = {z:.2f} mm",
    ]
    for instr in instructions:
        lines.append(f"; >> {instr}")
    if display_msg:
        m0_line = f"M0 {display_msg}"
    else:
        m0_line = "M0 ; unconditional stop — head stays in place, press knob/LCD to resume"
    lines.extend([
        "; " + "=" * 50,
        "",
        m0_line,
    ])
    if display_msg:
        lines.append(";silverink")
    lines.append("")
    return lines


def _pause_block(label: str, z: float, instructions: list[str]) -> list[str]:
    """Generate a firmware pause block (M601) with user instructions.

    ``M601`` on the MK3S / Core One:
    - Retracts filament
    - Parks the head
    - Beeps and shows LCD prompt
    - Waits for user to press the knob
    - Resumes print

    After the firmware resumes, we insert a **nozzle wipe** sequence
    that moves to the front-left bed edge and wipes back and forth at
    Z = 0.2 mm (barely touching the bed surface) to scrub off any
    filament blob accumulated during the pause.  Then the nozzle
    retracts, lifts, and returns to the paused Z height — the slicer's
    own travel commands position X/Y back to the print.
    """
    # Wipe geometry (front-left corner of the bed)
    wipe_y = 1.0        # 1 mm from front edge
    wipe_x_start = 5.0  # start of wipe stroke
    wipe_x_end = 45.0   # end of wipe stroke
    wipe_z = 0.2        # just touching the bed surface
    wipe_speed = 3000    # mm/min (50 mm/s)
    travel_speed = 12000 # mm/min (200 mm/s)
    wipe_passes = 3      # back-and-forth strokes

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
        "; ── Nozzle wipe after resume ──────────────────────",
        "; Scrub nozzle on the front bed edge to remove any",
        "; filament blob accumulated during the pause.",
        "G1 E-1.4 F3600 ; retract filament",
        f"G0 Z{max(z, 5.0):.2f} F720 ; safe Z height",
        f"G0 X{wipe_x_start:.1f} Y{wipe_y:.1f} F{travel_speed} ; travel to wipe start",
        f"G0 Z{wipe_z:.2f} F720 ; lower to wipe height",
    ])

    # Wipe strokes
    for i in range(wipe_passes):
        lines.append(f"G0 X{wipe_x_end:.1f} Y{wipe_y:.1f} F{wipe_speed} ; wipe stroke {i*2+1}")
        lines.append(f"G0 X{wipe_x_start:.1f} Y{wipe_y:.1f} F{wipe_speed} ; wipe stroke {i*2+2}")

    lines.extend([
        f"G0 Z{max(z + 2.0, 5.0):.2f} F720 ; lift after wipe",
        "G1 E1.3 F1200 ; unretract (slightly less to avoid blob)",
        "; ── End nozzle wipe ───────────────────────────────",
        "",
    ])
    return lines


# ── M73 recalculation ─────────────────────────────────────────────

_M73_P_RE = re.compile(r"^M73\s+P(\d+)\s+R(\d+)")   # normal mode
_M73_Q_RE = re.compile(r"^M73\s+Q(\d+)\s+S(\d+)")   # silent mode
_TIME_META_RE = re.compile(
    r"^;\s*estimated printing time \((\w+ mode)\)\s*=\s*(.+)",
)


def _recalculate_m73(lines: list[str]) -> list[str]:
    """Recalculate M73 progress/remaining-time commands.

    After ironing is stripped the original M73 commands no longer
    reflect reality — progress jumps from ~74% straight to 100% and
    the initial ``R`` value is far too high.

    Strategy
    --------
    1. Find the original total time from the first ``M73 P0 Rxxx``.
    2. Count *move lines* (G0/G1) as a proxy for elapsed time.
    3. For each M73 command, compute the fraction of move lines that
       precede it and derive new P (progress %) and R (remaining min).
    4. Update the ``estimated printing time`` metadata comments in the
       footer to match.
    """
    # -- Pass 1: count total move lines and find original times -----
    total_moves = 0
    orig_total_normal = 0    # minutes, from first M73 P0 R...
    orig_total_silent = 0    # minutes, from first M73 Q0 S...

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("G0 ") or stripped.startswith("G1 "):
            total_moves += 1
        if not orig_total_normal:
            m = _M73_P_RE.match(stripped)
            if m and int(m.group(1)) == 0:
                orig_total_normal = int(m.group(2))
        if not orig_total_silent:
            m = _M73_Q_RE.match(stripped)
            if m and int(m.group(1)) == 0:
                orig_total_silent = int(m.group(2))

    if total_moves == 0 or (orig_total_normal == 0 and orig_total_silent == 0):
        return lines  # nothing to recalculate

    # The original total time included ironing that we stripped.
    # We need the *new* total time.  Use the last real (pre-final)
    # M73 to figure out how much time the surviving code represents.
    # Walk backwards to find the second-to-last M73 P line.
    last_real_p, last_real_r = 0, 0
    last_real_q, last_real_s = 0, 0
    for line in reversed(lines):
        stripped = line.strip()
        if not last_real_p:
            m = _M73_P_RE.match(stripped)
            if m and int(m.group(1)) < 100:
                last_real_p = int(m.group(1))
                last_real_r = int(m.group(2))
        if not last_real_q:
            m = _M73_Q_RE.match(stripped)
            if m and int(m.group(1)) < 100:
                last_real_q = int(m.group(1))
                last_real_s = int(m.group(2))
        if last_real_p and last_real_q:
            break

    # New total time = time elapsed up to last real marker + remaining
    # time_elapsed = orig_total - last_real_r
    # But last_real_p% of orig was completed, meaning the actual
    # content is (orig_total - last_real_r) in real moves.
    # The stripped ironing accounts for (100 - last_real_p)% of orig.
    # New total ≈ orig_total - (100 - last_real_p)/100 * orig_total
    #           = orig_total * last_real_p / 100 + last_real_r
    # But that double-counts remaining.  Simpler:
    #   new_total = orig_total - stripped_time
    #   stripped_time ≈ last_real_r  (the jump from last_real to end)
    # Actually: new_total = (orig_total - last_real_r)
    # because the remaining last_real_r minutes were all ironing.
    # But that's not quite right either — last_real_r has some real
    # printing too.
    # Best approach: new_total_normal = orig_total_normal * last_real_p / 100 + last_real_r
    # Wait no.  Let's think clearly:
    #   At last_real M73: P=74, R=61 out of orig 238
    #   Time elapsed so far = 238 - 61 = 177 min
    #   Progress = 74%, so 74% of the original print took 177 min
    #   The remaining 26% (ironing) would take 61 min
    #   After stripping, the total print is just those 177 min
    #   new_total = orig_total - last_real_r = 238 - 61 = 177

    new_total_normal = max(orig_total_normal - last_real_r, 1) if last_real_p else orig_total_normal
    new_total_silent = max(orig_total_silent - last_real_s, 1) if last_real_q else orig_total_silent

    # -- Pass 2: rewrite M73 and metadata lines ---------------------
    moves_so_far = 0
    result: list[str] = []

    for line in lines:
        stripped = line.strip()

        # Count moves before this line
        if stripped.startswith("G0 ") or stripped.startswith("G1 "):
            moves_so_far += 1

        # M73 P... R... (normal mode)
        m = _M73_P_RE.match(stripped)
        if m:
            frac = moves_so_far / total_moves if total_moves else 0
            pct = min(int(frac * 100), 100)
            remaining = max(int(new_total_normal * (1.0 - frac) + 0.5), 0)
            result.append(f"M73 P{pct} R{remaining}")
            continue

        # M73 Q... S... (silent mode)
        m = _M73_Q_RE.match(stripped)
        if m:
            frac = moves_so_far / total_moves if total_moves else 0
            pct = min(int(frac * 100), 100)
            remaining = max(int(new_total_silent * (1.0 - frac) + 0.5), 0)
            result.append(f"M73 Q{pct} S{remaining}")
            continue

        # Update estimated printing time metadata in footer
        mt = _TIME_META_RE.match(stripped)
        if mt:
            mode = mt.group(1)
            if mode == "normal mode":
                result.append(f"; estimated printing time ({mode}) = {_fmt_time(new_total_normal)}")
            elif mode == "silent mode":
                result.append(f"; estimated printing time ({mode}) = {_fmt_time(new_total_silent)}")
            else:
                result.append(line)
            continue

        result.append(line)

    log.info(
        "Recalculated M73: normal %dmin→%dmin, silent %dmin→%dmin (%d moves)",
        orig_total_normal, new_total_normal,
        orig_total_silent, new_total_silent,
        total_moves,
    )
    return result


def _fmt_time(minutes: int) -> str:
    """Format minutes as ``Xh Ym Zs`` like PrusaSlicer."""
    h = minutes // 60
    m = minutes % 60
    if h > 0:
        return f"{h}h {m}m 0s"
    return f"{m}m 0s"


def postprocess_gcode(
    gcode_path: Path,
    output_path: Path | None,
    ink_z: float,
    component_z: float,
    trace_segments: list[tuple[float, float, float, float]] | None = None,
    bed_offset: tuple[float, float] | None = None,
    silverink_only: bool = False,
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
    trace_segments : list or None
        Trace path segments as ``(x1, y1, x2, y2)`` in mm.  Used to
        filter ironing moves over trace channels.
    bed_offset : tuple or None
        ``(dx, dy)`` offset from model-local coords to bed coords.
        Computed from ``compute_bed_offset(stl_path, bed_size)``.

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

    out: list[str] = []
    total_layers = 0
    ink_injected = False
    component_injected = False
    ink_layer_num = -1
    comp_layer_num = -1
    ironing_moves_removed = 0
    ironing_layers_stripped = 0
    ironing_lines_stripped = 0
    current_z = 0.0                  # track Z for ironing filtering


    # Track whether we're inside the ink layer (between ink_z and the
    # next layer change) so we can filter ironing in that range.
    in_ink_layer = False

    # silverink_only: skip all layers before the ink layer, keeping
    # only the startup preamble (before the first ;LAYER_CHANGE) and
    # the ink layer itself (at ink_z).
    past_preamble = False       # True once we've seen the first ;LAYER_CHANGE
    at_ink_layer = False        # True once we reach the ink layer (Z ≈ ink_z)

    stages = []

    track_x, track_y = 0.0, 0.0

    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]

        # Detect when the preamble ends (first ;LAYER_CHANGE)
        if silverink_only and not past_preamble and line.strip() == ';LAYER_CHANGE':
            past_preamble = True

        # In silverink_only mode, detect when we arrive at the ink layer
        if silverink_only and past_preamble and not at_ink_layer:
            z_peek = _Z_RE.match(line)
            if z_peek:
                z_peek_val = float(z_peek.group(1))
                if abs(z_peek_val - ink_z) < 0.01:
                    at_ink_layer = True

        # silverink_only: skip lines between preamble and ink layer
        if silverink_only and past_preamble and not at_ink_layer:
            i += 1
            continue

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

            # ── Ink layer pause ──────────────────────────────
            # Trigger one layer ABOVE ink_z so the floor layer
            # (including ironing) prints first, then we pause to
            # deposit ink into the smooth ironed channels.
            if not ink_injected and z_val > ink_z + 0.01:
                ink_injected = True
                ink_layer_num = total_layers
                in_ink_layer = True

                # Insert ironing marker
                out.extend(_ironing_block(ink_z))

                if ironing_moves_removed:
                    stages.append(
                        f"Removed {ironing_moves_removed} ironing moves over trace channels"
                    )

                # Insert ink pause
                out.extend(_ink_pause_block(
                    "DEPOSIT CONDUCTIVE INK",
                    ink_z,
                    [
                        "The floor surface has been ironed.",
                        "Deposit conductive ink along the trace channels.",
                        "Press the knob when done to resume printing.",
                    ],
                    display_msg="connect silver ink",
                ))

                stages.append(f"Ink pause at Z={ink_z:.2f}")
                stages.append(f"Ink layer: {ink_layer_num}")

                if silverink_only:
                    stages.append("Silver ink debug mode — stopping after ink pause")
                    z_offset = ink_z - 0.2
                    _Z_PARAM = re.compile(r'(?<=Z)([\d.]+)')
                    shifted_out = []
                    for ol in out:
                        s_ol = ol.strip()
                        if s_ol.startswith(';Z:'):
                            old_z = float(s_ol[3:])
                            shifted_out.append(f";Z:{max(old_z - z_offset, 0.0):.3f}")
                        elif re.match(r'^G[01]\s', s_ol) and 'Z' in ol:
                            shifted_out.append(_Z_PARAM.sub(
                                lambda m: f"{max(float(m.group(1)) - z_offset, 0.0):.3f}", ol
                            ))
                        else:
                            shifted_out.append(ol)
                    out = shifted_out
                    stages.append(f"Z-offset: shifted down by {z_offset:.2f} mm")
                    for j in range(len(raw_lines) - 1, -1, -1):
                        if raw_lines[j].strip() == "; prusaslicer_config = begin":
                            out.extend(raw_lines[j:])
                            break
                    break

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

        # ── Ink-layer ironing: keep as-is ──────────────────
        # The entire ink-layer floor must be ironed — including the
        # trace channel areas — so that micro-gaps from FDM printing
        # are sealed and conductive ink cannot seep through.  We do
        # NOT filter or suppress any ironing moves at this Z; the
        # slicer's ironing pass is used unmodified.

        # ── Strip ironing from non-ink layers ─────────────
        # We only need ironing at the ink layer for a smooth, sealed
        # floor surface.  Ironing on other layers (battery compartment
        # floor, shell ceiling, etc.) is unnecessary and wastes time.
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
            # Walk backwards to find the retract / G92 E0 that
            # precedes the travel → unretract leading into ironing.
            preamble_start = None
            # Method 1: G92 E0 (MK3S absolute-E mode)
            for k in range(len(out) - 1, max(0, len(out) - 20), -1):
                if out[k].strip() == 'G92 E0':
                    preamble_start = k
                    if k > 0 and re.match(
                        r'^G1\s+E[\d.]+\s+F\d+', out[k - 1].strip()
                    ):
                        preamble_start = k - 1
                    break
            # Method 2: Core One M83 — find last retract (G1 E-… F…)
            if preamble_start is None:
                for k in range(len(out) - 1, max(0, len(out) - 15), -1):
                    s = out[k].strip()
                    if re.match(r'^G1\s+E-[\d.]+\s+F\d+$', s):
                        preamble_start = k
                        break
                    # Stop at the previous extrusion move
                    if re.match(r'^G1\s+.*[XY].*E[\d.]', s):
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

                # Method 1: G92 E0 (MK3S)
                g92_idx = None
                for k in range(len(section) - 1, -1, -1):
                    if section[k].strip() == 'G92 E0':
                        g92_idx = k
                        break

                if g92_idx is not None:
                    # Keep from G92 E0 onward (travel + unretract).
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
                else:
                    # Method 2: Core One M83 — no G92 E0 markers.
                    # Parse the section to find where the postamble
                    # would position the nozzle, then emit a clean
                    # retract → travel → unretract sequence.
                    target_x, target_y, target_z = track_x, track_y, current_z
                    last_m204 = None
                    for line_s in section:
                        m_k = _MOVE_RE.match(line_s)
                        if m_k:
                            if m_k.group("x"):
                                target_x = float(m_k.group("x"))
                            if m_k.group("y"):
                                target_y = float(m_k.group("y"))
                        z_m = re.match(r'^G[01]\s+Z([\d.]+)', line_s.strip())
                        if z_m:
                            target_z = float(z_m.group(1))
                        if line_s.strip().startswith('M204'):
                            last_m204 = line_s

                    # Emit corrective travel to the position the
                    # ironing postamble would have reached.
                    dist = math.hypot(target_x - track_x, target_y - track_y)
                    if dist > 0.5:
                        out.append(f"G1 E-0.8 F2700 ; retract (ironing stripped)")
                        out.append(f"G0 Z{target_z + 0.6:.3f} F720 ; Z-hop")
                        out.append(f"G0 X{target_x:.3f} Y{target_y:.3f} F21000 ; travel (ironing stripped)")
                        out.append(f"G0 Z{target_z:.3f} F720 ; lower")
                        out.append(f"G1 E0.8 F1500 ; unretract")
                    if last_m204:
                        out.append(last_m204)
                    track_x, track_y = target_x, target_y
                    skipped = len(section)

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

        i += 1

    # ── Recalculate M73 progress after ironing was stripped ────
    if ironing_lines_stripped:
        out = _recalculate_m73(out)

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
    return PostProcessResult(
        output_path=output_path,
        total_layers=total_layers,
        ink_layer=ink_layer_num,
        component_layer=comp_layer_num,
        stages=stages,
    )

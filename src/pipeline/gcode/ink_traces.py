"""Generate G-code toolpaths for conductive ink deposition.

Converts trace routing data (paths already in world-mm from the Python
router) into G-code move commands that lay down conductive ink along
each net.  The ink is deposited on the freshly ironed floor surface
at a fixed Z.

The output is a list of G-code lines that can be injected into the
main print G-code by the post-processor.
"""

from __future__ import annotations

import logging
from typing import Sequence

log = logging.getLogger(__name__)

# ── Ink deposition defaults ────────────────────────────────────────

INK_TRAVEL_SPEED = 3000    # mm/min — rapid move to trace start
INK_DRAW_SPEED = 300       # mm/min — slow linear move while dispensing
INK_Z_HOP = 1.0            # mm — lift between traces to avoid dragging


def generate_ink_gcode(
    routing_result: dict,
    ink_z: float,
    *,
    draw_speed: float = INK_DRAW_SPEED,
    travel_speed: float = INK_TRAVEL_SPEED,
    z_hop: float = INK_Z_HOP,
) -> list[str]:
    """Build G-code lines for conductive ink along each routed trace.

    Parameters
    ----------
    routing_result : dict
        The ``routing.json`` data.  Each trace has ``net_id`` (str)
        and ``path`` (list of ``[x_mm, y_mm]`` waypoints already in mm).
    ink_z : float
        Z-height (mm) at which to deposit ink (top of ironed floor).
    draw_speed : float
        Feed rate (mm/min) while dispensing ink.
    travel_speed : float
        Feed rate (mm/min) for rapid non-dispensing moves.
    z_hop : float
        Z lift (mm) between traces.

    Returns
    -------
    list[str]
        G-code lines (without trailing newlines).
    """
    traces = routing_result.get("traces", [])
    if not traces:
        return ["; INK: no traces to deposit"]

    lines: list[str] = []
    lines.append("")
    lines.append("; " + "=" * 50)
    lines.append("; CONDUCTIVE INK DEPOSITION")
    lines.append(f"; Z = {ink_z:.2f} mm — {len(traces)} traces")
    lines.append("; " + "=" * 50)
    lines.append("")

    # Lift to safe height before starting ink pass
    # Retract filament first to prevent ooze during long travels
    lines.append("G1 E-0.80000 F2700 ; retract before ink travels")
    lines.append(f"G0 Z{ink_z + z_hop:.3f} F{travel_speed}")

    for trace in traces:
        net = trace.get("net_id", "unknown")
        path = trace.get("path", [])
        if len(path) < 2:
            continue

        simplified = _simplify_path(path)
        if len(simplified) < 2:
            continue

        start_x, start_y = simplified[0][0], simplified[0][1]

        lines.append(f"")
        lines.append(f"; --- trace: {net} ({len(simplified)} points) ---")

        # Rapid to start position (lifted)
        lines.append(f"G0 Z{ink_z + z_hop:.3f} F{travel_speed}")
        lines.append(f"G0 X{start_x:.3f} Y{start_y:.3f} F{travel_speed}")

        # Lower to ink Z
        lines.append(f"G0 Z{ink_z:.3f} F1000")

        # Trace the path
        for pt in simplified[1:]:
            x, y = pt[0], pt[1]
            lines.append(f"G1 X{x:.3f} Y{y:.3f} F{draw_speed}")

        # Lift after trace
        lines.append(f"G0 Z{ink_z + z_hop:.3f} F1000")

    lines.append("")
    lines.append("; Unretract — restore filament state before next M601 pause")
    lines.append("G1 E0.80000 F1500 ; unretract after ink travels")
    lines.append("")
    lines.append("; " + "=" * 50)
    lines.append("; END CONDUCTIVE INK")
    lines.append("; " + "=" * 50)
    lines.append("")

    log.info("Generated ink G-code: %d traces, %d lines", len(traces), len(lines))
    return lines


# ── Trace segment extraction (for postprocessor) ──────────────────

def extract_trace_segments(
    routing_result: dict,
) -> list[tuple[float, float, float, float]]:
    """Return trace paths as ``(x1, y1, x2, y2)`` mm line segments.

    This is used by the post-processor to know *where* the traces run
    so it can skip ironing over them and add a highlight extrusion pass.
    """
    if not routing_result:
        return []

    traces = routing_result.get("traces", [])
    if not traces:
        return []

    segments: list[tuple[float, float, float, float]] = []
    for trace in traces:
        path = trace.get("path", [])
        simplified = _simplify_path(path)
        if len(simplified) < 2:
            continue
        for j in range(len(simplified) - 1):
            x1, y1 = simplified[j][0], simplified[j][1]
            x2, y2 = simplified[j + 1][0], simplified[j + 1][1]
            segments.append((x1, y1, x2, y2))

    log.info("Extracted %d trace segments from %d traces", len(segments), len(traces))
    return segments


# ── Path simplification ───────────────────────────────────────────

def _simplify_path(path: list[Sequence[float]]) -> list[Sequence[float]]:
    """Remove collinear intermediate points, keeping corners only."""
    if len(path) <= 2:
        return list(path)

    result = [path[0]]
    for i in range(1, len(path) - 1):
        prev = path[i - 1]
        curr = path[i]
        nxt = path[i + 1]

        dx1 = curr[0] - prev[0]
        dy1 = curr[1] - prev[1]
        dx2 = nxt[0] - curr[0]
        dy2 = nxt[1] - curr[1]

        if (dx1, dy1) != (dx2, dy2):
            result.append(curr)

    result.append(path[-1])
    return result

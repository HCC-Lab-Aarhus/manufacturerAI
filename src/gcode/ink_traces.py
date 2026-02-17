"""
Generate G-code toolpaths for conductive ink deposition.

Converts trace routing data (grid-coordinate paths from the TS router)
into G-code move commands that lay down conductive ink along each net.
The ink is deposited on the freshly ironed floor surface at a fixed Z.

The output is a list of G-code lines that can be injected into the
main print G-code by the post-processor.
"""

from __future__ import annotations

import logging
from typing import Sequence

from src.config.hardware import hw
from src.geometry.polygon import polygon_bounds

log = logging.getLogger("manufacturerAI.gcode.ink_traces")

# ── Ink deposition defaults ────────────────────────────────────────

INK_TRAVEL_SPEED = 3000    # mm/min — rapid move to trace start
INK_DRAW_SPEED = 300       # mm/min — slow linear move while dispensing
INK_Z_HOP = 1.0            # mm — lift between traces to avoid dragging


def generate_ink_gcode(
    routing_result: dict,
    pcb_layout: dict,
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
        The router output with ``traces`` list.  Each trace has
        ``net`` (str) and ``path`` (list of ``{x, y}`` grid coords).
    pcb_layout : dict
        The ``pcb_layout.json`` — needed for the board outline origin
        offset so grid coords can be converted to mm.
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

    # Grid → mm conversion (same as cutouts.py)
    board_outline = pcb_layout.get("board", {}).get("outline_polygon", [])
    if board_outline:
        o_min_x, o_min_y, _, _ = polygon_bounds(board_outline)
    else:
        o_min_x = o_min_y = 0.0

    grid = hw.grid_resolution  # 0.5 mm

    def grid_to_mm(gx: float, gy: float) -> tuple[float, float]:
        return gx * grid + o_min_x, gy * grid + o_min_y

    lines: list[str] = []
    lines.append("")
    lines.append("; " + "=" * 50)
    lines.append("; CONDUCTIVE INK DEPOSITION")
    lines.append(f"; Z = {ink_z:.2f} mm — {len(traces)} traces")
    lines.append("; " + "=" * 50)
    lines.append("")

    # Lift to safe height before starting ink pass
    # Retract filament first to prevent ooze during long travels
    lines.append("G1 E-2.00000 F3600 ; retract before ink travels")
    lines.append(f"G0 Z{ink_z + z_hop:.3f} F{travel_speed}")
    lines.append("G91 ; relative positioning")
    lines.append("G90 ; back to absolute")

    for trace in traces:
        net = trace.get("net", "unknown")
        path = trace.get("path", [])
        if len(path) < 2:
            continue

        # Simplify: only keep direction-change points
        simplified = _simplify_path(path)
        if len(simplified) < 2:
            continue

        start_x, start_y = grid_to_mm(simplified[0]["x"], simplified[0]["y"])

        lines.append(f"")
        lines.append(f"; --- trace: {net} ({len(simplified)} points) ---")

        # Rapid to start position (lifted)
        lines.append(f"G0 Z{ink_z + z_hop:.3f} F{travel_speed}")
        lines.append(f"G0 X{start_x:.3f} Y{start_y:.3f} F{travel_speed}")

        # Lower to ink Z
        lines.append(f"G0 Z{ink_z:.3f} F1000")

        # Trace the path
        for pt in simplified[1:]:
            x, y = grid_to_mm(pt["x"], pt["y"])
            lines.append(f"G1 X{x:.3f} Y{y:.3f} F{draw_speed}")

        # Lift after trace
        lines.append(f"G0 Z{ink_z + z_hop:.3f} F1000")

    lines.append("")
    lines.append("; Unretract — restore filament state before next M601 pause")
    lines.append("G1 E2.00000 F1500 ; unretract after ink travels")
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
    pcb_layout: dict,
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

    board_outline = pcb_layout.get("board", {}).get("outline_polygon", [])
    if board_outline:
        o_min_x, o_min_y, _, _ = polygon_bounds(board_outline)
    else:
        o_min_x = o_min_y = 0.0

    grid = hw.grid_resolution

    def grid_to_mm(gx: float, gy: float) -> tuple[float, float]:
        return gx * grid + o_min_x, gy * grid + o_min_y

    segments: list[tuple[float, float, float, float]] = []
    for trace in traces:
        path = trace.get("path", [])
        simplified = _simplify_path(path)
        if len(simplified) < 2:
            continue
        for j in range(len(simplified) - 1):
            x1, y1 = grid_to_mm(simplified[j]["x"], simplified[j]["y"])
            x2, y2 = grid_to_mm(simplified[j + 1]["x"], simplified[j + 1]["y"])
            segments.append((x1, y1, x2, y2))

    log.info("Extracted %d trace segments from %d traces", len(segments), len(traces))
    return segments


# ── Path simplification ───────────────────────────────────────────

def _simplify_path(path: list[dict]) -> list[dict]:
    """Remove collinear intermediate points, keeping corners only."""
    if len(path) <= 2:
        return list(path)

    result = [path[0]]
    for i in range(1, len(path) - 1):
        prev = path[i - 1]
        curr = path[i]
        nxt = path[i + 1]

        # Direction from prev→curr vs curr→nxt
        dx1 = curr["x"] - prev["x"]
        dy1 = curr["y"] - prev["y"]
        dx2 = nxt["x"] - curr["x"]
        dy2 = nxt["y"] - curr["y"]

        # Keep if direction changes
        if (dx1, dy1) != (dx2, dy2):
            result.append(curr)

    result.append(path[-1])
    return result

"""Router output dataclasses and configuration constants."""

from __future__ import annotations

from dataclasses import dataclass, field


# ── Output dataclasses ─────────────────────────────────────────────


@dataclass
class Trace:
    """A routed trace segment belonging to a net."""

    net_id: str
    path: list[tuple[float, float]]    # waypoints in mm, Manhattan segments


@dataclass
class RoutingResult:
    """Complete routing result, ready for the SCAD generator."""

    traces: list[Trace]
    pin_assignments: dict[str, str]     # "mcu_1:gpio" -> "mcu_1:PD2"
    failed_nets: list[str]

    @property
    def ok(self) -> bool:
        return len(self.failed_nets) == 0


# ── Configuration ──────────────────────────────────────────────────

GRID_RESOLUTION_MM = 0.5            # grid cell size in mm
TRACE_WIDTH_MM = 0.8                # conductive ink trace width
TRACE_CLEARANCE_MM = 0.5            # minimum gap between traces
EDGE_CLEARANCE_MM = 1.5             # minimum distance from traces to outline edge

TURN_PENALTY = 5                    # A* cost penalty for changing direction
CROSSING_PENALTY = 500              # A* cost for crossing an occupied cell (rip-up mode)

MAX_RIP_UP_ATTEMPTS = 20           # outer random-ordering attempts
INNER_RIP_UP_LIMIT = 15            # inner rip-up iterations per attempt
TIME_BUDGET_S = 30.0                # maximum wall-clock time for routing

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


# ── Router configuration ──────────────────────────────────────────
#
# All magic numbers live here.  Import the module-level constants
# for backward-compat, or pass a RouterConfig to route_traces().


@dataclass
class RouterConfig:
    """All tuneable router parameters in one place."""

    grid_resolution_mm: float = 0.5      # grid cell size in mm
    trace_width_mm: float = 1.0          # conductive ink trace width
    trace_clearance_mm: float = 2.0      # minimum gap between traces
    edge_clearance_mm: float = 1.5       # min distance from traces to outline edge

    turn_penalty: int = 5                # A* cost penalty for changing direction
    crossing_penalty: int = 500          # A* cost for crossing an occupied cell (rip-up)

    max_rip_up_attempts: int = 20        # outer random-ordering attempts
    inner_rip_up_limit: int = 100        # inner rip-up iterations per attempt
    time_budget_s: float = 60.0          # maximum wall-clock time for routing


# Module-level defaults (used when no RouterConfig is passed)
_DEFAULT_CFG = RouterConfig()

GRID_RESOLUTION_MM = _DEFAULT_CFG.grid_resolution_mm
TRACE_WIDTH_MM = _DEFAULT_CFG.trace_width_mm
TRACE_CLEARANCE_MM = _DEFAULT_CFG.trace_clearance_mm
EDGE_CLEARANCE_MM = _DEFAULT_CFG.edge_clearance_mm

TURN_PENALTY = _DEFAULT_CFG.turn_penalty
CROSSING_PENALTY = _DEFAULT_CFG.crossing_penalty

MAX_RIP_UP_ATTEMPTS = _DEFAULT_CFG.max_rip_up_attempts
INNER_RIP_UP_LIMIT = _DEFAULT_CFG.inner_rip_up_limit
TIME_BUDGET_S = _DEFAULT_CFG.time_budget_s

"""Routing serialization — JSON conversion."""

from __future__ import annotations

from src.pipeline.design.models import Net

from .models import Trace, JumperWire, RoutingResult


def routing_to_dict(result: RoutingResult) -> dict:
    """Serialize a RoutingResult to a JSON-safe dict."""
    d: dict = {
        "traces": [
            {
                "net_id": t.net_id,
                "path": [list(p) for p in t.path],
            }
            for t in result.traces
        ],
        "pin_assignments": dict(result.pin_assignments),
        "failed_nets": list(result.failed_nets),
        "jumpers": [
            {
                "net_id": j.net_id,
                "start": list(j.start),
                "end": list(j.end),
                "length_mm": j.length_mm,
            }
            for j in result.jumpers
        ],
    }
    return d


def parse_routing(data: dict) -> RoutingResult:
    """Parse a routing.json dict back into a RoutingResult."""
    traces = [
        Trace(
            net_id=t["net_id"],
            path=[tuple(p) for p in t["path"]],
        )
        for t in data.get("traces", [])
    ]

    pin_assignments = dict(data.get("pin_assignments", {}))
    failed_nets = list(data.get("failed_nets", []))
    jumpers = [
        JumperWire(
            net_id=j["net_id"],
            start=tuple(j["start"]),
            end=tuple(j["end"]),
            length_mm=j["length_mm"],
        )
        for j in data.get("jumpers", [])
    ]

    return RoutingResult(
        traces=traces,
        pin_assignments=pin_assignments,
        failed_nets=failed_nets,
        jumpers=jumpers,
    )

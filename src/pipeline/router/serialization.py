"""Routing serialization — JSON conversion."""

from __future__ import annotations

from src.pipeline.design.models import Net

from .models import Trace, JumperWire, JumperEndpoint, RoutingResult


def _endpoint_to_dict(ep: JumperEndpoint) -> dict:
    d: dict = {"x": ep.x, "y": ep.y}
    if ep.pin_center is not None:
        d["pin_center"] = list(ep.pin_center)
        d["pin_radius_mm"] = ep.pin_radius_mm
    return d


def _endpoint_from_dict(d: dict | list) -> JumperEndpoint:
    if isinstance(d, list):
        return JumperEndpoint(x=d[0], y=d[1])
    pin_center = tuple(d["pin_center"]) if "pin_center" in d else None
    return JumperEndpoint(
        x=d["x"], y=d["y"],
        pin_center=pin_center,
        pin_radius_mm=d.get("pin_radius_mm", 0.0),
    )


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
                "start": _endpoint_to_dict(j.start),
                "end": _endpoint_to_dict(j.end),
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
            start=_endpoint_from_dict(j["start"]),
            end=_endpoint_from_dict(j["end"]),
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

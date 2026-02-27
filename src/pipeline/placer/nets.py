"""Net connectivity graph for placement scoring."""

from __future__ import annotations

from dataclasses import dataclass

from src.catalog.models import Component
from src.pipeline.design.models import Net


@dataclass
class NetEdge:
    """An edge in the net-connectivity graph between two instances."""
    net_id: str
    other_iid: str
    my_pins: list[str]
    other_pins: list[str]


def build_net_graph(nets: list[Net]) -> dict[str, list[NetEdge]]:
    """Build net connectivity: instance_id -> [NetEdge, ...].

    For each net, creates edges between every pair of participating
    component instances.  Used during scoring to compute pin-to-pin
    proximity.
    """
    graph: dict[str, list[NetEdge]] = {}

    for net in nets:
        # Group pins by instance
        by_inst: dict[str, list[str]] = {}
        for ref in net.pins:
            if ":" not in ref:
                continue
            iid, pid = ref.split(":", 1)
            by_inst.setdefault(iid, []).append(pid)

        iids = list(by_inst.keys())
        for i, a in enumerate(iids):
            for b in iids[i + 1:]:
                graph.setdefault(a, []).append(
                    NetEdge(net.id, b, by_inst[a], by_inst[b]))
                graph.setdefault(b, []).append(
                    NetEdge(net.id, a, by_inst[b], by_inst[a]))

    return graph


def resolve_pin_positions(
    pin_ids: list[str],
    cat: Component,
) -> list[tuple[float, float]]:
    """Get local positions for a list of pin IDs or group IDs.

    For group IDs (MCU gpio, etc.) returns the centroid of all pins
    in that group.  The router will later resolve the exact pin.
    """
    pin_map = {p.id: p.position_mm for p in cat.pins}
    group_map: dict[str, tuple[float, float]] = {}
    if cat.pin_groups:
        for g in cat.pin_groups:
            g_pins = [pin_map[p] for p in g.pin_ids if p in pin_map]
            if g_pins:
                group_map[g.id] = (
                    sum(p[0] for p in g_pins) / len(g_pins),
                    sum(p[1] for p in g_pins) / len(g_pins),
                )

    positions: list[tuple[float, float]] = []
    for pid in pin_ids:
        if pid in pin_map:
            positions.append(pin_map[pid])
        elif pid in group_map:
            positions.append(group_map[pid])
    return positions

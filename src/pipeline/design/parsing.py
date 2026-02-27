"""Design spec parsing — convert raw dicts/JSON into DesignSpec."""

from __future__ import annotations

from .models import (
    ComponentInstance, Net, OutlineVertex, Outline, UIPlacement, DesignSpec,
)


def parse_design(data: dict) -> DesignSpec:
    """Parse a raw dict (from JSON / tool input) into a DesignSpec."""
    components = [
        ComponentInstance(
            catalog_id=c["catalog_id"],
            instance_id=c["instance_id"],
            config=c.get("config"),
            mounting_style=c.get("mounting_style"),
        )
        for c in data["components"]
    ]

    nets = [
        Net(id=n["id"], pins=list(n["pins"]))
        for n in data["nets"]
    ]

    outline_data = data["outline"]
    outline = _parse_outline(outline_data)

    ui_placements = [
        UIPlacement(
            instance_id=p["instance_id"],
            x_mm=float(p["x_mm"]),
            y_mm=float(p["y_mm"]),
            edge_index=p.get("edge_index"),
        )
        for p in data["ui_placements"]
    ]

    return DesignSpec(
        components=components,
        nets=nets,
        outline=outline,
        ui_placements=ui_placements,
    )


def _parse_outline(data: list) -> Outline:
    """Parse outline from a flat list of vertex objects.

    Format:
        [{"x": 0, "y": 0}, {"x": 30, "y": 0}, {"x": 30, "y": 80, "ease_in": 8}]
    """
    points = []
    for v in data:
        raw_in = v.get("ease_in")
        raw_out = v.get("ease_out")
        # If only one side is given, mirror it to the other
        if raw_in is not None and raw_out is None:
            raw_out = raw_in
        elif raw_out is not None and raw_in is None:
            raw_in = raw_out
        points.append(OutlineVertex(
            x=float(v["x"]),
            y=float(v["y"]),
            ease_in=float(raw_in) if raw_in else 0,
            ease_out=float(raw_out) if raw_out else 0,
        ))
    return Outline(points=points)

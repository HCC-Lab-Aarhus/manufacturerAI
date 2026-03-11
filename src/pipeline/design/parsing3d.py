"""Parse 3D design JSON into DesignSpec3D dataclasses."""

from __future__ import annotations

from .models import ComponentInstance, Net
from .models3d import CSGNode, SurfacePlacement, DesignSpec3D


def parse_design_3d(data: dict) -> DesignSpec3D:
    """Parse a raw dict (from JSON / tool input) into a DesignSpec3D."""
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

    shape = _parse_csg_node(data["shape"])

    surface_placements = [
        SurfacePlacement(
            instance_id=sp["instance_id"],
            position=tuple(float(v) for v in sp["position"]) if "position" in sp else (0.0, 0.0, 0.0),
            face_hint=sp.get("face_hint"),
            rotation_deg=float(sp.get("rotation_deg", 0.0)),
            offset_mm=tuple(float(v) for v in sp["offset_mm"]) if "offset_mm" in sp else None,
        )
        for sp in data.get("surface_placements", [])
    ]

    return DesignSpec3D(
        components=components,
        nets=nets,
        shape=shape,
        surface_placements=surface_placements,
    )


def _parse_csg_node(data: dict) -> CSGNode:
    """Recursively parse a CSG node dict into a CSGNode dataclass."""
    if "op" in data:
        children = [_parse_csg_node(c) for c in data.get("children", [])]
        return CSGNode(
            op=data["op"],
            children=children,
            center=_parse_vec3(data.get("center"), (0.0, 0.0, 0.0)),
            rotate=_parse_vec3(data.get("rotate")),
        )
    elif "type" in data:
        return CSGNode(
            type=data["type"],
            center=_parse_vec3(data.get("center"), (0.0, 0.0, 0.0)),
            size=_parse_vec3(data.get("size")),
            radius=_float_or_none(data.get("radius")),
            height=_float_or_none(data.get("height")),
            axis=data.get("axis", "z"),
            top_radius=_float_or_none(data.get("top_radius")),
            rotate=_parse_vec3(data.get("rotate")),
        )
    else:
        raise ValueError("CSG node must have 'type' (primitive) or 'op' (boolean)")


def _parse_vec3(
    val: list | tuple | None,
    default: tuple[float, float, float] | None = None,
) -> tuple[float, float, float] | None:
    """Parse a 3-element list/tuple into a float tuple, or return default."""
    if val is None:
        return default
    if len(val) != 3:
        raise ValueError(f"Expected 3-element vector, got {len(val)}")
    return (float(val[0]), float(val[1]), float(val[2]))


def _float_or_none(val) -> float | None:
    return float(val) if val is not None else None

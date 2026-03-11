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
        # radius: number → scalar, array → per-axis radii
        radius_val = data.get("radius")
        radius, radii = _parse_scalar_or_tuple(radius_val)

        # radius_top (accept legacy "top_radius" alias)
        radius_top_val = data.get("radius_top", data.get("top_radius"))
        radius_top, radii_top = _parse_scalar_or_tuple(radius_top_val)

        # size: number → cube, array → [x,y,z]
        size_val = data.get("size")
        if isinstance(size_val, (int, float)):
            v = float(size_val)
            size: tuple[float, float, float] | None = (v, v, v)
        else:
            size = _parse_vec3(size_val)

        size_top = _parse_vec3(data.get("size_top"))

        return CSGNode(
            type=data["type"],
            center=_parse_vec3(data.get("center"), (0.0, 0.0, 0.0)),
            size=size,
            size_top=size_top,
            radius=radius,
            radii=radii,
            height=_float_or_none(data.get("height")),
            axis=data.get("axis", "z"),
            radius_top=radius_top,
            radii_top=radii_top,
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


def _parse_scalar_or_tuple(
    val,
) -> tuple[float | None, tuple[float, ...] | None]:
    """Parse a value that can be a scalar or an array.

    Returns ``(scalar, None)`` if *val* is a number,
    ``(None, tuple)`` if *val* is a list/tuple,
    or ``(None, None)`` if *val* is None.
    """
    if val is None:
        return None, None
    if isinstance(val, (list, tuple)):
        return None, tuple(float(v) for v in val)
    return float(val), None


def _float_or_none(val) -> float | None:
    return float(val) if val is not None else None

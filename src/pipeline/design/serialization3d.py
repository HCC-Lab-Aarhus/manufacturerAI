"""Serialize DesignSpec3D to JSON-safe dicts."""

from __future__ import annotations

from .models3d import CSGNode, SurfacePlacement, DesignSpec3D


def design3d_to_dict(spec: DesignSpec3D) -> dict:
    """Convert a DesignSpec3D to a JSON-serializable dict."""
    return {
        "components": [
            {
                "catalog_id": ci.catalog_id,
                "instance_id": ci.instance_id,
                **({
                    "config": ci.config} if ci.config else {}),
                **({
                    "mounting_style": ci.mounting_style} if ci.mounting_style else {}),
            }
            for ci in spec.components
        ],
        "nets": [
            {"id": n.id, "pins": n.pins}
            for n in spec.nets
        ],
        "shape": _csg_node_to_dict(spec.shape),
        "surface_placements": [
            _surface_placement_to_dict(sp)
            for sp in spec.surface_placements
        ],
    }


def _csg_node_to_dict(node: CSGNode) -> dict:
    """Recursively serialize a CSGNode to a dict."""
    d: dict = {}

    if node.is_primitive:
        d["type"] = node.type
        if node.center != (0.0, 0.0, 0.0):
            d["center"] = list(node.center)
        if node.size is not None:
            d["size"] = list(node.size)
        if node.size_top is not None:
            d["size_top"] = list(node.size_top)
        if node.radii is not None:
            d["radius"] = list(node.radii)
        elif node.radius is not None:
            d["radius"] = node.radius
        if node.radii_top is not None:
            d["radius_top"] = list(node.radii_top)
        elif node.radius_top is not None:
            d["radius_top"] = node.radius_top
        if node.height is not None:
            d["height"] = node.height
        if node.axis != "z":
            d["axis"] = node.axis
    elif node.is_operation:
        d["op"] = node.op
        d["children"] = [_csg_node_to_dict(c) for c in node.children]

    if node.rotate is not None:
        d["rotate"] = list(node.rotate)

    return d


def _surface_placement_to_dict(sp: SurfacePlacement) -> dict:
    """Serialize a SurfacePlacement."""
    d: dict = {
        "instance_id": sp.instance_id,
        "position": list(sp.position),
    }
    if sp.face_hint:
        d["face_hint"] = sp.face_hint
    if sp.rotation_deg != 0.0:
        d["rotation_deg"] = sp.rotation_deg
    if sp.offset_mm is not None:
        d["offset_mm"] = list(sp.offset_mm)
    if sp.snapped_position is not None:
        d["snapped_position"] = list(sp.snapped_position)
    if sp.surface_normal is not None:
        d["surface_normal"] = list(sp.surface_normal)
    if sp.face_id is not None:
        d["face_id"] = sp.face_id
    return d

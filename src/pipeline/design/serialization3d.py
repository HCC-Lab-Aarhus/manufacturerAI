"""Serialize DesignSpec3D to JSON-safe dicts."""

from __future__ import annotations

from .models3d import CSGNode, FitMarker, SurfacePlacement, DesignSpec3D


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

        # size / size_end — may contain per-axis fit markers
        if "size" in node.fit:
            d["size"] = _fit_to_json(node.fit["size"])
        elif node.size is not None:
            d["size"] = _inject_axis_fits(list(node.size), "size", node.fit)
        if "size_end" in node.fit:
            d["size_end"] = _fit_to_json(node.fit["size_end"])
        elif node.size_end is not None:
            d["size_end"] = _inject_axis_fits(list(node.size_end), "size_end", node.fit)

        # radius
        if "radius" in node.fit:
            d["radius"] = _fit_to_json(node.fit["radius"])
        elif node.radii is not None:
            d["radius"] = list(node.radii)
        elif node.radius is not None:
            d["radius"] = node.radius

        # radius_end
        if "radius_end" in node.fit:
            d["radius_end"] = _fit_to_json(node.fit["radius_end"])
        elif node.radii_end is not None:
            d["radius_end"] = list(node.radii_end)
        elif node.radius_end is not None:
            d["radius_end"] = node.radius_end

        # height
        if "height" in node.fit:
            d["height"] = _fit_to_json(node.fit["height"])
        elif node.height is not None:
            d["height"] = node.height

        if node.axis != "z":
            d["axis"] = node.axis
    elif node.is_operation:
        d["op"] = node.op
        d["children"] = [_csg_node_to_dict(c) for c in node.children]

    if node.rotate is not None:
        d["rotate"] = list(node.rotate)

    return d


def _fit_to_json(marker: FitMarker):
    """Serialize a FitMarker back to JSON form."""
    if marker.cap is None:
        return "fit"
    return {"fit": marker.cap}


_AXES = ("x", "y", "z")


def _inject_axis_fits(
    vec: list, field_name: str, fit: dict[str, FitMarker],
) -> list:
    """Replace elements in a 3-element list with fit markers where present."""
    for i, axis in enumerate(_AXES):
        key = f"{field_name}.{axis}"
        if key in fit:
            vec[i] = _fit_to_json(fit[key])
    return vec


def _surface_placement_to_dict(sp: SurfacePlacement) -> dict:
    """Serialize a SurfacePlacement."""
    d: dict = {
        "instance_id": sp.instance_id,
        "face": sp.face,
        "at": list(sp.at),
    }
    if sp.rotation_deg != 0.0:
        d["rotation_deg"] = sp.rotation_deg
    if sp.snapped_position is not None:
        d["snapped_position"] = list(sp.snapped_position)
    if sp.surface_normal is not None:
        d["surface_normal"] = list(sp.surface_normal)
    if sp.face_id is not None:
        d["face_id"] = sp.face_id
    return d

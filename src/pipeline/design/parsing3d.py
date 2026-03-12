"""Parse 3D design JSON into DesignSpec3D dataclasses."""

from __future__ import annotations

from .models import ComponentInstance, Net
from .models3d import CSGNode, FitMarker, SurfacePlacement, DesignSpec3D


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
        _parse_surface_placement(sp)
        for sp in data.get("surface_placements", [])
    ]

    return DesignSpec3D(
        components=components,
        nets=nets,
        shape=shape,
        surface_placements=surface_placements,
    )


def _parse_surface_placement(sp: dict) -> SurfacePlacement:
    """Parse a single surface placement dict."""
    at = tuple(float(v) for v in sp["at"]) if "at" in sp else (0.0, 0.0, 0.0)

    return SurfacePlacement(
        instance_id=sp["instance_id"],
        face=sp.get("face", "top"),
        at=at,
        rotation_deg=float(sp.get("rotation_deg", 0.0)),
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
        ptype = data["type"]
        fit: dict[str, FitMarker] = {}

        # radius: number → scalar, array → per-axis radii, "fit" → marker
        radius_val = data.get("radius")
        radius_val, _fit = _extract_fit(radius_val)
        if _fit is not None:
            fit["radius"] = _fit
        radius, radii = _parse_scalar_or_tuple(radius_val)

        radius_end_val = data.get("radius_end")
        radius_end_val, _fit = _extract_fit(radius_end_val)
        if _fit is not None:
            fit["radius_end"] = _fit
        radius_end, radii_end = _parse_scalar_or_tuple(radius_end_val)

        # height
        height_val = data.get("height")
        height_val, _fit = _extract_fit(height_val)
        if _fit is not None:
            fit["height"] = _fit
        height = _float_or_none(height_val)

        # size: number → cube, array → [x,y,z], may contain per-element "fit"
        size_val = data.get("size")
        size_val, fit = _extract_fit_vec3(size_val, "size", fit)
        if isinstance(size_val, (int, float)):
            v = float(size_val)
            size: tuple[float, float, float] | None = (v, v, v)
        else:
            size = _parse_vec3(size_val)

        size_end_val = data.get("size_end")
        size_end_val, fit = _extract_fit_vec3(size_end_val, "size_end", fit)
        size_end = _parse_vec3(size_end_val)

        return CSGNode(
            type=ptype,
            center=_parse_vec3(data.get("center"), (0.0, 0.0, 0.0)),
            size=size,
            size_end=size_end,
            radius=radius,
            radii=radii,
            height=height,
            axis=data.get("axis", "z"),
            radius_end=radius_end,
            radii_end=radii_end,
            rotate=_parse_vec3(data.get("rotate")),
            fit=fit,
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


def _is_fit(val) -> bool:
    """Check if a value is a fit marker (``"fit"`` or ``{"fit": N}``)."""
    if val == "fit":
        return True
    if isinstance(val, dict) and "fit" in val:
        return True
    return False


def _extract_fit(val):
    """If *val* is a fit marker, return ``(None, FitMarker)``.
    Otherwise return ``(val, None)`` unchanged."""
    if val == "fit":
        return None, FitMarker()
    if isinstance(val, dict) and "fit" in val:
        cap = val["fit"]
        return None, FitMarker(cap=float(cap) if cap is not None else None)
    return val, None


_SIZE_AXES = ("x", "y", "z")


def _extract_fit_vec3(
    val,
    field_name: str,
    fit: dict[str, FitMarker],
) -> tuple:
    """Handle per-element ``"fit"`` inside a 3-element vector like ``size``.

    Returns ``(cleaned_val, updated_fit_dict)``.  Elements that are fit
    markers are replaced with ``0.0`` in the vector so downstream parsing
    still gets a valid tuple, and per-axis keys like ``size.x`` are added
    to the fit dict.
    """
    if val is None or isinstance(val, (int, float)):
        return val, fit
    if not isinstance(val, (list, tuple)) or len(val) != 3:
        return val, fit
    cleaned = list(val)
    for i, elem in enumerate(val):
        if _is_fit(elem):
            _, marker = _extract_fit(elem)
            fit[f"{field_name}.{_SIZE_AXES[i]}"] = marker
            cleaned[i] = 0.0
    return cleaned, fit

"""Validate a DesignSpec3D against the catalog."""

from __future__ import annotations

from src.catalog import CatalogResult
from .models3d import CSGNode, DesignSpec3D

_VALID_PRIMITIVES = {"box", "cylinder", "sphere"}
_VALID_OPS = {"union", "difference", "intersection"}
_VALID_AXES = {"x", "y", "z"}
_VALID_HINTS = {"top", "bottom", "front", "back", "left", "right"}


def validate_design_3d(
    spec: DesignSpec3D,
    catalog: CatalogResult,
) -> list[str]:
    """Validate a 3D design spec. Returns error messages (empty = valid)."""
    errors: list[str] = []
    catalog_map = {c.id: c for c in catalog.components}

    # Component validation (same rules as 2D)
    seen_ids: set[str] = set()
    for ci in spec.components:
        if ci.catalog_id not in catalog_map:
            errors.append(f"Component '{ci.instance_id}': unknown catalog_id '{ci.catalog_id}'")
        if ci.instance_id in seen_ids:
            errors.append(f"Duplicate instance_id '{ci.instance_id}'")
        seen_ids.add(ci.instance_id)

    instance_to_catalog = {}
    for ci in spec.components:
        if ci.catalog_id in catalog_map:
            instance_to_catalog[ci.instance_id] = catalog_map[ci.catalog_id]

    # Mounting style overrides
    for ci in spec.components:
        if ci.mounting_style and ci.catalog_id in catalog_map:
            cat = catalog_map[ci.catalog_id]
            if ci.mounting_style not in cat.mounting.allowed_styles:
                errors.append(
                    f"Component '{ci.instance_id}': mounting_style '{ci.mounting_style}' "
                    f"not in allowed_styles {cat.mounting.allowed_styles}"
                )

    # Net validation
    for net in spec.nets:
        if len(net.pins) < 2:
            errors.append(f"Net '{net.id}': must have at least 2 pins")
        for pin_ref in net.pins:
            if ":" not in pin_ref:
                errors.append(f"Net '{net.id}': invalid pin reference '{pin_ref}'")
                continue
            iid, pid = pin_ref.split(":", 1)
            if iid not in seen_ids:
                errors.append(f"Net '{net.id}': unknown instance '{iid}' in '{pin_ref}'")
                continue
            if iid not in instance_to_catalog:
                continue
            cat = instance_to_catalog[iid]
            pin_ids = {p.id for p in cat.pins}
            group_ids = {g.id for g in cat.pin_groups} if cat.pin_groups else set()
            if pid not in pin_ids and pid not in group_ids:
                errors.append(
                    f"Net '{net.id}': unknown pin/group '{pid}' on '{iid}' (catalog: {cat.id})"
                )

    # CSG shape validation
    _validate_csg_node(spec.shape, errors, path="shape")

    # Surface placement validation
    for sp in spec.surface_placements:
        if sp.instance_id not in seen_ids:
            errors.append(f"Surface placement: unknown instance '{sp.instance_id}'")
        if len(sp.position) != 3:
            errors.append(
                f"Surface placement '{sp.instance_id}': position must be [x, y, z]"
            )
        if sp.face_hint and sp.face_hint not in _VALID_HINTS:
            errors.append(
                f"Surface placement '{sp.instance_id}': invalid face_hint '{sp.face_hint}'"
            )

    return errors


def _validate_csg_node(node: CSGNode, errors: list[str], path: str) -> None:
    """Recursively validate a CSG node."""
    if node.is_primitive:
        if node.type not in _VALID_PRIMITIVES:
            errors.append(f"{path}: unknown primitive type '{node.type}'")

        if node.type == "box":
            if node.size is None:
                errors.append(f"{path}: box requires 'size' [x, y, z]")
            elif any(s <= 0 for s in node.size):
                errors.append(f"{path}: box size dimensions must be > 0")
            if node.size_top is not None:
                if any(s < 0 for s in node.size_top):
                    errors.append(f"{path}: size_top dimensions must be >= 0")
                if node.axis not in _VALID_AXES:
                    errors.append(f"{path}: axis must be 'x', 'y', or 'z'")

        elif node.type == "sphere":
            _validate_radius_fields(node, errors, path, radii_len=3)

        elif node.type == "cylinder":
            _validate_radius_fields(node, errors, path, radii_len=2)
            if node.height is None:
                errors.append(f"{path}: cylinder requires 'height'")
            elif node.height <= 0:
                errors.append(f"{path}: height must be > 0")
            if node.axis not in _VALID_AXES:
                errors.append(f"{path}: axis must be 'x', 'y', or 'z'")
            if node.radius_top is not None and node.radius_top < 0:
                errors.append(f"{path}: radius_top must be >= 0")
            if node.radii_top is not None:
                if len(node.radii_top) != 2:
                    errors.append(f"{path}: radii_top must have 2 elements [ra, rb]")
                elif any(r < 0 for r in node.radii_top):
                    errors.append(f"{path}: radii_top values must be >= 0")

    elif node.is_operation:
        if node.op not in _VALID_OPS:
            errors.append(f"{path}: unknown operation '{node.op}'")
        if len(node.children) < 2:
            errors.append(f"{path}: operation '{node.op}' requires at least 2 children")
        for i, child in enumerate(node.children):
            _validate_csg_node(child, errors, path=f"{path}.children[{i}]")
    else:
        errors.append(f"{path}: node must have 'type' (primitive) or 'op' (operation)")


def _validate_radius_fields(
    node: CSGNode, errors: list[str], path: str, radii_len: int,
) -> None:
    """Validate that a node has exactly one of radius/radii and values are > 0."""
    has_scalar = node.radius is not None
    has_array = node.radii is not None
    if not has_scalar and not has_array:
        errors.append(f"{path}: {node.type} requires 'radius'")
        return
    if has_scalar and node.radius <= 0:
        errors.append(f"{path}: radius must be > 0")
    if has_array:
        if len(node.radii) != radii_len:
            errors.append(
                f"{path}: {node.type} radii must have {radii_len} "
                f"element{'s' if radii_len > 1 else ''}"
            )
        elif any(r <= 0 for r in node.radii):
            errors.append(f"{path}: all radii must be > 0")

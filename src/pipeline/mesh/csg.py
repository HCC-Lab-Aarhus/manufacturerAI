"""CSG tree evaluation — converts a CSGNode tree into a trimesh triangle mesh.

Supports primitives (box, cylinder, sphere) — each optionally tapered — and
boolean operations (union, difference, intersection) using the manifold3d
engine.
"""

from __future__ import annotations

import logging
import math

import numpy as np
import trimesh

from src.pipeline.design.models3d import CSGNode, FitMarker

log = logging.getLogger(__name__)

_CYLINDER_SECTIONS = 48
_SPHERE_SUBDIVISIONS = 3


def _has_fit(node: CSGNode) -> bool:
    """Return True if *node* (or any descendant) uses fit markers."""
    if node.fit:
        return True
    return any(_has_fit(c) for c in node.children)


def _apply_op(
    accumulated: trimesh.Trimesh, other: trimesh.Trimesh, op: str,
) -> trimesh.Trimesh:
    if op == "union":
        return accumulated.union(other, engine="manifold")
    elif op == "difference":
        return accumulated.difference(other, engine="manifold")
    elif op == "intersection":
        return accumulated.intersection(other, engine="manifold")
    raise ValueError(f"Unknown CSG operation: {op}")


def evaluate_csg(
    node: CSGNode,
    context: trimesh.Trimesh | None = None,
) -> trimesh.Trimesh:
    """Recursively evaluate a CSGNode tree into a single trimesh mesh.

    *context* is the accumulated sibling mesh, passed in so that ``fit``
    markers can ray-cast against it.
    """
    if node.is_primitive:
        if node.fit:
            mesh = _create_primitive_fit(node, context)
        else:
            mesh = _create_primitive(node)
    elif node.is_operation:
        if not node.children:
            raise ValueError("CSG operation node has no children")
        # Evaluate children sequentially so later children can use the
        # accumulated result as context for fit resolution.
        accumulated = None
        for child in node.children:
            child_mesh = evaluate_csg(child, context=accumulated)
            if accumulated is None:
                accumulated = child_mesh
            else:
                accumulated = _apply_op(accumulated, child_mesh, node.op)
        mesh = accumulated
    else:
        raise ValueError("CSGNode must be a primitive or an operation")

    if node.rotate:
        rx, ry, rz = (math.radians(a) for a in node.rotate)
        R = trimesh.transformations.euler_matrix(rx, ry, rz, axes="sxyz")
        mesh.apply_transform(R)

    return mesh


# ── Primitive dispatch ─────────────────────────────────────────────

def _create_primitive(node: CSGNode) -> trimesh.Trimesh:
    """Create a trimesh mesh from a CSG primitive node."""
    ptype = node.type

    if ptype == "box":
        mesh = _build_box(node)
        if node.size_end is not None:
            mesh = _align_to_axis(mesh, node.axis)

    elif ptype == "sphere":
        mesh = _build_sphere(node)

    elif ptype == "cylinder":
        mesh = _build_cylinder(node)
        mesh = _align_to_axis(mesh, node.axis)

    else:
        raise ValueError(f"Unknown primitive type: {ptype}")

    if node.center != (0.0, 0.0, 0.0):
        mesh.apply_translation(node.center)

    return mesh


# ── Shape builders ─────────────────────────────────────────────────

def _build_box(node: CSGNode) -> trimesh.Trimesh:
    if node.size is None:
        raise ValueError("Box requires 'size'")
    if node.size_end is None:
        return trimesh.creation.box(extents=node.size)
    axis_idx = {"x": 0, "y": 1, "z": 2}[node.axis]
    cross = [i for i in range(3) if i != axis_idx]
    height = node.size[axis_idx]
    bx, by = node.size[cross[0]], node.size[cross[1]]
    tx, ty = node.size_end[cross[0]], node.size_end[cross[1]]
    return _create_tapered_box(bx, by, tx, ty, height)


def _build_sphere(node: CSGNode) -> trimesh.Trimesh:
    if node.radii is not None:
        mesh = trimesh.creation.icosphere(
            subdivisions=_SPHERE_SUBDIVISIONS, radius=1.0,
        )
        mesh.vertices *= np.array(node.radii)
        return mesh
    if node.radius is None:
        raise ValueError("Sphere requires 'radius'")
    return trimesh.creation.icosphere(
        subdivisions=_SPHERE_SUBDIVISIONS, radius=node.radius,
    )


def _build_cylinder(node: CSGNode) -> trimesh.Trimesh:
    if node.height is None:
        raise ValueError("Cylinder requires 'height'")
    has_taper = node.radius_end is not None or node.radii_end is not None
    if has_taper:
        brx, bry = _resolve_radii_pair(node.radius, node.radii)
        trx, try_ = _resolve_radii_pair(node.radius_end, node.radii_end, default=0.0)
        return _create_frustum(brx, bry, trx, try_, node.height, _CYLINDER_SECTIONS)
    if node.radii is not None:
        mesh = trimesh.creation.cylinder(
            radius=1.0, height=node.height, sections=_CYLINDER_SECTIONS,
        )
        mesh.vertices[:, 0] *= node.radii[0]
        mesh.vertices[:, 1] *= node.radii[1]
        return mesh
    if node.radius is None:
        raise ValueError("Cylinder requires 'radius'")
    return trimesh.creation.cylinder(
        radius=node.radius, height=node.height, sections=_CYLINDER_SECTIONS,
    )


# ── Mesh constructors ─────────────────────────────────────────────

def _create_frustum(
    brx: float, bry: float,
    trx: float, try_: float,
    height: float, sections: int,
) -> trimesh.Trimesh:
    """Build a frustum / cone along Z with independent elliptical radii."""
    angles = np.linspace(0, 2 * np.pi, sections, endpoint=False)
    cos_a = np.cos(angles)
    sin_a = np.sin(angles)
    hz = height / 2

    verts: list[list[float]] = []
    faces: list[list[int]] = []

    # Bottom ring
    for i in range(sections):
        verts.append([brx * cos_a[i], bry * sin_a[i], -hz])

    top_is_point = trx <= 0 and try_ <= 0

    if top_is_point:
        apex = len(verts)
        verts.append([0.0, 0.0, hz])
        for i in range(sections):
            j = (i + 1) % sections
            faces.append([i, j, apex])
    else:
        top_start = len(verts)
        for i in range(sections):
            verts.append([trx * cos_a[i], try_ * sin_a[i], hz])
        for i in range(sections):
            j = (i + 1) % sections
            bi, bj = i, j
            ti, tj = top_start + i, top_start + j
            faces.append([bi, bj, tj])
            faces.append([bi, tj, ti])
        # Top cap (normal must point +z outward)
        tc = len(verts)
        verts.append([0.0, 0.0, hz])
        for i in range(sections):
            j = (i + 1) % sections
            faces.append([tc, top_start + i, top_start + j])

    # Bottom cap (normal must point −z outward)
    bc = len(verts)
    verts.append([0.0, 0.0, -hz])
    for i in range(sections):
        j = (i + 1) % sections
        faces.append([bc, j, i])

    return trimesh.Trimesh(
        vertices=np.array(verts, dtype=float),
        faces=np.array(faces, dtype=int),
    )


def _create_tapered_box(
    bx: float, by: float,
    tx: float, ty: float,
    height: float,
) -> trimesh.Trimesh:
    """Build a tapered box along Z.

    Bottom rectangle *bx × by* at z = −h/2,
    top rectangle *tx × ty* at z = +h/2.
    """
    hz = height / 2
    bx2, by2 = bx / 2, by / 2
    tx2, ty2 = tx / 2, ty / 2

    verts = np.array([
        [-bx2, -by2, -hz],  # 0 bottom
        [ bx2, -by2, -hz],  # 1
        [ bx2,  by2, -hz],  # 2
        [-bx2,  by2, -hz],  # 3
        [-tx2, -ty2,  hz],  # 4 top
        [ tx2, -ty2,  hz],  # 5
        [ tx2,  ty2,  hz],  # 6
        [-tx2,  ty2,  hz],  # 7
    ], dtype=float)

    faces = np.array([
        [0, 2, 1], [0, 3, 2],      # bottom
        [4, 5, 6], [4, 6, 7],      # top
        [0, 1, 5], [0, 5, 4],      # front (−y)
        [2, 3, 7], [2, 7, 6],      # back (+y)
        [1, 2, 6], [1, 6, 5],      # right (+x)
        [3, 0, 4], [3, 4, 7],      # left (−x)
    ], dtype=int)

    mesh = trimesh.Trimesh(vertices=verts, faces=faces)
    mesh.merge_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    return mesh


# ── Helpers ────────────────────────────────────────────────────────

def _resolve_radii_pair(
    scalar: float | None,
    array: tuple[float, ...] | None,
    default: float | None = None,
) -> tuple[float, float]:
    """Return (rx, ry) from either a scalar radius or a 2-element radii tuple."""
    if array is not None:
        return array[0], array[1]
    if scalar is not None:
        return scalar, scalar
    if default is not None:
        return default, default
    raise ValueError("No radius specified")


def _align_to_axis(mesh: trimesh.Trimesh, axis: str) -> trimesh.Trimesh:
    """Rotate a Z-aligned mesh to align with the specified axis."""
    if axis == "z":
        return mesh
    elif axis == "x":
        R = trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0])
        mesh.apply_transform(R)
    elif axis == "y":
        R = trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0])
        mesh.apply_transform(R)
    return mesh


# ── Fit resolution ─────────────────────────────────────────────────

_AXIS_IDX = {"x": 0, "y": 1, "z": 2}


def _fit_cap_or_bbox(
    marker: FitMarker,
    context: trimesh.Trimesh,
    axis_idx: int | None = None,
) -> float:
    """Return the extent to use as the maximum for a fit dimension.

    If the marker has a cap, use that.  Otherwise derive from the context
    bounding box (full diagonal for radius, axis extent for height/size).
    """
    if marker.cap is not None:
        return marker.cap
    bbox = context.bounds  # [[min_x, min_y, min_z], [max_x, max_y, max_z]]
    if axis_idx is not None:
        return float(bbox[1][axis_idx] - bbox[0][axis_idx])
    diag = np.linalg.norm(bbox[1] - bbox[0])
    return float(diag)


def _create_primitive_fit(
    node: CSGNode, context: trimesh.Trimesh | None,
) -> trimesh.Trimesh:
    """Build a primitive with ``fit`` markers resolved against *context*.

    Strategy: build the primitive at maximum extent, then for each vertex
    affected by a fit dimension, ray-cast inward toward the primitive center
    and snap it to the first surface intersection on the context mesh.
    """
    if context is None:
        raise ValueError(
            "Cannot resolve 'fit' dimensions without a context mesh "
            "(node must be inside a boolean operation)"
        )

    axis_idx = _AXIS_IDX[node.axis]

    # Build a fully-resolved copy of the node with fit caps filled in
    resolved = _resolve_node_caps(node, context, axis_idx)

    # Create the primitive at maximum extent
    mesh = _create_primitive(resolved)

    # Now shrink-wrap: for each fit key, move affected vertices inward
    # until they hit the context surface
    center = np.array(resolved.center, dtype=float)
    verts = mesh.vertices.copy()

    for key, marker in node.fit.items():
        verts = _shrink_vertices(
            verts, key, center, axis_idx, context, resolved,
        )

    mesh.vertices = verts
    mesh.fix_normals()
    return mesh


def _resolve_node_caps(
    node: CSGNode, context: trimesh.Trimesh, axis_idx: int,
) -> CSGNode:
    """Return a shallow copy of *node* with fit-marked fields filled to caps."""
    import copy
    resolved = copy.copy(node)
    resolved.fit = {}  # clear so _create_primitive sees a normal node

    for key, marker in node.fit.items():
        cap = _fit_cap_or_bbox(marker, context, _axis_for_key(key, axis_idx))

        if key == "height":
            resolved.height = cap
        elif key == "radius":
            resolved.radius = cap
            resolved.radii = None
        elif key == "radius_end":
            resolved.radius_end = cap
            resolved.radii_end = None
        elif key.startswith("size."):
            ai = "xyz".index(key[-1])
            if resolved.size is None:
                resolved.size = (cap, cap, cap)
            s = list(resolved.size)
            s[ai] = cap
            resolved.size = tuple(s)
        elif key.startswith("size_end."):
            ai = "xyz".index(key[-1])
            if resolved.size_end is None:
                resolved.size_end = (cap, cap, cap)
            s = list(resolved.size_end)
            s[ai] = cap
            resolved.size_end = tuple(s)

    return resolved


def _axis_for_key(key: str, node_axis_idx: int) -> int | None:
    """Map a fit key to a bounding-box axis index, or None for radial."""
    if key == "height":
        return node_axis_idx
    if key.startswith("size.") or key.startswith("size_end."):
        return "xyz".index(key[-1])
    # radius/radius_end → radial, use None to get diagonal
    return None


def _shrink_vertices(
    verts: np.ndarray,
    fit_key: str,
    center: np.ndarray,
    axis_idx: int,
    context: trimesh.Trimesh,
    resolved: CSGNode,
) -> np.ndarray:
    """Move the vertices affected by *fit_key* inward to the context surface."""

    if fit_key == "height":
        return _shrink_height(verts, center, axis_idx, context, resolved)
    elif fit_key in ("radius", "radius_end"):
        return _shrink_radial(verts, fit_key, center, axis_idx, context, resolved)
    elif fit_key.startswith("size.") or fit_key.startswith("size_end."):
        return _shrink_size_axis(verts, fit_key, center, context, resolved)

    return verts


def _shrink_height(
    verts: np.ndarray,
    center: np.ndarray,
    axis_idx: int,
    context: trimesh.Trimesh,
    resolved: CSGNode,
) -> np.ndarray:
    """Shrink top/bottom face vertices along the axis to the context surface."""
    h = resolved.height
    if h is None:
        return verts

    mid = center[axis_idx]
    top_z = mid + h / 2
    bot_z = mid - h / 2
    tol = h * 0.001

    # Top face vertices: those near the +axis end
    top_mask = np.abs(verts[:, axis_idx] - top_z) < tol
    if np.any(top_mask):
        direction = np.zeros(3)
        direction[axis_idx] = -1.0  # inward
        verts = _raycast_shrink(verts, top_mask, direction, context)

    # Bottom face vertices: those near the -axis end
    bot_mask = np.abs(verts[:, axis_idx] - bot_z) < tol
    if np.any(bot_mask):
        direction = np.zeros(3)
        direction[axis_idx] = 1.0  # inward
        verts = _raycast_shrink(verts, bot_mask, direction, context)

    return verts


def _shrink_radial(
    verts: np.ndarray,
    fit_key: str,
    center: np.ndarray,
    axis_idx: int,
    context: trimesh.Trimesh,
    resolved: CSGNode,
) -> np.ndarray:
    """Shrink perimeter vertices radially inward toward the axis."""
    h = resolved.height or 0.0
    mid = center[axis_idx]
    tol = h * 0.001 if h else 0.5

    cross_axes = [i for i in range(3) if i != axis_idx]

    if fit_key == "radius_end":
        # Only the +axis end ring
        top_z = mid + h / 2
        mask = np.abs(verts[:, axis_idx] - top_z) < tol
    else:
        # All perimeter vertices (radius applies to main body or −axis end)
        mask = np.ones(len(verts), dtype=bool)
        # Exclude cap centroid vertices (those at the center radially)
        radial_dist = np.sqrt(
            (verts[:, cross_axes[0]] - center[cross_axes[0]]) ** 2
            + (verts[:, cross_axes[1]] - center[cross_axes[1]]) ** 2
        )
        mask &= radial_dist > 0.01
        # If we also have radius_end fit, only affect the -axis end
        if "radius_end" in resolved.fit or resolved.radius_end is not None:
            bot_z = mid - h / 2
            mask &= np.abs(verts[:, axis_idx] - bot_z) < tol

    if not np.any(mask):
        return verts

    # Direction: from each vertex radially inward toward the axis
    directions = np.zeros_like(verts[mask])
    for ci in cross_axes:
        directions[:, ci] = center[ci] - verts[mask, ci]

    norms = np.linalg.norm(directions, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-8)
    directions /= norms

    return _raycast_shrink(verts, mask, directions, context)


def _shrink_size_axis(
    verts: np.ndarray,
    fit_key: str,
    center: np.ndarray,
    context: trimesh.Trimesh,
    resolved: CSGNode,
) -> np.ndarray:
    """Shrink the two faces perpendicular to a size axis inward."""
    ai = "xyz".index(fit_key.split(".")[-1])

    is_end = fit_key.startswith("size_end.")
    sz = resolved.size_end if is_end else resolved.size
    if sz is None:
        return verts

    extent = sz[ai]
    mid = center[ai]
    pos_face = mid + extent / 2
    neg_face = mid - extent / 2
    tol = extent * 0.001

    # +face vertices
    pos_mask = np.abs(verts[:, ai] - pos_face) < tol
    if np.any(pos_mask):
        direction = np.zeros(3)
        direction[ai] = -1.0
        verts = _raycast_shrink(verts, pos_mask, direction, context)

    # -face vertices
    neg_mask = np.abs(verts[:, ai] - neg_face) < tol
    if np.any(neg_mask):
        direction = np.zeros(3)
        direction[ai] = 1.0
        verts = _raycast_shrink(verts, neg_mask, direction, context)

    return verts


def _raycast_shrink(
    verts: np.ndarray,
    mask: np.ndarray,
    directions: np.ndarray,
    context: trimesh.Trimesh,
) -> np.ndarray:
    """Cast rays from selected vertices along *directions* and snap them to
    the first intersection with *context*.

    *directions* is either a single (3,) vector (broadcast to all) or an
    (N, 3) array matching the masked vertices.
    """
    origins = verts[mask].copy()
    n = len(origins)

    if directions.ndim == 1:
        dirs = np.tile(directions, (n, 1))
    else:
        dirs = directions

    locs, ray_idx, _ = context.ray.intersects_location(
        origins, dirs, multiple_hits=False,
    )

    if len(locs) == 0:
        return verts

    verts = verts.copy()
    idx_into_masked = np.where(mask)[0]

    for i in range(len(locs)):
        vi = idx_into_masked[ray_idx[i]]
        verts[vi] = locs[i]

    return verts


# ── Exporters ──────────────────────────────────────────────────────

def mesh_to_glb_bytes(mesh: trimesh.Trimesh) -> bytes:
    """Export a trimesh to binary glTF (.glb)."""
    return mesh.export(file_type="glb")


def mesh_to_stl_bytes(mesh: trimesh.Trimesh) -> bytes:
    """Export a trimesh mesh to binary STL bytes."""
    return mesh.export(file_type="stl")


def mesh_to_dict(mesh: trimesh.Trimesh) -> dict:
    """Export a trimesh mesh to a JSON-serializable dict of vertices and faces."""
    return {
        "vertices": mesh.vertices.tolist(),
        "faces": mesh.faces.tolist(),
    }

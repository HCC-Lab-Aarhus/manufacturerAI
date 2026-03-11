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

from src.pipeline.design.models3d import CSGNode

log = logging.getLogger(__name__)

_CYLINDER_SECTIONS = 48
_SPHERE_SUBDIVISIONS = 3


def evaluate_csg(node: CSGNode) -> trimesh.Trimesh:
    """Recursively evaluate a CSGNode tree into a single trimesh mesh."""
    if node.is_primitive:
        mesh = _create_primitive(node)
    elif node.is_operation:
        if not node.children:
            raise ValueError("CSG operation node has no children")
        child_meshes = [evaluate_csg(c) for c in node.children]
        mesh = child_meshes[0]
        for other in child_meshes[1:]:
            if node.op == "union":
                mesh = mesh.union(other, engine="manifold")
            elif node.op == "difference":
                mesh = mesh.difference(other, engine="manifold")
            elif node.op == "intersection":
                mesh = mesh.intersection(other, engine="manifold")
            else:
                raise ValueError(f"Unknown CSG operation: {node.op}")
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
        if node.size_top is not None:
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
    if node.size_top is None:
        return trimesh.creation.box(extents=node.size)
    axis_idx = {"x": 0, "y": 1, "z": 2}[node.axis]
    cross = [i for i in range(3) if i != axis_idx]
    height = node.size[axis_idx]
    bx, by = node.size[cross[0]], node.size[cross[1]]
    tx, ty = node.size_top[cross[0]], node.size_top[cross[1]]
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
    has_taper = node.radius_top is not None or node.radii_top is not None
    if has_taper:
        brx, bry = _resolve_radii_pair(node.radius, node.radii)
        trx, try_ = _resolve_radii_pair(node.radius_top, node.radii_top, default=0.0)
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

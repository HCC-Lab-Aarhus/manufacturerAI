"""CSG tree evaluation — converts a CSGNode tree into a trimesh triangle mesh.

Supports primitives (box, cylinder, sphere, cone) and boolean operations
(union, difference, intersection) using the manifold3d engine.
"""

from __future__ import annotations

import logging
import math

import numpy as np
import trimesh

from src.pipeline.design.models3d import CSGNode

log = logging.getLogger(__name__)

# Segment count for curved primitives (cylinders, cones, spheres).
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


def _create_primitive(node: CSGNode) -> trimesh.Trimesh:
    """Create a trimesh mesh from a CSG primitive node."""
    ptype = node.type

    if ptype == "box":
        if node.size is None:
            raise ValueError("Box primitive requires 'size' (x, y, z)")
        mesh = trimesh.creation.box(extents=node.size)

    elif ptype == "cylinder":
        if node.radius is None or node.height is None:
            raise ValueError("Cylinder primitive requires 'radius' and 'height'")
        mesh = trimesh.creation.cylinder(
            radius=node.radius,
            height=node.height,
            sections=_CYLINDER_SECTIONS,
        )
        mesh = _align_to_axis(mesh, node.axis)

    elif ptype == "sphere":
        if node.radius is None:
            raise ValueError("Sphere primitive requires 'radius'")
        mesh = trimesh.creation.icosphere(
            subdivisions=_SPHERE_SUBDIVISIONS,
            radius=node.radius,
        )

    elif ptype == "cone":
        if node.radius is None or node.height is None:
            raise ValueError("Cone primitive requires 'radius' and 'height'")
        top_r = node.top_radius if node.top_radius is not None else 0.0
        if top_r > 0:
            mesh = trimesh.creation.cone(
                radius=node.radius,
                height=node.height,
                sections=_CYLINDER_SECTIONS,
            )
        else:
            mesh = trimesh.creation.cone(
                radius=node.radius,
                height=node.height,
                sections=_CYLINDER_SECTIONS,
            )
        mesh = _align_to_axis(mesh, node.axis)

    else:
        raise ValueError(f"Unknown primitive type: {ptype}")

    if node.center != (0.0, 0.0, 0.0):
        mesh.apply_translation(node.center)

    return mesh


def mesh_to_glb_bytes(mesh: trimesh.Trimesh) -> bytes:
    """Export a trimesh to binary glTF (.glb)."""
    return mesh.export(file_type="glb")


def _align_to_axis(mesh: trimesh.Trimesh, axis: str) -> trimesh.Trimesh:
    """Rotate a Z-aligned mesh to align with the specified axis.

    trimesh creates cylinders/cones along Z by default.
    """
    if axis == "z":
        return mesh
    elif axis == "x":
        R = trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0])
        mesh.apply_transform(R)
    elif axis == "y":
        R = trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0])
        mesh.apply_transform(R)
    return mesh


def mesh_to_stl_bytes(mesh: trimesh.Trimesh) -> bytes:
    """Export a trimesh mesh to binary STL bytes."""
    return mesh.export(file_type="stl")


def mesh_to_dict(mesh: trimesh.Trimesh) -> dict:
    """Export a trimesh mesh to a JSON-serializable dict of vertices and faces."""
    return {
        "vertices": mesh.vertices.tolist(),
        "faces": mesh.faces.tolist(),
    }

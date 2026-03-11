"""Surface placement utilities — project components onto mesh surfaces.

Uses ray-casting: for each placement, a ray is shot from outside the mesh
bounding box inward along the ``face`` direction.  The first intersection
on a face whose normal matches the direction is the placement position.
"""

from __future__ import annotations

import logging
from typing import Sequence

import numpy as np
import trimesh

from src.pipeline.design.models3d import SurfacePlacement

log = logging.getLogger(__name__)

_HINT_ANGLE_THRESHOLD = 60.0

_HINT_NORMALS: dict[str, np.ndarray] = {
    "top":    np.array([0.0,  0.0,  1.0]),
    "bottom": np.array([0.0,  0.0, -1.0]),
    "front":  np.array([0.0, -1.0,  0.0]),
    "back":   np.array([0.0,  1.0,  0.0]),
    "left":   np.array([-1.0, 0.0,  0.0]),
    "right":  np.array([1.0,  0.0,  0.0]),
}

# Depth axis index and whether the outward face is on the positive side.
_FACE_AXIS: dict[str, tuple[int, bool]] = {
    "top":    (2, True),   # +z face
    "bottom": (2, False),  # -z face
    "back":   (1, True),   # +y face
    "front":  (1, False),  # -y face
    "right":  (0, True),   # +x face
    "left":   (0, False),  # -x face
}

_RAY_MARGIN = 10.0


def project_to_surface(
    mesh: trimesh.Trimesh,
    placement: SurfacePlacement,
) -> SurfacePlacement:
    """Project a placement onto the mesh surface by ray-casting.

    The ``at`` coordinate's depth axis (perpendicular to ``face``) is
    overridden — a ray is cast from outside the bounding box inward,
    and the first hit on an appropriately-oriented face is used.

    Mutates ``snapped_position``, ``surface_normal``, and ``face_id``
    on *placement* and returns it.
    """
    face = placement.face
    at = np.array(placement.at, dtype=np.float64)
    bbox_min, bbox_max = mesh.bounds[0], mesh.bounds[1]

    axis_idx, is_positive = _FACE_AXIS[face]

    origin = at.copy()
    direction = np.zeros(3)
    if is_positive:
        origin[axis_idx] = bbox_max[axis_idx] + _RAY_MARGIN
        direction[axis_idx] = -1.0
    else:
        origin[axis_idx] = bbox_min[axis_idx] - _RAY_MARGIN
        direction[axis_idx] = 1.0

    locations, _, face_ids = mesh.ray.intersects_location(
        ray_origins=[origin],
        ray_directions=[direction],
    )

    if len(locations) > 0:
        hint_normal = _HINT_NORMALS[face]
        cos_threshold = np.cos(np.radians(_HINT_ANGLE_THRESHOLD))
        for i in np.argsort(np.linalg.norm(locations - origin, axis=1)):
            fid = int(face_ids[i])
            if mesh.face_normals[fid] @ hint_normal >= cos_threshold:
                snapped = locations[i]
                placement.snapped_position = tuple(float(v) for v in snapped)
                placement.surface_normal = tuple(float(v) for v in mesh.face_normals[fid])
                placement.face_id = fid
                return placement

    log.warning(
        "Ray-cast missed for '%s' face=%s at=%s; falling back to nearest face",
        placement.instance_id, face, placement.at,
    )
    snapped, normal, fid = _snap_with_hint(mesh, at, face)
    placement.snapped_position = tuple(float(v) for v in snapped)
    placement.surface_normal = tuple(float(v) for v in normal)
    placement.face_id = int(fid)
    return placement


def project_all(
    mesh: trimesh.Trimesh,
    placements: Sequence[SurfacePlacement],
) -> list[SurfacePlacement]:
    """Project all surface placements onto the mesh."""
    return [project_to_surface(mesh, p) for p in placements]


def validate_surface_flatness(
    mesh: trimesh.Trimesh,
    face_id: int,
    radius_mm: float = 5.0,
    max_angle_deg: float = 15.0,
) -> bool:
    """Check that the mesh surface near a face is locally flat enough for a component."""
    target_normal = mesh.face_normals[face_id]
    target_center = mesh.triangles_center[face_id]

    all_centers = mesh.triangles_center
    distances = np.linalg.norm(all_centers - target_center, axis=1)
    nearby = np.where(distances < radius_mm)[0]

    if len(nearby) == 0:
        return True

    nearby_normals = mesh.face_normals[nearby]
    dots = np.clip(nearby_normals @ target_normal, -1.0, 1.0)
    angles = np.degrees(np.arccos(dots))

    return float(np.max(angles)) <= max_angle_deg


# ── Internal helpers ──────────────────────────────────────────────

def _snap_with_hint(
    mesh: trimesh.Trimesh,
    point: np.ndarray,
    face: str,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Find closest point on mesh faces whose normal roughly matches the face direction."""
    hint_dir = _HINT_NORMALS[face]
    cos_threshold = np.cos(np.radians(_HINT_ANGLE_THRESHOLD))

    dots = mesh.face_normals @ hint_dir
    candidate_mask = dots >= cos_threshold

    if not np.any(candidate_mask):
        log.warning(
            "No faces match face '%s'; falling back to nearest face",
            face,
        )
        return _snap_nearest(mesh, point)

    candidate_ids = np.where(candidate_mask)[0]
    candidate_centers = mesh.triangles_center[candidate_ids]
    dists = np.linalg.norm(candidate_centers - point, axis=1)
    best_local = int(np.argmin(dists))
    best_face = int(candidate_ids[best_local])

    target_tri = mesh.triangles[best_face]
    closest_on_face = _closest_point_on_triangle(point, target_tri)

    return closest_on_face, mesh.face_normals[best_face], best_face


def _snap_nearest(
    mesh: trimesh.Trimesh,
    point: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Find the closest point on the mesh surface."""
    closest, _distance, face_id = trimesh.proximity.closest_point(mesh, [point])
    fid = int(face_id[0])
    return closest[0], mesh.face_normals[fid], fid


def _closest_point_on_triangle(
    point: np.ndarray,
    triangle: np.ndarray,
) -> np.ndarray:
    """Project a point onto a triangle, clamping to the triangle surface."""
    a, b, c = triangle[0], triangle[1], triangle[2]
    ab, ac = b - a, c - a

    normal = np.cross(ab, ac)
    n_len_sq = normal @ normal
    if n_len_sq < 1e-12:
        return a

    t = normal @ (a - point) / n_len_sq
    projected = point + t * normal

    v0, v1, v2 = c - a, b - a, projected - a
    dot00, dot01, dot02 = v0 @ v0, v0 @ v1, v0 @ v2
    dot11, dot12 = v1 @ v1, v1 @ v2

    inv_denom = 1.0 / max(dot00 * dot11 - dot01 * dot01, 1e-12)
    u = (dot11 * dot02 - dot01 * dot12) * inv_denom
    v = (dot00 * dot12 - dot01 * dot02) * inv_denom

    if u >= 0 and v >= 0 and (u + v) <= 1:
        return projected

    return a + np.clip(np.dot(projected - a, ab) / max(ab @ ab, 1e-12), 0, 1) * ab

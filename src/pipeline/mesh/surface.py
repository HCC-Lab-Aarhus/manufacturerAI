"""Surface placement utilities — snap components to mesh surfaces and validate."""

from __future__ import annotations

import logging
from typing import Sequence

import numpy as np
import trimesh

from src.pipeline.design.models3d import SurfacePlacement

log = logging.getLogger(__name__)

# Maximum angle (degrees) between face normal and hint direction to be a candidate.
_HINT_ANGLE_THRESHOLD = 60.0

# Hint direction vectors (unit vectors in world space).
_HINT_NORMALS = {
    "top":    np.array([0.0,  0.0,  1.0]),
    "bottom": np.array([0.0,  0.0, -1.0]),
    "front":  np.array([0.0, -1.0,  0.0]),
    "back":   np.array([0.0,  1.0,  0.0]),
    "left":   np.array([-1.0, 0.0,  0.0]),
    "right":  np.array([1.0,  0.0,  0.0]),
}


def snap_to_surface(
    mesh: trimesh.Trimesh,
    placement: SurfacePlacement,
) -> SurfacePlacement:
    """Snap a surface placement to the nearest mesh face.

    Updates ``snapped_position``, ``surface_normal``, and ``face_id``
    on the placement (mutates in place and returns it).
    """
    point = np.array(placement.position, dtype=np.float64)

    if placement.face_hint and placement.face_hint in _HINT_NORMALS:
        result = _snap_with_hint(mesh, point, placement.face_hint)
    else:
        result = _snap_nearest(mesh, point)

    snapped, normal, face_id = result
    placement.snapped_position = tuple(float(v) for v in snapped)
    placement.surface_normal = tuple(float(v) for v in normal)
    placement.face_id = int(face_id)
    return placement


def snap_all(
    mesh: trimesh.Trimesh,
    placements: Sequence[SurfacePlacement],
) -> list[SurfacePlacement]:
    """Snap all surface placements to the mesh."""
    return [snap_to_surface(mesh, p) for p in placements]


def validate_surface_flatness(
    mesh: trimesh.Trimesh,
    face_id: int,
    radius_mm: float = 5.0,
    max_angle_deg: float = 15.0,
) -> bool:
    """Check that the mesh surface near a face is locally flat enough for a component.

    Examines neighboring faces within ``radius_mm`` of the face centroid and
    checks that all their normals are within ``max_angle_deg`` of the target face normal.
    """
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


def _snap_nearest(
    mesh: trimesh.Trimesh,
    point: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Find the closest point on the mesh surface."""
    closest, distance, face_id = trimesh.proximity.closest_point(mesh, [point])
    fid = int(face_id[0])
    return closest[0], mesh.face_normals[fid], fid


def _snap_with_hint(
    mesh: trimesh.Trimesh,
    point: np.ndarray,
    face_hint: str,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Find closest point on mesh faces whose normal roughly matches the hint."""
    hint_dir = _HINT_NORMALS[face_hint]
    cos_threshold = np.cos(np.radians(_HINT_ANGLE_THRESHOLD))

    dots = mesh.face_normals @ hint_dir
    candidate_mask = dots >= cos_threshold

    if not np.any(candidate_mask):
        log.warning(
            "No faces match hint '%s'; falling back to nearest face",
            face_hint,
        )
        return _snap_nearest(mesh, point)

    candidate_ids = np.where(candidate_mask)[0]
    candidate_centers = mesh.triangles_center[candidate_ids]
    dists = np.linalg.norm(candidate_centers - point, axis=1)
    best_local = int(np.argmin(dists))
    best_face = int(candidate_ids[best_local])

    closest, _, _ = trimesh.proximity.closest_point(mesh, [point])
    snapped = closest[0]

    target_tri = mesh.triangles[best_face]
    closest_on_face = _closest_point_on_triangle(point, target_tri)

    return closest_on_face, mesh.face_normals[best_face], best_face


# ── Zone detection ──────────────────────────────────────────────

# Mapping from face hint to (u_axis, v_axis, depth_axis) indices.
_FACE_AXES: dict[str, tuple[int, int, int]] = {
    "top":    (0, 1, 2),  # u=x, v=y, depth=z
    "bottom": (0, 1, 2),
    "front":  (0, 2, 1),  # u=x, v=z, depth=y
    "back":   (0, 2, 1),
    "left":   (1, 2, 0),  # u=y, v=z, depth=x
    "right":  (1, 2, 0),
}

# Faces where depth = max (outward-pointing faces).
_DEPTH_MAX_FACES = {"top", "right", "back"}


def find_placement_zones(
    mesh: trimesh.Trimesh,
) -> dict[str, dict]:
    """Detect flat placement zones for each face direction.

    Returns a dict keyed by face_hint ("top", "front", etc.) with:
      center  – [u, v] center of the zone in face-plane coords
      bounds  – [u_min, v_min, u_max, v_max]
      depth   – the depth coordinate (z for top, y for front, etc.)
      axes    – human label, e.g. "x, y" for top
    """
    cos_threshold = np.cos(np.radians(_HINT_ANGLE_THRESHOLD))
    axis_labels = {0: "x", 1: "y", 2: "z"}
    zones: dict[str, dict] = {}

    for hint, hint_normal in _HINT_NORMALS.items():
        dots = mesh.face_normals @ hint_normal
        mask = dots >= cos_threshold
        if not mask.any():
            continue

        u_idx, v_idx, d_idx = _FACE_AXES[hint]
        centers = mesh.triangles_center[mask]

        if hint in _DEPTH_MAX_FACES:
            depth = float(np.max(centers[:, d_idx]))
            near = centers[:, d_idx] >= (depth - 2.0)
        else:
            depth = float(np.min(centers[:, d_idx]))
            near = centers[:, d_idx] <= (depth + 2.0)

        pts = centers[near]
        if len(pts) == 0:
            continue

        zones[hint] = {
            "center": [round(float(np.mean(pts[:, u_idx])), 1),
                        round(float(np.mean(pts[:, v_idx])), 1)],
            "bounds": [round(float(np.min(pts[:, u_idx])), 1),
                        round(float(np.min(pts[:, v_idx])), 1),
                        round(float(np.max(pts[:, u_idx])), 1),
                        round(float(np.max(pts[:, v_idx])), 1)],
            "depth": round(depth, 1),
            "axes": f"{axis_labels[u_idx]}, {axis_labels[v_idx]}",
        }

    return zones


def resolve_face_offset(
    zones: dict[str, dict],
    face_hint: str,
    offset: tuple[float, float],
) -> tuple[float, float, float]:
    """Convert a 2D face-relative offset to a 3D world position."""
    zone = zones[face_hint]
    u = zone["center"][0] + offset[0]
    v = zone["center"][1] + offset[1]
    d = zone["depth"]

    u_idx, v_idx, d_idx = _FACE_AXES[face_hint]
    pos = [0.0, 0.0, 0.0]
    pos[u_idx] = u
    pos[v_idx] = v
    pos[d_idx] = d
    return (pos[0], pos[1], pos[2])


def _closest_point_on_triangle(
    point: np.ndarray,
    triangle: np.ndarray,
) -> np.ndarray:
    """Project a point onto a triangle, clamping to the triangle surface."""
    a, b, c = triangle[0], triangle[1], triangle[2]
    ab, ac, ap = b - a, c - a, point - a

    d1, d2 = ab @ ap, ac @ ap
    d3, d4 = ab @ (point - b), ac @ (point - b)
    d5, d6 = ab @ (point - c), ac @ (point - c)

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

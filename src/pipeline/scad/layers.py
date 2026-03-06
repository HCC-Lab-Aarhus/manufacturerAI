"""layers.py — generate OpenSCAD lines for the shell body.

Two code paths are selected automatically:

**Uniform height** (all ceiling Z values within 0.1 mm of each other)
  Uses the original stacked ``polygon() + linear_extrude()`` approach — one
  layer per chamfer/fillet step plus a single straight-wall extrude.  This
  path is unchanged from the previous implementation.

**Variable height** (per-vertex ``z_top`` values differ by > 0.1 mm)
  Uses a single OpenSCAD ``polyhedron()`` primitive built from vertex rings.
  Each ring corresponds to a profile step (bottom edge, top edge) or to the
  top/bottom of the straight wall section.  Because every vertex in a ring
  can sit at a different Z, the ceiling can follow the per-vertex heights
  defined in the design outline.

  Edge profiles (chamfer / fillet) are applied via miter-normal insets
  computed in pure Python — preserving the exact vertex count across all
  rings so the face index table stays consistent.

The ``flat_pts`` argument is the Bézier-expanded 2-D footprint polygon from
``outline.tessellate_outline`` — identical to the polygon used for cutout
placement so the shell and cutouts are always aligned.
"""

from __future__ import annotations

import math
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.pipeline.design.models import Outline, Enclosure

from src.pipeline.design.models import Outline, Enclosure
from src.pipeline.design.height_field import blended_height as _blended_height
from shapely.geometry import Polygon as _ShapelyPoly

log = logging.getLogger(__name__)

# Number of profile steps per chamfer / fillet zone.
# 14 gives smooth quarter-circle fillets (~6.4° per step).
_CURVE_STEPS = 14

# Number of concentric rings inside the top cap (between the perimeter ring
# and the centroid point).  More rings → smoother height transitions on the
# visible top surface for variable-height shells.
# 14 rings match _CURVE_STEPS giving consistent radial density: each ring
# spans ~1/15 of the cap radius, similar to the fillet arc step size.
_CAP_RINGS = 14

# Height variation threshold below which the uniform path is used (mm).
_VARIABLE_HEIGHT_THRESHOLD = 0.1


# ── Shapely inset helper (2-D string, for uniform path) ──────────────────────


def _inset_polygon(
    pts: list[list[float]],
    inset: float,
) -> str | None:
    """Shrink *pts* inward by *inset* mm (Shapely mitre-buffer).

    Returns the formatted ``points`` string for an OpenSCAD ``polygon()``,
    or ``None`` if the inset collapses the polygon entirely.
    """
    if inset <= 0:
        return ", ".join(f"[{x:.3f}, {y:.3f}]" for x, y in pts)
    poly = _ShapelyPoly(pts)
    shrunk = poly.buffer(-inset, join_style="mitre", mitre_limit=5.0)
    if shrunk.is_empty:
        return None
    if shrunk.geom_type == "MultiPolygon":
        shrunk = max(shrunk.geoms, key=lambda g: g.area)
    coords = list(shrunk.exterior.coords)[:-1]
    return ", ".join(f"[{x:.3f}, {y:.3f}]" for x, y in coords)


# ── Per-vertex miter inset (for polyhedron path) ──────────────────────────────


def _polygon_signed_area(pts: list[list[float]]) -> float:
    """Signed shoelace area.  Positive = CCW in standard math coords (Y up)."""
    n = len(pts)
    return 0.5 * sum(
        pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
        for i in range(n)
    )


def _inset_polygon_pts(
    pts: list[list[float]],
    inset: float,
    _area: float | None = None,
) -> list[list[float]]:
    """Inset each vertex along its miter normal by ``inset`` mm.

    Unlike the Shapely-based ``_inset_polygon``, this function always returns
    exactly ``len(pts)`` vertices — a hard requirement for building consistent
    polyhedron ring tables.  A miter limit of 5× prevents very acute corners
    from producing extreme spikes.

    Parameters
    ----------
    pts    : 2-D polygon vertices [[x, y], ...].
    inset  : Inward offset in mm.  ≤ 0 returns a copy of the original vertices.
    _area  : Pre-computed signed area (optional, avoids recomputing in a loop).
    """
    if inset < 1e-9:
        return [[x, y] for x, y in pts]

    n = len(pts)
    area = _area if _area is not None else _polygon_signed_area(pts)
    # +1 → CCW math convention (interior is to the left of each directed edge).
    # −1 → CW math / CCW screen convention (interior to the right).
    sign = 1.0 if area >= 0 else -1.0

    result: list[list[float]] = []
    for i in range(n):
        x0, y0 = pts[(i - 1) % n]
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]

        # Normalised tangent of the incoming edge (prev → current)
        dx_in = x1 - x0
        dy_in = y1 - y0
        len_in = math.hypot(dx_in, dy_in)
        dx_in /= max(len_in, 1e-12)
        dy_in /= max(len_in, 1e-12)

        # Normalised tangent of the outgoing edge (current → next)
        dx_out = x2 - x1
        dy_out = y2 - y1
        len_out = math.hypot(dx_out, dy_out)
        dx_out /= max(len_out, 1e-12)
        dy_out /= max(len_out, 1e-12)

        # Inward normals: left-perpendicular for CCW, right for CW
        nx_in  = sign * (-dy_in);  ny_in  = sign * dx_in
        nx_out = sign * (-dy_out); ny_out = sign * dx_out

        # Miter bisector (average of the two inward normals, normalised)
        bx = nx_in + nx_out
        by = ny_in + ny_out
        b_len = math.hypot(bx, by)
        if b_len < 1e-9:
            bx, by = nx_in, ny_in
        else:
            bx /= b_len
            by /= b_len

        # Scale: how far along the bisector to travel to get the requested
        # perpendicular inset distance.  Clamped so miter ≤ 5× inset.
        cos_a = nx_in * bx + ny_in * by
        miter = inset / max(cos_a, 0.2)

        result.append([x1 + bx * miter, y1 + by * miter])

    return result


# ── Polyhedron ring builder ───────────────────────────────────────────────────


def _smooth_top_zs(
    top_zs: list[float],
    sigma: float = 4.0,
    passes: int = 3,
) -> list[float]:
    """Smooth per-vertex ceiling heights along the perimeter ring.

    Applies a Gaussian blur (σ in vertex-index units) along the circular
    array of ceiling heights.  After every pass the result is clamped from
    below by the *original* values so component clearances are never reduced.
    """
    N    = len(top_zs)
    orig = list(top_zs)
    arr  = list(top_zs)
    half = int(math.ceil(3.0 * sigma))

    for _ in range(passes):
        new_arr: list[float] = []
        for i in range(N):
            wsum = 0.0
            vsum = 0.0
            for di in range(-half, half + 1):
                j = (i + di) % N
                w = math.exp(-0.5 * (di / sigma) ** 2)
                vsum += arr[j] * w
                wsum += w
            new_arr.append(vsum / wsum)
        # Clamp from below to preserve minimum component clearances
        arr = [max(orig[i], new_arr[i]) for i in range(N)]

    return arr


def _smooth_ring_z(
    zs: list[float],
    sigma: float = 4.0,
    passes: int = 2,
) -> list[float]:
    """Circular Gaussian blur along a cap ring — NO clamping.

    Unlike ``_smooth_top_zs`` this helper does **not** enforce a floor,
    so high-z spokes (horn tips) can be blended downward into their
    neighbours.  Applied after IDW z computation to break the spoke-aligned
    ridge that IDW with a radial-spoke layout would otherwise create.
    """
    N    = len(zs)
    arr  = list(zs)
    half = int(math.ceil(3.0 * sigma))

    for _ in range(passes):
        new_arr: list[float] = []
        for i in range(N):
            wsum = 0.0
            vsum = 0.0
            for di in range(-half, half + 1):
                j = (i + di) % N
                w = math.exp(-0.5 * (di / sigma) ** 2)
                vsum += arr[j] * w
                wsum += w
            new_arr.append(vsum / wsum)
        arr = new_arr

    return arr


def _idw_cap_z(
    bx: float,
    by: float,
    perim: list[list[float]],
    power: float = 2.0,
    eps: float = 0.01,
) -> float:
    """Inverse-distance weighted z at interior cap point (bx, by).

    Weights each perimeter ring vertex ``[x, y, z]`` by ``1 / dist**power``.
    The result is a weighted mean of all ceiling heights, which:

    * Is naturally smooth (no discontinuities)
    * Is bounded between ``min(z)`` and ``max(z)`` of the perimeter ring
      so it can never raise a valley above its ceiling height
    * Converges exactly to ``perim[i][2]`` as (bx, by) → perim[i]

    These properties make IDW safer than Gaussian blur + clamp for cap
    surfaces: smooth hills form around peaks, flat valleys stay flat, and
    cutout cylinder depths are never violated.
    """
    w_sum  = 0.0
    wz_sum = 0.0
    for p in perim:
        d = math.hypot(bx - p[0], by - p[1])
        if d < eps:
            return p[2]          # coincident vertex → return exact ceiling z
        w = 1.0 / d ** power
        wz_sum += w * p[2]
        w_sum  += w
    return wz_sum / w_sum


def _gauss2d_cap_z(
    bx: float,
    by: float,
    perim: list[list[float]],
    sigma_mm: float = 20.0,
) -> float:
    """2D Gaussian-weighted mean of perimeter z heights.

    Unlike IDW (Shepard's method), Gaussian weights decay exponentially
    and do **not** create sharp radial ridges along each perimeter spoke.
    The result is a smoothly undulating interior surface that blends all
    ceiling heights without any per-vertex discontinuities.

    With ``sigma_mm=20`` the influence of each perimeter vertex extends
    ~40 mm (≈2σ), covering typical enclosure widths without over-smoothing
    the height differences between, e.g., horn peaks and flat sides.
    """
    sig2   = 2.0 * sigma_mm * sigma_mm
    w_sum  = 0.0
    wz_sum = 0.0
    for p in perim:
        d2 = (bx - p[0]) ** 2 + (by - p[1]) ** 2
        w  = math.exp(-d2 / sig2)
        wz_sum += w * p[2]
        w_sum  += w
    if w_sum < 1e-12:
        return sum(p[2] for p in perim) / len(perim)
    return wz_sum / w_sum


def _build_rings(
    flat_pts: list[list[float]],
    top_zs: list[float],
    enclosure: Enclosure,
) -> list[list[list[float]]]:
    """Build an ordered list of vertex rings from bottom to top.

    Each ring is a list of N ``[x, y, z]`` points (N = len(flat_pts)).
    Rings are stacked bottom → top:

    * Bottom edge zone: ``_CURVE_STEPS + 1`` rings (last ring = z=bot_size,
      full-width polygon = start of the straight wall).
    * OR single flat bottom ring at z=0 (no bottom profile).
    * Top edge zone: ``_CURVE_STEPS + 1`` rings (first ring = per-vertex
      ``top_zs[i] − top_size``, full-width = end of the straight wall).
    * OR single per-vertex top ring at z=top_zs[i] (no top profile).

    The straight wall section is implicitly encoded as the quad between the
    last bottom ring and the first top ring.
    """
    N = len(flat_pts)
    edge_top = enclosure.edge_top
    edge_bot = enclosure.edge_bottom

    top_type = (edge_top.type    if edge_top else "none") or "none"
    top_size = (edge_top.size_mm if edge_top else 0.0)    or 0.0
    bot_type = (edge_bot.type    if edge_bot else "none") or "none"
    bot_size = (edge_bot.size_mm if edge_bot else 0.0)    or 0.0

    has_top = top_size > 0 and top_type in ("chamfer", "fillet")
    has_bot = bot_size > 0 and bot_type in ("chamfer", "fillet")

    area = _polygon_signed_area(flat_pts)
    rings: list[list[list[float]]] = []

    # ── Bottom edge rings ──────────────────────────────────────────────────────
    if has_bot:
        for step in range(_CURVE_STEPS + 1):
            frac = step / _CURVE_STEPS
            if bot_type == "fillet":
                theta = (1.0 - frac) * (math.pi / 2)
                inset = bot_size * (1.0 - math.cos(theta))
                z     = bot_size * (1.0 - math.sin(theta))
            else:  # chamfer
                inset = bot_size * (1.0 - frac)
                z     = bot_size * frac
            ipts = _inset_polygon_pts(flat_pts, inset, _area=area)
            rings.append([[ix, iy, z] for ix, iy in ipts])
    else:
        rings.append([[x, y, 0.0] for x, y in flat_pts])

    # ── Top edge rings (also encodes the straight wall top in ring[0]) ─────────
    if has_top:
        for step in range(_CURVE_STEPS + 1):
            frac = step / _CURVE_STEPS
            if top_type == "fillet":
                theta    = frac * (math.pi / 2)
                inset    = top_size * (1.0 - math.cos(theta))
                z_offset = top_size * math.sin(theta)
            else:  # chamfer
                inset    = top_size * frac
                z_offset = top_size * frac
            ipts = _inset_polygon_pts(flat_pts, inset, _area=area)
            ring = [
                [ipts[i][0], ipts[i][1], (top_zs[i] - top_size) + z_offset]
                for i in range(N)
            ]
            rings.append(ring)
    else:
        rings.append([[flat_pts[i][0], flat_pts[i][1], top_zs[i]] for i in range(N)])

    return rings


def _polyhedron_shell(
    flat_pts: list[list[float]],
    top_zs: list[float],
    enclosure: Enclosure,
    outline: Outline | None = None,
) -> list[str]:
    """Emit an OpenSCAD ``polyhedron()`` for a variable-height shell body.

    Each outline vertex can have a different ceiling height (from ``top_zs``).
    Edge profiles (chamfer / fillet) are applied via per-vertex miter insets
    so the ring vertex count is always identical — a requirement for building
    a valid polyhedron face table.

    Face winding follows OpenSCAD's left-hand/CW-from-outside convention so
    all outward normals point away from the interior.  The ``convexity``
    parameter is set to 10 to assist CGAL in evaluating the CSG tree even
    for non-convex shapes.
    """
    N = len(flat_pts)
    rings = _build_rings(flat_pts, top_zs, enclosure)
    R = len(rings)

    # Flat point list (ring-major, vertex-minor order)
    all_pts: list[list[float]] = [pt for ring in rings for pt in ring]

    # ── Build concentric cap rings for the top surface ─────────────────────────
    # XY positions interpolate linearly from the top-perimeter ring inward to
    # the centroid.  Z heights use a 2D Gaussian weighted mean of the
    # perimeter, ceiling-clamped to the wall height at each spoke:
    #
    #   gz  = gauss2d(bx, by, last_ring, sigma=20 mm)
    #   bz  = min(gz, last_ring[i][2])
    #
    # This has exactly two behaviours depending on which region spoke i is in:
    #
    #   • Flat-side spokes (wall z ≈ 18–28 mm, Gaussian pulls higher):
    #     clamp fires → bz = wall z → cap surface meets the wall rim exactly
    #     with no visible step or overhang at the perimeter.
    #
    #   • Horn spokes (wall z ≈ 44–46 mm, Gaussian stays below that):
    #     clamp never fires → bz = Gaussian → smooth dome; the horn wall
    #     correctly sticks up above the dome (that IS what a horn looks like).
    #
    # This eliminates the radial "fin" ridge that the previous boundary-blend
    # approach produced: blending 95 % of top_zs[horn]=46 mm at k=0 made the
    # horn spoke stay at ~45 mm while adjacent (non-horn) spokes were at ~28 mm,
    # generating a 17 mm near-vertical face running from the horn tip to the
    # centroid.  The pure-Gaussian + clamp approach gives a maximum adjacent
    # z-diff of ~3 mm at k=0 compared to ~15 mm previously.
    last_ring = rings[-1]
    cx = sum(p[0] for p in last_ring) / N
    cy = sum(p[1] for p in last_ring) / N

    cap_rings: list[list[list[float]]] = []
    for k in range(_CAP_RINGS):
        t = (k + 1) / (_CAP_RINGS + 1)  # 0 < t < 1, never 0 or 1
        cap_ring: list[list[float]] = []
        for i in range(N):
            bx = last_ring[i][0] * (1.0 - t) + cx * t
            by = last_ring[i][1] * (1.0 - t) + cy * t
            gz = _gauss2d_cap_z(bx, by, last_ring)
            # Ceiling = blended_height at this interior XY (same source as
            # cutout cylinder tops) so cap surface never blocks any hole.
            # Falls back to the perimeter wall z when outline unavailable.
            if outline is not None:
                ceiling = _blended_height(bx, by, outline, enclosure)
            else:
                ceiling = last_ring[i][2]
            bz = min(gz, ceiling)
            cap_ring.append([bx, by, bz])
        cap_rings.append(cap_ring)

    # Centroid Z: pure Gaussian (no wall vertex to clamp against).
    cz = _gauss2d_cap_z(cx, cy, last_ring)

    # Append cap ring points, then centroid, to the flat point list
    for cap_ring in cap_rings:
        all_pts.extend(cap_ring)
    all_pts.append([cx, cy, cz])

    # Index helpers
    # Main rings: 0 … R*N-1  (ring ri, vertex vi → ri*N + vi%N)
    # Cap ring k (0-based): R*N + k*N … R*N + k*N + N-1
    # Centroid: R*N + _CAP_RINGS*N
    center_idx = R * N + _CAP_RINGS * N

    def cap_base(k: int) -> int:
        return R * N + k * N

    def idx(ri: int, vi: int) -> int:
        return ri * N + (vi % N)

    # Determine winding: OpenSCAD uses CW-from-outside (left-hand) convention.
    # For a CCW polygon (area > 0 in math / Y-up coordinates):
    #   bottom cap:  list as-is (CCW in XY = CW from below = CW from outside ✓)
    #   top cap:     reversed   (CW in XY  = CW from above = CW from outside ✓)
    #   side faces:  [a, d, c], [a, c, b]  (CW from the outer right side)
    # For a CW polygon (area < 0): all three are flipped.
    area = _polygon_signed_area(flat_pts)
    ccw = area >= 0

    faces: list[list[int]] = []

    # ── Bottom cap ─────────────────────────────────────────────────────────────
    bot_face = list(range(N))
    if not ccw:
        bot_face = bot_face[::-1]
    faces.append(bot_face)

    # ── Top cap — concentric ring layers + final centroid fan ──────────────────
    # Winding for top-facing outward normals (CW from above = CW from outside):
    #   For each quad (a=outer_vi, b=outer_nxt, c=inner_nxt, d=inner_vi):
    #     CCW polygon: [d,b,a], [d,c,b]
    #     CW  polygon: [d,a,b], [d,b,c]
    #   This reduces to the existing centroid fan [center,nxt,curr] when the
    #   "inner ring" degenerates to a single centroid point.

    def _cap_quad(a: int, b: int, c: int, d: int) -> None:
        """Emit two triangles for a top-cap quad (outer→inner, inward-facing up)."""
        if ccw:
            faces.append([d, b, a])
            faces.append([d, c, b])
        else:
            faces.append([d, a, b])
            faces.append([d, b, c])

    # Layer 0: last main ring → first cap ring
    for vi in range(N):
        a = idx(R - 1, vi)
        b = idx(R - 1, vi + 1)
        c = cap_base(0) + (vi + 1) % N
        d = cap_base(0) + vi
        _cap_quad(a, b, c, d)

    # Layers 1…_CAP_RINGS-1: successive cap ring pairs
    for k in range(_CAP_RINGS - 1):
        for vi in range(N):
            a = cap_base(k)     + vi
            b = cap_base(k)     + (vi + 1) % N
            c = cap_base(k + 1) + (vi + 1) % N
            d = cap_base(k + 1) + vi
            _cap_quad(a, b, c, d)

    # Final layer: innermost cap ring → centroid fan
    innermost = cap_base(_CAP_RINGS - 1)
    for vi in range(N):
        curr = innermost + vi
        nxt  = innermost + (vi + 1) % N
        if ccw:
            faces.append([center_idx, nxt, curr])
        else:
            faces.append([center_idx, curr, nxt])

    # ── Side faces (triangulated quads, two triangles per ring-edge pair) ──────
    # Quad vertices: a = ring_i[j],   b = ring_i[j+1],
    #                c = ring_{i+1}[j+1], d = ring_{i+1}[j]
    # CCW polygon:  CW-from-outside winding → [a,d,c] + [a,c,b]
    # CW  polygon:  opposite              → [a,b,c] + [a,c,d]
    for ri in range(R - 1):
        for vi in range(N):
            a = idx(ri,     vi)
            b = idx(ri,     vi + 1)
            c = idx(ri + 1, vi + 1)
            d = idx(ri + 1, vi)
            if ccw:
                faces.append([a, d, c])
                faces.append([a, c, b])
            else:
                faces.append([a, b, c])
                faces.append([a, c, d])

    # ── Format as OpenSCAD source ──────────────────────────────────────────────
    min_z = min(top_zs)
    max_z = max(top_zs)
    edge_top = enclosure.edge_top
    edge_bot = enclosure.edge_bottom
    top_type = (edge_top.type    if edge_top else "none") or "none"
    top_size = (edge_top.size_mm if edge_top else 0.0)    or 0.0
    bot_type = (edge_bot.type    if edge_bot else "none") or "none"
    bot_size = (edge_bot.size_mm if edge_bot else 0.0)    or 0.0

    pts_str   = ", ".join(
        f"[{x:.4f}, {y:.4f}, {z:.4f}]" for x, y, z in all_pts
    )
    faces_str = ", ".join(
        "[" + ", ".join(str(i) for i in face) + "]" for face in faces
    )

    log.info(
        "Shell body (polyhedron): %d rings, %d pts, %d faces, z=%.1f..%.1f mm",
        R, len(all_pts), len(faces), min_z, max_z,
    )

    return [
        f"// Shell body — variable-height polyhedron",
        f"// ceiling z: {min_z:.1f}..{max_z:.1f} mm"
        f"  bottom={bot_type}({bot_size:.1f}mm)  top={top_type}({top_size:.1f}mm)",
        f"// {N} footprint verts, {R} rings, {len(all_pts)} pts, {len(faces)} faces",
        f"polyhedron(",
        f"  points   = [{pts_str}],",
        f"  faces    = [{faces_str}],",
        f"  convexity = 10",
        f");",
    ]


# ── Public API ─────────────────────────────────────────────────────────────────


def shell_body_lines(
    outline:   Outline,
    enclosure: Enclosure,
    flat_pts:  list[list[float]],
    top_zs:    list[float] | None = None,
    indent:    str = "",
) -> list[str]:
    """Return OpenSCAD lines for the shell body.

    Automatically selects between two implementations:

    * **Uniform path** — when ``top_zs`` is ``None`` or all values are within
      ``_VARIABLE_HEIGHT_THRESHOLD`` mm of each other.  Uses the original
      stacked ``linear_extrude`` approach with Shapely pre-computed insets.
      Fast to compile and unchanged from the previous behaviour.

    * **Polyhedron path** — when ceiling heights vary significantly across
      outline vertices.  Emits a single ``polyhedron()`` primitive whose
      top ring follows the per-vertex heights in ``top_zs``.  Edge profiles
      are handled by additional intermediate rings built with the miter-inset
      helper ``_inset_polygon_pts``.

    Parameters
    ----------
    outline   : Outline   The design outline (used only for logging context).
    enclosure : Enclosure Edge-profile settings and default height.
    flat_pts  : list      Bézier-expanded 2-D footprint vertices [[x, y], ...].
    top_zs    : list | None
        Per-vertex ceiling heights, one value per element of ``flat_pts``.
        Pass ``None`` (or omit) to always use the uniform path.
    indent    : str       Unused legacy parameter; kept for API compatibility.
    """
    # ── Decide which path to use ───────────────────────────────────────────────
    if top_zs is not None and len(top_zs) == len(flat_pts):
        z_range = max(top_zs) - min(top_zs)
        if z_range >= _VARIABLE_HEIGHT_THRESHOLD:
            return _polyhedron_shell(flat_pts, top_zs, enclosure, outline=outline)
        # All heights are effectively equal — use the uniform value from top_zs
        # (which may differ slightly from enclosure.height_mm if all vertices
        # carry an explicit z_top that overrides the enclosure default).
        h_uniform = top_zs[0]
    else:
        h_uniform = enclosure.height_mm

    # ── Uniform path (original stacked linear_extrude) ────────────────────────
    h        = h_uniform
    edge_top = enclosure.edge_top
    edge_bot = enclosure.edge_bottom

    top_type = (edge_top.type    if edge_top else "none") or "none"
    top_size = (edge_top.size_mm if edge_top else 0.0)    or 0.0
    bot_type = (edge_bot.type    if edge_bot else "none") or "none"
    bot_size = (edge_bot.size_mm if edge_bot else 0.0)    or 0.0

    has_top = top_size > 0 and top_type in ("chamfer", "fillet")
    has_bot = bot_size > 0 and bot_type in ("chamfer", "fillet")

    # Full-size polygon string (used for straight wall and as base for insets)
    full_pts = ", ".join(f"[{x:.3f}, {y:.3f}]" for x, y in flat_pts)

    # ── Simple case: no edge profiles ─────────────────────────────────────────
    if not has_top and not has_bot:
        log.info("Shell body: plain extrude h=%.1f mm, %d verts", h, len(flat_pts))
        return [
            f"// Shell body — plain extrude, h={h:.1f} mm",
            f"linear_extrude(height = {h:.3f})",
            f"    polygon(points = [{full_pts}]);",
        ]

    # ── Stacked-layer helper ───────────────────────────────────────────────────
    def _zone_layers(
        z_base:       float,
        size:         float,
        profile_type: str,
        direction:    str,   # "bottom" or "top"
    ) -> list[str]:
        """Build stacked linear_extrude layers for one chamfer/fillet zone."""
        out: list[str] = []
        for i in range(_CURVE_STEPS):
            frac0 = i       / _CURVE_STEPS
            frac1 = (i + 1) / _CURVE_STEPS

            if direction == "bottom":
                theta0 = (1.0 - frac0) * (math.pi / 2)
                theta1 = (1.0 - frac1) * (math.pi / 2)
                if profile_type == "fillet":
                    inset0 = size * (1.0 - math.cos(theta0))
                    z0     = size * (1.0 - math.sin(theta0))
                    z1     = size * (1.0 - math.sin(theta1))
                else:  # chamfer
                    inset0 = size * (1.0 - frac0)
                    z0     = size * frac0
                    z1     = size * frac1
            else:  # top
                theta0 = frac0 * (math.pi / 2)
                theta1 = frac1 * (math.pi / 2)
                if profile_type == "fillet":
                    inset0 = size * (1.0 - math.cos(theta0))
                    z0     = z_base + size * math.sin(theta0)
                    z1     = z_base + size * math.sin(theta1)
                else:  # chamfer
                    inset0 = size * frac0
                    z0     = z_base + size * frac0
                    z1     = z_base + size * frac1

            dz = z1 - z0
            if dz < 1e-6:
                continue

            p = _inset_polygon(flat_pts, inset0)
            if p is None:
                continue

            out += [
                f"    translate([0, 0, {z0:.4f}])",
                f"        linear_extrude(height = {dz:.4f})",
                f"            polygon(points = [{p}]);",
            ]
        return out

    # ── Assemble zones ─────────────────────────────────────────────────────────
    wall_z0 = bot_size if has_bot else 0.0
    wall_z1 = (h - top_size) if has_top else h
    wall_h  = wall_z1 - wall_z0

    lines: list[str] = [
        f"// Shell body — stacked-layer extrude, h={h:.1f} mm",
        f"// bottom={bot_type}({bot_size:.1f} mm)  top={top_type}({top_size:.1f} mm)",
        f"// {len(flat_pts)} footprint vertices, {_CURVE_STEPS} steps per profile",
        "union() {",
    ]

    if has_bot:
        lines.append(f"    // Bottom {bot_type} ({bot_size:.1f} mm)")
        lines += _zone_layers(0.0, bot_size, bot_type, "bottom")

    if wall_h > 0:
        lines += [
            f"    // Straight wall ({wall_z0:.3f} mm to {wall_z1:.3f} mm)",
            f"    translate([0, 0, {wall_z0:.4f}])",
            f"        linear_extrude(height = {wall_h:.4f})",
            f"            polygon(points = [{full_pts}]);",
        ]

    if has_top:
        lines.append(f"    // Top {top_type} ({top_size:.1f} mm)")
        lines += _zone_layers(wall_z1, top_size, top_type, "top")

    lines.append("}")

    n_layers = (
        (_CURVE_STEPS if has_bot else 0)
        + (1 if wall_h > 0 else 0)
        + (_CURVE_STEPS if has_top else 0)
    )
    log.info(
        "Shell body: %d layers, h=%.1f mm, %d footprint verts",
        n_layers, h, len(flat_pts),
    )
    return lines

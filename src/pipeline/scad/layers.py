"""layers.py — generate OpenSCAD lines for the shell body.

Emits a single ``polyhedron()`` primitive built from vertex rings.  Each ring
corresponds to a profile step (bottom edge, top edge) or to the top/bottom
of the straight wall section.  Because every vertex in a ring can sit at a
different Z, the ceiling and floor can follow per-vertex heights defined in
the design outline.

Edge profiles (chamfer / fillet) are applied via miter-normal insets computed
in pure Python — preserving the exact vertex count across all rings so the
face index table stays consistent.

The ``flat_pts`` argument is the Bézier-expanded 2-D footprint polygon from
``outline.tessellate_outline`` — identical to the polygon used for cutout
placement so the shell and cutouts are always aligned.
"""

from __future__ import annotations

import math
import logging

from src.pipeline.design.models import Outline, Enclosure
from src.pipeline.design.height_field import blended_height as _blended_height

log = logging.getLogger(__name__)

# Number of profile steps per chamfer / fillet zone.
# 6 gives adequate fillet quality for 3D printing (~15° per step).
_CURVE_STEPS = 6

# Number of concentric rings inside the top/bottom caps (between the
# perimeter ring and the centroid point).  More rings give smoother height
# transitions on the visible surface for variable-height shells.
_CAP_RINGS = 14

# Height variation threshold: when per-vertex heights differ by more than
# this, cap-ring interpolation is enabled for smooth surface transitions.
_VARIABLE_HEIGHT_THRESHOLD = 0.1

# Minimum vertical gap between the last bottom-profile ring and the first
# top-profile ring.  Prevents coincident vertices at the chamfer/fillet
# junction that create mixed-topology meshes triggering CGAL 4.x assertions.
_MIN_WALL_GAP = 0.2


# ── Per-vertex miter inset ─────────────────────────────────────────────────────


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

    Always returns exactly ``len(pts)`` vertices — a hard requirement for
    building consistent polyhedron ring tables.  A miter limit of 5×
    prevents very acute corners from producing extreme spikes.

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
    bottom_zs: list[float] | None = None,
) -> list[list[list[float]]]:
    """Build an ordered list of vertex rings from bottom to top.

    Each ring is a list of N ``[x, y, z]`` points (N = len(flat_pts)).
    Rings are stacked bottom → top:

    * Bottom edge zone: ``_CURVE_STEPS + 1`` rings (last ring =
      z=bottom_zs[i]+bot_size, full-width polygon = start of the straight wall).
    * OR single bottom ring at z=bottom_zs[i] (no bottom profile).
    * Top edge zone: ``_CURVE_STEPS + 1`` rings (first ring = per-vertex
      ``top_zs[i] − top_size``, full-width = end of the straight wall).
    * OR single per-vertex top ring at z=top_zs[i] (no top profile).

    The straight wall section is implicitly encoded as the quad between the
    last bottom ring and the first top ring.

    Parameters
    ----------
    bottom_zs : Per-vertex floor heights.  ``None`` or all-zeros gives a flat
                bottom at z=0.
    """
    N = len(flat_pts)
    if bottom_zs is None:
        bottom_zs = [0.0] * N

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
                inset_frac = 1.0 - math.cos(theta)
                z_frac     = 1.0 - math.sin(theta)
            else:  # chamfer
                inset_frac = 1.0 - frac
                z_frac     = frac
            inset = bot_size * inset_frac
            ipts = _inset_polygon_pts(flat_pts, inset, _area=area)
            ring = []
            for i in range(N):
                # Clamp bot_size so the bottom profile doesn't exceed available wall
                avail = max(top_zs[i] - bottom_zs[i], _MIN_WALL_GAP)
                eff_bs = min(bot_size, avail * 0.45)
                eff_bs = max(eff_bs, 0.01)
                z = bottom_zs[i] + eff_bs * z_frac
                ring.append([ipts[i][0], ipts[i][1], z])
            rings.append(ring)
    else:
        rings.append([[flat_pts[i][0], flat_pts[i][1], bottom_zs[i]] for i in range(N)])

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
            ring = []
            for i in range(N):
                last_bot_z = (bottom_zs[i] + bot_size) if has_bot else bottom_zs[i]
                avail = max(top_zs[i] - last_bot_z, _MIN_WALL_GAP)
                eff_ts = min(top_size, avail - _MIN_WALL_GAP)
                eff_ts = max(eff_ts, 0.01)
                z = (top_zs[i] - eff_ts) + z_offset * (eff_ts / top_size)
                ring.append([ipts[i][0], ipts[i][1], z])
            rings.append(ring)
    else:
        rings.append([[flat_pts[i][0], flat_pts[i][1], top_zs[i]] for i in range(N)])

    return rings


def _polyhedron_shell(
    flat_pts: list[list[float]],
    top_zs: list[float],
    enclosure: Enclosure,
    outline: Outline | None = None,
    bottom_zs: list[float] | None = None,
    cap_rings: int = _CAP_RINGS,
) -> list[str]:
    """Emit an OpenSCAD ``polyhedron()`` for the shell body.

    Each outline vertex can have a different ceiling height (from ``top_zs``)
    and/or a different floor height (from ``bottom_zs``).  When heights are
    uniform, ``cap_rings=0`` produces a simple centroid fan for the caps.
    Edge profiles (chamfer / fillet) are applied via per-vertex miter insets
    so the ring vertex count is always identical — a requirement for building
    a valid polyhedron face table.

    Face winding follows OpenSCAD's left-hand/CW-from-outside convention so
    all outward normals point away from the interior.  The ``convexity``
    parameter is set to 10 to assist CGAL in evaluating the CSG tree even
    for non-convex shapes.
    """
    N = len(flat_pts)
    if bottom_zs is None:
        bottom_zs = [0.0] * N
    n_cap_rings = cap_rings
    rings = _build_rings(flat_pts, top_zs, enclosure, bottom_zs=bottom_zs)
    R = len(rings)

    # Flat point list (ring-major, vertex-minor order)
    all_pts: list[list[float]] = [pt for ring in rings for pt in ring]

    # ── Build concentric cap rings for the top surface ─────────────────────────
    # XY: linearly interpolated from perimeter ring to centroid.
    # Z: IDW (Shepard's method) with power varying from 3 (outer) to 2 (inner).
    # High power near the perimeter keeps the first cap ring tight to each
    # wall-top z; lower power for inner rings gives smooth transitions.
    last_ring = rings[-1]
    cx = sum(p[0] for p in last_ring) / N
    cy = sum(p[1] for p in last_ring) / N

    top_cap_list: list[list[list[float]]] = []
    for k in range(n_cap_rings):
        t = (k + 1) / (n_cap_rings + 1)
        idw_power = 2.0 + 1.0 * (1.0 - k / (n_cap_rings - 1 or 1))

        cap_ring: list[list[float]] = []
        for i in range(N):
            bx = last_ring[i][0] * (1.0 - t) + cx * t
            by = last_ring[i][1] * (1.0 - t) + cy * t
            bz = _idw_cap_z(bx, by, last_ring, power=idw_power)
            cap_ring.append([bx, by, bz])

        if k == 0:
            cap_ring = [
                [cap_ring[j][0], cap_ring[j][1],
                 min(cap_ring[j][2], last_ring[j][2])]
                for j in range(N)
            ]
        top_cap_list.append(cap_ring)

    cz = _idw_cap_z(cx, cy, last_ring, power=2.0)

    for cap_ring in top_cap_list:
        all_pts.extend(cap_ring)
    all_pts.append([cx, cy, cz])

    # Bottom cap rings — concentric rings for variable-height bottom surface
    bot_ring = rings[0]
    bot_cx = sum(p[0] for p in bot_ring) / N
    bot_cy = sum(p[1] for p in bot_ring) / N

    bot_z_range = max(p[2] for p in bot_ring) - min(p[2] for p in bot_ring)
    variable_bot = bot_z_range >= _VARIABLE_HEIGHT_THRESHOLD

    if variable_bot:
        bot_cap_list: list[list[list[float]]] = []
        for k in range(n_cap_rings):
            t = (k + 1) / (n_cap_rings + 1)
            idw_power = 2.0 + 1.0 * (1.0 - k / (n_cap_rings - 1 or 1))
            bcp_ring: list[list[float]] = []
            for i in range(N):
                bx = bot_ring[i][0] * (1.0 - t) + bot_cx * t
                by = bot_ring[i][1] * (1.0 - t) + bot_cy * t
                bz = _idw_cap_z(bx, by, bot_ring, power=idw_power)
                bcp_ring.append([bx, by, bz])
            if k == 0:
                bcp_ring = [
                    [bcp_ring[j][0], bcp_ring[j][1],
                     max(bcp_ring[j][2], bot_ring[j][2])]
                    for j in range(N)
                ]
            bot_cap_list.append(bcp_ring)

        bot_cz = _idw_cap_z(bot_cx, bot_cy, bot_ring, power=2.0)

        for bcp_ring in bot_cap_list:
            all_pts.extend(bcp_ring)
        all_pts.append([bot_cx, bot_cy, bot_cz])

        n_bot_cap_rings = n_cap_rings
    else:
        bot_cz = sum(p[2] for p in bot_ring) / N
        all_pts.append([bot_cx, bot_cy, bot_cz])
        n_bot_cap_rings = 0

    # Index helpers
    # Main rings: 0 … R*N-1  (ring ri, vertex vi → ri*N + vi%N)
    # Top cap ring k: R*N + k*N … R*N + k*N + N-1
    # Top centroid: R*N + n_cap_rings*N
    # Bottom cap ring k: R*N + n_cap_rings*N + 1 + k*N … (+ N-1)
    # Bottom centroid: R*N + n_cap_rings*N + 1 + n_bot_cap_rings*N
    center_idx = R * N + n_cap_rings * N
    bot_cap_base_offset = center_idx + 1

    def bot_cap_base(k: int) -> int:
        return bot_cap_base_offset + k * N

    bot_center_idx = bot_cap_base_offset + n_bot_cap_rings * N

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
    if n_bot_cap_rings > 0:
        # Concentric ring layers for variable-height bottom surface.
        # Winding for bottom-facing outward normals (CW from below):
        #   For each quad (a=outer_vi, b=outer_nxt, c=inner_nxt, d=inner_vi):
        #     CCW polygon: [d,a,b], [d,b,c]   (opposite of top cap)
        #     CW  polygon: [d,b,a], [d,c,b]
        def _bot_cap_quad(a: int, b: int, c: int, d: int) -> None:
            if ccw:
                faces.append([d, a, b])
                faces.append([d, b, c])
            else:
                faces.append([d, b, a])
                faces.append([d, c, b])

        # Layer 0: first main ring → first bottom cap ring
        for vi in range(N):
            a = idx(0, vi)
            b = idx(0, vi + 1)
            c = bot_cap_base(0) + (vi + 1) % N
            d = bot_cap_base(0) + vi
            _bot_cap_quad(a, b, c, d)

        for k in range(n_bot_cap_rings - 1):
            for vi in range(N):
                a = bot_cap_base(k)     + vi
                b = bot_cap_base(k)     + (vi + 1) % N
                c = bot_cap_base(k + 1) + (vi + 1) % N
                d = bot_cap_base(k + 1) + vi
                _bot_cap_quad(a, b, c, d)

        # Final layer: innermost bottom cap ring → centroid fan
        innermost_bot = bot_cap_base(n_bot_cap_rings - 1)
        for vi in range(N):
            curr = innermost_bot + vi
            nxt  = innermost_bot + (vi + 1) % N
            if ccw:
                faces.append([bot_center_idx, curr, nxt])
            else:
                faces.append([bot_center_idx, nxt, curr])
    else:
        # Simple centroid fan for flat bottom
        for vi in range(N):
            curr = vi
            nxt  = (vi + 1) % N
            if ccw:
                faces.append([bot_center_idx, curr, nxt])
            else:
                faces.append([bot_center_idx, nxt, curr])

    # ── Top cap — concentric ring layers + final centroid fan ──────────────────
    # Winding for top-facing outward normals (CW from above = CW from outside):
    #   For each quad (a=outer_vi, b=outer_nxt, c=inner_nxt, d=inner_vi):
    #     CCW polygon: [d,b,a], [d,c,b]
    #     CW  polygon: [d,a,b], [d,b,c]

    if n_cap_rings > 0:
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

        for k in range(n_cap_rings - 1):
            for vi in range(N):
                a = cap_base(k)     + vi
                b = cap_base(k)     + (vi + 1) % N
                c = cap_base(k + 1) + (vi + 1) % N
                d = cap_base(k + 1) + vi
                _cap_quad(a, b, c, d)

        # Final layer: innermost cap ring → centroid fan
        innermost = cap_base(n_cap_rings - 1)
        for vi in range(N):
            curr = innermost + vi
            nxt  = innermost + (vi + 1) % N
            if ccw:
                faces.append([center_idx, nxt, curr])
            else:
                faces.append([center_idx, curr, nxt])
    else:
        # No cap rings: last main ring → centroid fan directly
        for vi in range(N):
            curr = idx(R - 1, vi)
            nxt  = idx(R - 1, (vi + 1) % N)
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
        R, len(all_pts), len(faces), min(bottom_zs), max_z,
    )

    return [
        f"// Shell body — polyhedron",
        f"// ceiling z: {min_z:.1f}..{max_z:.1f} mm  floor z: {min(bottom_zs):.1f}..{max(bottom_zs):.1f} mm"
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
    bottom_zs: list[float] | None = None,
) -> list[str]:
    """Return OpenSCAD lines for the shell body as a single ``polyhedron()``.

    Parameters
    ----------
    outline   : Outline   The design outline (used only for logging context).
    enclosure : Enclosure Edge-profile settings and default height.
    flat_pts  : list      Bézier-expanded 2-D footprint vertices [[x, y], ...].
    top_zs    : list | None
        Per-vertex ceiling heights, one per ``flat_pts`` vertex.
        Defaults to ``enclosure.height_mm`` for all vertices.
    bottom_zs : list | None
        Per-vertex floor heights, one per ``flat_pts`` vertex.
        Defaults to 0.0 for all vertices.
    """
    N = len(flat_pts)

    eff_top = top_zs if (top_zs is not None and len(top_zs) == N) else [enclosure.height_mm] * N
    eff_bot = bottom_zs if (bottom_zs is not None and len(bottom_zs) == N) else [0.0] * N

    # Use full cap-ring interpolation when heights vary across vertices;
    # flat surfaces need only a simple centroid fan (cap_rings=0).
    top_varies = (max(eff_top) - min(eff_top)) >= _VARIABLE_HEIGHT_THRESHOLD
    bot_varies = (max(eff_bot) - min(eff_bot)) >= _VARIABLE_HEIGHT_THRESHOLD or max(eff_bot) >= _VARIABLE_HEIGHT_THRESHOLD
    cap_rings = _CAP_RINGS if (top_varies or bot_varies) else 0

    return _polyhedron_shell(flat_pts, eff_top, enclosure, outline=outline,
                             bottom_zs=eff_bot, cap_rings=cap_rings)

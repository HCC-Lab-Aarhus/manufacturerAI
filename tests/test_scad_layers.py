"""Tests for src/pipeline/scad/layers.py — shell body polyhedron generation.

Coverage
--------
* ``_polygon_signed_area``         — correct sign for CCW and CW polygons.
* ``_inset_polygon_pts``           — vertex count preserved; inset moves inward.
* ``_earclip``                     — correct triangulation for convex and non-convex polygons.
* ``_build_rings``                 — correct ring count for all edge-profile combinations.
* ``shell_body_lines``             — polyhedron with ear-clipped caps.
* polyhedron output structure      — correct point/face counts, valid index range.
* Edge profiles in polyhedron path — extra rings are emitted for chamfer/fillet.
* Winding sanity                   — bottom-cap and top-cap face indices are reversed
                                     from each other (required for correct outward normals).
* OpenSCAD syntax check            — if the ``openscad`` binary is available, verifies
                                     that the emitted SCAD file parses without errors.
"""

from __future__ import annotations

import math
import re
import tempfile
import unittest
from pathlib import Path

from src.pipeline.design.models import (
    Enclosure,
    EdgeProfile,
    Outline,
    OutlineVertex,
)
from src.pipeline.scad.layers import (
    _CURVE_STEPS,
    _build_rings,
    _earclip,
    _inset_polygon_pts,
    _polygon_signed_area,
    shell_body_lines,
)


# ── Shared polygon fixtures ────────────────────────────────────────────────────


def _ccw_square(side: float = 20.0) -> list[list[float]]:
    """CCW (math-convention) square: (0,0),(side,0),(side,side),(0,side).

    Signed area > 0 in standard math coords (Y up).
    """
    s = side
    return [[0.0, 0.0], [s, 0.0], [s, s], [0.0, s]]


def _cw_square(side: float = 20.0) -> list[list[float]]:
    """CW (math-convention) square — reverse of the CCW version.

    Signed area < 0 (same shape, opposite orientation).
    """
    return _ccw_square(side)[::-1]


def _rect_pts(w: float = 30.0, h: float = 50.0) -> list[list[float]]:
    """CCW rectangle centred at origin."""
    hw, hh = w / 2, h / 2
    return [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]]


def _plain_enclosure(height: float = 25.0) -> Enclosure:
    return Enclosure(height_mm=height)


def _fillet_enclosure(height: float = 25.0, size: float = 2.0) -> Enclosure:
    profile = EdgeProfile(type="fillet", size_mm=size)
    return Enclosure(height_mm=height, edge_top=profile, edge_bottom=profile)


def _chamfer_enclosure(height: float = 25.0, size: float = 2.0) -> Enclosure:
    profile = EdgeProfile(type="chamfer", size_mm=size)
    return Enclosure(height_mm=height, edge_top=profile, edge_bottom=profile)


def _outline_from_pts(pts: list[list[float]]) -> Outline:
    return Outline(points=[OutlineVertex(x=x, y=y) for x, y in pts])


# ── _polygon_signed_area ───────────────────────────────────────────────────────


class TestPolygonSignedArea(unittest.TestCase):
    """Verify the signed-area helper correctly identifies orientation."""

    def test_ccw_square_positive(self):
        area = _polygon_signed_area(_ccw_square(10.0))
        self.assertGreater(area, 0)
        self.assertAlmostEqual(area, 100.0, places=6)

    def test_cw_square_negative(self):
        area = _polygon_signed_area(_cw_square(10.0))
        self.assertLess(area, 0)
        self.assertAlmostEqual(area, -100.0, places=6)

    def test_degenerate_triangle(self):
        pts = [[0.0, 0.0], [5.0, 0.0], [0.0, 5.0]]
        area = _polygon_signed_area(pts)
        self.assertAlmostEqual(abs(area), 12.5, places=6)


# ── _inset_polygon_pts ────────────────────────────────────────────────────────


class TestInsetPolygonPts(unittest.TestCase):
    """Validate the per-vertex miter inset preserves vertex count and moves inward."""

    def _centroid(self, pts):
        n = len(pts)
        return sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n

    def test_zero_inset_returns_copy(self):
        pts = _ccw_square(20.0)
        result = _inset_polygon_pts(pts, 0.0)
        self.assertEqual(len(result), len(pts))
        for r, o in zip(result, pts):
            self.assertAlmostEqual(r[0], o[0])
            self.assertAlmostEqual(r[1], o[1])

    def test_vertex_count_preserved_ccw(self):
        pts = _rect_pts(30, 50)
        for inset in (0.5, 1.0, 2.0, 3.0):
            result = _inset_polygon_pts(pts, inset)
            self.assertEqual(len(result), len(pts),
                             f"vertex count changed at inset={inset}")

    def test_vertex_count_preserved_cw(self):
        pts = _cw_square(20.0)
        for inset in (0.5, 1.5):
            result = _inset_polygon_pts(pts, inset)
            self.assertEqual(len(result), len(pts))

    def test_inset_moves_toward_centroid_ccw(self):
        """Each inseted vertex should be closer to the centroid."""
        pts = _ccw_square(20.0)
        cx, cy = self._centroid(pts)
        result = _inset_polygon_pts(pts, 2.0)
        for orig, ins in zip(pts, result):
            d_orig = math.hypot(orig[0] - cx, orig[1] - cy)
            d_ins  = math.hypot(ins[0]  - cx, ins[1]  - cy)
            self.assertLess(d_ins, d_orig,
                            "inset vertex should be closer to centroid")

    def test_inset_moves_toward_centroid_cw(self):
        """CW polygon: inset should still shrink toward the centroid."""
        pts = _cw_square(20.0)
        cx, cy = self._centroid(pts)
        result = _inset_polygon_pts(pts, 2.0)
        for orig, ins in zip(pts, result):
            d_orig = math.hypot(orig[0] - cx, orig[1] - cy)
            d_ins  = math.hypot(ins[0]  - cx, ins[1]  - cy)
            self.assertLess(d_ins, d_orig)

    def test_right_angle_miter_is_45_degrees(self):
        """For a 90° right-angle corner the bisector sits at 45°."""
        pts = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]
        result = _inset_polygon_pts(pts, 1.0)
        # Bottom-left corner (0,0): inset should move to roughly (1, 1)
        self.assertAlmostEqual(result[0][0], 1.0, delta=0.05)
        self.assertAlmostEqual(result[0][1], 1.0, delta=0.05)


# ── _build_rings ──────────────────────────────────────────────────────────────


class TestBuildRings(unittest.TestCase):
    """Check ring counts for all edge-profile combinations."""

    def setUp(self):
        self.pts = _rect_pts(30, 50)

    def _ring_count_for(self, enc: Enclosure) -> int:
        rings = _build_rings(self.pts, enc)
        return len(rings)

    def test_no_profiles_gives_two_rings(self):
        # bottom ring (z=0) + top ring (z=top_zs)
        self.assertEqual(self._ring_count_for(_plain_enclosure()), 2)

    def test_bottom_only_fillet(self):
        enc = Enclosure(
            height_mm=25.0,
            edge_bottom=EdgeProfile(type="fillet", size_mm=2.0),
        )
        # _CURVE_STEPS+1 bottom rings + 1 top ring
        expected = _CURVE_STEPS + 1 + 1
        self.assertEqual(self._ring_count_for(enc), expected)

    def test_top_only_fillet(self):
        enc = Enclosure(
            height_mm=25.0,
            edge_top=EdgeProfile(type="fillet", size_mm=2.0),
        )
        # 1 bottom ring + _CURVE_STEPS+1 top rings
        expected = 1 + _CURVE_STEPS + 1
        self.assertEqual(self._ring_count_for(enc), expected)

    def test_both_profiles_fillet(self):
        expected = (_CURVE_STEPS + 1) + (_CURVE_STEPS + 1)
        self.assertEqual(self._ring_count_for(_fillet_enclosure(25.0, 2.0)), expected)

    def test_both_profiles_chamfer(self):
        expected = (_CURVE_STEPS + 1) + (_CURVE_STEPS + 1)
        self.assertEqual(self._ring_count_for(_chamfer_enclosure(25.0, 2.0)), expected)

    def test_each_ring_has_n_vertices(self):
        rings = _build_rings(self.pts, _fillet_enclosure())
        n = len(self.pts)
        for i, ring in enumerate(rings):
            self.assertEqual(len(ring), n, f"ring {i} has wrong vertex count")

    def test_bottom_ring_is_at_z_zero(self):
        """First ring must start at z=0 (no bottom profile case)."""
        rings = _build_rings(self.pts, _plain_enclosure())
        for pt in rings[0]:
            self.assertAlmostEqual(pt[2], 0.0)

    def test_top_ring_is_at_enclosure_height(self):
        """Last ring z-values must equal enclosure.height_mm."""
        enc = _plain_enclosure(25.0)
        rings = _build_rings(self.pts, enc)
        last = rings[-1]
        for pt in last:
            self.assertAlmostEqual(pt[2], 25.0, places=4)


# ── shell_body_lines: uniform path ────────────────────────────────────────────


class TestEarclip(unittest.TestCase):
    """Validate the ear-clipping polygon triangulation."""

    def test_triangle(self):
        pts = [[0.0, 0.0], [10.0, 0.0], [5.0, 10.0]]
        tris = _earclip(pts)
        self.assertEqual(len(tris), 1)
        self.assertEqual(tris[0], (0, 1, 2))

    def test_square(self):
        pts = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]
        tris = _earclip(pts)
        self.assertEqual(len(tris), 2)

    def test_l_shape_nonconvex(self):
        """Non-convex L-shape should produce N-2=4 triangles."""
        pts = [[0, 0], [10, 0], [10, 5], [5, 5], [5, 10], [0, 10]]
        tris = _earclip(pts)
        self.assertEqual(len(tris), 4)
        for a, b, c in tris:
            self.assertGreaterEqual(min(a, b, c), 0)
            self.assertLess(max(a, b, c), 6)

    def test_cw_polygon(self):
        """CW winding should also triangulate correctly."""
        pts = [[0.0, 0.0], [0.0, 10.0], [10.0, 10.0], [10.0, 0.0]]
        tris = _earclip(pts)
        self.assertEqual(len(tris), 2)

    def test_all_indices_unique_per_triangle(self):
        pts = [[0, 0], [20, 0], [20, 10], [15, 10], [15, 20], [0, 20]]
        tris = _earclip(pts)
        for a, b, c in tris:
            self.assertEqual(len({a, b, c}), 3)


# ── shell_body_lines: uniform path ────────────────────────────────────────────


class TestShellBodyUniformHeight(unittest.TestCase):
    """Shell body uses polyhedron for all height configurations."""

    def setUp(self):
        self.pts = _rect_pts(30, 50)
        self.outline = _outline_from_pts(self.pts)

    def _joined(self, lines):
        return "\n".join(lines)

    def test_plain_is_polyhedron_path(self):
        lines = shell_body_lines(self.outline, _plain_enclosure(25.0), self.pts)
        scad = self._joined(lines)
        self.assertIn("polyhedron", scad)

    def test_with_fillet_uses_polyhedron(self):
        lines = shell_body_lines(self.outline, _fillet_enclosure(25.0), self.pts)
        scad = self._joined(lines)
        self.assertIn("polyhedron", scad)


# ── shell_body_lines: polyhedron path ───────────────────────────────────────────


class TestShellBodyPolyhedronPath(unittest.TestCase):
    """shell_body_lines emits a polyhedron with ear-clipped caps."""

    def setUp(self):
        self.pts = _rect_pts(30, 50)
        self.outline = _outline_from_pts(self.pts)
        self.enclosure  = _plain_enclosure(20.0)

    def _get_scad(self, enc=None):
        enc = enc or self.enclosure
        return "\n".join(shell_body_lines(self.outline, enc, self.pts))

    # ── Output structure ────────────────────────────────────────────────────────

    def test_polyhedron_emitted(self):
        self.assertIn("polyhedron(", self._get_scad())

    def test_no_linear_extrude(self):
        self.assertNotIn("linear_extrude", self._get_scad())

    # ── points= field ──────────────────────────────────────────────────────────

    def _parse_points(self, scad: str) -> list[list[float]]:
        """Extract all [x,y,z] triples from points=[...] in the SCAD."""
        m = re.search(r"points\s*=\s*\[(.+?)\](?=,\s*faces)", scad, re.DOTALL)
        self.assertIsNotNone(m, "could not find points=[...] in SCAD output")
        triples = re.findall(r"\[([^]]+)\]", m.group(1))
        result = []
        for t in triples:
            vals = [float(v.strip()) for v in t.split(",")]
            self.assertEqual(len(vals), 3, "each point must have 3 coordinates")
            result.append(vals)
        return result

    def _parse_faces(self, scad: str) -> list[list[int]]:
        """Extract all face index lists from faces=[...] in the SCAD."""
        m = re.search(r"faces\s*=\s*\[(.+?)\](?=,\s*convexity)", scad, re.DOTALL)
        self.assertIsNotNone(m, "could not find faces=[...] in SCAD output")
        face_strs = re.findall(r"\[([^\]]+)\]", m.group(1))
        faces = []
        for fs in face_strs:
            faces.append([int(i.strip()) for i in fs.split(",") if i.strip()])
        return faces

    def test_point_count_no_profiles(self):
        """No profiles → R*N ring points (no cap rings or centroids)."""
        N = len(self.pts)
        R = 2  # 1 bottom ring + 1 top ring
        scad = self._get_scad()
        pts = self._parse_points(scad)
        self.assertEqual(len(pts), R * N)

    def test_point_count_with_both_fillets(self):
        """Both profiles → 2*(CS+1) main rings × N points."""
        N = len(self.pts)
        R = 2 * (_CURVE_STEPS + 1)
        expected_pts = R * N
        scad = self._get_scad(enc=_fillet_enclosure(20.0, 2.0))
        pts = self._parse_points(scad)
        self.assertEqual(len(pts), expected_pts)

    def test_all_point_z_values_are_finite(self):
        scad = self._get_scad()
        pts = self._parse_points(scad)
        for pt in pts:
            self.assertTrue(math.isfinite(pt[2]), f"z={pt[2]} is not finite")

    def test_bottom_ring_z_is_zero(self):
        """First ring (indices 0..N-1) must all have z=0."""
        N = len(self.pts)
        scad = self._get_scad()
        pts = self._parse_points(scad)
        for i in range(N):
            self.assertAlmostEqual(pts[i][2], 0.0, places=3,
                                   msg=f"bottom ring pt {i} z should be 0")

    def test_top_ring_z_matches_enclosure_height(self):
        """Last ring z-values must equal enclosure.height_mm."""
        N = len(self.pts)
        scad = self._get_scad()
        pts = self._parse_points(scad)
        top_ring_start = N
        for i in range(N):
            self.assertAlmostEqual(pts[top_ring_start + i][2],
                                   self.enclosure.height_mm, places=3)

    # ── faces= field ───────────────────────────────────────────────────────────

    def test_face_count_no_profiles(self):
        """2 main rings → (N-2) bottom + (N-2) top ear-clip tris + (R-1)*N*2 side tris."""
        N = len(self.pts)
        R = 2
        expected_faces = 2 * (N - 2) + (R - 1) * N * 2
        scad = self._get_scad()
        faces = self._parse_faces(scad)
        self.assertEqual(len(faces), expected_faces)

    def test_face_count_with_both_fillets(self):
        """Both fillets → (N-2) bot + (N-2) top + (R-1)*N*2 sides."""
        N = len(self.pts)
        R = 2 * (_CURVE_STEPS + 1)
        expected_faces = 2 * (N - 2) + (R - 1) * N * 2
        scad = self._get_scad(enc=_fillet_enclosure(20.0, 2.0))
        faces = self._parse_faces(scad)
        self.assertEqual(len(faces), expected_faces)

    def test_all_face_indices_in_range(self):
        """Every face index must refer to a valid point."""
        N = len(self.pts)
        scad = self._get_scad()
        pts   = self._parse_points(scad)
        faces = self._parse_faces(scad)
        total_pts = len(pts)
        for face in faces:
            for idx in face:
                self.assertGreaterEqual(idx, 0)
                self.assertLess(idx, total_pts,
                                f"face index {idx} out of range (max {total_pts-1})")

    def test_bottom_and_top_caps_cover_all_ring_vertices(self):
        """Bottom cap triangles reference all first-ring vertices; top cap
        triangles reference all last-ring vertices."""
        N = len(self.pts)
        R = 2
        scad = self._get_scad()
        pts   = self._parse_points(scad)
        faces = self._parse_faces(scad)

        bot_indices = set(range(N))
        top_indices = set(range((R - 1) * N, R * N))

        bot_tris = [f for f in faces if all(i in bot_indices for i in f)]
        bot_verts = {v for tri in bot_tris for v in tri}
        self.assertEqual(bot_verts, bot_indices)
        self.assertEqual(len(bot_tris), N - 2)

        top_tris = [f for f in faces if all(i in top_indices for i in f)]
        top_verts = {v for tri in top_tris for v in tri}
        self.assertEqual(top_verts, top_indices)
        self.assertEqual(len(top_tris), N - 2)

    def test_bottom_and_top_caps_are_reversed_from_each_other(self):
        """Bottom and top cap triangles must have opposite 2-D signed areas,
        ensuring outward normals point in opposite Z directions."""
        N = len(self.pts)
        R = 2
        scad = self._get_scad()
        pts   = self._parse_points(scad)
        faces = self._parse_faces(scad)

        bot_indices = set(range(N))
        top_indices = set(range((R - 1) * N, R * N))

        bot_tris = [f for f in faces if all(i in bot_indices for i in f)]
        top_tris = [f for f in faces if all(i in top_indices for i in f)]

        def _signed_area_face(face_indices, all_pts):
            n = len(face_indices)
            return 0.5 * sum(
                all_pts[face_indices[i]][0] * all_pts[face_indices[(i + 1) % n]][1]
                - all_pts[face_indices[(i + 1) % n]][0] * all_pts[face_indices[i]][1]
                for i in range(n)
            )

        bot_area = sum(_signed_area_face(t, pts) for t in bot_tris)
        top_area = sum(_signed_area_face(t, pts) for t in top_tris)

        self.assertNotEqual(
            bot_area > 0, top_area > 0,
            f"Bottom and top caps must have opposite winding "
            f"(bot_area={bot_area:.2f}, top_area={top_area:.2f})"
        )

    # ── Edge profiles in polyhedron path ───────────────────────────────────────

    def test_chamfer_produces_more_rings_than_plain(self):
        scad_plain   = self._get_scad(enc=_plain_enclosure(20.0))
        scad_chamfer = self._get_scad(enc=_chamfer_enclosure(20.0, 2.0))
        pts_plain   = self._parse_points(scad_plain)
        pts_chamfer = self._parse_points(scad_chamfer)
        self.assertGreater(len(pts_chamfer), len(pts_plain))

    def test_fillet_and_chamfer_same_ring_count(self):
        """Fillet and chamfer both use _CURVE_STEPS+1 rings per zone."""
        scad_f = self._get_scad(enc=_fillet_enclosure(20.0, 2.0))
        scad_c = self._get_scad(enc=_chamfer_enclosure(20.0, 2.0))
        pts_f = self._parse_points(scad_f)
        pts_c = self._parse_points(scad_c)
        self.assertEqual(len(pts_f), len(pts_c))

    def test_top_profile_ring_z_matches_enclosure_height(self):
        """With a top fillet, the last ring z must equal enclosure.height_mm."""
        enc = _fillet_enclosure(20.0, 2.0)
        N = len(self.pts)
        scad = self._get_scad(enc=enc)
        pts = self._parse_points(scad)
        R = len(pts) // N
        top_ring_start = (R - 1) * N
        top_ring = pts[top_ring_start:top_ring_start + N]
        for i in range(N):
            self.assertAlmostEqual(top_ring[i][2], enc.height_mm, places=3,
                                   msg=f"vertex {i}: fillet last ring z != enclosure height")


# ── More polygons: 8-vertex octagon and 3-vertex triangle ─────────────────────


class TestPolyhedronWithVariousPolygons(unittest.TestCase):
    """Ensure the polyhedron path works for polygons of different sizes."""

    def _octagon(self, r: float = 15.0) -> list[list[float]]:
        n = 8
        return [
            [r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n)]
            for i in range(n)
        ]

    def _run_checks(self, pts, enc):
        """Run the basic structural checks for any polygon."""
        outline = _outline_from_pts(pts)
        lines = shell_body_lines(outline, enc, pts)
        scad = "\n".join(lines)
        self.assertIn("polyhedron(", scad)

        # Extract and validate
        m_pts = re.search(r"points\s*=\s*\[(.+?)\](?=,\s*faces)", scad, re.DOTALL)
        self.assertIsNotNone(m_pts)
        raw_pts = re.findall(r"\[([^]]+)\]", m_pts.group(1))
        total_pts = len(raw_pts)

        m_faces = re.search(r"faces\s*=\s*\[(.+?)\](?=,\s*convexity)", scad, re.DOTALL)
        self.assertIsNotNone(m_faces)
        face_strs = re.findall(r"\[([^\]]+)\]", m_faces.group(1))
        faces = [[int(i) for i in fs.split(",") if i.strip()] for fs in face_strs]

        for face in faces:
            for idx in face:
                self.assertGreaterEqual(idx, 0)
                self.assertLess(idx, total_pts)

    def test_octagon_no_profiles(self):
        pts = self._octagon()
        self._run_checks(pts, _plain_enclosure(22.0))

    def test_octagon_with_fillet(self):
        pts = self._octagon()
        self._run_checks(pts, _fillet_enclosure(22.0, 1.5))

    def test_triangle_no_profiles(self):
        pts = [[0.0, 0.0], [20.0, 0.0], [10.0, 20.0]]
        self._run_checks(pts, _plain_enclosure(20.0))


# ── OpenSCAD syntax check (optional, requires openscad binary) ─────────────────


class TestOpenSCADSyntax(unittest.TestCase):
    """If the openscad binary is available, verify the generated SCAD parses."""

    def _openscad_available(self) -> bool:
        from src.pipeline.scad.compiler import _find_openscad
        return _find_openscad() is not None

    def _write_and_check(self, scad_lines: list[str]) -> tuple[bool, str]:
        from src.pipeline.scad.compiler import check_scad
        scad = "$fn = 16;\n" + "\n".join(scad_lines) + "\n"
        with tempfile.NamedTemporaryFile(
            suffix=".scad", mode="w", delete=False, encoding="utf-8"
        ) as f:
            f.write(scad)
            tmp = Path(f.name)
        try:
            return check_scad(tmp)
        finally:
            tmp.unlink(missing_ok=True)

    def test_polyhedron_plain_parses(self):
        if not self._openscad_available():
            self.skipTest("openscad not found")
        pts = _rect_pts(30, 50)
        outline = _outline_from_pts(pts)
        lines = shell_body_lines(outline, _plain_enclosure(20.0), pts)
        ok, msg = self._write_and_check(lines)
        self.assertTrue(ok, f"OpenSCAD syntax error: {msg}")

    def test_polyhedron_fillet_parses(self):
        if not self._openscad_available():
            self.skipTest("openscad not found")
        pts = _rect_pts(30, 50)
        outline = _outline_from_pts(pts)
        lines = shell_body_lines(outline, _fillet_enclosure(20.0, 2.0), pts)
        ok, msg = self._write_and_check(lines)
        self.assertTrue(ok, f"OpenSCAD syntax error: {msg}")

    def test_uniform_plain_parses(self):
        if not self._openscad_available():
            self.skipTest("openscad not found")
        pts = _rect_pts(30, 50)
        outline = _outline_from_pts(pts)
        lines = shell_body_lines(outline, _plain_enclosure(25.0), pts)
        ok, msg = self._write_and_check(lines)
        self.assertTrue(ok, f"OpenSCAD syntax error: {msg}")



if __name__ == "__main__":
    unittest.main()

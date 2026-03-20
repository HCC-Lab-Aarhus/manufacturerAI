"""Tests for src/pipeline/design/shape2d.py — 2D CSG tessellation & validation.

Coverage
--------
* Rectangle: basic, corner_radius, tapered (size_end+axis), triangle (size_end=0), rotated.
* Ellipse: circle, oval, rotated oval, capsule (end_center+radius_end).
* Boolean ops: union, difference, intersection, nested.
* Node-level transforms: rotate on ops, scale (uniform & per-axis), mirror (x/y/xy).
* Combined transforms: scale + mirror + rotate on a single node.
* Per-primitive z_top / z_bottom attribution.
* Negative tests: bad types, bad scale/mirror/rotate values, depth limit.
"""

from __future__ import annotations

import unittest

from src.pipeline.design.shape2d import validate_shape, tessellate_shape


# ── Helpers ────────────────────────────────────────────────────────────────────

def _bbox(outline):
    """Return (min_x, min_y, max_x, max_y) for an Outline."""
    xs = [p.x for p in outline.points]
    ys = [p.y for p in outline.points]
    return min(xs), min(ys), max(xs), max(ys)


def _width(outline):
    bx = _bbox(outline)
    return bx[2] - bx[0]


def _height(outline):
    bx = _bbox(outline)
    return bx[3] - bx[1]


# ── Rectangle Validation ──────────────────────────────────────────────────────

class TestValidateRectangle(unittest.TestCase):

    def test_basic(self):
        node = {"type": "rectangle", "center": [25, 50], "size": [50, 100]}
        self.assertEqual(validate_shape(node), [])

    def test_corner_radius(self):
        node = {"type": "rectangle", "center": [25, 50], "size": [50, 100], "corner_radius": 8}
        self.assertEqual(validate_shape(node), [])

    def test_tapered(self):
        node = {"type": "rectangle", "center": [25, 50], "size": [30, 80], "size_end": [10, 80], "axis": "y"}
        self.assertEqual(validate_shape(node), [])

    def test_triangle(self):
        node = {"type": "rectangle", "center": [25, 50], "size": [30, 80], "size_end": [0, 80], "axis": "y"}
        self.assertEqual(validate_shape(node), [])

    def test_rotated(self):
        node = {"type": "rectangle", "center": [25, 50], "size": [20, 60], "rotate": 45}
        self.assertEqual(validate_shape(node), [])

    def test_missing_center(self):
        node = {"type": "rectangle", "size": [10, 20]}
        errs = validate_shape(node)
        self.assertTrue(any("center" in e for e in errs))

    def test_missing_size(self):
        node = {"type": "rectangle", "center": [0, 0]}
        errs = validate_shape(node)
        self.assertTrue(any("size" in e for e in errs))

    def test_negative_size(self):
        node = {"type": "rectangle", "center": [0, 0], "size": [-5, 10]}
        errs = validate_shape(node)
        self.assertTrue(any("positive" in e for e in errs))

    def test_bad_size_end_length(self):
        node = {"type": "rectangle", "center": [0, 0], "size": [10, 10], "size_end": [5]}
        errs = validate_shape(node)
        self.assertTrue(any("size_end" in e for e in errs))

    def test_bad_axis(self):
        node = {"type": "rectangle", "center": [0, 0], "size": [10, 10], "axis": "z"}
        errs = validate_shape(node)
        self.assertTrue(any("axis" in e for e in errs))

    def test_negative_corner_radius(self):
        node = {"type": "rectangle", "center": [0, 0], "size": [10, 10], "corner_radius": -1}
        errs = validate_shape(node)
        self.assertTrue(any("corner_radius" in e for e in errs))


# ── Ellipse Validation ────────────────────────────────────────────────────────

class TestValidateEllipse(unittest.TestCase):

    def test_circle(self):
        node = {"type": "ellipse", "center": [25, 25], "radius": 20}
        self.assertEqual(validate_shape(node), [])

    def test_oval(self):
        node = {"type": "ellipse", "center": [25, 25], "radius": [20, 30]}
        self.assertEqual(validate_shape(node), [])

    def test_rotated_oval(self):
        node = {"type": "ellipse", "center": [25, 25], "radius": [20, 10], "rotate": 45}
        self.assertEqual(validate_shape(node), [])

    def test_capsule(self):
        node = {"type": "ellipse", "center": [50, 90], "radius": 8, "end_center": [20, 55], "radius_end": 3}
        self.assertEqual(validate_shape(node), [])

    def test_missing_radius(self):
        node = {"type": "ellipse", "center": [0, 0]}
        errs = validate_shape(node)
        self.assertTrue(any("radius" in e for e in errs))

    def test_negative_radius(self):
        node = {"type": "ellipse", "center": [0, 0], "radius": -5}
        errs = validate_shape(node)
        self.assertTrue(any("radius" in e for e in errs))

    def test_bad_radius_array(self):
        node = {"type": "ellipse", "center": [0, 0], "radius": [10, -5]}
        errs = validate_shape(node)
        self.assertTrue(any("radius" in e for e in errs))

    def test_bad_end_center(self):
        node = {"type": "ellipse", "center": [0, 0], "radius": 5, "end_center": [10]}
        errs = validate_shape(node)
        self.assertTrue(any("end_center" in e for e in errs))


# ── Boolean Operations ────────────────────────────────────────────────────────

class TestValidateOperations(unittest.TestCase):

    def test_union(self):
        node = {"op": "union", "children": [
            {"type": "rectangle", "center": [10, 10], "size": [20, 20]},
            {"type": "ellipse", "center": [25, 10], "radius": 10},
        ]}
        self.assertEqual(validate_shape(node), [])

    def test_difference(self):
        node = {"op": "difference", "children": [
            {"type": "rectangle", "center": [25, 50], "size": [50, 100]},
            {"type": "ellipse", "center": [0, 50], "radius": [8, 15]},
        ]}
        self.assertEqual(validate_shape(node), [])

    def test_intersection(self):
        node = {"op": "intersection", "children": [
            {"type": "rectangle", "center": [25, 40], "size": [50, 80]},
            {"type": "ellipse", "center": [25, 40], "radius": [30, 45]},
        ]}
        self.assertEqual(validate_shape(node), [])

    def test_unknown_op(self):
        node = {"op": "xor", "children": [
            {"type": "rectangle", "center": [0, 0], "size": [10, 10]},
            {"type": "rectangle", "center": [5, 5], "size": [10, 10]},
        ]}
        errs = validate_shape(node)
        self.assertTrue(any("xor" in e for e in errs))

    def test_too_few_children(self):
        node = {"op": "union", "children": [
            {"type": "rectangle", "center": [0, 0], "size": [10, 10]},
        ]}
        errs = validate_shape(node)
        self.assertTrue(any("2 children" in e for e in errs))

    def test_nested_ops(self):
        node = {"op": "union", "children": [
            {"op": "difference", "children": [
                {"type": "rectangle", "center": [25, 50], "size": [50, 100]},
                {"type": "ellipse", "center": [0, 50], "radius": 10},
            ]},
            {"type": "ellipse", "center": [25, 10], "radius": 15},
        ]}
        self.assertEqual(validate_shape(node), [])

    def test_depth_limit(self):
        node = {"type": "rectangle", "center": [0, 0], "size": [10, 10]}
        for _ in range(25):
            node = {"op": "union", "children": [
                node,
                {"type": "rectangle", "center": [0, 0], "size": [10, 10]},
            ]}
        errs = validate_shape(node)
        self.assertTrue(any("depth" in e for e in errs))

    def test_missing_type_and_op(self):
        node = {"center": [0, 0]}
        errs = validate_shape(node)
        self.assertTrue(any("type" in e or "op" in e for e in errs))

    def test_unknown_primitive_type(self):
        node = {"type": "hexagon", "center": [0, 0]}
        errs = validate_shape(node)
        self.assertTrue(any("hexagon" in e for e in errs))


# ── Node-Level Transform Validation ──────────────────────────────────────────

class TestValidateTransforms(unittest.TestCase):

    def test_rotate_on_primitive(self):
        node = {"type": "rectangle", "center": [10, 10], "size": [20, 40], "rotate": 30}
        self.assertEqual(validate_shape(node), [])

    def test_rotate_on_op(self):
        node = {"op": "union", "children": [
            {"type": "rectangle", "center": [10, 20], "size": [8, 30]},
            {"type": "ellipse", "center": [10, 5], "radius": 6},
        ], "rotate": 45}
        self.assertEqual(validate_shape(node), [])

    def test_rotate_bad_type(self):
        node = {"type": "rectangle", "center": [0, 0], "size": [10, 10], "rotate": "abc"}
        errs = validate_shape(node)
        self.assertTrue(any("rotate" in e for e in errs))

    def test_scale_uniform(self):
        node = {"type": "rectangle", "center": [10, 10], "size": [20, 40], "scale": 1.5}
        self.assertEqual(validate_shape(node), [])

    def test_scale_per_axis(self):
        node = {"type": "ellipse", "center": [25, 25], "radius": 15, "scale": [1.0, 0.5]}
        self.assertEqual(validate_shape(node), [])

    def test_scale_on_op(self):
        node = {"op": "difference", "children": [
            {"type": "rectangle", "center": [25, 50], "size": [50, 100]},
            {"type": "ellipse", "center": [0, 50], "radius": [8, 15]},
        ], "scale": [1.2, 0.8]}
        self.assertEqual(validate_shape(node), [])

    def test_scale_zero_rejected(self):
        node = {"type": "rectangle", "center": [0, 0], "size": [10, 10], "scale": 0}
        errs = validate_shape(node)
        self.assertTrue(any("scale" in e for e in errs))

    def test_scale_zero_in_array_rejected(self):
        node = {"type": "rectangle", "center": [0, 0], "size": [10, 10], "scale": [1, 0]}
        errs = validate_shape(node)
        self.assertTrue(any("scale" in e for e in errs))

    def test_scale_bad_array_len(self):
        node = {"type": "rectangle", "center": [0, 0], "size": [10, 10], "scale": [1, 2, 3]}
        errs = validate_shape(node)
        self.assertTrue(any("scale" in e for e in errs))

    def test_scale_bad_type(self):
        node = {"type": "rectangle", "center": [0, 0], "size": [10, 10], "scale": "big"}
        errs = validate_shape(node)
        self.assertTrue(any("scale" in e for e in errs))

    def test_mirror_x(self):
        node = {"type": "rectangle", "center": [25, 50], "size": [20, 40], "mirror": "x"}
        self.assertEqual(validate_shape(node), [])

    def test_mirror_y(self):
        node = {"type": "rectangle", "center": [25, 50], "size": [20, 40], "mirror": "y"}
        self.assertEqual(validate_shape(node), [])

    def test_mirror_xy(self):
        node = {"type": "rectangle", "center": [25, 50], "size": [20, 40], "mirror": "xy"}
        self.assertEqual(validate_shape(node), [])

    def test_mirror_on_op(self):
        node = {"op": "union", "children": [
            {"type": "rectangle", "center": [10, 20], "size": [10, 30]},
            {"type": "ellipse", "center": [10, 5], "radius": 8},
        ], "mirror": "y"}
        self.assertEqual(validate_shape(node), [])

    def test_mirror_bad_axis(self):
        node = {"type": "rectangle", "center": [0, 0], "size": [10, 10], "mirror": "z"}
        errs = validate_shape(node)
        self.assertTrue(any("mirror" in e for e in errs))

    def test_combined_transforms(self):
        node = {"op": "union", "children": [
            {"type": "rectangle", "center": [10, 15], "size": [6, 20], "size_end": [2, 20], "axis": "y"},
            {"type": "ellipse", "center": [10, 5], "radius": 5},
        ], "rotate": 45, "scale": 0.8}
        self.assertEqual(validate_shape(node), [])

    def test_all_transforms_together(self):
        node = {"type": "rectangle", "center": [20, 30], "size": [10, 20],
                "rotate": 15, "scale": [1.5, 0.7], "mirror": "x"}
        self.assertEqual(validate_shape(node), [])


# ── Tessellation Basic ────────────────────────────────────────────────────────

class TestTessellateBasic(unittest.TestCase):

    def test_rectangle_4_vertices(self):
        node = {"type": "rectangle", "center": [25, 50], "size": [50, 100]}
        out = tessellate_shape(node)
        self.assertEqual(len(out.points), 4)

    def test_rectangle_bbox(self):
        node = {"type": "rectangle", "center": [25, 50], "size": [50, 100]}
        out = tessellate_shape(node)
        bx = _bbox(out)
        self.assertAlmostEqual(bx[0], 0, places=1)
        self.assertAlmostEqual(bx[1], 0, places=1)
        self.assertAlmostEqual(bx[2], 50, places=1)
        self.assertAlmostEqual(bx[3], 100, places=1)

    def test_rounded_rectangle_more_vertices(self):
        node = {"type": "rectangle", "center": [25, 50], "size": [50, 100], "corner_radius": 8}
        out = tessellate_shape(node)
        self.assertGreater(len(out.points), 4)

    def test_circle_many_vertices(self):
        node = {"type": "ellipse", "center": [25, 25], "radius": 20}
        out = tessellate_shape(node)
        self.assertGreater(len(out.points), 10)

    def test_tapered_rectangle(self):
        node = {"type": "rectangle", "center": [25, 50], "size": [30, 80], "size_end": [10, 80], "axis": "y"}
        out = tessellate_shape(node)
        self.assertGreaterEqual(len(out.points), 4)

    def test_capsule(self):
        node = {"type": "ellipse", "center": [50, 90], "radius": 8, "end_center": [20, 55], "radius_end": 3}
        out = tessellate_shape(node)
        self.assertGreater(len(out.points), 4)

    def test_union(self):
        node = {"op": "union", "children": [
            {"type": "rectangle", "center": [25, 50], "size": [50, 100]},
            {"type": "ellipse", "center": [25, 10], "radius": 20},
        ]}
        out = tessellate_shape(node)
        self.assertGreater(len(out.points), 4)

    def test_difference(self):
        node = {"op": "difference", "children": [
            {"type": "rectangle", "center": [25, 50], "size": [50, 100], "corner_radius": 10},
            {"type": "ellipse", "center": [0, 50], "radius": [10, 20]},
        ]}
        out = tessellate_shape(node)
        self.assertGreater(len(out.points), 4)

    def test_empty_result_raises(self):
        node = {"op": "difference", "children": [
            {"type": "rectangle", "center": [0, 0], "size": [10, 10]},
            {"type": "rectangle", "center": [0, 0], "size": [100, 100]},
        ]}
        with self.assertRaises(ValueError):
            tessellate_shape(node)


# ── Tessellation Transforms ──────────────────────────────────────────────────

class TestTessellateTransforms(unittest.TestCase):

    def test_scale_doubles_width(self):
        base = {"type": "rectangle", "center": [25, 50], "size": [20, 40]}
        scaled = {"type": "rectangle", "center": [25, 50], "size": [20, 40], "scale": 2.0}
        w_base = _width(tessellate_shape(base))
        w_scaled = _width(tessellate_shape(scaled))
        self.assertAlmostEqual(w_scaled, w_base * 2, delta=0.2)

    def test_scale_per_axis(self):
        base = {"type": "rectangle", "center": [25, 50], "size": [20, 40]}
        scaled = {"type": "rectangle", "center": [25, 50], "size": [20, 40], "scale": [2.0, 1.0]}
        w_base = _width(tessellate_shape(base))
        h_base = _height(tessellate_shape(base))
        out = tessellate_shape(scaled)
        self.assertAlmostEqual(_width(out), w_base * 2, delta=0.2)
        self.assertAlmostEqual(_height(out), h_base, delta=0.2)

    def test_mirror_x_symmetric_rect(self):
        node = {"type": "rectangle", "center": [25, 50], "size": [20, 40], "mirror": "x"}
        out = tessellate_shape(node)
        self.assertEqual(len(out.points), 4)

    def test_mirror_y_preserves_shape(self):
        node = {"type": "rectangle", "center": [25, 50], "size": [20, 40], "mirror": "y"}
        out = tessellate_shape(node)
        w = _width(out)
        self.assertAlmostEqual(w, 20, delta=0.2)

    def test_rotate_on_op_changes_vertices(self):
        children = [
            {"type": "rectangle", "center": [25, 50], "size": [50, 100]},
            {"type": "ellipse", "center": [25, 10], "radius": 20},
        ]
        out_no_rot = tessellate_shape({"op": "union", "children": children})
        out_rot = tessellate_shape({"op": "union", "children": children, "rotate": 45})
        xs_nr = [p.x for p in out_no_rot.points]
        xs_r = [p.x for p in out_rot.points]
        self.assertNotAlmostEqual(max(xs_nr), max(xs_r), delta=1.0)

    def test_scale_on_op(self):
        children = [
            {"type": "rectangle", "center": [25, 50], "size": [50, 100]},
            {"type": "ellipse", "center": [25, 10], "radius": 20},
        ]
        out_base = tessellate_shape({"op": "union", "children": children})
        out_scaled = tessellate_shape({"op": "union", "children": children, "scale": 0.5})
        self.assertAlmostEqual(_width(out_scaled), _width(out_base) * 0.5, delta=1.0)

    def test_combined_transforms_on_op(self):
        node = {"op": "union", "children": [
            {"type": "rectangle", "center": [10, 15], "size": [6, 20], "size_end": [2, 20], "axis": "y"},
            {"type": "ellipse", "center": [10, 5], "radius": 5},
        ], "rotate": 45, "scale": 0.8}
        out = tessellate_shape(node)
        self.assertGreater(len(out.points), 4)


# ── Z-Height Attribution ─────────────────────────────────────────────────────

class TestZAttribution(unittest.TestCase):

    def test_z_top_inherited(self):
        node = {"type": "rectangle", "center": [25, 50], "size": [50, 100], "z_top": 30}
        out = tessellate_shape(node)
        for p in out.points:
            self.assertEqual(p.z_top, 30)

    def test_z_bottom_inherited(self):
        node = {"type": "rectangle", "center": [25, 50], "size": [50, 100], "z_bottom": 5}
        out = tessellate_shape(node)
        for p in out.points:
            self.assertEqual(p.z_bottom, 5)

    def test_default_z_propagated(self):
        node = {"type": "rectangle", "center": [25, 50], "size": [50, 100]}
        out = tessellate_shape(node, default_z_top=20, default_z_bottom=2)
        for p in out.points:
            self.assertEqual(p.z_top, 20)
            self.assertEqual(p.z_bottom, 2)

    def test_z_follows_op_rotation(self):
        """z_top must follow vertices through op-level rotation."""
        node = {"op": "union", "children": [
            {"type": "rectangle", "center": [25, 25], "size": [50, 50], "z_top": 20},
            {"type": "rectangle", "center": [25, 75], "size": [50, 50], "z_top": 35},
        ], "rotate": 90}
        out = tessellate_shape(node)
        # After 90° CCW around centroid (25,50):
        # Original top rect (z=20) moves to right side (x > 25)
        # Original bottom rect (z=35) moves to left side (x < 25)
        for p in out.points:
            if p.x > 50:
                self.assertEqual(p.z_top, 20,
                    f"Right-side vertex ({p.x:.0f},{p.y:.0f}) should be z_top=20")
            elif p.x < 0:
                self.assertEqual(p.z_top, 35,
                    f"Left-side vertex ({p.x:.0f},{p.y:.0f}) should be z_top=35")

    def test_z_follows_op_scale(self):
        """z_top regions must scale with the geometry."""
        node = {"op": "union", "children": [
            {"type": "rectangle", "center": [25, 25], "size": [50, 50], "z_top": 15},
            {"type": "rectangle", "center": [25, 75], "size": [50, 50], "z_top": 30},
        ], "scale": 0.5}
        out = tessellate_shape(node)
        for p in out.points:
            if p.y < 50:
                self.assertEqual(p.z_top, 15,
                    f"Top vertex ({p.x:.0f},{p.y:.0f}) should be z_top=15")

    def test_z_with_primitive_scale(self):
        """Scaled primitive z_top must cover all output vertices."""
        node = {"type": "rectangle", "center": [25, 50], "size": [10, 10],
                "scale": 3.0, "z_top": 30}
        out = tessellate_shape(node)
        for p in out.points:
            self.assertEqual(p.z_top, 30)


# ── Complex Tree ──────────────────────────────────────────────────────────────

class TestComplexTree(unittest.TestCase):

    def test_mixed_primitives_and_transforms(self):
        tree = {
            "op": "union",
            "children": [
                {"type": "rectangle", "center": [25, 50], "size": [50, 100], "corner_radius": 5},
                {"type": "rectangle", "center": [50, 30], "size": [12, 40],
                 "size_end": [4, 40], "axis": "y", "rotate": -30},
                {"type": "ellipse", "center": [10, 20], "radius": 8,
                 "end_center": [40, 10], "radius_end": 3},
            ],
        }
        self.assertEqual(validate_shape(tree), [])
        out = tessellate_shape(tree)
        self.assertGreater(len(out.points), 10)

    def test_nested_with_transforms(self):
        tree = {
            "op": "difference",
            "children": [
                {"op": "union", "children": [
                    {"type": "rectangle", "center": [25, 50], "size": [50, 100], "corner_radius": 8},
                    {"type": "ellipse", "center": [25, 5], "radius": 20},
                ], "scale": 1.1},
                {"type": "ellipse", "center": [0, 50], "radius": [10, 18]},
                {"type": "ellipse", "center": [50, 50], "radius": [10, 18], "mirror": "x"},
            ],
            "rotate": 5,
        }
        self.assertEqual(validate_shape(tree), [])
        out = tessellate_shape(tree)
        self.assertGreater(len(out.points), 10)


if __name__ == "__main__":
    unittest.main()

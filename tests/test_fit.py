"""Tests for the ``fit`` adaptive dimension feature."""

from __future__ import annotations

import math
import unittest

import numpy as np

from src.pipeline.design.models3d import CSGNode, FitMarker
from src.pipeline.design.parsing3d import _parse_csg_node
from src.pipeline.design.serialization3d import _csg_node_to_dict
from src.pipeline.design.validation3d import _validate_csg_node
from src.pipeline.mesh.csg import evaluate_csg


# ── Parsing ────────────────────────────────────────────────────────


class TestFitParsing(unittest.TestCase):
    """Verify that ``"fit"`` and ``{"fit": N}`` are parsed into FitMarker."""

    def test_height_fit_bare(self):
        node = _parse_csg_node({
            "type": "cylinder", "radius": 5, "height": "fit", "axis": "z",
        })
        self.assertIn("height", node.fit)
        self.assertIsNone(node.fit["height"].cap)
        self.assertIsNone(node.height)

    def test_height_fit_capped(self):
        node = _parse_csg_node({
            "type": "cylinder", "radius": 5, "height": {"fit": 30}, "axis": "z",
        })
        self.assertIn("height", node.fit)
        self.assertAlmostEqual(node.fit["height"].cap, 30.0)

    def test_radius_fit(self):
        node = _parse_csg_node({
            "type": "cylinder", "radius": "fit", "height": 10, "axis": "z",
        })
        self.assertIn("radius", node.fit)
        self.assertIsNone(node.radius)
        self.assertIsNone(node.radii)

    def test_radius_end_fit(self):
        node = _parse_csg_node({
            "type": "cylinder", "radius": 5, "radius_end": "fit", "height": 10,
        })
        self.assertIn("radius_end", node.fit)

    def test_size_per_element_fit(self):
        node = _parse_csg_node({
            "type": "box", "size": [10, "fit", 20],
        })
        self.assertIn("size.y", node.fit)
        self.assertIsNone(node.fit["size.y"].cap)
        # The fit element is replaced with 0.0 in the tuple
        self.assertEqual(node.size[0], 10.0)
        self.assertEqual(node.size[1], 0.0)
        self.assertEqual(node.size[2], 20.0)

    def test_size_per_element_fit_capped(self):
        node = _parse_csg_node({
            "type": "box", "size": [10, {"fit": 50}, 20],
        })
        self.assertIn("size.y", node.fit)
        self.assertAlmostEqual(node.fit["size.y"].cap, 50.0)

    def test_no_fit_fields_normal_node(self):
        node = _parse_csg_node({
            "type": "box", "size": [10, 20, 30],
        })
        self.assertEqual(len(node.fit), 0)


# ── Serialization roundtrip ────────────────────────────────────────


class TestFitSerialization(unittest.TestCase):
    """Verify that fit markers survive a serialize→parse roundtrip."""

    def test_height_fit_roundtrip(self):
        node = CSGNode(
            type="cylinder", radius=5.0, height=None, axis="z",
            fit={"height": FitMarker()},
        )
        d = _csg_node_to_dict(node)
        self.assertEqual(d["height"], "fit")
        reparsed = _parse_csg_node(d)
        self.assertIn("height", reparsed.fit)
        self.assertIsNone(reparsed.fit["height"].cap)

    def test_height_fit_capped_roundtrip(self):
        node = CSGNode(
            type="cylinder", radius=5.0, height=None, axis="z",
            fit={"height": FitMarker(cap=25.0)},
        )
        d = _csg_node_to_dict(node)
        self.assertEqual(d["height"], {"fit": 25.0})
        reparsed = _parse_csg_node(d)
        self.assertAlmostEqual(reparsed.fit["height"].cap, 25.0)

    def test_radius_fit_roundtrip(self):
        node = CSGNode(
            type="cylinder", radius=None, height=10.0, axis="z",
            fit={"radius": FitMarker()},
        )
        d = _csg_node_to_dict(node)
        self.assertEqual(d["radius"], "fit")

    def test_size_axis_fit_roundtrip(self):
        node = CSGNode(
            type="box", size=(10.0, 0.0, 20.0),
            fit={"size.y": FitMarker(cap=40.0)},
        )
        d = _csg_node_to_dict(node)
        self.assertEqual(d["size"], [10.0, {"fit": 40.0}, 20.0])
        reparsed = _parse_csg_node(d)
        self.assertIn("size.y", reparsed.fit)
        self.assertAlmostEqual(reparsed.fit["size.y"].cap, 40.0)


# ── Validation ─────────────────────────────────────────────────────


class TestFitValidation(unittest.TestCase):
    """Verify validation catches misuse of ``fit``."""

    def test_fit_at_root_rejected(self):
        node = CSGNode(
            type="cylinder", radius=5.0, height=None, axis="z",
            fit={"height": FitMarker()},
        )
        errors: list[str] = []
        _validate_csg_node(node, errors, path="shape", inside_op=False)
        self.assertTrue(any("boolean operation" in e for e in errors))

    def test_fit_inside_op_allowed(self):
        node = CSGNode(
            type="cylinder", radius=5.0, height=None, axis="z",
            fit={"height": FitMarker()},
        )
        errors: list[str] = []
        _validate_csg_node(node, errors, path="shape.children[1]", inside_op=True)
        self.assertFalse(any("boolean operation" in e for e in errors))

    def test_fit_invalid_field_for_type(self):
        node = CSGNode(
            type="box", size=(10.0, 20.0, 30.0),
            fit={"radius": FitMarker()},
        )
        errors: list[str] = []
        _validate_csg_node(node, errors, path="shape.children[1]", inside_op=True)
        self.assertTrue(any("not valid" in e for e in errors))

    def test_fit_negative_cap_rejected(self):
        node = CSGNode(
            type="cylinder", radius=5.0, height=None, axis="z",
            fit={"height": FitMarker(cap=-5.0)},
        )
        errors: list[str] = []
        _validate_csg_node(node, errors, path="shape.children[1]", inside_op=True)
        self.assertTrue(any("cap" in e and "> 0" in e for e in errors))


# ── CSG mesh evaluation ───────────────────────────────────────────


class TestFitMeshResolution(unittest.TestCase):
    """Verify that fit-marked primitives are resolved against context mesh."""

    def test_height_fit_cylinder_through_box(self):
        """A cylinder with height='fit' inside a difference with a box
        should have its vertices clamped to the box surfaces."""
        box_h = 20.0
        tree = CSGNode(
            op="difference",
            children=[
                CSGNode(type="box", size=(60.0, 40.0, box_h)),
                CSGNode(
                    type="cylinder", radius=3.0, height=None, axis="z",
                    center=(0.0, 0.0, 0.0),
                    fit={"height": FitMarker()},
                ),
            ],
        )
        mesh = evaluate_csg(tree)
        # The result should be a box with a hole
        self.assertTrue(mesh.is_watertight)
        # Volume should be less than the original box
        box_vol = 60.0 * 40.0 * box_h
        self.assertLess(mesh.volume, box_vol)
        # Volume should be close to box minus cylinder
        cyl_vol = math.pi * 3.0 ** 2 * box_h
        expected = box_vol - cyl_vol
        self.assertAlmostEqual(mesh.volume, expected, delta=expected * 0.05)

    def test_height_fit_capped(self):
        """A cylinder with height={"fit": 10} should not exceed 10mm even
        if the box is thicker."""
        tree = CSGNode(
            op="difference",
            children=[
                CSGNode(type="box", size=(60.0, 40.0, 30.0)),
                CSGNode(
                    type="cylinder", radius=3.0, height=None, axis="z",
                    center=(0.0, 0.0, 0.0),
                    fit={"height": FitMarker(cap=10.0)},
                ),
            ],
        )
        mesh = evaluate_csg(tree)
        # The cylinder should only be 10mm tall (not 30mm)
        # So the hole should NOT go all the way through
        box_vol = 60.0 * 40.0 * 30.0
        cyl_vol = math.pi * 3.0 ** 2 * 10.0
        expected = box_vol - cyl_vol
        self.assertAlmostEqual(mesh.volume, expected, delta=expected * 0.05)

    def test_radius_fit_cylinder_in_sphere(self):
        """Cylinder with radius='fit' inside a sphere should conform
        to the sphere interior."""
        tree = CSGNode(
            op="intersection",
            children=[
                CSGNode(type="sphere", radius=20.0),
                CSGNode(
                    type="cylinder", radius=None, height=5.0, axis="z",
                    fit={"radius": FitMarker()},
                ),
            ],
        )
        mesh = evaluate_csg(tree)
        self.assertTrue(mesh.is_watertight)
        # Result should be a disc-shaped slice of the sphere.
        # All vertices should be within the sphere radius.
        dists = np.linalg.norm(mesh.vertices, axis=1)
        self.assertTrue(np.all(dists <= 20.0 + 0.5))

    def test_no_fit_unchanged(self):
        """A normal tree without fit should produce identical results."""
        tree = CSGNode(
            op="union",
            children=[
                CSGNode(type="box", size=(10.0, 10.0, 10.0)),
                CSGNode(
                    type="cylinder", radius=3.0, height=5.0, axis="z",
                    center=(0.0, 0.0, 7.5),
                ),
            ],
        )
        mesh = evaluate_csg(tree)
        self.assertTrue(mesh.is_watertight)
        box_vol = 10.0 * 10.0 * 10.0
        self.assertGreater(mesh.volume, box_vol)

    def test_fit_without_context_raises(self):
        """A fit node at the root (no parent op) should raise."""
        node = CSGNode(
            type="cylinder", radius=5.0, height=None, axis="z",
            fit={"height": FitMarker()},
        )
        with self.assertRaises(ValueError):
            evaluate_csg(node)


if __name__ == "__main__":
    unittest.main()

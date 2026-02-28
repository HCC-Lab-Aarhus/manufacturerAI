"""Tests for the component placer (Stage 3).

Uses the flashlight fixture as the primary test case:
  - 30×80mm rectangle outline
  - Button at (15, 45), LED at (15, 70) — UI-placed
  - Battery (25×48mm) and resistor (2.5×6.5mm) — auto-placed

Validates:
  - All components are placed
  - All placements are inside the outline
  - No overlaps (with keepout margins)
  - Net-connected components are reasonably close
  - Battery (bottom-mount) ends up near the bottom
  - Serialization round-trips correctly
"""

from __future__ import annotations

import json
import math
import unittest

from shapely.geometry import Polygon, box as shapely_box

from src.catalog.loader import load_catalog
from src.pipeline.design.models import (
    ComponentInstance, Net, OutlineVertex, Outline, UIPlacement, DesignSpec,
)
from src.pipeline.placer import (
    PlacedComponent,
    FullPlacement,
    PlacementError,
    place_components,
    placement_to_dict,
    parse_placement,
    footprint_halfdims,
    footprint_envelope_halfdims,
    pin_world_xy,
    aabb_gap,
    rect_inside_polygon,
)
from tests.flashlight_fixture import make_flashlight_design


class TestPlacerGeometryHelpers(unittest.TestCase):
    """Unit tests for low-level geometry functions."""

    def testfootprint_halfdims_rect(self):
        """Rect body dimensions swap at 90°/270°."""
        from src.catalog.models import Component, Body, Mounting, Pin
        cat = Component(
            id="test", name="test", description="", category="passive",
            ui_placement=False,
            body=Body(shape="rect", width_mm=6.0, length_mm=10.0, height_mm=3.0),
            mounting=Mounting(style="internal", allowed_styles=["internal"],
                              blocks_routing=False, keepout_margin_mm=1.0),
            pins=[],
        )
        self.assertEqual(footprint_halfdims(cat, 0), (3.0, 5.0))
        self.assertEqual(footprint_halfdims(cat, 90), (5.0, 3.0))
        self.assertEqual(footprint_halfdims(cat, 180), (3.0, 5.0))
        self.assertEqual(footprint_halfdims(cat, 270), (5.0, 3.0))

    def testfootprint_halfdims_circle(self):
        """Circle body is rotation-invariant."""
        from src.catalog.models import Component, Body, Mounting
        cat = Component(
            id="test", name="test", description="", category="indicator",
            ui_placement=True,
            body=Body(shape="circle", diameter_mm=8.0, height_mm=5.0),
            mounting=Mounting(style="top", allowed_styles=["top"],
                              blocks_routing=False, keepout_margin_mm=1.0),
            pins=[],
        )
        for rot in (0, 90, 180, 270):
            self.assertEqual(footprint_halfdims(cat, rot), (4.0, 4.0))

    def testpin_world_xy_no_rotation(self):
        wx, wy = pin_world_xy((3.0, 4.0), 10.0, 20.0, 0)
        self.assertAlmostEqual(wx, 13.0)
        self.assertAlmostEqual(wy, 24.0)

    def testpin_world_xy_90_rotation(self):
        wx, wy = pin_world_xy((3.0, 0.0), 10.0, 20.0, 90)
        self.assertAlmostEqual(wx, 10.0, places=5)
        self.assertAlmostEqual(wy, 23.0, places=5)

    def testaabb_gap_separated(self):
        # Two 2×2 boxes, 5mm apart horizontally
        gap = aabb_gap(0, 0, 1, 1, 6, 0, 1, 1)
        self.assertAlmostEqual(gap, 4.0)

    def testaabb_gap_touching(self):
        gap = aabb_gap(0, 0, 1, 1, 2, 0, 1, 1)
        self.assertAlmostEqual(gap, 0.0)

    def testaabb_gap_overlapping(self):
        gap = aabb_gap(0, 0, 2, 2, 1, 1, 2, 2)
        self.assertLess(gap, 0)

    def testrect_inside_polygon(self):
        poly = Polygon([(0, 0), (30, 0), (30, 80), (0, 80)])
        self.assertTrue(rect_inside_polygon(15, 40, 5, 5, poly))
        self.assertFalse(rect_inside_polygon(1, 1, 5, 5, poly))   # extends outside
        self.assertFalse(rect_inside_polygon(28, 40, 5, 5, poly))  # right edge out

    def test_footprint_envelope_larger_than_body(self):
        """Envelope includes pins that extend beyond the body."""
        from src.catalog.models import Component, Body, Mounting, Pin
        cat = Component(
            id="test_env", name="test", description="", category="passive",
            ui_placement=False,
            body=Body(shape="rect", width_mm=6.5, length_mm=2.5, height_mm=2.5),
            mounting=Mounting(style="internal", allowed_styles=["internal"],
                              blocks_routing=False, keepout_margin_mm=1.0),
            pins=[
                Pin(id="1", label="L1", position_mm=(-5.0, 0),
                    direction="bidirectional", hole_diameter_mm=0.8,
                    description=""),
                Pin(id="2", label="L2", position_mm=(5.0, 0),
                    direction="bidirectional", hole_diameter_mm=0.8,
                    description=""),
            ],
        )
        # Body half-dims: (3.25, 1.25)
        body_hw, body_hh = footprint_halfdims(cat, 0)
        self.assertAlmostEqual(body_hw, 3.25)
        self.assertAlmostEqual(body_hh, 1.25)

        # Envelope must cover pins at ±5.0 + pad radius 0.4
        env_hw, env_hh = footprint_envelope_halfdims(cat, 0)
        self.assertAlmostEqual(env_hw, 5.4)   # 5.0 + 0.4
        self.assertGreaterEqual(env_hh, body_hh)

        # At 90° rotation the axes swap
        env_hw90, env_hh90 = footprint_envelope_halfdims(cat, 90)
        self.assertAlmostEqual(env_hh90, 5.4)
        self.assertGreaterEqual(env_hw90, body_hh)


class TestFlashlightPlacement(unittest.TestCase):
    """Integration test using the flashlight fixture."""

    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()
        cls.design = make_flashlight_design()
        cls.catalog_map = {c.id: c for c in cls.catalog.components}

    def test_placement_succeeds(self):
        """All 4 components should be placed without error."""
        result = place_components(self.design, self.catalog)
        self.assertIsInstance(result, FullPlacement)
        self.assertEqual(len(result.components), 4)

    def test_all_instance_ids_present(self):
        """Every component from the design appears in the placement."""
        result = place_components(self.design, self.catalog)
        placed_ids = {c.instance_id for c in result.components}
        design_ids = {c.instance_id for c in self.design.components}
        self.assertEqual(placed_ids, design_ids)

    def test_ui_components_at_specified_positions(self):
        """Button and LED should be at their UI-specified positions."""
        result = place_components(self.design, self.catalog)
        by_id = {c.instance_id: c for c in result.components}

        btn = by_id["btn_1"]
        self.assertAlmostEqual(btn.x_mm, 17.5)
        self.assertAlmostEqual(btn.y_mm, 70.0)

        led = by_id["led_1"]
        self.assertAlmostEqual(led.x_mm, 17.5)
        self.assertAlmostEqual(led.y_mm, 100.0)

    def test_all_inside_outline(self):
        """Every component envelope (body + pins) must lie inside the outline."""
        result = place_components(self.design, self.catalog)
        outline_poly = Polygon(self.design.outline.vertices)

        for pc in result.components:
            cat = self.catalog_map[pc.catalog_id]
            ehw, ehh = footprint_envelope_halfdims(cat, pc.rotation_deg)
            rect = shapely_box(
                pc.x_mm - ehw, pc.y_mm - ehh,
                pc.x_mm + ehw, pc.y_mm + ehh,
            )
            self.assertTrue(
                outline_poly.contains(rect),
                f"{pc.instance_id} at ({pc.x_mm}, {pc.y_mm}) envelope outside outline",
            )

    def test_no_overlaps(self):
        """No two component envelopes should overlap (respecting keepout)."""
        result = place_components(self.design, self.catalog)
        comps = result.components
        for i in range(len(comps)):
            ci = comps[i]
            cat_i = self.catalog_map[ci.catalog_id]
            ehw_i, ehh_i = footprint_envelope_halfdims(cat_i, ci.rotation_deg)
            ko_i = cat_i.mounting.keepout_margin_mm
            for j in range(i + 1, len(comps)):
                cj = comps[j]
                cat_j = self.catalog_map[cj.catalog_id]
                ehw_j, ehh_j = footprint_envelope_halfdims(cat_j, cj.rotation_deg)
                ko_j = cat_j.mounting.keepout_margin_mm
                gap = aabb_gap(
                    ci.x_mm, ci.y_mm, ehw_i, ehh_i,
                    cj.x_mm, cj.y_mm, ehw_j, ehh_j,
                )
                required = max(ko_i, ko_j)
                self.assertGreaterEqual(
                    gap, required - 0.01,
                    f"{ci.instance_id} and {cj.instance_id} envelopes overlap: "
                    f"gap={gap:.2f}mm < required={required:.2f}mm",
                )

    def test_battery_near_bottom(self):
        """Battery (bottom-mount) should be placed in the lower half."""
        result = place_components(self.design, self.catalog)
        bat = next(c for c in result.components if c.instance_id == "bat_1")
        # The outline is 0-120mm tall; battery should be in the lower third
        self.assertLess(
            bat.y_mm, 50.0,
            f"Battery at y={bat.y_mm:.1f}mm — expected near bottom (< 50mm)",
        )

    def test_valid_rotations(self):
        """All rotations must be 0, 90, 180, or 270."""
        result = place_components(self.design, self.catalog)
        for c in result.components:
            self.assertIn(c.rotation_deg, (0, 90, 180, 270),
                          f"{c.instance_id} has invalid rotation {c.rotation_deg}")

    def test_outline_and_nets_passed_through(self):
        """FullPlacement should pass through outline and nets unchanged."""
        result = place_components(self.design, self.catalog)
        self.assertEqual(result.outline, self.design.outline)
        self.assertEqual(result.nets, self.design.nets)


class TestPlacementSerialization(unittest.TestCase):
    """Test placement serialization round-trip."""

    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()
        cls.design = make_flashlight_design()
        cls.placement = place_components(cls.design, cls.catalog)

    def test_to_dict(self):
        """placement_to_dict produces a valid JSON-serializable dict."""
        d = placement_to_dict(self.placement)
        # Should be JSON-serializable
        json_str = json.dumps(d)
        self.assertIsInstance(json_str, str)
        # Check structure
        self.assertIn("components", d)
        self.assertIn("outline", d)
        self.assertIn("nets", d)
        self.assertEqual(len(d["components"]), 4)

    def test_round_trip(self):
        """placement_to_dict -> parse_placement should preserve data."""
        d = placement_to_dict(self.placement)
        restored = parse_placement(d)
        self.assertEqual(len(restored.components), len(self.placement.components))
        for orig, rest in zip(self.placement.components, restored.components):
            self.assertEqual(orig.instance_id, rest.instance_id)
            self.assertEqual(orig.catalog_id, rest.catalog_id)
            self.assertAlmostEqual(orig.x_mm, rest.x_mm, places=2)
            self.assertAlmostEqual(orig.y_mm, rest.y_mm, places=2)
            self.assertEqual(orig.rotation_deg, rest.rotation_deg)


class TestPlacementErrors(unittest.TestCase):
    """Test error handling for impossible placements."""

    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()

    def test_component_too_large_for_outline(self):
        """A component bigger than the outline should raise PlacementError."""
        tiny_outline = DesignSpec(
            components=[
                ComponentInstance(
                    catalog_id="battery_holder_2xAAA",
                    instance_id="bat_1",
                ),
            ],
            nets=[],
            outline=Outline(points=[
                OutlineVertex(x=0, y=0),
                OutlineVertex(x=10, y=0),   # only 10mm wide
                OutlineVertex(x=10, y=10),  # only 10mm tall
                OutlineVertex(x=0, y=10),
            ]),
            ui_placements=[],
        )
        with self.assertRaises(PlacementError) as ctx:
            place_components(tiny_outline, self.catalog)
        self.assertIn("bat_1", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

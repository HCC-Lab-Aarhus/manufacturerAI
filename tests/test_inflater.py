"""Tests for polygon trace inflation."""

from __future__ import annotations

import unittest

from shapely.geometry import Polygon, LineString, Point

from src.pipeline.config import TRACE_RULES
from src.pipeline.router.models import Trace, InflatedTrace, RoutingResult
from src.pipeline.inflation import inflate_traces, build_obstacle_polygons, inflation_to_dict, parse_inflation


def _box(margin: float = 20.0) -> Polygon:
    return Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])


def _simple_result(*traces: Trace) -> RoutingResult:
    return RoutingResult(
        traces=list(traces),
        pin_assignments={},
        failed_nets=[],
    )


class TestInflateBasic(unittest.TestCase):

    def test_single_trace_expands(self):
        trace = Trace(net_id="A", path=[(30, 50), (70, 50)])
        result = _simple_result(trace)
        outline = _box()

        inflated = inflate_traces(result, outline, [])

        self.assertEqual(len(inflated), 1)
        it = inflated[0]
        self.assertEqual(it.net_id, "A")
        self.assertIsInstance(it.polygon, Polygon)

        cl_len = LineString(trace.path).length
        avg_width = it.polygon.area / cl_len
        self.assertGreater(avg_width, TRACE_RULES.trace_width_mm)

    def test_two_parallel_traces_share_space(self):
        gap_mm = 10.0
        t1 = Trace(net_id="A", path=[(20, 45), (80, 45)])
        t2 = Trace(net_id="B", path=[(20, 45 + gap_mm), (80, 45 + gap_mm)])
        result = _simple_result(t1, t2)
        outline = _box()

        inflated = inflate_traces(result, outline, [])

        self.assertEqual(len(inflated), 2)
        a_area = inflated[0].polygon.area
        b_area = inflated[1].polygon.area
        ratio = min(a_area, b_area) / max(a_area, b_area)
        self.assertGreater(ratio, 0.7, "Parallel traces should have roughly equal area")

    def test_traces_do_not_overlap(self):
        t1 = Trace(net_id="A", path=[(20, 40), (80, 40)])
        t2 = Trace(net_id="B", path=[(20, 50), (80, 50)])
        result = _simple_result(t1, t2)
        outline = _box()

        inflated = inflate_traces(result, outline, [])

        overlap = inflated[0].polygon.intersection(inflated[1].polygon)
        self.assertLess(overlap.area, 1.0, "Inflated traces should not significantly overlap")

    def test_max_width_cap(self):
        trace = Trace(net_id="A", path=[(10, 50), (90, 50)])
        result = _simple_result(trace)
        outline = _box()
        max_w = 8.0

        inflated = inflate_traces(result, outline, [], max_width_mm=max_w)

        it = inflated[0]
        cl = LineString(trace.path)
        for frac in [0.1, 0.3, 0.5, 0.7, 0.9]:
            pt = cl.interpolate(frac, normalized=True)
            cross = LineString([(pt.x, pt.y - max_w), (pt.x, pt.y + max_w)])
            section = cross.intersection(it.polygon)
            self.assertLessEqual(section.length, max_w + 0.5)

    def test_obstacle_limits_expansion(self):
        trace = Trace(net_id="A", path=[(20, 50), (80, 50)])
        result = _simple_result(trace)
        outline = _box()
        obstacle = Polygon([(40, 55), (60, 55), (60, 70), (40, 70)])

        inflated = inflate_traces(result, outline, [obstacle])

        it = inflated[0]
        overlap = it.polygon.intersection(obstacle)
        self.assertLess(overlap.area, 1.0, "Trace should not expand into obstacle")

    def test_stays_inside_outline(self):
        trace = Trace(net_id="A", path=[(5, 50), (95, 50)])
        result = _simple_result(trace)
        outline = _box()

        inflated = inflate_traces(result, outline, [])

        it = inflated[0]
        outside = it.polygon.difference(outline)
        self.assertLess(outside.area, 1.0, "Polygon should stay inside outline")

    def test_minimum_width_preserved(self):
        t1 = Trace(net_id="A", path=[(20, 49), (80, 49)])
        t2 = Trace(net_id="B", path=[(20, 51), (80, 51)])
        result = _simple_result(t1, t2)
        outline = _box()

        inflated = inflate_traces(result, outline, [])

        for it in inflated:
            cl_len = LineString(it.centreline).length
            if cl_len > 0:
                avg_w = it.polygon.area / cl_len
                self.assertGreaterEqual(avg_w, TRACE_RULES.trace_width_mm * 0.9)


class TestInflateEmpty(unittest.TestCase):

    def test_no_traces(self):
        result = _simple_result()
        outline = _box()
        inflated = inflate_traces(result, outline, [])
        self.assertEqual(inflated, [])

    def test_single_point_trace(self):
        trace = Trace(net_id="A", path=[(50, 50), (50, 50)])
        result = _simple_result(trace)
        outline = _box()
        inflated = inflate_traces(result, outline, [])
        self.assertEqual(len(inflated), 1)
        self.assertGreater(inflated[0].polygon.area, 0)


class TestSerializationRoundTrip(unittest.TestCase):

    def test_round_trip(self):
        trace = Trace(net_id="X", path=[(10, 20), (30, 20), (30, 50)])
        result = _simple_result(trace)
        outline = _box()

        inflated = inflate_traces(result, outline, [])

        d = inflation_to_dict(inflated)
        self.assertIn("inflated_traces", d)
        self.assertEqual(len(d["inflated_traces"]), 1)

        parsed = parse_inflation(d)
        self.assertEqual(len(parsed), 1)
        pit = parsed[0]
        self.assertEqual(pit.net_id, "X")
        self.assertIsInstance(pit.polygon, Polygon)

        area_diff = abs(inflated[0].polygon.area - pit.polygon.area)
        self.assertLess(area_diff, 0.5)


class TestBuildObstacles(unittest.TestCase):

    def test_empty(self):
        obstacles = build_obstacle_polygons([], {})
        self.assertEqual(obstacles, [])


class TestPolygonSmoothing(unittest.TestCase):

    def test_polygon_not_excessive_vertices(self):
        trace = Trace(net_id="A", path=[(10, 50), (90, 50)])
        result = _simple_result(trace)
        outline = _box()
        inflated = inflate_traces(result, outline, [])
        n_verts = len(inflated[0].polygon.exterior.coords)
        self.assertLess(n_verts, 500)


class TestPinClearance(unittest.TestCase):

    def test_foreign_pin_excluded(self):
        trace = Trace(net_id="A", path=[(20, 50), (80, 50)])
        result = _simple_result(trace)
        outline = _box()

        foreign_pin_pos = (50, 55)
        pin_positions = {
            "comp:own_pin": (20, 50),
            "comp:foreign": foreign_pin_pos,
        }
        net_pin_ids = {
            "A": {"comp:own_pin"},
        }

        inflated = inflate_traces(
            result, outline, [],
            pin_positions=pin_positions,
            net_pin_ids=net_pin_ids,
            pin_clearance_mm=1.0,
        )

        it = inflated[0]
        foreign_pt = Point(foreign_pin_pos)
        dist = it.polygon.boundary.distance(foreign_pt)
        if it.polygon.contains(foreign_pt):
            self.fail("Inflated polygon covers a foreign pin")
        self.assertGreaterEqual(dist, 0.5, "Polygon edge too close to foreign pin")

    def test_own_pin_not_excluded(self):
        trace = Trace(net_id="A", path=[(20, 50), (80, 50)])
        result = _simple_result(trace)
        outline = _box()

        own_pin_pos = (50, 50)
        pin_positions = {"comp:p1": own_pin_pos}
        net_pin_ids = {"A": {"comp:p1"}}

        inflated = inflate_traces(
            result, outline, [],
            pin_positions=pin_positions,
            net_pin_ids=net_pin_ids,
        )

        it = inflated[0]
        self.assertTrue(
            it.polygon.contains(Point(own_pin_pos)),
            "Trace should cover its own pin",
        )


if __name__ == "__main__":
    unittest.main()

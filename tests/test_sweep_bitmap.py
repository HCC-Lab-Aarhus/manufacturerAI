"""Tests for the bed bitmap geometry and bitmap generation.

Covers:
  - PrintheadConfig derived properties
  - bed_bitmap() dimensions for every printer definition
  - BedBitmap.bed_to_pixel() coordinate transform
  - get_printer() fallback behaviour
  - generate_trace_bitmap() rasterization correctness
"""

from __future__ import annotations

import math
import unittest

from src.pipeline.config import (
    PIXEL_SIZE_MM,
    PrinterDef,
    PRINTERS,
    BedBitmap,
    bed_bitmap,
    get_printer,
    DEFAULT_PRINTER,
)
from src.pipeline.router.models import Trace, RoutingResult
from src.pipeline.router.bitmap import generate_trace_bitmap


# ── Pixel resolution constant ──────────────────────────────────────

class TestPixelSize(unittest.TestCase):

    def test_pixel_size(self):
        self.assertAlmostEqual(PIXEL_SIZE_MM, 0.1371)


# ── bed_bitmap() dimensions ───────────────────────────────────────

class TestBedBitmapDimensions(unittest.TestCase):
    """Verify bed_bitmap() produces correct dimensions for every printer."""

    def test_mk3s_dimensions(self):
        pdef = PRINTERS["mk3s"]
        grid = bed_bitmap(pdef)
        expected_cols = math.ceil(250.0 / 0.1371)
        expected_rows = math.ceil(210.0 / 0.1371)
        self.assertEqual(grid.cols, expected_cols)
        self.assertEqual(grid.rows, expected_rows)

    def test_mk3s_plus_dimensions(self):
        pdef = PRINTERS["mk3s_plus"]
        grid = bed_bitmap(pdef)
        expected_cols = math.ceil(250.0 / 0.1371)
        expected_rows = math.ceil(210.0 / 0.1371)
        self.assertEqual(grid.cols, expected_cols)
        self.assertEqual(grid.rows, expected_rows)

    def test_coreone_dimensions(self):
        pdef = PRINTERS["coreone"]
        grid = bed_bitmap(pdef)
        expected_cols = math.ceil(250.0 / 0.1371)
        expected_rows = math.ceil(250.0 / 0.1371)
        self.assertEqual(grid.cols, expected_cols)
        self.assertEqual(grid.rows, expected_rows)

    def test_coreone_deeper_than_mk3s(self):
        mk3s = bed_bitmap(PRINTERS["mk3s"])
        core = bed_bitmap(PRINTERS["coreone"])
        self.assertEqual(mk3s.cols, core.cols)
        self.assertGreater(core.rows, mk3s.rows)

    def test_pixel_size_matches_constant(self):
        for pid, pdef in PRINTERS.items():
            with self.subTest(printer=pid):
                grid = bed_bitmap(pdef)
                self.assertAlmostEqual(grid.pixel_size_mm, PIXEL_SIZE_MM)


# ── BedBitmap.bed_to_pixel() ──────────────────────────────────────

class TestBedToPixel(unittest.TestCase):

    def setUp(self):
        self.pdef = PRINTERS["mk3s_plus"]
        self.grid = bed_bitmap(self.pdef)

    def test_origin_maps_to_zero(self):
        px, py = self.grid.bed_to_pixel(0.0, 0.0)
        self.assertAlmostEqual(px, 0.0, places=6)
        self.assertAlmostEqual(py, 0.0, places=6)

    def test_known_point(self):
        px, py = self.grid.bed_to_pixel(100.0, 100.0)
        self.assertAlmostEqual(px, 100.0 / 0.1371, places=3)
        self.assertAlmostEqual(py, 100.0 / 0.1371, places=3)

    def test_transform_is_pure_scaling(self):
        px1, py1 = self.grid.bed_to_pixel(50.0, 50.0)
        px2, py2 = self.grid.bed_to_pixel(60.0, 70.0)
        self.assertAlmostEqual((px2 - px1) * 0.1371, 10.0, places=6)
        self.assertAlmostEqual((py2 - py1) * 0.1371, 20.0, places=6)

    def test_all_printers_same_transform(self):
        grids = {pid: bed_bitmap(pdef) for pid, pdef in PRINTERS.items()}
        ref_px, _ = grids["mk3s"].bed_to_pixel(100.0, 100.0)
        for pid, g in grids.items():
            px, _ = g.bed_to_pixel(100.0, 100.0)
            self.assertAlmostEqual(px, ref_px, places=6, msg=pid)


# ── get_printer() ─────────────────────────────────────────────────

class TestGetPrinter(unittest.TestCase):

    def test_known_printer(self):
        p = get_printer("mk3s")
        self.assertEqual(p.id, "mk3s")

    def test_default_printer(self):
        p = get_printer(None)
        self.assertEqual(p.id, DEFAULT_PRINTER)

    def test_unknown_falls_back(self):
        p = get_printer("nonexistent_printer")
        self.assertEqual(p.id, DEFAULT_PRINTER)

    def test_case_insensitive(self):
        p = get_printer("CoreOne")
        self.assertEqual(p.id, "coreone")


# ── PrinterDef keepout / usable area ──────────────────────────────

class TestPrinterDefUsableArea(unittest.TestCase):

    def test_usable_width(self):
        pdef = PRINTERS["coreone"]
        expected = pdef.nominal_bed_width - pdef.keepout_left - pdef.keepout_right
        self.assertAlmostEqual(pdef.usable_width, expected)

    def test_usable_depth(self):
        pdef = PRINTERS["coreone"]
        expected = pdef.nominal_bed_depth - pdef.keepout_front - pdef.keepout_back
        self.assertAlmostEqual(pdef.usable_depth, expected)

    def test_bed_width_is_usable_width(self):
        for pid, pdef in PRINTERS.items():
            with self.subTest(printer=pid):
                self.assertEqual(pdef.bed_width, pdef.usable_width)
                self.assertEqual(pdef.bed_depth, pdef.usable_depth)


# ── generate_trace_bitmap() ───────────────────────────────────────

class TestGenerateTraceBitmap(unittest.TestCase):

    def setUp(self):
        self.pdef = PRINTERS["coreone"]
        self.grid = bed_bitmap(self.pdef)

    def test_empty_routing_produces_blank_bitmap(self):
        result = RoutingResult(traces=[], pin_assignments={}, failed_nets=[])
        lines = generate_trace_bitmap(result, 0.5, grid=self.grid)
        self.assertEqual(len(lines), self.grid.rows)
        self.assertTrue(all(len(l) == self.grid.cols for l in lines))
        self.assertTrue(all(c == '0' for line in lines for c in line))

    def test_bitmap_only_contains_0_and_1(self):
        trace = Trace(
            net_id="test",
            path=[(5.0, 5.0), (15.0, 5.0)],
        )
        result = RoutingResult(traces=[trace], pin_assignments={}, failed_nets=[])
        model_to_bed = (80.0, 80.0)
        lines = generate_trace_bitmap(result, 0.5, grid=self.grid, model_to_bed=model_to_bed)
        all_chars = set(c for line in lines for c in line)
        self.assertTrue(all_chars.issubset({'0', '1'}))

    def test_trace_produces_ink(self):
        trace = Trace(
            net_id="signal",
            path=[(0.0, 5.0), (20.0, 5.0)],
        )
        result = RoutingResult(traces=[trace], pin_assignments={}, failed_nets=[])
        model_to_bed = (100.0, 100.0)
        lines = generate_trace_bitmap(result, 0.5, grid=self.grid, model_to_bed=model_to_bed)
        total_ink = sum(line.count('1') for line in lines)
        self.assertGreater(total_ink, 0)

    def test_out_of_bounds_trace_produces_no_ink(self):
        trace = Trace(
            net_id="offscreen",
            path=[(-1000.0, -1000.0), (-900.0, -1000.0)],
        )
        result = RoutingResult(traces=[trace], pin_assignments={}, failed_nets=[])
        lines = generate_trace_bitmap(result, 0.5, grid=self.grid)
        total_ink = sum(line.count('1') for line in lines)
        self.assertEqual(total_ink, 0)

    def test_horizontal_trace_width(self):
        trace_w = 0.5
        trace = Trace(
            net_id="hline",
            path=[(5.0, 10.0), (15.0, 10.0)],
        )
        result = RoutingResult(traces=[trace], pin_assignments={}, failed_nets=[])
        model_to_bed = (100.0, 100.0)
        lines = generate_trace_bitmap(result, trace_w, grid=self.grid, model_to_bed=model_to_bed)

        inked_rows = [i for i, line in enumerate(lines) if '1' in line]
        self.assertGreater(len(inked_rows), 0)
        expected_rows = max(1, round(trace_w / self.grid.pixel_size_mm))
        self.assertAlmostEqual(len(inked_rows), expected_rows, delta=2)


if __name__ == "__main__":
    unittest.main()

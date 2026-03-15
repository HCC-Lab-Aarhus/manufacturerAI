"""Tests for the sweep-grid geometry, bitmap generation, and calibration bitmap.

Covers:
  - PrintheadConfig derived properties
  - sweep_grid() dimensions for every printer definition
  - SweepGrid.bed_to_bitmap() coordinate transform
  - get_printer() fallback behaviour
  - generate_trace_bitmap() rasterization correctness
  - _calibration_bitmap() structure and reference hash
"""

from __future__ import annotations

import hashlib
import math
import unittest

from src.pipeline.config import (
    PRINTHEAD,
    PrintheadConfig,
    PrinterDef,
    PRINTERS,
    SweepGrid,
    sweep_grid,
    get_printer,
    DEFAULT_PRINTER,
    _PADDING_STRIPS,
)
from src.pipeline.router.models import Trace, RoutingResult
from src.pipeline.router.bitmap import generate_trace_bitmap
from src.web.routes.debug import _calibration_bitmap


# ── PrintheadConfig ────────────────────────────────────────────────

class TestPrintheadConfig(unittest.TestCase):

    def test_defaults(self):
        ph = PRINTHEAD
        self.assertEqual(ph.nozzle_count, 128)
        self.assertAlmostEqual(ph.nozzle_pitch_mm, 0.1371)
        self.assertEqual(ph.lane_step_nozzles, 32)

    def test_printhead_width(self):
        self.assertAlmostEqual(PRINTHEAD.printhead_width_mm, 128 * 0.1371, places=6)

    def test_lane_width(self):
        self.assertAlmostEqual(PRINTHEAD.lane_width_mm, 32 * 0.1371, places=6)

    def test_pixel_size_equals_nozzle_pitch(self):
        self.assertEqual(PRINTHEAD.pixel_size_mm, PRINTHEAD.nozzle_pitch_mm)


# ── sweep_grid() dimensions ───────────────────────────────────────

class TestSweepGridDimensions(unittest.TestCase):
    """Verify sweep_grid() produces correct dimensions for every printer."""

    def _expected(self, pdef: PrinterDef, ph: PrintheadConfig = PRINTHEAD):
        x_start = abs(pdef.inkjet_offset_x)
        x_end = pdef.nominal_bed_width
        increment = ph.lane_width_mm
        pixel = ph.pixel_size_mm
        step = ph.lane_step_nozzles

        num_lanes = 1 + int((x_end - x_start + 1e-9) / increment)
        data_cols = (num_lanes - _PADDING_STRIPS) * step
        data_rows = math.ceil((pdef.nominal_bed_depth - abs(pdef.inkjet_offset_y)) / pixel)
        data_x_start = x_start + _PADDING_STRIPS * step * pixel
        return num_lanes, data_cols, data_rows, data_x_start

    def test_mk3s_dimensions(self):
        pdef = PRINTERS["mk3s"]
        grid = sweep_grid(pdef)
        _, cols, rows, _ = self._expected(pdef)
        self.assertEqual(grid.data_cols, 1312)
        self.assertEqual(grid.data_rows, 1299)
        self.assertEqual(grid.data_cols, cols)
        self.assertEqual(grid.data_rows, rows)

    def test_mk3s_plus_dimensions(self):
        pdef = PRINTERS["mk3s_plus"]
        grid = sweep_grid(pdef)
        self.assertEqual(grid.data_cols, 1312)
        self.assertEqual(grid.data_rows, 1299)

    def test_coreone_dimensions(self):
        pdef = PRINTERS["coreone"]
        grid = sweep_grid(pdef)
        self.assertEqual(grid.data_cols, 1312)
        self.assertEqual(grid.data_rows, 1591)

    def test_coreone_deeper_than_mk3s(self):
        mk3s = sweep_grid(PRINTERS["mk3s"])
        core = sweep_grid(PRINTERS["coreone"])
        self.assertEqual(mk3s.data_cols, core.data_cols)
        self.assertGreater(core.data_rows, mk3s.data_rows)

    def test_pixel_size_matches_printhead(self):
        for pid, pdef in PRINTERS.items():
            with self.subTest(printer=pid):
                grid = sweep_grid(pdef)
                self.assertAlmostEqual(grid.pixel_size_mm, PRINTHEAD.pixel_size_mm)

    def test_lane_count_is_44(self):
        """All current printers have 250mm bed width → 44 lanes."""
        for pid, pdef in PRINTERS.items():
            with self.subTest(printer=pid):
                num_lanes, _, _, _ = self._expected(pdef)
                self.assertEqual(num_lanes, 44)


# ── SweepGrid.bed_to_bitmap() ─────────────────────────────────────

class TestBedToBitmap(unittest.TestCase):

    def setUp(self):
        self.pdef = PRINTERS["mk3s_plus"]
        self.grid = sweep_grid(self.pdef)

    def test_transform_known_point(self):
        bx, by = self.grid.bed_to_bitmap(100.0, 100.0)
        expected_bx = 100.0 - 70.7616 - (-57.6) + (-1.8)  # 85.0384
        expected_by = 100.0 - 32.0 - (-32.0) + 2.7         # 102.7
        self.assertAlmostEqual(bx, expected_bx, places=3)
        self.assertAlmostEqual(by, expected_by, places=3)

    def test_origin_maps_correctly(self):
        """Bed origin (0,0): X should be negative (bitmap starts at ~70mm),
        Y should be near zero (bitmap starts at Y=32mm, but calibration +2.7mm
        pushes it slightly positive)."""
        bx, by = self.grid.bed_to_bitmap(0.0, 0.0)
        self.assertLess(bx, 0)
        self.assertAlmostEqual(by, 2.7, places=3)

    def test_transform_is_pure_translation(self):
        """bed_to_bitmap is a linear offset — check with two points."""
        bx1, by1 = self.grid.bed_to_bitmap(50.0, 50.0)
        bx2, by2 = self.grid.bed_to_bitmap(60.0, 70.0)
        self.assertAlmostEqual(bx2 - bx1, 10.0, places=6)
        self.assertAlmostEqual(by2 - by1, 20.0, places=6)

    def test_all_printers_same_offset(self):
        """All current printers share the same inkjet/calibration offsets,
        so the X transform offset should be identical."""
        grids = {pid: sweep_grid(pdef) for pid, pdef in PRINTERS.items()}
        ref_bx, _ = grids["mk3s"].bed_to_bitmap(100.0, 100.0)
        for pid, g in grids.items():
            bx, _ = g.bed_to_bitmap(100.0, 100.0)
            self.assertAlmostEqual(bx, ref_bx, places=6, msg=pid)


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


# ── generate_trace_bitmap() ───────────────────────────────────────

class TestGenerateTraceBitmap(unittest.TestCase):

    def setUp(self):
        self.pdef = PRINTERS["coreone"]
        self.grid = sweep_grid(self.pdef)

    def test_empty_routing_produces_blank_bitmap(self):
        result = RoutingResult(traces=[], pin_assignments={}, failed_nets=[])
        lines = generate_trace_bitmap(result, 0.5, grid=self.grid)
        self.assertEqual(len(lines), self.grid.data_rows)
        self.assertTrue(all(len(l) == self.grid.data_cols for l in lines))
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
        """A horizontal trace centred on the bed should produce at least some '1's."""
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
        """A trace far outside the bed should clip to zero ink."""
        trace = Trace(
            net_id="offscreen",
            path=[(-1000.0, -1000.0), (-900.0, -1000.0)],
        )
        result = RoutingResult(traces=[trace], pin_assignments={}, failed_nets=[])
        lines = generate_trace_bitmap(result, 0.5, grid=self.grid)
        total_ink = sum(line.count('1') for line in lines)
        self.assertEqual(total_ink, 0)

    def test_horizontal_trace_width(self):
        """A horizontal trace should span roughly trace_width / pixel_size rows."""
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


# ── _calibration_bitmap() ─────────────────────────────────────────

class TestCalibrationBitmap(unittest.TestCase):

    def setUp(self):
        self.pdef = PRINTERS["coreone"]
        self.grid = sweep_grid(self.pdef)
        self.bitmap = _calibration_bitmap(self.pdef, self.grid, 100, 5, 5)
        self.lines = self.bitmap.split('\n')

    def test_row_count(self):
        self.assertEqual(len(self.lines), self.grid.data_rows)

    def test_col_count(self):
        for i, line in enumerate(self.lines):
            self.assertEqual(len(line), self.grid.data_cols, f"row {i}")

    def test_only_binary_chars(self):
        chars = set(self.bitmap.replace('\n', ''))
        self.assertTrue(chars.issubset({'0', '1'}))

    def test_has_ink(self):
        total_ink = self.bitmap.count('1')
        self.assertGreater(total_ink, 0)

    def test_three_squares_present(self):
        """Three corners should have ink; top-right is intentionally blank.
        Check that ink exists in lower-left, lower-right and upper-left
        quadrants of the bitmap but not exclusively in one quadrant."""
        rows = self.lines
        mid_r = len(rows) // 2
        mid_c = self.grid.data_cols // 2

        def quadrant_ink(r_start, r_end, c_start, c_end):
            return sum(
                rows[r][c] == '1'
                for r in range(r_start, r_end)
                for c in range(c_start, c_end)
            )

        q_tl = quadrant_ink(0, mid_r, 0, mid_c)
        q_tr = quadrant_ink(0, mid_r, mid_c, self.grid.data_cols)
        q_bl = quadrant_ink(mid_r, len(rows), 0, mid_c)
        q_br = quadrant_ink(mid_r, len(rows), mid_c, self.grid.data_cols)

        nonzero = [q for q in [q_tl, q_tr, q_bl, q_br] if q > 0]
        self.assertGreaterEqual(len(nonzero), 3, "Expected ink in at least 3 quadrants")

    def test_reference_hash_y32_to_210(self):
        """The Y=32–210mm region of the coreone calibration bitmap must match
        the reference hash from before the refactor."""
        region = '\n'.join(self.lines[292:1591])
        h = hashlib.sha256(region.encode()).hexdigest()
        expected = '591f7d7664c61fc84eb761a19e81cb11b144c798108bba115384846df4f18a57'
        self.assertEqual(h, expected, "Calibration bitmap regression: hash mismatch")

    def test_mk3s_calibration_dimensions(self):
        pdef = PRINTERS["mk3s"]
        grid = sweep_grid(pdef)
        bitmap = _calibration_bitmap(pdef, grid, 100, 5, 5)
        lines = bitmap.split('\n')
        self.assertEqual(len(lines), 1299)
        self.assertEqual(len(lines[0]), 1312)


if __name__ == "__main__":
    unittest.main()

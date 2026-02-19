"""
Tests for the component placer.

Verifies that:
- Components are placed correctly on boards with enough room.
- PlacementError is raised (not a jank fallback) when the board is too small.
- Edge cases between "just fits" and "just doesn't" are handled cleanly.

Run:  python -m pytest tests/test_placer.py -v
"""

from __future__ import annotations

import pytest

from src.pcb.placer import place_components, PlacementError
from src.config.hardware import hw


# ── Helpers ────────────────────────────────────────────────────────


def _rect_outline(w: float, h: float) -> list[list[float]]:
    """Simple rectangular outline at the origin."""
    return [[0, 0], [w, 0], [w, h], [0, h]]


def _centered_buttons(
    board_w: float,
    board_h: float,
    count: int,
    spacing_y: float = 20.0,
) -> list[dict]:
    """Place *count* buttons vertically centered in the board."""
    cx = board_w / 2
    total = (count - 1) * spacing_y
    start_y = (board_h - total) / 2
    return [
        {"id": f"SW{i+1}", "label": f"btn{i+1}", "x": cx, "y": start_y + i * spacing_y}
        for i in range(count)
    ]


def _component_ids(layout: dict) -> set[str]:
    return {c["id"] for c in layout["components"]}


def _component_by_id(layout: dict, cid: str) -> dict:
    return next(c for c in layout["components"] if c["id"] == cid)


# ── 1. Comfortable board — everything fits easily ─────────────────


class TestComfortablePlacement:
    """Board is generously sized; all components must place without error."""

    def test_wide_board_3_buttons(self):
        """70×200 board with 3 buttons — plenty of room."""
        outline = _rect_outline(70, 200)
        buttons = _centered_buttons(70, 200, count=3)
        layout = place_components(outline, buttons)

        placed = _component_ids(layout)
        assert "BAT1" in placed
        assert "U1" in placed
        assert "D1" in placed
        assert "SW1" in placed
        assert "SW2" in placed
        assert "SW3" in placed

    def test_no_overlapping_centers(self):
        """No two components share the same center."""
        outline = _rect_outline(70, 200)
        buttons = _centered_buttons(70, 200, count=3)
        layout = place_components(outline, buttons)

        centers = [tuple(c["center"]) for c in layout["components"]]
        assert len(centers) == len(set(centers)), "Duplicate component centers!"

    def test_all_centers_inside_board(self):
        """Every component center must be inside the board polygon."""
        outline = _rect_outline(70, 200)
        buttons = _centered_buttons(70, 200, count=3)
        layout = place_components(outline, buttons)

        board_poly = layout["board"]["outline_polygon"]
        from src.geometry.polygon import point_in_polygon, ensure_ccw
        ccw = ensure_ccw(board_poly)

        for comp in layout["components"]:
            cx, cy = comp["center"]
            assert point_in_polygon(cx, cy, ccw), (
                f"{comp['id']} at ({cx:.1f}, {cy:.1f}) is outside the board"
            )

    def test_battery_controller_not_on_buttons(self):
        """Battery and controller must not overlap any button keepout."""
        outline = _rect_outline(70, 200)
        buttons = _centered_buttons(70, 200, count=4, spacing_y=25)
        layout = place_components(outline, buttons)

        bat = _component_by_id(layout, "BAT1")
        ctrl = _component_by_id(layout, "U1")

        for comp in [bat, ctrl]:
            cx, cy = comp["center"]
            for btn in layout["components"]:
                if btn["type"] != "button":
                    continue
                bx, by = btn["center"]
                # Min clearance: button pin extent + margin
                dist = ((cx - bx) ** 2 + (cy - by) ** 2) ** 0.5
                assert dist > 5.0, (
                    f"{comp['id']} is only {dist:.1f}mm from {btn['id']}"
                )


# ── 2. Impossible boards — must raise PlacementError ──────────────


class TestImpossiblePlacement:
    """Board is way too small; PlacementError must be raised."""

    def test_tiny_board_battery_fails(self):
        """
        A 15×15 board can't fit even the battery compartment (25×48 mm).
        """
        outline = _rect_outline(15, 15)
        buttons: list[dict] = []
        with pytest.raises(PlacementError, match="battery"):
            place_components(outline, buttons)

    def test_tiny_board_no_buttons_fails(self):
        """
        A 30×55 board: battery compartment (25×48) barely fits,
        but no room for the controller (10×36).
        """
        outline = _rect_outline(30, 55)
        buttons: list[dict] = []
        with pytest.raises(PlacementError):
            place_components(outline, buttons)

    def test_narrow_board_buttons_block_everything(self):
        """
        30×200 board with 5 buttons down the centre.
        The center column of buttons leaves no room beside them
        for the 25mm-wide battery compartment + 10mm controller.
        """
        outline = _rect_outline(30, 200)
        buttons = _centered_buttons(30, 200, count=5, spacing_y=30)
        with pytest.raises(PlacementError):
            place_components(outline, buttons)

    def test_placement_error_has_useful_fields(self):
        """The raised error contains component, dimensions, suggestion."""
        outline = _rect_outline(15, 15)
        with pytest.raises(PlacementError) as exc_info:
            place_components(outline, [])
        err = exc_info.value
        assert err.component in ("battery", "controller")
        assert "width_mm" in err.dimensions
        assert "height_mm" in err.dimensions
        assert len(err.suggestion) > 10

    def test_placement_error_to_dict(self):
        """to_dict() produces a serialisable summary."""
        outline = _rect_outline(15, 15)
        with pytest.raises(PlacementError) as exc_info:
            place_components(outline, [])
        d = exc_info.value.to_dict()
        assert "component" in d
        assert "dimensions" in d
        assert "suggestion" in d
        assert isinstance(d["occupied_count"], int)


# ── 3. Near-impossible — just barely too small ────────────────────


class TestNearImpossiblePlacement:
    """
    Boards that are just a few mm too small.
    Must raise PlacementError (not silently overlap).
    """

    def test_board_1mm_too_narrow_for_battery(self):
        """
        The battery compartment is 25mm wide; with 2mm wall clearance on
        each side, 29mm is the absolute minimum board width.  At 28mm
        (1mm less) the battery cannot physically fit.
        """
        outline = _rect_outline(28, 200)
        buttons: list[dict] = []
        with pytest.raises(PlacementError, match="battery"):
            place_components(outline, buttons)

    def test_board_1mm_too_short_for_battery(self):
        """
        The battery compartment is 48mm tall.  A 40×51 board has only ~47mm
        usable height after wall inset — 1mm short.
        """
        outline = _rect_outline(40, 51)
        buttons: list[dict] = []
        with pytest.raises(PlacementError, match="battery"):
            place_components(outline, buttons)

    def test_just_at_boundary_width_succeeds(self):
        """38mm-wide board fits the battery compartment (25mm + routing clearance + margins)."""
        outline = _rect_outline(38, 200)
        buttons: list[dict] = []
        layout = place_components(outline, buttons)
        assert "BAT1" in _component_ids(layout)

    def test_just_at_boundary_height_succeeds(self):
        """40×105 board fits battery compartment (48mm) + controller (36mm)."""
        outline = _rect_outline(40, 105)
        buttons: list[dict] = []
        layout = place_components(outline, buttons)
        assert "BAT1" in _component_ids(layout)


# ── 4. Barely fits — should succeed, no error ─────────────────────


class TestBarelyFitsPlacement:
    """
    Boards that are just large enough.  These must NOT raise PlacementError.
    """

    def test_wide_enough_for_side_by_side(self):
        """
        With a 65mm-wide board and buttons centered, the battery and
        controller can sit to the left of the buttons.
        """
        outline = _rect_outline(65, 200)
        buttons = _centered_buttons(65, 200, count=3, spacing_y=25)
        layout = place_components(outline, buttons)
        assert "BAT1" in _component_ids(layout)
        assert "U1" in _component_ids(layout)

    def test_long_board_stacks_vertically(self):
        """
        Buttons clustered at the top of a long, narrow board;
        battery and controller fit below them.
        """
        outline = _rect_outline(50, 250)
        buttons = [
            {"id": "SW1", "label": "A", "x": 25, "y": 200},
            {"id": "SW2", "label": "B", "x": 25, "y": 220},
        ]
        layout = place_components(outline, buttons)
        placed = _component_ids(layout)
        assert "BAT1" in placed
        assert "U1" in placed

        # Battery and controller should be below the buttons
        bat = _component_by_id(layout, "BAT1")
        ctrl = _component_by_id(layout, "U1")
        assert bat["center"][1] < 180, "Battery should be below the buttons"

    def test_5_buttons_wide_board(self):
        """5 buttons on a 70×200 board — should work."""
        outline = _rect_outline(70, 200)
        buttons = _centered_buttons(70, 200, count=5, spacing_y=20)
        layout = place_components(outline, buttons)
        assert len(layout["components"]) == 5 + 3  # 5 buttons + bat + ctrl + diode


# ── 5. Regression: old fallback bug ───────────────────────────────


class TestNoFallbackRegression:
    """
    The old code fell back to placing the controller at the board center
    when the grid scan failed, causing it to land on top of a button.
    Verify this can never happen again.
    """

    def test_controller_never_on_button(self):
        """
        80mm board with centered buttons — the exact scenario that
        used to produce an overlap.
        """
        outline = _rect_outline(80, 200)
        buttons = [
            {"id": "btn_1", "label": "P", "x": 40, "y": 80},
            {"id": "btn_2", "label": "V", "x": 40, "y": 120},
            {"id": "btn_3", "label": "D", "x": 40, "y": 160},
        ]
        layout = place_components(outline, buttons)

        ctrl = _component_by_id(layout, "U1")
        for btn in layout["components"]:
            if btn["type"] != "button":
                continue
            assert tuple(ctrl["center"]) != tuple(btn["center"]), (
                f"Controller placed on top of {btn['id']}!"
            )


# ── 6. Concave / irregular outlines ─────────────────────────────


class TestConcaveOutlines:
    """
    Irregular/non-rectangular outlines must not allow components to
    extend through concavities or overlap each other's SCAD pockets.
    """

    @staticmethod
    def _no_scad_overlap(layout: dict, skip_buttons: bool = True) -> None:
        """Assert no pair of placed components has overlapping SCAD pockets.

        When *skip_buttons* is True, any pair involving a button is
        skipped—button positions are user-defined and not under the
        placer's control.
        """
        from src.pcb.placer import _cutout_rect, _rects_overlap
        margin = hw.component_margin
        comps = layout["components"]
        for i in range(len(comps)):
            ri = _cutout_rect(comps[i], margin)
            for j in range(i + 1, len(comps)):
                if skip_buttons and (
                    comps[i]["type"] == "button" or comps[j]["type"] == "button"
                ):
                    continue
                rj = _cutout_rect(comps[j], margin)
                assert not _rects_overlap(ri, rj), (
                    f"SCAD pockets overlap: {comps[i]['id']} vs {comps[j]['id']}"
                )

    def test_hourglass_no_overlap(self):
        """Hourglass shape with narrow waist — components must not
        extend through the concavity.
        """
        outline = [
            [0, 0], [60, 0], [60, 40], [40, 65], [60, 90],
            [60, 130], [0, 130], [0, 90], [20, 65], [0, 40],
        ]
        buttons = [
            {"id": "btn_1", "label": "A", "x": 30, "y": 100},
            {"id": "btn_2", "label": "B", "x": 30, "y": 115},
        ]
        layout = place_components(outline, buttons)
        bat = _component_by_id(layout, "BAT1")
        ctrl = _component_by_id(layout, "U1")

        # Both must be inside the board polygon
        from src.geometry.polygon import point_in_polygon, ensure_ccw
        ccw = ensure_ccw(layout["board"]["outline_polygon"])
        for comp in [bat, ctrl]:
            cx, cy = comp["center"]
            assert point_in_polygon(cx, cy, ccw), (
                f"{comp['id']} center outside polygon"
            )

        self._no_scad_overlap(layout)

    def test_egg_battery_fits(self):
        """Egg/teardrop shape — wide bottom, narrow top."""
        outline = [
            [5, 0], [55, 0], [65, 30], [65, 90],
            [55, 130], [30, 150], [5, 130], [-5, 90], [-5, 30],
        ]
        buttons = [
            {"id": "btn_1", "label": "A", "x": 30, "y": 95},
            {"id": "btn_2", "label": "B", "x": 30, "y": 115},
        ]
        layout = place_components(outline, buttons)
        self._no_scad_overlap(layout)

    def test_capsule_tall_no_overlap(self):
        """Capsule/pill shape."""
        outline = [
            [10, 0], [40, 0], [50, 15], [50, 125], [40, 140],
            [10, 140], [0, 125], [0, 15],
        ]
        buttons = [
            {"id": "btn_1", "label": "A", "x": 25, "y": 80},
            {"id": "btn_2", "label": "B", "x": 25, "y": 100},
        ]
        layout = place_components(outline, buttons)
        self._no_scad_overlap(layout)

    def test_wide_rounded_no_overlap(self):
        """Wide rounded rectangle with tapered ends."""
        outline = [
            [5, 0], [65, 0], [70, 10], [70, 100], [65, 115],
            [50, 120], [20, 120], [5, 115], [0, 100], [0, 10],
        ]
        buttons = [
            {"id": "btn_1", "label": "A", "x": 35, "y": 70},
            {"id": "btn_2", "label": "B", "x": 35, "y": 90},
        ]
        layout = place_components(outline, buttons)
        self._no_scad_overlap(layout)

    def test_peanut_refuses_if_no_fit(self):
        """Peanut shape (two lobes + narrow neck) — battery must not
        span across the neck.
        """
        outline = [
            [10, 0], [50, 0], [55, 15], [50, 35], [42, 50],
            [50, 65], [55, 85], [50, 110], [10, 110],
            [5, 85], [10, 65], [18, 50], [10, 35], [5, 15],
        ]
        buttons = [
            {"id": "btn_1", "label": "A", "x": 30, "y": 80},
            {"id": "btn_2", "label": "B", "x": 30, "y": 95},
        ]
        # Either succeeds without overlap, or raises PlacementError
        try:
            layout = place_components(outline, buttons)
            self._no_scad_overlap(layout)
        except PlacementError:
            pass  # Expected — battery can't fit in either lobe

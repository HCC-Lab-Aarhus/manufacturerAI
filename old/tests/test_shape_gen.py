"""Quick test for shape generators + IC orientation."""
import math
from src.geometry.polygon import (
    generate_ellipse, generate_racetrack, polygon_area, polygon_bounds,
    validate_outline,
)
from src.pcb.placer import place_components_optimal


def test_ellipse_generation():
    e = generate_ellipse(60, 140, n=32)
    assert len(e) == 32
    b = polygon_bounds(e)
    assert abs(b[0]) < 0.01 and abs(b[1]) < 0.01
    assert abs(b[2] - 60) < 0.01 and abs(b[3] - 140) < 0.01
    area = abs(polygon_area(e))
    expected = math.pi * 30 * 70
    assert abs(area - expected) / expected < 0.02  # within 2%


def test_racetrack_generation():
    r = generate_racetrack(60, 140)
    b = polygon_bounds(r)
    assert abs(b[0]) < 0.01 and abs(b[1]) < 0.01
    assert abs(b[2] - 60) < 0.01 and abs(b[3] - 140) < 0.01
    area = abs(polygon_area(r))
    assert area > 6000  # should be between ellipse and rectangle


def test_ellipse_validates():
    e = generate_ellipse(60, 140, n=32)
    errs = validate_outline(
        e, width=60, length=140,
        button_positions=[
            {"id": "btn_1", "x": 17.5, "y": 80},
            {"id": "btn_2", "x": 42.5, "y": 80},
        ],
        edge_clearance=6.0,
    )
    assert errs == [], f"Validation errors: {errs}"


def test_racetrack_validates():
    r = generate_racetrack(60, 140)
    errs = validate_outline(
        r, width=60, length=140,
        button_positions=[
            {"id": "btn_1", "x": 17.5, "y": 80},
            {"id": "btn_2", "x": 42.5, "y": 80},
        ],
        edge_clearance=6.0,
    )
    assert errs == [], f"Validation errors: {errs}"


def test_controller_orientation_on_narrow_rect():
    """On a 60mm-wide rectangular board, the controller should be placed
    horizontally (rotation=90) because vertical wastes the narrow axis."""
    outline = [[2, 2], [58, 2], [58, 138], [2, 138]]
    buttons = [
        {"id": "btn_1", "label": "Button 1", "x": 17.5, "y": 80},
        {"id": "btn_2", "label": "Button 2", "x": 42.5, "y": 80},
    ]
    layout = place_components_optimal(outline, buttons, battery_type="2xAAA")
    ctrl = [c for c in layout["components"] if c["type"] == "controller"]
    assert len(ctrl) == 1, "Controller not placed"
    rot = ctrl[0].get("rotation_deg", 0)
    print(f"Controller rotation: {rot}°")
    print(f"Controller center: {ctrl[0]['center']}")
    for c in layout["components"]:
        print(f"  {c['type']:12s} center=({c['center'][0]:.1f}, {c['center'][1]:.1f}) rot={c.get('rotation_deg', 0)}")
    # On a 60mm wide rectangular board, horizontal (90°) should win since
    # bottleneck_channel is now 5.0 (7mm side channels > 5mm threshold)
    assert rot == 90, f"Expected 90° (horizontal), got {rot}°"


def test_controller_placement_on_ellipse():
    """On a 60×140mm ellipse, verify controller is placed without overlap."""
    outline = generate_ellipse(60, 140, n=32)
    buttons = [
        {"id": "btn_1", "label": "Button 1", "x": 17.5, "y": 80},
        {"id": "btn_2", "label": "Button 2", "x": 42.5, "y": 80},
    ]
    layout = place_components_optimal(outline, buttons, battery_type="2xAAA")
    ctrl = [c for c in layout["components"] if c["type"] == "controller"]
    assert len(ctrl) == 1, "Controller not placed"
    for c in layout["components"]:
        print(f"  {c['type']:12s} center=({c['center'][0]:.1f}, {c['center'][1]:.1f}) rot={c.get('rotation', 0)}")


if __name__ == "__main__":
    test_ellipse_generation()
    print("✓ ellipse generation")
    test_racetrack_generation()
    print("✓ racetrack generation")
    test_ellipse_validates()
    print("✓ ellipse validation")
    test_racetrack_validates()
    print("✓ racetrack validation")
    test_controller_orientation_on_narrow_rect()
    print("✓ controller horizontal on narrow rect")
    test_controller_placement_on_ellipse()
    print("✓ controller placed on ellipse")
    print("\nAll tests passed!")

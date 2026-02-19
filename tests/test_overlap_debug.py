"""Debug script to find battery-controller overlap scenarios."""
from src.pcb.placer import place_components
from src.config.hardware import hw


def check_overlap(name, outline, buttons):
    try:
        layout = place_components(outline, buttons)
        bat = next(c for c in layout["components"] if c["type"] == "battery")
        ctrl = next(c for c in layout["components"] if c["type"] == "controller")

        bx, by = bat["center"]
        cx, cy = ctrl["center"]
        bat_w, bat_h = bat["body_width_mm"], bat["body_height_mm"]

        ctrl_cfg_w, ctrl_cfg_h = 10, 36
        rot = ctrl["rotation_deg"]
        if rot == 90:
            ctrl_w, ctrl_h = ctrl_cfg_h, ctrl_cfg_w
        else:
            ctrl_w, ctrl_h = ctrl_cfg_w, ctrl_cfg_h

        # Physical overlap (no padding)
        x_ovl = min(bx + bat_w/2, cx + ctrl_w/2) - max(bx - bat_w/2, cx - ctrl_w/2)
        y_ovl = min(by + bat_h/2, cy + ctrl_h/2) - max(by - bat_h/2, cy - ctrl_h/2)

        overlaps = x_ovl > 0 and y_ovl > 0
        if cy > by:
            gap_y = (cy - ctrl_h/2) - (by + bat_h/2)
        else:
            gap_y = (by - bat_h/2) - (cy + ctrl_h/2)

        status = "OVERLAP!" if overlaps else "ok"
        print(f"{name:20s} bat=({bx:.1f},{by:.1f}) ctrl=({cx:.1f},{cy:.1f}) rot={rot} gap_y={gap_y:.1f}mm {status}")
        if overlaps:
            print(f"  -> x_ovl={x_ovl:.1f} y_ovl={y_ovl:.1f}")
    except Exception as e:
        print(f"{name:20s} ERROR: {e}")


# Test cases with various outline shapes
cases = [
    # ── Irregular / non-rectangular outlines ──────────────────────
    # These are the shapes an LLM might generate for a remote control

    # Classic TV remote — rounded rectangle with tapered top
    ("remote_tapered", [
        [10, 0], [50, 0], [55, 10], [55, 110], [50, 130], [40, 140],
        [20, 140], [10, 130], [5, 110], [5, 10],
    ], [
        {"id": "btn_1", "label": "P+", "x": 30, "y": 85},
        {"id": "btn_2", "label": "P-", "x": 30, "y": 100},
    ]),

    # Egg / teardrop — wide at bottom, narrow at top
    ("egg_shape", [
        [15, 0], [45, 0], [55, 30], [55, 70], [45, 100],
        [30, 115], [15, 100], [5, 70], [5, 30],
    ], [
        {"id": "btn_1", "label": "A", "x": 30, "y": 60},
        {"id": "btn_2", "label": "B", "x": 30, "y": 80},
    ]),

    # Capsule / pill shape
    ("capsule_tall", [
        [10, 0], [40, 0], [50, 15], [50, 125], [40, 140],
        [10, 140], [0, 125], [0, 15],
    ], [
        {"id": "btn_1", "label": "A", "x": 25, "y": 80},
        {"id": "btn_2", "label": "B", "x": 25, "y": 100},
    ]),

    # Hourglass — narrows in the middle, wide at top and bottom
    ("hourglass", [
        [0, 0], [60, 0], [60, 40], [40, 65], [60, 90],
        [60, 130], [0, 130], [0, 90], [20, 65], [0, 40],
    ], [
        {"id": "btn_1", "label": "A", "x": 30, "y": 100},
        {"id": "btn_2", "label": "B", "x": 30, "y": 115},
    ]),

    # Guitar pick shape — wide bottom, pointed top
    ("guitar_pick", [
        [5, 0], [55, 0], [60, 20], [55, 60], [45, 95],
        [30, 120], [15, 95], [5, 60], [0, 20],
    ], [
        {"id": "btn_1", "label": "A", "x": 30, "y": 45},
        {"id": "btn_2", "label": "B", "x": 30, "y": 65},
    ]),

    # Organic blob — asymmetric
    ("organic_blob", [
        [5, 0], [50, 0], [55, 15], [60, 50], [55, 90],
        [45, 120], [25, 130], [10, 115], [0, 80], [0, 30],
    ], [
        {"id": "btn_1", "label": "A", "x": 30, "y": 75},
        {"id": "btn_2", "label": "B", "x": 30, "y": 95},
    ]),

    # Wide rounded — like an Apple TV remote but wider
    ("wide_rounded", [
        [5, 0], [65, 0], [70, 10], [70, 100], [65, 115],
        [50, 120], [20, 120], [5, 115], [0, 100], [0, 10],
    ], [
        {"id": "btn_1", "label": "A", "x": 25, "y": 70},
        {"id": "btn_2", "label": "B", "x": 45, "y": 70},
        {"id": "btn_3", "label": "C", "x": 35, "y": 85},
    ]),

    # Triangle-ish — narrows sharply at top
    ("triangle_ish", [
        [0, 0], [60, 0], [55, 40], [45, 80], [35, 110],
        [25, 110], [15, 80], [5, 40],
    ], [
        {"id": "btn_1", "label": "A", "x": 30, "y": 45},
        {"id": "btn_2", "label": "B", "x": 30, "y": 65},
    ]),

    # Narrow capsule — barely fits battery width-wise
    ("narrow_capsule", [
        [5, 0], [33, 0], [38, 15], [38, 125], [33, 140],
        [5, 140], [0, 125], [0, 15],
    ], [
        {"id": "btn_1", "label": "A", "x": 19, "y": 85},
        {"id": "btn_2", "label": "B", "x": 19, "y": 105},
    ]),

    # D-pad style — wide with 4 buttons in a cross
    ("dpad_remote", [
        [5, 0], [65, 0], [70, 15], [70, 105], [65, 120],
        [5, 120], [0, 105], [0, 15],
    ], [
        {"id": "btn_1", "label": "Up", "x": 35, "y": 80},
        {"id": "btn_2", "label": "Down", "x": 35, "y": 60},
        {"id": "btn_3", "label": "Left", "x": 20, "y": 70},
        {"id": "btn_4", "label": "Right", "x": 50, "y": 70},
    ]),

    # Peanut shape — two lobes connected by narrow neck
    ("peanut", [
        [10, 0], [50, 0], [55, 15], [50, 35], [42, 50],
        [50, 65], [55, 85], [50, 110], [10, 110],
        [5, 85], [10, 65], [18, 50], [10, 35], [5, 15],
    ], [
        {"id": "btn_1", "label": "A", "x": 30, "y": 80},
        {"id": "btn_2", "label": "B", "x": 30, "y": 95},
    ]),

    # Hexagon
    ("hexagon", [
        [15, 0], [45, 0], [60, 30], [60, 90], [45, 120],
        [15, 120], [0, 90], [0, 30],
    ], [
        {"id": "btn_1", "label": "A", "x": 30, "y": 65},
        {"id": "btn_2", "label": "B", "x": 30, "y": 85},
    ]),
]

for name, outline, buttons in cases:
    check_overlap(name, outline, buttons)

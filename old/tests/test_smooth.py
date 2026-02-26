"""Test smooth_polygon behavior on various shapes."""
import math
from src.geometry.polygon import smooth_polygon, _interior_angle, ensure_ccw


def test_shapes():
    # 1. Coarse octagon (8-vertex 'circle') — should be smoothed
    n = 8
    radius = 30
    octagon = [
        [radius * math.cos(2 * math.pi * i / n),
         radius * math.sin(2 * math.pi * i / n)]
        for i in range(n)
    ]
    smoothed = smooth_polygon(octagon)
    print(f"Octagon:     {len(octagon):3d} verts -> {len(smoothed):3d} verts  "
          f"{'SMOOTHED' if len(smoothed) > len(octagon) else 'KEPT'}")

    # 2. Rectangle (4 sharp corners) — should NOT be smoothed
    rect = [[0, 0], [60, 0], [60, 140], [0, 140]]
    smoothed_rect = smooth_polygon(rect)
    print(f"Rectangle:   {len(rect):3d} verts -> {len(smoothed_rect):3d} verts  "
          f"{'SMOOTHED' if len(smoothed_rect) > len(rect) else 'KEPT'}")

    # 3. 12-vertex oval — should be smoothed
    n2 = 12
    oval = [
        [25 * math.cos(2 * math.pi * i / n2),
         60 * math.sin(2 * math.pi * i / n2)]
        for i in range(n2)
    ]
    smoothed_oval = smooth_polygon(oval)
    print(f"Oval(12):    {len(oval):3d} verts -> {len(smoothed_oval):3d} verts  "
          f"{'SMOOTHED' if len(smoothed_oval) > len(oval) else 'KEPT'}")

    # 4. Diamond — should NOT be smoothed (sharp 90° corners)
    diamond = [[30, 0], [60, 75], [30, 150], [0, 75]]
    smoothed_d = smooth_polygon(diamond)
    print(f"Diamond:     {len(diamond):3d} verts -> {len(smoothed_d):3d} verts  "
          f"{'SMOOTHED' if len(smoothed_d) > len(diamond) else 'KEPT'}")

    # 5. Rounded rectangle (8 verts, mixed sharp + gentle corners)
    rounded_rect = [[5, 0], [55, 0], [60, 10], [60, 140],
                    [55, 150], [5, 150], [0, 140], [0, 10]]
    ccw = ensure_ccw(rounded_rect)
    angles = []
    for i in range(len(ccw)):
        a = ccw[(i - 1) % len(ccw)]
        b = ccw[i]
        c = ccw[(i + 1) % len(ccw)]
        angles.append(_interior_angle(a, b, c))
    smooth_count = sum(1 for a in angles if a >= 160)
    print(f"RoundedRect: angles = [{', '.join(f'{a:.0f}' for a in angles)}]  "
          f"smooth: {smooth_count}/{len(angles)}")
    smoothed_rr = smooth_polygon(rounded_rect)
    print(f"RoundedRect: {len(rounded_rect):3d} verts -> {len(smoothed_rr):3d} verts  "
          f"{'SMOOTHED' if len(smoothed_rr) > len(rounded_rect) else 'KEPT'}")

    # 6. Hexagon (interior angles 120°)
    hexagon = [[15, 0], [45, 0], [60, 30], [60, 90],
               [45, 120], [15, 120], [0, 90], [0, 30]]
    ccw_h = ensure_ccw(hexagon)
    hangles = []
    for i in range(len(ccw_h)):
        a = ccw_h[(i - 1) % len(ccw_h)]
        b = ccw_h[i]
        c = ccw_h[(i + 1) % len(ccw_h)]
        hangles.append(_interior_angle(a, b, c))
    print(f"Hexagon:     angles = [{', '.join(f'{a:.0f}' for a in hangles)}]")
    smoothed_hex = smooth_polygon(hexagon)
    print(f"Hexagon:     {len(hexagon):3d} verts -> {len(smoothed_hex):3d} verts  "
          f"{'SMOOTHED' if len(smoothed_hex) > len(hexagon) else 'KEPT'}")

    # 7. 24-vertex circle — already smooth enough
    n3 = 24
    circle24 = [
        [30 * math.cos(2 * math.pi * i / n3),
         30 * math.sin(2 * math.pi * i / n3)]
        for i in range(n3)
    ]
    smoothed_c24 = smooth_polygon(circle24)
    print(f"Circle(24):  {len(circle24):3d} verts -> {len(smoothed_c24):3d} verts  "
          f"{'SMOOTHED' if len(smoothed_c24) > len(circle24) else 'KEPT'}")

    # 8. T-shape — intentional sharp corners, should NOT be smoothed
    t_shape = [[15, 0], [45, 0], [45, 70], [60, 80], [60, 110],
               [55, 120], [5, 120], [0, 110], [0, 80], [15, 70]]
    smoothed_t = smooth_polygon(t_shape)
    print(f"T-shape:     {len(t_shape):3d} verts -> {len(smoothed_t):3d} verts  "
          f"{'SMOOTHED' if len(smoothed_t) > len(t_shape) else 'KEPT'}")


if __name__ == "__main__":
    test_shapes()

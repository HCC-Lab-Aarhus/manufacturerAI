"""Test routing at different diamond widths to find minimum viable width."""
import json
import time
from pathlib import Path

from src.pcb.placer import place_components, generate_placement_candidates
from src.pcb.routability import score_placement, detect_crossings
from src.pcb.router_bridge import route_traces


def test_width(w: int):
    half = w // 2
    outline = [[half, 0], [w, 47], [w, 146], [half, 192], [0, 146], [0, 47]]
    buttons = [
        {"id": "btn_1", "x": half, "y": 26},
        {"id": "btn_2", "x": half, "y": 96},
        {"id": "btn_3", "x": half, "y": 166},
    ]

    try:
        layout = place_components(outline, buttons)
    except Exception as e:
        print(f"  {w}mm: placement failed: {e}")
        return

    score, bottlenecks = score_placement(layout, outline)
    crossings = detect_crossings(layout)
    print(f"  {w}mm: score={score}, bottlenecks={len(bottlenecks)}, crossings={len(crossings)}")

    for b in bottlenecks[:2]:
        print(f"    y={b.y_mm}: avail={b.available_mm}mm, need={b.required_mm}mm, short={b.shortfall_mm}mm")

    # Only try routing shapes that score > -5
    if score < -10:
        print(f"    Skipping routing (score too low)")
        return

    out = Path(f"outputs/test_diamond_{w}")
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    result = route_traces(layout, out, max_attempts=15)
    elapsed = time.time() - t0

    traces = len(result.get("traces", []))
    failed = result.get("failed_nets", [])
    success = result.get("success", False)
    print(f"    routing: {'OK' if success else 'FAIL'}, {traces} traced, {len(failed)} failed, {elapsed:.1f}s")
    for f in failed:
        net = f.get("netName", str(f)) if isinstance(f, dict) else str(f)
        print(f"      FAIL: {net}")


if __name__ == "__main__":
    print("Testing diamond routing at different widths...")
    for w in [56, 65, 75, 85, 95]:
        test_width(w)
        print()

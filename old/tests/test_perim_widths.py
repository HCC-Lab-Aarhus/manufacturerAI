"""Test perimeter routing at different diamond widths."""
import json, time
from pathlib import Path
from src.pcb.placer import place_components
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

    out = Path(f"outputs/test_perim_{w}")
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    result = route_traces(layout, out, max_attempts=15)
    elapsed = time.time() - t0

    traces = len(result.get("traces", []))
    failed = result.get("failed_nets", [])
    success = result.get("success", False)
    status = "OK" if success else "FAIL"
    print(f"  {w}mm: {status}, {traces} traced, {len(failed)} failed, {elapsed:.1f}s")
    for f in failed:
        net = f.get("netName", str(f)) if isinstance(f, dict) else str(f)
        print(f"    FAIL: {net}")


if __name__ == "__main__":
    print("Testing perimeter routing at different diamond widths...")
    for w in [56, 60, 65, 70, 75, 85]:
        test_width(w)
        print()

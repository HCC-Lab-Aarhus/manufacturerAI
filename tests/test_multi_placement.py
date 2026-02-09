"""Quick test: multi-placement routing on the diamond shape."""

import json, sys, time
from pathlib import Path
from src.pcb.placer import generate_placement_candidates
from src.pcb.routability import score_placement
from src.pcb.router_bridge import route_traces

# Diamond outline
outline = [[28,0],[56,47],[56,146],[28,192],[0,146],[0,47]]
buttons = [
    {"id": "btn_1", "x": 28, "y": 26},
    {"id": "btn_2", "x": 28, "y": 96},
    {"id": "btn_3", "x": 28, "y": 166},
]

# Generate and score candidates
candidates = generate_placement_candidates(outline, buttons)
scored = []
for layout in candidates:
    score, bottlenecks = score_placement(layout, outline)
    scored.append((score, layout, bottlenecks))
scored.sort(key=lambda s: s[0], reverse=True)

output_dir = Path("outputs/test_multi_placement")
output_dir.mkdir(parents=True, exist_ok=True)

SCREEN_BUDGET = 8  # fast screening: 8 rip-up attempts

print(f"=== Phase A: Fast screening ({len(scored)} candidates, {SCREEN_BUDGET} attempts each) ===", flush=True)
screen_results = []
for i, (score, layout, bn) in enumerate(scored):
    bat = next(c for c in layout["components"] if c["type"] == "battery")
    ctrl = next(c for c in layout["components"] if c["type"] == "controller")
    print(f"\n--- Candidate {i}: score={score:.1f}, battery=({bat['center'][0]:.0f},{bat['center'][1]:.0f}), controller=({ctrl['center'][0]:.0f},{ctrl['center'][1]:.0f}) ---", flush=True)
    
    if score < -10 and i > 2:
        print("  SKIPPED (score too low)", flush=True)
        continue
    
    t0 = time.time()
    try:
        result = route_traces(layout, output_dir, max_attempts=SCREEN_BUDGET)
        elapsed = time.time() - t0
        success = result.get("success", False)
        traces = len(result.get("traces", []))
        failed = [f.get("netName", str(f)) if isinstance(f, dict) else str(f) for f in result.get("failed_nets", [])]
        print(f"  {'SUCCESS' if success else 'FAILED'} — {traces} traces routed, {len(failed)} failed: {failed} ({elapsed:.1f}s)", flush=True)
        screen_results.append((traces, i, layout, result))
        if success:
            print("  WINNER FOUND IN SCREENING!", flush=True)
            (output_dir / "winning_layout.json").write_text(json.dumps(layout, indent=2))
            (output_dir / "winning_routing.json").write_text(json.dumps(result, indent=2))
            sys.exit(0)
    except Exception as e:
        print(f"  ERROR: {e}", flush=True)

# Phase B: thorough routing on top 2
screen_results.sort(key=lambda t: t[0], reverse=True)
thorough = screen_results[:2]
print(f"\n=== Phase B: Thorough routing (top {len(thorough)} candidates, full budget) ===", flush=True)
for routed, idx, layout, _ in thorough:
    bat = next(c for c in layout["components"] if c["type"] == "battery")
    ctrl = next(c for c in layout["components"] if c["type"] == "controller")
    print(f"\n--- Thorough: candidate {idx} ({routed} screened), battery=({bat['center'][0]:.0f},{bat['center'][1]:.0f}), controller=({ctrl['center'][0]:.0f},{ctrl['center'][1]:.0f}) ---", flush=True)
    
    t0 = time.time()
    try:
        result = route_traces(layout, output_dir)  # full budget
        elapsed = time.time() - t0
        success = result.get("success", False)
        traces = len(result.get("traces", []))
        failed = [f.get("netName", str(f)) if isinstance(f, dict) else str(f) for f in result.get("failed_nets", [])]
        print(f"  {'SUCCESS' if success else 'FAILED'} — {traces} traces routed, {len(failed)} failed: {failed} ({elapsed:.1f}s)", flush=True)
        if success:
            print("  WINNER FOUND!", flush=True)
            (output_dir / "winning_layout.json").write_text(json.dumps(layout, indent=2))
            (output_dir / "winning_routing.json").write_text(json.dumps(result, indent=2))
            sys.exit(0)
    except Exception as e:
        print(f"  ERROR: {e}", flush=True)

print("\nNo candidate routed successfully.", flush=True)

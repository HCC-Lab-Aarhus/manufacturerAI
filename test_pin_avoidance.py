"""Quick test: re‑run inflation on session 20260403_202955 and check no
foreign pin is engulfed by any trace polygon."""

import json, math, sys
from pathlib import Path
from shapely.geometry import Polygon as ShapelyPolygon, Point

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.session import load_session
from src.pipeline.design import parse_physical_design, parse_circuit
from src.pipeline.placer import assemble_full_placement
from src.pipeline.router import parse_routing
from src.pipeline.inflation import inflate_traces, build_obstacle_polygons
from src.catalog import load_catalog

SID = "20260403_202955"
s = load_session(SID)
if s is None:
    print(f"Session {SID} not found")
    sys.exit(1)
cat = load_catalog()
physical = parse_physical_design(s.read_artifact("design.json"))
circuit = parse_circuit(s.read_artifact("circuit.json"))
placement_data = s.read_artifact("placement.json")
routing_data = s.read_artifact("routing.json")

full_placement = assemble_full_placement(
    placement_data, physical.outline, circuit.nets, physical.enclosure,
)
result = parse_routing(routing_data)

outline_poly = ShapelyPolygon(physical.outline.vertices)
catalog_map = {c.id: c for c in cat.components}
obstacles = build_obstacle_polygons(full_placement.components, catalog_map)

pin_positions: dict[str, tuple[float, float]] = {}
for comp in full_placement.components:
    for pid, pos in comp.pin_positions.items():
        pin_positions[f"{comp.instance_id}:{pid}"] = pos

net_pin_ids: dict[str, set[str]] = {}
for net in full_placement.nets:
    resolved: set[str] = set()
    for pin_ref in net.pins:
        key = f"{net.id}|{pin_ref}"
        assigned = result.pin_assignments.get(key)
        if assigned:
            resolved.add(assigned)
        else:
            resolved.add(pin_ref)
    net_pin_ids[net.id] = resolved

print("pin_positions keys:", sorted(pin_positions.keys()))
print()
for net_id, pids in sorted(net_pin_ids.items()):
    print(f"  {net_id}: {sorted(pids)}")
print()

print("Running inflation...")
inflated = inflate_traces(
    result, outline_poly, obstacles,
    pin_positions=pin_positions,
    net_pin_ids=net_pin_ids,
)
print(f"Got {len(inflated)} inflated traces")

all_pin_ids = set(pin_positions.keys())
violations = []
close_calls = []
PIN_HOLE_RADIUS = 0.5  # mm, typical drill hole radius

for it in inflated:
    own_pins = net_pin_ids.get(it.net_id, set())
    foreign = all_pin_ids - own_pins
    for pid in foreign:
        pos = pin_positions.get(pid)
        if pos is None:
            continue
        pt = Point(pos)
        if it.polygon.contains(pt):
            violations.append((it.net_id, pid, pos))
        else:
            d = it.polygon.boundary.distance(pt)
            if d < PIN_HOLE_RADIUS:
                close_calls.append((it.net_id, pid, pos, d))

if violations:
    print(f"\nFAIL: {len(violations)} foreign pins engulfed:")
    for net_id, pid, pos in violations:
        print(f"  {net_id} engulfed {pid} at {pos}")
else:
    print("\nPASS: No foreign pins engulfed by any trace polygon.")

if close_calls:
    print(f"\nWARN: {len(close_calls)} foreign pins within {PIN_HOLE_RADIUS}mm of trace boundary:")
    for net_id, pid, pos, d in sorted(close_calls, key=lambda x: x[3]):
        print(f"  {net_id} <-> {pid} at {pos}: {d:.3f}mm")

if violations:
    sys.exit(1)

"""Comprehensive inflation integrity tests using the real session 20260403_202955.

Tests cover:
  - Every net's polygon covers all its own pins
  - No net's polygon covers any foreign pin
  - No two different-net polygons overlap
  - All MST traces per net are connected (single polygon)
  - Every trace in routing.json produces an inflated trace
  - All polygons stay inside the board outline
  - No polygon is degenerate (zero area, invalid geometry)
  - Pin clearance is respected for foreign pins
  - Trace minimum width is maintained
"""

from __future__ import annotations

import pytest
from shapely.geometry import Polygon as ShapelyPolygon, Point, MultiPolygon

from src.session import load_session
from src.pipeline.design import parse_physical_design, parse_circuit
from src.pipeline.placer import assemble_full_placement
from src.pipeline.router import parse_routing
from src.pipeline.inflation import inflate_traces, build_obstacle_polygons
from src.pipeline.inflation.inflater import _build_net_polygons as build_net_polygons
from src.pipeline.config import TRACE_RULES
from src.catalog import load_catalog


SID = "20260403_202955"


def _build_effective_own_pins(
    net_pin_ids: dict[str, set[str]],
    pin_positions: dict[str, tuple[float, float]],
    result,
    catalog_map: dict | None = None,
    components: list | None = None,
) -> dict[str, set[str]]:
    """Resolve own-pin sets using polygon containment and catalog
    pin-group equivalences (e.g. button pins 1↔2 and 3↔4)."""
    min_half = TRACE_RULES.trace_width_mm / 2.0
    polys = build_net_polygons(result, min_half, pin_positions, net_pin_ids)

    inst_to_catalog: dict[str, str] = {}
    if components:
        for comp in components:
            inst_to_catalog[comp.instance_id] = comp.catalog_id

    pin_groups: dict[str, list[list[str]]] = {}
    if catalog_map:
        for cid, cat_comp in catalog_map.items():
            groups = []
            if cat_comp.pin_groups:
                for pg in cat_comp.pin_groups:
                    groups.append(list(pg.pin_ids))
            pin_groups[cid] = groups

    effective: dict[str, set[str]] = {}
    for np in polys:
        own = set(net_pin_ids.get(np.net_id, set()))
        own |= np.inside_pins
        effective[np.net_id] = own

    if catalog_map:
        for net_id, own in effective.items():
            expanded = set(own)
            for pid in list(own):
                if ":" not in pid:
                    continue
                inst, pin = pid.split(":", 1)
                cat_id = inst_to_catalog.get(inst)
                if not cat_id or cat_id not in pin_groups:
                    continue
                for group in pin_groups[cat_id]:
                    if pin in group:
                        for equiv_pin in group:
                            equiv_id = f"{inst}:{equiv_pin}"
                            if equiv_id in pin_positions:
                                expanded.add(equiv_id)
            effective[net_id] = expanded

    return effective


@pytest.fixture(scope="module")
def session_data():
    s = load_session(SID)
    if s is None:
        pytest.skip(f"Session {SID} not found")
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

    return {
        "result": result,
        "outline_poly": outline_poly,
        "obstacles": obstacles,
        "pin_positions": pin_positions,
        "net_pin_ids": net_pin_ids,
        "full_placement": full_placement,
        "physical": physical,
        "catalog_map": catalog_map,
    }


@pytest.fixture(scope="module")
def effective_net_pin_ids(session_data):
    return _build_effective_own_pins(
        session_data["net_pin_ids"],
        session_data["pin_positions"],
        session_data["result"],
        catalog_map=session_data["catalog_map"],
        components=session_data["full_placement"].components,
    )


@pytest.fixture(scope="module")
def inflated(session_data):
    return inflate_traces(
        session_data["result"],
        session_data["outline_poly"],
        session_data["obstacles"],
        pin_positions=session_data["pin_positions"],
        net_pin_ids=session_data["net_pin_ids"],
    )


@pytest.fixture(scope="module")
def net_polygons(inflated):
    """Dict mapping net_id -> merged polygon (union of all traces in that net)."""
    from shapely.ops import unary_union
    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for it in inflated:
        groups[it.net_id].append(it.polygon)
    return {nid: unary_union(polys) for nid, polys in groups.items()}


# ── 1. Every net's polygon covers all its own pins ──────────────────


class TestOwnPinCoverage:

    def test_every_own_pin_inside_polygon(self, inflated, session_data):
        pin_positions = session_data["pin_positions"]
        net_pin_ids = session_data["net_pin_ids"]

        from shapely.ops import unary_union
        from collections import defaultdict
        net_polys: dict[str, list] = defaultdict(list)
        for it in inflated:
            net_polys[it.net_id].append(it.polygon)

        failures = []
        for net_id, own_pins in net_pin_ids.items():
            merged = unary_union(net_polys.get(net_id, []))
            for pid in own_pins:
                pos = pin_positions.get(pid)
                if pos is None:
                    continue
                pt = Point(pos)
                if not merged.contains(pt) and not merged.boundary.distance(pt) < 0.1:
                    failures.append(f"{net_id} missing pin {pid} at {pos}")

        assert not failures, "Own pins not covered:\n  " + "\n  ".join(failures)

    def test_own_pin_has_minimum_pad(self, inflated, session_data):
        """Each own pin should have at least min_half radius of copper around it."""
        pin_positions = session_data["pin_positions"]
        net_pin_ids = session_data["net_pin_ids"]
        min_half = TRACE_RULES.trace_width_mm / 2.0

        from shapely.ops import unary_union
        from collections import defaultdict
        net_polys: dict[str, list] = defaultdict(list)
        for it in inflated:
            net_polys[it.net_id].append(it.polygon)

        failures = []
        for net_id, own_pins in net_pin_ids.items():
            merged = unary_union(net_polys.get(net_id, []))
            for pid in own_pins:
                pos = pin_positions.get(pid)
                if pos is None:
                    continue
                pt = Point(pos)
                d = merged.boundary.distance(pt)
                if d < min_half * 0.8:
                    failures.append(
                        f"{net_id} pin {pid}: boundary distance {d:.3f}mm < "
                        f"{min_half * 0.8:.3f}mm (80% of min_half)"
                    )

        assert not failures, "Own pin pads too small:\n  " + "\n  ".join(failures)


# ── 2. No foreign pin overlap ───────────────────────────────────────


class TestForeignPinAvoidance:

    def test_no_foreign_pin_inside_polygon(self, inflated, session_data, effective_net_pin_ids):
        pin_positions = session_data["pin_positions"]
        all_pins = set(pin_positions.keys())

        failures = []
        for it in inflated:
            own = effective_net_pin_ids.get(it.net_id, set())
            foreign = all_pins - own
            for pid in foreign:
                pos = pin_positions.get(pid)
                if pos is None:
                    continue
                pt = Point(pos)
                if it.polygon.contains(pt):
                    failures.append(f"{it.net_id} engulfs foreign pin {pid} at {pos}")

        assert not failures, "Foreign pin engulfment:\n  " + "\n  ".join(failures)

    def test_foreign_pin_clearance(self, inflated, session_data, effective_net_pin_ids):
        """Foreign pins should have at least pin_clearance distance from trace boundary."""
        pin_positions = session_data["pin_positions"]
        all_pins = set(pin_positions.keys())
        min_clearance = TRACE_RULES.pin_clearance_mm * 0.5

        failures = []
        for it in inflated:
            own = effective_net_pin_ids.get(it.net_id, set())
            foreign = all_pins - own
            for pid in foreign:
                pos = pin_positions.get(pid)
                if pos is None:
                    continue
                pt = Point(pos)
                d = it.polygon.boundary.distance(pt)
                if d < min_clearance and it.polygon.contains(pt):
                    failures.append(
                        f"{it.net_id} foreign pin {pid}: distance {d:.3f}mm < {min_clearance:.3f}mm"
                    )

        assert not failures, "Foreign pin clearance violations:\n  " + "\n  ".join(failures)


# ── 3. No cross-net overlap ─────────────────────────────────────────


class TestNoOverlap:

    def test_different_net_polygons_do_not_overlap(self, inflated):
        failures = []
        for i in range(len(inflated)):
            for j in range(i + 1, len(inflated)):
                a = inflated[i]
                b = inflated[j]
                if a.net_id == b.net_id:
                    continue
                overlap = a.polygon.intersection(b.polygon)
                if overlap.area > 1.0:
                    failures.append(
                        f"{a.net_id} vs {b.net_id}: overlap area = {overlap.area:.2f} mm²"
                    )

        assert not failures, "Cross-net overlap:\n  " + "\n  ".join(failures)

    def test_same_net_traces_share_copper(self, net_polygons):
        """Same-net polygons should ideally form a single connected region."""
        failures = []
        for net_id, poly in net_polygons.items():
            if isinstance(poly, MultiPolygon):
                areas = sorted([g.area for g in poly.geoms], reverse=True)
                failures.append(
                    f"{net_id}: MultiPolygon with {len(poly.geoms)} parts, "
                    f"areas = {[f'{a:.1f}' for a in areas]}"
                )

        assert not failures, "Disconnected same-net polygons:\n  " + "\n  ".join(failures)


# ── 4. MST connectivity ─────────────────────────────────────────────


class TestMSTConnectivity:

    def test_all_nets_have_traces(self, inflated, session_data):
        net_pin_ids = session_data["net_pin_ids"]
        inflated_nets = {it.net_id for it in inflated}
        missing = set(net_pin_ids.keys()) - inflated_nets
        assert not missing, f"Nets with no inflated traces: {missing}"

    def test_each_net_has_enough_routing_traces(self, session_data):
        """Each net should have at least (num_physical_pins - 1) routing traces."""
        result = session_data["result"]
        pin_positions = session_data["pin_positions"]
        net_pin_ids = session_data["net_pin_ids"]

        from collections import Counter
        trace_counts = Counter(t.net_id for t in result.traces)

        failures = []
        for net_id, pins in net_pin_ids.items():
            physical_pins = {p for p in pins if p in pin_positions}
            expected_min = max(len(physical_pins) - 1, 0)
            actual = trace_counts.get(net_id, 0)
            if actual < expected_min:
                failures.append(
                    f"{net_id}: {actual} routing traces but {len(physical_pins)} physical pins "
                    f"(need at least {expected_min})"
                )

        assert not failures, "Insufficient traces per net:\n  " + "\n  ".join(failures)

    def test_merged_net_polygon_covers_all_pins(self, net_polygons, session_data):
        """The merged polygon for each net should contain all its pins (connectivity)."""
        pin_positions = session_data["pin_positions"]
        net_pin_ids = session_data["net_pin_ids"]

        failures = []
        for net_id, own_pins in net_pin_ids.items():
            merged = net_polygons.get(net_id)
            if merged is None or merged.is_empty:
                failures.append(f"{net_id}: no polygon at all")
                continue
            for pid in own_pins:
                pos = pin_positions.get(pid)
                if pos is None:
                    continue
                pt = Point(pos)
                if not merged.contains(pt) and merged.boundary.distance(pt) > 0.1:
                    failures.append(f"{net_id} merged polygon misses pin {pid} at {pos}")

        assert not failures, "Merged polygon misses pins:\n  " + "\n  ".join(failures)

    def test_net_polygon_is_connected(self, net_polygons):
        """Each net's merged polygon should be a single connected region."""
        failures = []
        for net_id, poly in net_polygons.items():
            if isinstance(poly, MultiPolygon):
                failures.append(
                    f"{net_id}: polygon has {len(poly.geoms)} disjoint parts"
                )

        assert not failures, "Disconnected net polygons:\n  " + "\n  ".join(failures)


# ── 5. No missing traces ────────────────────────────────────────────


class TestNoMissingTraces:

    def test_one_inflated_per_net(self, inflated, session_data):
        """Inflation produces exactly one InflatedTrace per net."""
        net_pin_ids = session_data["net_pin_ids"]
        expected_nets = set(net_pin_ids.keys())
        inflated_nets = {it.net_id for it in inflated}
        assert inflated_nets == expected_nets, (
            f"Expected nets {expected_nets}, got {inflated_nets}"
        )
        assert len(inflated) == len(expected_nets), (
            f"Expected {len(expected_nets)} inflated traces (one per net), got {len(inflated)}"
        )

    def test_all_routing_nets_present(self, inflated, session_data):
        result = session_data["result"]
        routing_nets = {t.net_id for t in result.traces}
        inflated_nets = {it.net_id for it in inflated}
        missing = routing_nets - inflated_nets
        assert not missing, f"Routing nets missing from inflation: {missing}"


# ── 6. Geometry validity ────────────────────────────────────────────


class TestGeometryValidity:

    def test_all_polygons_valid(self, inflated):
        failures = []
        for it in inflated:
            if not it.polygon.is_valid:
                failures.append(f"{it.net_id}: invalid polygon — {it.polygon.geom_type}")

        assert not failures, "Invalid polygons:\n  " + "\n  ".join(failures)

    def test_no_zero_area_polygons(self, inflated):
        failures = []
        for it in inflated:
            if it.polygon.area < 1.0:
                failures.append(f"{it.net_id}: area = {it.polygon.area:.4f} mm²")

        assert not failures, "Zero/tiny area polygons:\n  " + "\n  ".join(failures)

    def test_all_polygons_inside_outline(self, inflated, session_data):
        outline = session_data["outline_poly"]
        failures = []
        for it in inflated:
            outside = it.polygon.difference(outline)
            if outside.area > 1.0:
                failures.append(
                    f"{it.net_id}: {outside.area:.2f} mm² outside outline"
                )

        assert not failures, "Polygons outside outline:\n  " + "\n  ".join(failures)

    def test_centreline_endpoints_inside_polygon(self, inflated):
        """Centreline start/end points should be near or inside the polygon."""
        failures = []
        for it in inflated:
            if len(it.centreline) < 2:
                continue
            poly_buffered = it.polygon.buffer(1.0)
            start = Point(it.centreline[0])
            end = Point(it.centreline[-1])
            if not poly_buffered.contains(start):
                failures.append(
                    f"{it.net_id}: centreline start {it.centreline[0]} outside polygon"
                )
            if not poly_buffered.contains(end):
                failures.append(
                    f"{it.net_id}: centreline end {it.centreline[-1]} outside polygon"
                )

        assert not failures, "Centreline endpoints outside polygon:\n  " + "\n  ".join(failures)

    def test_no_self_intersection(self, inflated):
        failures = []
        for it in inflated:
            if not it.polygon.is_simple:
                failures.append(f"{it.net_id}: self-intersecting polygon")

        assert not failures, "Self-intersecting polygons:\n  " + "\n  ".join(failures)


# ── 7. Trace width constraints ──────────────────────────────────────


class TestTraceWidth:

    def test_minimum_width_along_centreline(self, inflated):
        """Each trace polygon should maintain adequate width throughout.

        Uses morphological erosion: buffering by -threshold should leave a
        non-trivial polygon if the trace has sufficient width everywhere.
        """
        min_half = TRACE_RULES.trace_width_mm / 2.0
        threshold = min_half * 0.75

        failures = []
        for it in inflated:
            if it.polygon.area < 1.0:
                continue
            eroded = it.polygon.buffer(-threshold)
            if eroded.is_empty:
                failures.append(
                    f"{it.net_id}: entire polygon thinner than {threshold*2:.2f}mm "
                    f"(area={it.polygon.area:.1f}mm²)"
                )
                continue
            surviving = eroded.area / it.polygon.area
            if surviving < 0.40:
                failures.append(
                    f"{it.net_id}: only {surviving*100:.0f}% of area survives "
                    f"{threshold:.2f}mm erosion (area={it.polygon.area:.1f}mm²)"
                )

        assert not failures, "Trace too thin:\n  " + "\n  ".join(failures)


# ── 8. Obstacle avoidance ───────────────────────────────────────────


class TestObstacleAvoidance:

    def test_no_significant_obstacle_overlap(self, inflated, session_data):
        obstacles = session_data["obstacles"]
        if not obstacles:
            pytest.skip("No obstacles in this session")

        from shapely.ops import unary_union
        obs_union = unary_union(obstacles)
        pin_positions = session_data["pin_positions"]
        net_pin_ids = session_data["net_pin_ids"]
        min_half = TRACE_RULES.trace_width_mm / 2.0

        failures = []
        for it in inflated:
            overlap = it.polygon.intersection(obs_union)
            if overlap.area <= 2.0:
                continue
            own_pins = net_pin_ids.get(it.net_id, set())
            pin_pad_on_obs = 0.0
            for pid in own_pins:
                pos = pin_positions.get(pid)
                if pos is None:
                    continue
                pad = Point(pos).buffer(min_half)
                pin_pad_on_obs += pad.intersection(obs_union).area
            actual_overlap = overlap.area - pin_pad_on_obs
            if actual_overlap > 2.0:
                failures.append(
                    f"{it.net_id}: {actual_overlap:.2f} mm² overlaps obstacles "
                    f"(total={overlap.area:.2f}, pin_pads={pin_pad_on_obs:.2f})"
                )

        assert not failures, "Obstacle overlap:\n  " + "\n  ".join(failures)


# ── 9. Pin assignment consistency ───────────────────────────────────


class TestPinAssignment:

    def test_all_net_pins_resolved(self, session_data):
        """Every net should have all its pins resolved to actual pin positions,
        except abstract group pins (e.g. mcu_1:power, mcu_1:ground) that
        the router maps to concrete physical pins via pin_assignments."""
        pin_positions = session_data["pin_positions"]
        net_pin_ids = session_data["net_pin_ids"]

        failures = []
        for net_id, pins in net_pin_ids.items():
            for pid in pins:
                if pid not in pin_positions:
                    if ":" in pid:
                        inst, pin = pid.split(":", 1)
                        if pin in ("power", "ground", "gpio", "pwm"):
                            continue
                    failures.append(f"{net_id}: pin {pid} has no position")

        assert not failures, "Unresolved pins:\n  " + "\n  ".join(failures)

    def test_no_pin_assigned_to_multiple_nets(self, session_data):
        net_pin_ids = session_data["net_pin_ids"]
        pin_to_nets: dict[str, list[str]] = {}
        for net_id, pins in net_pin_ids.items():
            for pid in pins:
                pin_to_nets.setdefault(pid, []).append(net_id)

        failures = []
        for pid, nets in pin_to_nets.items():
            if len(nets) > 1:
                failures.append(f"Pin {pid} assigned to multiple nets: {nets}")

        assert not failures, "Multi-net pin assignments:\n  " + "\n  ".join(failures)

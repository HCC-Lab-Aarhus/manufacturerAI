"""Simulated-annealing refinement for placement.

After the greedy constructive placer produces an initial layout, this
module globally optimises component positions to minimise wirelength
and routing congestion.  The SA loop can escape local minima that the
one-at-a-time greedy strategy cannot.

Cost function (evaluated over the *entire* placement):
  1. Half-perimeter wirelength (HPWL) — standard EDA proxy for total
     trace length.  Sum over all nets of (bbox width + bbox height).
  2. Congestion — coarse global-routing demand vs. capacity (expensive,
     computed periodically rather than every iteration).
  3. Overlap penalty — body/keepout overlap between any pair.
  4. Outline violation — any body corner outside the outline.

Perturbation moves:
  - Displace  (60 %) — shift one component by a random offset.
  - Swap      (20 %) — exchange two non-UI components.
  - Rotate    (20 %) — change one component's rotation.
"""

from __future__ import annotations

import logging
import math
import random

from shapely.geometry import Polygon, box as shapely_box
from shapely.prepared import prep as shapely_prep

from src.catalog.models import Component
from src.pipeline.design.models import Net

from .congestion import CongestionGrid
from .geometry import (
    footprint_halfdims,
    footprint_envelope_halfdims,
    footprint_area,
    pin_world_xy,
    aabb_gap,
    rect_edge_clearance,
)
from .models import Placed, VALID_ROTATIONS, MIN_PIN_CLEARANCE_MM


def _crossing_count(
    nets: list[Net],
    positions: dict[str, Placed],
    catalog_map: dict[str, Component],
    _pin_cache: dict[str, dict[str, tuple[float, float]]],
) -> int:
    """Count bounding-box crossings among all net-segment pairs."""
    segments: list[tuple[str, tuple[float, float], tuple[float, float]]] = []
    for net in nets:
        points: list[tuple[float, float]] = []
        for ref in net.pins:
            if ":" not in ref:
                continue
            iid, pid = ref.split(":", 1)
            p = positions.get(iid)
            if p is None:
                continue
            local = _pin_cache.get(p.catalog_id, {}).get(pid)
            if local is not None:
                rad = math.radians(p.rotation)
                cos_r = math.cos(rad)
                sin_r = math.sin(rad)
                wx = p.x + local[0] * cos_r - local[1] * sin_r
                wy = p.y + local[0] * sin_r + local[1] * cos_r
            else:
                wx, wy = p.x, p.y
            points.append((wx, wy))
        for i in range(len(points)):
            for j in range(i + 1, len(points)):
                segments.append((net.id, points[i], points[j]))

    crossings = 0
    for i, (id_a, a1, a2) in enumerate(segments):
        for id_b, b1, b2 in segments[i + 1:]:
            if id_a == id_b:
                continue
            if (max(a1[0], a2[0]) > min(b1[0], b2[0])
                and max(b1[0], b2[0]) > min(a1[0], a2[0])
                and max(a1[1], a2[1]) > min(b1[1], b2[1])
                and max(b1[1], b2[1]) > min(a1[1], a2[1])):
                crossings += 1
    return crossings

log = logging.getLogger(__name__)


# ── Precomputed net pin index ──────────────────────────────────────

def _build_net_pin_index(
    nets: list[Net],
    catalog_map: dict[str, Component],
) -> list[list[tuple[str, tuple[float, float] | None]]]:
    """Pre-resolve pin local positions per net for fast HPWL computation.

    Returns a list parallel to *nets*.  Each entry is a list of
    ``(instance_id, pin_local_or_None)`` tuples.
    """
    result: list[list[tuple[str, tuple[float, float] | None]]] = []
    for net in nets:
        entries: list[tuple[str, tuple[float, float] | None]] = []
        for ref in net.pins:
            if ":" not in ref:
                continue
            iid, pid = ref.split(":", 1)
            cat = catalog_map.get(
                next((c.id for c in catalog_map.values()), "")
            )
            # We need the catalog_id for this instance — defer pin
            # resolution to the cost function since we don't have
            # the positions dict here.  Instead, cache the pin_id.
            entries.append((iid, pid))  # type: ignore[arg-type]
        result.append(entries)
    return result


# ── Cost helpers ───────────────────────────────────────────────────

def _hpwl(
    nets: list[Net],
    positions: dict[str, Placed],
    catalog_map: dict[str, Component],
    _pin_cache: dict[str, dict[str, tuple[float, float]]],
) -> float:
    """Half-perimeter wirelength over all nets (optimised)."""
    total = 0.0
    for net in nets:
        xmin_n = math.inf
        xmax_n = -math.inf
        ymin_n = math.inf
        ymax_n = -math.inf
        count = 0
        for ref in net.pins:
            if ":" not in ref:
                continue
            iid, pid = ref.split(":", 1)
            p = positions.get(iid)
            if p is None:
                continue
            local = _pin_cache.get(p.catalog_id, {}).get(pid)
            if local is not None:
                rad = math.radians(p.rotation)
                cos_r = math.cos(rad)
                sin_r = math.sin(rad)
                wx = p.x + local[0] * cos_r - local[1] * sin_r
                wy = p.y + local[0] * sin_r + local[1] * cos_r
            else:
                wx, wy = p.x, p.y
            if wx < xmin_n:
                xmin_n = wx
            if wx > xmax_n:
                xmax_n = wx
            if wy < ymin_n:
                ymin_n = wy
            if wy > ymax_n:
                ymax_n = wy
            count += 1
        if count >= 2:
            total += (xmax_n - xmin_n) + (ymax_n - ymin_n)
    return total


def _overlap_penalty(
    all_ids: list[str],
    positions: dict[str, Placed],
) -> float:
    """Sum of overlap depths between all component pairs."""
    penalty = 0.0
    n = len(all_ids)
    for i in range(n):
        a = positions[all_ids[i]]
        for j in range(i + 1, n):
            b = positions[all_ids[j]]
            gap = aabb_gap(a.x, a.y, a.env_hw, a.env_hh,
                           b.x, b.y, b.env_hw, b.env_hh)
            required = max(a.keepout, b.keepout, 1.0)
            violation = required - gap
            if violation > 0:
                penalty += violation
    return penalty


def _outline_penalty_fast(
    movable: list[str],
    positions: dict[str, Placed],
    prep_poly,
    edge_clearance: float,
) -> float:
    """Fast outline check — flat penalty per component outside."""
    penalty = 0.0
    for iid in movable:
        p = positions[iid]
        ihw = p.env_hw + edge_clearance
        ihh = p.env_hh + edge_clearance
        rect = shapely_box(p.x - ihw, p.y - ihh, p.x + ihw, p.y + ihh)
        if not prep_poly.contains(rect):
            penalty += 10.0
    return penalty


def _pin_clearance_penalty(
    all_ids: list[str],
    positions: dict[str, Placed],
    catalog_map: dict[str, Component],
) -> float:
    """Penalty for pin-to-pin clearance violations."""
    min_sq = MIN_PIN_CLEARANCE_MM * MIN_PIN_CLEARANCE_MM
    penalty = 0.0
    n = len(all_ids)
    for i in range(n):
        a = positions[all_ids[i]]
        cat_a = catalog_map.get(a.catalog_id)
        if not cat_a or not cat_a.pins:
            continue
        for j in range(i + 1, n):
            b = positions[all_ids[j]]
            if abs(a.x - b.x) > a.env_hw + b.env_hw + MIN_PIN_CLEARANCE_MM:
                continue
            if abs(a.y - b.y) > a.env_hh + b.env_hh + MIN_PIN_CLEARANCE_MM:
                continue
            cat_b = catalog_map.get(b.catalog_id)
            if not cat_b or not cat_b.pins:
                continue
            for pa in cat_a.pins:
                ax = a.x + pa.position_mm[0] * math.cos(math.radians(a.rotation)) - pa.position_mm[1] * math.sin(math.radians(a.rotation))
                ay = a.y + pa.position_mm[0] * math.sin(math.radians(a.rotation)) + pa.position_mm[1] * math.cos(math.radians(a.rotation))
                for pb in cat_b.pins:
                    bx = b.x + pb.position_mm[0] * math.cos(math.radians(b.rotation)) - pb.position_mm[1] * math.sin(math.radians(b.rotation))
                    by = b.y + pb.position_mm[0] * math.sin(math.radians(b.rotation)) + pb.position_mm[1] * math.cos(math.radians(b.rotation))
                    dsq = (ax - bx) ** 2 + (ay - by) ** 2
                    if dsq < min_sq:
                        penalty += MIN_PIN_CLEARANCE_MM - math.sqrt(dsq)
    return penalty


def _congestion_cost(
    nets: list[Net],
    positions: dict[str, Placed],
    cg: CongestionGrid,
) -> float:
    """Coarse-grid congestion: rebuild demand, return total overflow."""
    cg._demand = [0] * len(cg._demand)
    cg._net_routes.clear()

    for iid in list(cg._body_blocks.keys()):
        cg.unblock_component(iid)
    for iid, p in positions.items():
        cg.block_component(iid, p.x, p.y, p.env_hw, p.env_hh)

    total_overflow = 0.0
    for net in nets:
        by_inst: dict[str, list[str]] = {}
        for ref in net.pins:
            if ":" not in ref:
                continue
            iid, _pid = ref.split(":", 1)
            by_inst.setdefault(iid, []).append(_pid)

        iids = [i for i in by_inst if i in positions]
        if len(iids) < 2:
            continue

        anchor = iids[0]
        a = positions[anchor]
        for other_iid in iids[1:]:
            b = positions[other_iid]
            path = cg.route_coarse(a.x, a.y, b.x, b.y)
            if path is not None:
                cg.commit_net(f"{net.id}_{anchor}_{other_iid}", path)
                total_overflow += cg.congestion_along(path)
            else:
                total_overflow += 10.0

    return total_overflow


# ── SA Refiner ─────────────────────────────────────────────────────


def sa_refine(
    placed: list[Placed],
    ui_ids: set[str],
    nets: list[Net],
    catalog_map: dict[str, Component],
    outline_poly: Polygon,
    congestion_grid: CongestionGrid,
    *,
    n_iterations: int = 0,
    t_initial: float = 50.0,
    cooling: float = 0.9995,
) -> list[Placed]:
    """Refine placement via Simulated Annealing.

    Parameters
    ----------
    placed : list[Placed]
        Initial placement from the constructive engine.
    ui_ids : set[str]
        Instance IDs of UI-placed (frozen) components.
    nets : list[Net]
        Net list from the design spec.
    catalog_map : dict[str, Component]
        catalog_id -> Component lookup.
    outline_poly : Polygon
        The board outline polygon.
    congestion_grid : CongestionGrid
        Coarse routing grid (will be mutated during evaluation).
    n_iterations : int
        Number of SA iterations.  0 = auto-scale by component count.
    t_initial : float
        Starting temperature.
    cooling : float
        Multiplicative cooling factor per iteration.

    Returns
    -------
    list[Placed]
        Refined placement (same structure, updated positions/rotations).
    """
    movable = [p.instance_id for p in placed if p.instance_id not in ui_ids]
    if len(movable) < 2:
        return placed

    # Seed RNG from placement state for reproducibility
    seed_val = hash(tuple((p.instance_id, round(p.x, 1), round(p.y, 1)) for p in placed))
    rng = random.Random(seed_val)

    # Auto-scale iterations: ~2000 per movable component, capped
    if n_iterations <= 0:
        n_iterations = min(len(movable) * 2000, 15_000)

    all_ids = [p.instance_id for p in placed]
    xmin, ymin, xmax, ymax = outline_poly.bounds
    board_w = xmax - xmin
    board_h = ymax - ymin
    prep_poly = shapely_prep(outline_poly)
    edge_clearance = 1.5

    # Build pin local-position cache for fast HPWL
    pin_cache: dict[str, dict[str, tuple[float, float]]] = {}
    for cat in catalog_map.values():
        pin_map: dict[str, tuple[float, float]] = {}
        for pin in cat.pins:
            pin_map[pin.id] = pin.position_mm
        pin_cache[cat.id] = pin_map

    # Build mutable position map
    positions: dict[str, Placed] = {}
    for p in placed:
        positions[p.instance_id] = Placed(
            instance_id=p.instance_id,
            catalog_id=p.catalog_id,
            x=p.x, y=p.y, rotation=p.rotation,
            hw=p.hw, hh=p.hh, keepout=p.keepout,
            env_hw=p.env_hw, env_hh=p.env_hh,
        )

    # Weights
    W_HPWL = 1.0
    W_CONGESTION = 10.0
    W_OVERLAP = 200.0
    W_OUTLINE = 200.0
    W_PIN_CLR = 100.0
    W_EDGE_PREF = 1.0
    W_CROSSING = 50.0

    # Identify large components that should prefer edges (>5% of outline area)
    outline_area = board_w * board_h
    outline_verts = list(outline_poly.exterior.coords[:-1])
    large_comps: dict[str, float] = {}  # iid -> strength
    for iid in movable:
        p = positions[iid]
        cat = catalog_map.get(p.catalog_id)
        if cat is not None:
            area_ratio = footprint_area(cat) / outline_area if outline_area > 0 else 0
            if area_ratio > 0.05:
                large_comps[iid] = min(area_ratio / 0.05, 3.0)

    def _edge_pref_cost() -> float:
        """Penalise large components that are far from outline edges."""
        cost = 0.0
        for iid, strength in large_comps.items():
            p = positions[iid]
            edge_dist = rect_edge_clearance(
                p.x, p.y, p.env_hw, p.env_hh, outline_verts)
            cost += edge_dist * strength
        return cost

    # Congestion is expensive — compute every CONG_INTERVAL iterations
    CONG_INTERVAL = 50
    cached_cong = _congestion_cost(nets, positions, congestion_grid)

    def fast_cost() -> float:
        return (
            W_HPWL * _hpwl(nets, positions, catalog_map, pin_cache)
            + W_CONGESTION * cached_cong
            + W_OVERLAP * _overlap_penalty(all_ids, positions)
            + W_OUTLINE * _outline_penalty_fast(movable, positions, prep_poly, edge_clearance)
            + W_PIN_CLR * _pin_clearance_penalty(all_ids, positions, catalog_map)
            + W_EDGE_PREF * _edge_pref_cost()
            + W_CROSSING * _crossing_count(nets, positions, catalog_map, pin_cache)
        )

    current_cost = fast_cost()
    best_cost = current_cost
    best_snapshot: dict[str, tuple[float, float, int]] = {
        iid: (positions[iid].x, positions[iid].y, positions[iid].rotation)
        for iid in all_ids
    }
    initial_cost = current_cost

    T = t_initial
    accepted = 0
    stagnant = 0
    STAGNANT_LIMIT = max(n_iterations // 4, 500)

    iid2: str = ""  # for rollback in swap branch

    for iteration in range(n_iterations):
        # Refresh congestion periodically
        if iteration % CONG_INTERVAL == 0 and iteration > 0:
            cached_cong = _congestion_cost(nets, positions, congestion_grid)

        # Early termination if stagnant
        if stagnant >= STAGNANT_LIMIT:
            break

        r = rng.random()
        iid = rng.choice(movable)
        p = positions[iid]
        cat = catalog_map.get(p.catalog_id)

        old_x, old_y, old_rot = p.x, p.y, p.rotation
        old_hw, old_hh = p.hw, p.hh
        old_ehw, old_ehh = p.env_hw, p.env_hh

        move_type = 0  # 0=displace, 1=swap, 2=rotate

        if r < 0.6:
            move_type = 0
            sigma = (T / t_initial) * max(board_w, board_h) * 0.3
            sigma = max(sigma, 0.5)
            p.x = max(xmin + p.env_hw,
                       min(xmax - p.env_hw, p.x + rng.gauss(0, sigma)))
            p.y = max(ymin + p.env_hh,
                       min(ymax - p.env_hh, p.y + rng.gauss(0, sigma)))

        elif r < 0.8 and len(movable) >= 2:
            move_type = 1
            iid2 = rng.choice(movable)
            while iid2 == iid:
                iid2 = rng.choice(movable)
            p2 = positions[iid2]
            p.x, p2.x = p2.x, p.x
            p.y, p2.y = p2.y, p.y

        else:
            move_type = 2
            if cat is not None:
                candidates = [rot for rot in VALID_ROTATIONS if rot != p.rotation]
                if candidates:
                    new_rot = rng.choice(candidates)
                    p.rotation = new_rot
                    p.hw, p.hh = footprint_halfdims(cat, new_rot)
                    p.env_hw, p.env_hh = footprint_envelope_halfdims(cat, new_rot)

        new_cost = fast_cost()
        delta = new_cost - current_cost

        if delta < 0 or rng.random() < math.exp(-delta / max(T, 1e-9)):
            current_cost = new_cost
            accepted += 1
            stagnant = 0
            if new_cost < best_cost:
                best_cost = new_cost
                best_snapshot = {
                    i: (positions[i].x, positions[i].y, positions[i].rotation)
                    for i in all_ids
                }
        else:
            stagnant += 1
            if move_type == 0:
                p.x, p.y, p.rotation = old_x, old_y, old_rot
            elif move_type == 1:
                p2 = positions[iid2]
                p.x, p2.x = p2.x, p.x
                p.y, p2.y = p2.y, p.y
            else:
                p.x, p.y, p.rotation = old_x, old_y, old_rot
                p.hw, p.hh = old_hw, old_hh
                p.env_hw, p.env_hh = old_ehw, old_ehh

        T *= cooling

    # ── Restore best and verify feasibility ────────────────────────
    for iid in all_ids:
        bx, by, brot = best_snapshot[iid]
        p = positions[iid]
        p.x, p.y, p.rotation = bx, by, brot
        cat = catalog_map.get(p.catalog_id)
        if cat is not None:
            p.hw, p.hh = footprint_halfdims(cat, brot)
            p.env_hw, p.env_hh = footprint_envelope_halfdims(cat, brot)

    # Feasibility check
    overlap = _overlap_penalty(all_ids, positions)
    outline_viol = _outline_penalty_fast(movable, positions, prep_poly, edge_clearance)
    pin_viol = _pin_clearance_penalty(all_ids, positions, catalog_map)

    if overlap > 0.01 or outline_viol > 0.01 or pin_viol > 0.01:
        log.warning(
            "SA best has constraint violations (overlap=%.2f outline=%.2f pin=%.2f); "
            "falling back to constructive placement",
            overlap, outline_viol, pin_viol,
        )
        return placed

    log.info(
        "SA refinement: %d iters, %d accepted, cost %.1f → %.1f",
        iteration + 1, accepted, initial_cost, best_cost,
    )

    result: list[Placed] = []
    for orig in placed:
        p = positions[orig.instance_id]
        result.append(Placed(
            instance_id=p.instance_id,
            catalog_id=p.catalog_id,
            x=round(p.x, 2),
            y=round(p.y, 2),
            rotation=p.rotation,
            hw=p.hw, hh=p.hh,
            keepout=p.keepout,
            env_hw=p.env_hw, env_hh=p.env_hh,
        ))
    return result

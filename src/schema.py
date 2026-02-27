"""
Design spec — dataclasses for the agent's output + validation.

The DesignSpec is what the LLM agent produces: which components to use,
how they connect electrically, the device outline shape, and where
UI-facing components are placed.

Usage:
    spec = parse_design(json_dict)
    errors = validate_design(spec, catalog)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.catalog import CatalogResult


# ── Dataclasses ────────────────────────────────────────────────────

@dataclass
class ComponentInstance:
    catalog_id: str
    instance_id: str
    config: dict | None = None
    mounting_style: str | None = None       # override from allowed_styles


@dataclass
class Net:
    id: str
    pins: list[str]     # "instance_id:pin_id" or "instance_id:group_id" for dynamic


@dataclass
class OutlineVertex:
    """A single vertex with optional corner easing.

    ease_in:  mm along the incoming edge (from prev vertex) where the
              curve begins.  0 = no easing on that side.
    ease_out: mm along the outgoing edge (to next vertex) where the
              curve ends.    0 = no easing on that side.

    If both are 0 the corner is sharp.  If only one is provided at
    parse time, the other defaults to the same value (symmetric).
    """
    x: float
    y: float
    ease_in: float = 0
    ease_out: float = 0


@dataclass
class Outline:
    """Device outline as a list of vertices, each with its own corner easing."""
    points: list[OutlineVertex]

    @property
    def vertices(self) -> list[tuple[float, float]]:
        """List of (x, y) tuples for polygon operations."""
        return [(p.x, p.y) for p in self.points]


@dataclass
class UIPlacement:
    instance_id: str
    x_mm: float
    y_mm: float
    edge_index: int | None = None       # side-mount: which outline edge (0-based)


@dataclass
class DesignSpec:
    components: list[ComponentInstance]
    nets: list[Net]
    outline: Outline
    ui_placements: list[UIPlacement]


# ── Parsing ────────────────────────────────────────────────────────

def parse_design(data: dict) -> DesignSpec:
    """Parse a raw dict (from JSON / tool input) into a DesignSpec."""
    components = [
        ComponentInstance(
            catalog_id=c["catalog_id"],
            instance_id=c["instance_id"],
            config=c.get("config"),
            mounting_style=c.get("mounting_style"),
        )
        for c in data["components"]
    ]

    nets = [
        Net(id=n["id"], pins=list(n["pins"]))
        for n in data["nets"]
    ]

    outline_data = data["outline"]
    outline = _parse_outline(outline_data)

    ui_placements = [
        UIPlacement(
            instance_id=p["instance_id"],
            x_mm=float(p["x_mm"]),
            y_mm=float(p["y_mm"]),
            edge_index=p.get("edge_index"),
        )
        for p in data["ui_placements"]
    ]

    return DesignSpec(
        components=components,
        nets=nets,
        outline=outline,
        ui_placements=ui_placements,
    )


def _parse_outline(data: list) -> Outline:
    """Parse outline from a flat list of vertex objects.

    Format:
        [{"x": 0, "y": 0}, {"x": 30, "y": 0}, {"x": 30, "y": 80, "ease_in": 8}]
    """
    points = []
    for v in data:
        raw_in = v.get("ease_in")
        raw_out = v.get("ease_out")
        # If only one side is given, mirror it to the other
        if raw_in is not None and raw_out is None:
            raw_out = raw_in
        elif raw_out is not None and raw_in is None:
            raw_in = raw_out
        points.append(OutlineVertex(
            x=float(v["x"]),
            y=float(v["y"]),
            ease_in=float(raw_in) if raw_in else 0,
            ease_out=float(raw_out) if raw_out else 0,
        ))
    return Outline(points=points)


# ── Validation ─────────────────────────────────────────────────────

def validate_design(spec: DesignSpec, catalog: CatalogResult) -> list[str]:
    """Validate a DesignSpec against the catalog. Returns error messages (empty = valid)."""
    errors: list[str] = []
    catalog_map = {c.id: c for c in catalog.components}

    # ── All catalog_ids must exist ──
    for ci in spec.components:
        if ci.catalog_id not in catalog_map:
            errors.append(f"Component '{ci.instance_id}': unknown catalog_id '{ci.catalog_id}'")

    # ── Instance IDs must be unique ──
    seen_ids: set[str] = set()
    for ci in spec.components:
        if ci.instance_id in seen_ids:
            errors.append(f"Duplicate instance_id '{ci.instance_id}'")
        seen_ids.add(ci.instance_id)

    # Build lookup: instance_id -> catalog Component (only for known catalog_ids)
    instance_to_catalog = {}
    for ci in spec.components:
        if ci.catalog_id in catalog_map:
            instance_to_catalog[ci.instance_id] = catalog_map[ci.catalog_id]

    # ── Mounting style overrides ──
    for ci in spec.components:
        if ci.mounting_style and ci.catalog_id in catalog_map:
            cat = catalog_map[ci.catalog_id]
            if ci.mounting_style not in cat.mounting.allowed_styles:
                errors.append(
                    f"Component '{ci.instance_id}': mounting_style '{ci.mounting_style}' "
                    f"not in allowed_styles {cat.mounting.allowed_styles}"
                )

    # ── Configurable fields ──
    for ci in spec.components:
        if ci.config and ci.catalog_id in catalog_map:
            cat = catalog_map[ci.catalog_id]
            if not cat.configurable:
                errors.append(
                    f"Component '{ci.instance_id}': has config but "
                    f"'{ci.catalog_id}' has no configurable fields"
                )
            else:
                for key in ci.config:
                    if key not in cat.configurable:
                        errors.append(
                            f"Component '{ci.instance_id}': unknown config key '{key}'"
                        )

    # ── Net pin references ──
    for net in spec.nets:
        if len(net.pins) < 2:
            errors.append(f"Net '{net.id}': must have at least 2 pins")
        for pin_ref in net.pins:
            if ":" not in pin_ref:
                errors.append(
                    f"Net '{net.id}': invalid pin reference '{pin_ref}' "
                    f"(expected 'instance_id:pin_id')"
                )
                continue
            iid, pid = pin_ref.split(":", 1)
            if iid not in seen_ids:
                errors.append(f"Net '{net.id}': unknown instance '{iid}' in '{pin_ref}'")
                continue
            if iid not in instance_to_catalog:
                continue  # catalog_id was unknown, already reported
            cat = instance_to_catalog[iid]
            pin_ids = {p.id for p in cat.pins}
            group_ids = {g.id for g in cat.pin_groups} if cat.pin_groups else set()
            if pid not in pin_ids and pid not in group_ids:
                errors.append(
                    f"Net '{net.id}': unknown pin/group '{pid}' on "
                    f"'{iid}' (catalog: {cat.id})"
                )

    # ── Each pin in at most one net (group refs are dynamic allocations) ──
    # Build lookup: (instance_id, group_id) -> PinGroup for allocatable groups
    allocatable_groups: dict[tuple[str, str], list[str]] = {}
    for ci in spec.components:
        if ci.instance_id not in instance_to_catalog:
            continue
        cat = instance_to_catalog[ci.instance_id]
        if cat.pin_groups:
            for g in cat.pin_groups:
                if g.allocatable:
                    allocatable_groups[(ci.instance_id, g.id)] = g.pin_ids

    pin_to_net: dict[str, str] = {}
    group_alloc_count: dict[tuple[str, str], list[str]] = {}  # (iid, gid) -> [net_ids]
    for net in spec.nets:
        for pin_ref in net.pins:
            if ":" not in pin_ref:
                continue  # already reported above
            iid, pid = pin_ref.split(":", 1)
            key = (iid, pid)
            if key in allocatable_groups:
                # Dynamic group ref — each use allocates a different pin
                group_alloc_count.setdefault(key, []).append(net.id)
            else:
                # Direct pin ref — must be unique
                if pin_ref in pin_to_net:
                    errors.append(
                        f"Pin '{pin_ref}' in both net '{pin_to_net[pin_ref]}' "
                        f"and net '{net.id}'"
                    )
                else:
                    pin_to_net[pin_ref] = net.id

    # ── Validate group allocation counts don't exceed pool size ──
    for (iid, gid), net_ids in group_alloc_count.items():
        pool = allocatable_groups[(iid, gid)]
        if len(net_ids) > len(pool):
            errors.append(
                f"Group '{iid}:{gid}' used in {len(net_ids)} nets "
                f"but only has {len(pool)} pins available "
                f"(nets: {', '.join(net_ids)})"
            )

    # ── UI placements must reference ui_placement=true components ──
    for up in spec.ui_placements:
        if up.instance_id not in instance_to_catalog:
            if up.instance_id not in seen_ids:
                errors.append(f"UI placement: unknown instance '{up.instance_id}'")
            continue
        cat = instance_to_catalog[up.instance_id]
        if not cat.ui_placement:
            errors.append(
                f"UI placement: '{up.instance_id}' ({cat.id}) has ui_placement=false"
            )

        # Resolve effective mounting style
        ci_match = next((ci for ci in spec.components if ci.instance_id == up.instance_id), None)
        eff_style = (ci_match.mounting_style if ci_match and ci_match.mounting_style else cat.mounting.style)

        if eff_style == "side":
            # Side-mount components must specify edge_index
            if up.edge_index is None:
                errors.append(
                    f"UI placement '{up.instance_id}': side-mount components "
                    f"require edge_index (which outline edge to mount on)"
                )
            elif up.edge_index < 0 or up.edge_index >= len(spec.outline.points):
                errors.append(
                    f"UI placement '{up.instance_id}': edge_index {up.edge_index} "
                    f"out of range (0–{len(spec.outline.points) - 1})"
                )
        elif up.edge_index is not None:
            errors.append(
                f"UI placement '{up.instance_id}': edge_index is only for "
                f"side-mount components (mounting style is '{eff_style}')"
            )

    # ── All ui_placement=true components must have a placement ──
    ui_placed = {up.instance_id for up in spec.ui_placements}
    for ci in spec.components:
        if ci.catalog_id in catalog_map:
            cat = catalog_map[ci.catalog_id]
            if cat.ui_placement and ci.instance_id not in ui_placed:
                errors.append(
                    f"Component '{ci.instance_id}' ({cat.id}) has "
                    f"ui_placement=true but no UIPlacement defined"
                )

    # ── Outline validation ──
    if len(spec.outline.points) < 3:
        errors.append("Outline must have at least 3 vertices")

    for i, pt in enumerate(spec.outline.points):
        if pt.ease_in < 0:
            errors.append(f"Vertex {i}: ease_in must be >= 0")
        if pt.ease_out < 0:
            errors.append(f"Vertex {i}: ease_out must be >= 0")

    # ── Outline polygon validity (Shapely) ──
    if len(spec.outline.vertices) >= 3:
        try:
            from shapely.geometry import Polygon, Point
            poly = Polygon(spec.outline.vertices)
            if not poly.is_valid:
                errors.append("Outline polygon is self-intersecting or invalid")
            elif poly.area <= 0:
                errors.append("Outline polygon has zero or negative area")
            else:
                # Check UI placements are inside the outline
                # (skip side-mount components — they sit on the wall)
                for up in spec.ui_placements:
                    if up.edge_index is not None:
                        continue  # side-mount: position is on the edge, not interior
                    pt = Point(up.x_mm, up.y_mm)
                    if not poly.contains(pt):
                        errors.append(
                            f"UI placement '{up.instance_id}' at "
                            f"({up.x_mm}, {up.y_mm}) is outside the outline"
                        )
        except ImportError:
            pass  # Shapely optional for polygon checks

    return errors


# ── Serialization ──────────────────────────────────────────────────

def design_to_dict(spec: DesignSpec) -> dict:
    """Convert a DesignSpec to a JSON-serializable dict."""
    return {
        "components": [
            {
                "catalog_id": ci.catalog_id,
                "instance_id": ci.instance_id,
                **({"config": ci.config} if ci.config else {}),
                **({"mounting_style": ci.mounting_style} if ci.mounting_style else {}),
            }
            for ci in spec.components
        ],
        "nets": [
            {"id": n.id, "pins": n.pins}
            for n in spec.nets
        ],
        "outline": [
            {
                "x": p.x,
                "y": p.y,
                **({"ease_in": p.ease_in} if p.ease_in else {}),
                **({"ease_out": p.ease_out} if p.ease_out else {}),
            }
            for p in spec.outline.points
        ],
        "ui_placements": [
            {
                "instance_id": p.instance_id,
                "x_mm": p.x_mm,
                "y_mm": p.y_mm,
                **({"edge_index": p.edge_index} if p.edge_index is not None else {}),
            }
            for p in spec.ui_placements
        ],
    }

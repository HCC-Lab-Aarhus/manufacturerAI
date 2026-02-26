"""
Catalog loader — reads catalog/*.json into typed dataclasses with validation.

Usage:
    catalog = load_catalog()            # list[Component]
    comp = get_component(catalog, "led_5mm_red")
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Dataclasses ────────────────────────────────────────────────────

@dataclass
class Body:
    shape: str                          # "rect" | "circle"
    height_mm: float
    width_mm: float | None = None       # rect only
    length_mm: float | None = None      # rect only
    diameter_mm: float | None = None    # circle only


@dataclass
class Cap:
    diameter_mm: float
    height_mm: float
    hole_clearance_mm: float


@dataclass
class Hatch:
    enabled: bool
    clearance_mm: float
    thickness_mm: float


@dataclass
class Mounting:
    style: str                          # "top" | "side" | "internal" | "bottom"
    allowed_styles: list[str]
    blocks_routing: bool
    keepout_margin_mm: float
    cap: Cap | None = None
    hatch: Hatch | None = None


@dataclass
class Pin:
    id: str
    label: str
    position_mm: tuple[float, float]
    direction: str                      # "in" | "out" | "bidirectional"
    hole_diameter_mm: float
    description: str
    voltage_v: float | None = None
    current_max_ma: float | None = None


@dataclass
class PinGroup:
    id: str
    pin_ids: list[str]
    description: str = ""
    fixed_net: str | None = None
    allocatable: bool = False
    capabilities: list[str] | None = None


@dataclass
class Component:
    id: str
    name: str
    description: str
    category: str                       # "indicator"|"switch"|"passive"|"active"|"power"|"mcu"
    ui_placement: bool
    body: Body
    mounting: Mounting
    pins: list[Pin]
    internal_nets: list[list[str]] = field(default_factory=list)
    pin_groups: list[PinGroup] | None = None
    configurable: dict | None = None
    source_file: str = ""               # path of the JSON file (for error reporting)


# ── Validation ─────────────────────────────────────────────────────

@dataclass
class ValidationError:
    component_id: str
    field: str
    message: str

    def __str__(self) -> str:
        return f"[{self.component_id}] {self.field}: {self.message}"


@dataclass
class CatalogResult:
    """Result of loading the catalog — components + any validation errors."""
    components: list[Component]
    errors: list[ValidationError]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def _validate_component(comp: Component) -> list[ValidationError]:
    """Run all validation checks on a single component."""
    errs: list[ValidationError] = []
    cid = comp.id

    # Body dimensions
    if comp.body.shape == "rect":
        if comp.body.width_mm is None or comp.body.width_mm <= 0:
            errs.append(ValidationError(cid, "body.width_mm", "Must be > 0 for rect shape"))
        if comp.body.length_mm is None or comp.body.length_mm <= 0:
            errs.append(ValidationError(cid, "body.length_mm", "Must be > 0 for rect shape"))
    elif comp.body.shape == "circle":
        if comp.body.diameter_mm is None or comp.body.diameter_mm <= 0:
            errs.append(ValidationError(cid, "body.diameter_mm", "Must be > 0 for circle shape"))
    else:
        errs.append(ValidationError(cid, "body.shape", f"Unknown shape '{comp.body.shape}', expected 'rect' or 'circle'"))

    if comp.body.height_mm <= 0:
        errs.append(ValidationError(cid, "body.height_mm", "Must be > 0"))

    # Mounting style
    valid_styles = {"top", "side", "internal", "bottom"}
    if comp.mounting.style not in valid_styles:
        errs.append(ValidationError(cid, "mounting.style", f"Unknown style '{comp.mounting.style}'"))
    for s in comp.mounting.allowed_styles:
        if s not in valid_styles:
            errs.append(ValidationError(cid, "mounting.allowed_styles", f"Unknown style '{s}'"))
    if comp.mounting.style not in comp.mounting.allowed_styles:
        errs.append(ValidationError(cid, "mounting.style",
                                    f"Default style '{comp.mounting.style}' not in allowed_styles {comp.mounting.allowed_styles}"))

    # Pin IDs unique
    pin_ids = [p.id for p in comp.pins]
    seen: set[str] = set()
    for pid in pin_ids:
        if pid in seen:
            errs.append(ValidationError(cid, f"pins.{pid}", "Duplicate pin ID"))
        seen.add(pid)

    pin_id_set = set(pin_ids)

    # Pin direction
    valid_dirs = {"in", "out", "bidirectional"}
    for pin in comp.pins:
        if pin.direction not in valid_dirs:
            errs.append(ValidationError(cid, f"pins.{pin.id}.direction",
                                        f"Unknown direction '{pin.direction}'"))

    # internal_nets reference valid pins
    for i, net_group in enumerate(comp.internal_nets):
        for pid in net_group:
            if pid not in pin_id_set:
                errs.append(ValidationError(cid, f"internal_nets[{i}]",
                                            f"References unknown pin '{pid}'"))

    # pin_groups reference valid pins
    if comp.pin_groups:
        for group in comp.pin_groups:
            for pid in group.pin_ids:
                if pid not in pin_id_set:
                    errs.append(ValidationError(cid, f"pin_groups.{group.id}",
                                                f"References unknown pin '{pid}'"))

    # Category
    valid_cats = {"indicator", "switch", "passive", "active", "power", "mcu"}
    if comp.category not in valid_cats:
        errs.append(ValidationError(cid, "category", f"Unknown category '{comp.category}'"))

    return errs


# ── Parsing ────────────────────────────────────────────────────────

def _parse_body(data: dict) -> Body:
    return Body(
        shape=data["shape"],
        height_mm=data["height_mm"],
        width_mm=data.get("width_mm"),
        length_mm=data.get("length_mm"),
        diameter_mm=data.get("diameter_mm"),
    )


def _parse_cap(data: dict | None) -> Cap | None:
    if data is None:
        return None
    return Cap(
        diameter_mm=data["diameter_mm"],
        height_mm=data["height_mm"],
        hole_clearance_mm=data["hole_clearance_mm"],
    )


def _parse_hatch(data: dict | None) -> Hatch | None:
    if data is None:
        return None
    return Hatch(
        enabled=data["enabled"],
        clearance_mm=data["clearance_mm"],
        thickness_mm=data["thickness_mm"],
    )


def _parse_mounting(data: dict) -> Mounting:
    return Mounting(
        style=data["style"],
        allowed_styles=data["allowed_styles"],
        blocks_routing=data["blocks_routing"],
        keepout_margin_mm=data["keepout_margin_mm"],
        cap=_parse_cap(data.get("cap")),
        hatch=_parse_hatch(data.get("hatch")),
    )


def _parse_pin(data: dict) -> Pin:
    pos = data.get("position_mm", [0, 0])
    return Pin(
        id=data["id"],
        label=data.get("label", data["id"]),
        position_mm=(pos[0], pos[1]),
        direction=data["direction"],
        voltage_v=data.get("voltage_v"),
        current_max_ma=data.get("current_max_ma"),
        hole_diameter_mm=data.get("hole_diameter_mm", 0.8),
        description=data.get("description", ""),
    )


def _parse_pin_group(data: dict) -> PinGroup:
    return PinGroup(
        id=data["id"],
        pin_ids=data["pin_ids"],
        description=data.get("description", ""),
        fixed_net=data.get("fixed_net"),
        allocatable=data.get("allocatable", False),
        capabilities=data.get("capabilities"),
    )


def _parse_component(data: dict, source_file: str = "") -> Component:
    return Component(
        id=data["id"],
        name=data["name"],
        description=data["description"],
        category=data["category"],
        ui_placement=data["ui_placement"],
        body=_parse_body(data["body"]),
        mounting=_parse_mounting(data["mounting"]),
        pins=[_parse_pin(p) for p in data["pins"]],
        internal_nets=data.get("internal_nets", []),
        pin_groups=[_parse_pin_group(g) for g in data["pin_groups"]] if data.get("pin_groups") else None,
        configurable=data.get("configurable"),
        source_file=source_file,
    )


# ── Public API ─────────────────────────────────────────────────────

CATALOG_DIR = Path(__file__).resolve().parent.parent / "catalog"


def load_catalog(catalog_dir: Path | None = None) -> CatalogResult:
    """Load all catalog/*.json files, parse and validate.

    Returns a CatalogResult with components and any validation errors.
    Components that fail to parse are skipped (error recorded).
    Components that parse but have validation issues are still included.
    """
    d = catalog_dir or CATALOG_DIR
    components: list[Component] = []
    errors: list[ValidationError] = []

    json_files = sorted(d.glob("*.json"))
    if not json_files:
        errors.append(ValidationError("_catalog", "files", f"No .json files found in {d}"))
        return CatalogResult(components=components, errors=errors)

    for path in json_files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(ValidationError(
                path.stem, "json", f"Parse error: {exc}"))
            continue
        except OSError as exc:
            errors.append(ValidationError(
                path.stem, "file", f"Read error: {exc}"))
            continue

        try:
            comp = _parse_component(raw, source_file=str(path))
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(ValidationError(
                raw.get("id", path.stem), "parse", f"Missing/invalid field: {exc}"))
            continue

        # Validate
        comp_errors = _validate_component(comp)
        errors.extend(comp_errors)
        components.append(comp)

    # Check for duplicate IDs across files
    id_counts: dict[str, int] = {}
    for comp in components:
        id_counts[comp.id] = id_counts.get(comp.id, 0) + 1
    for cid, count in id_counts.items():
        if count > 1:
            errors.append(ValidationError(cid, "id", f"Duplicate component ID (appears {count} times)"))

    return CatalogResult(components=components, errors=errors)


def get_component(catalog: list[Component] | CatalogResult, component_id: str) -> Component | None:
    """Look up a component by ID. Returns None if not found."""
    comps = catalog.components if isinstance(catalog, CatalogResult) else catalog
    for c in comps:
        if c.id == component_id:
            return c
    return None


def catalog_to_dict(result: CatalogResult) -> dict:
    """Serialize a CatalogResult to a JSON-safe dict for the web API."""
    return {
        "ok": result.ok,
        "component_count": len(result.components),
        "components": [_component_to_dict(c) for c in result.components],
        "errors": [{"component_id": e.component_id, "field": e.field, "message": e.message}
                   for e in result.errors],
    }


def _component_to_dict(c: Component) -> dict:
    """Serialize a Component to a JSON-safe dict."""
    d: dict[str, Any] = {
        "id": c.id,
        "name": c.name,
        "description": c.description,
        "category": c.category,
        "ui_placement": c.ui_placement,
        "body": {
            "shape": c.body.shape,
            "height_mm": c.body.height_mm,
        },
        "mounting": {
            "style": c.mounting.style,
            "allowed_styles": c.mounting.allowed_styles,
            "blocks_routing": c.mounting.blocks_routing,
            "keepout_margin_mm": c.mounting.keepout_margin_mm,
        },
        "pins": [
            {
                "id": p.id,
                "label": p.label,
                "position_mm": list(p.position_mm),
                "direction": p.direction,
                "voltage_v": p.voltage_v,
                "current_max_ma": p.current_max_ma,
                "hole_diameter_mm": p.hole_diameter_mm,
                "description": p.description,
            }
            for p in c.pins
        ],
        "internal_nets": c.internal_nets,
        "source_file": c.source_file,
    }

    # Body shape-specific fields
    if c.body.width_mm is not None:
        d["body"]["width_mm"] = c.body.width_mm
    if c.body.length_mm is not None:
        d["body"]["length_mm"] = c.body.length_mm
    if c.body.diameter_mm is not None:
        d["body"]["diameter_mm"] = c.body.diameter_mm

    # Optional mounting sub-objects
    if c.mounting.cap:
        d["mounting"]["cap"] = {
            "diameter_mm": c.mounting.cap.diameter_mm,
            "height_mm": c.mounting.cap.height_mm,
            "hole_clearance_mm": c.mounting.cap.hole_clearance_mm,
        }
    if c.mounting.hatch:
        d["mounting"]["hatch"] = {
            "enabled": c.mounting.hatch.enabled,
            "clearance_mm": c.mounting.hatch.clearance_mm,
            "thickness_mm": c.mounting.hatch.thickness_mm,
        }

    # Optional fields
    if c.pin_groups:
        d["pin_groups"] = [
            {
                "id": g.id,
                "pin_ids": g.pin_ids,
                "description": g.description,
                "fixed_net": g.fixed_net,
                "allocatable": g.allocatable,
                "capabilities": g.capabilities,
            }
            for g in c.pin_groups
        ]
    if c.configurable:
        d["configurable"] = c.configurable

    return d

"""Catalog serialization — convert dataclasses to JSON-safe dicts."""

from __future__ import annotations

from typing import Any

from .models import Component, CatalogResult


def catalog_to_dict(result: CatalogResult) -> dict:
    """Serialize a CatalogResult to a JSON-safe dict for the web API."""
    return {
        "ok": result.ok,
        "component_count": len(result.components),
        "components": [component_to_dict(c) for c in result.components],
        "errors": [{"component_id": e.component_id, "field": e.field, "message": e.message}
                   for e in result.errors],
    }


def component_to_dict(c: Component) -> dict:
    """Serialize a Component to a JSON-safe dict."""
    d: dict[str, Any] = {
        "id": c.id,
        "name": c.name,
        "description": c.description,
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

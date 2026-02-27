"""Tool definitions for the Anthropic API (list_components, get_component, submit_design)."""

from __future__ import annotations

from typing import Any


TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_components",
        "description": (
            "List all available components in the catalog with summary info "
            "(ID, category, name, pin count, mounting style, whether it needs "
            "UI placement). Already shown in your system prompt — use this "
            "only if you need a refresher."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_component",
        "description": (
            "Get full details for a specific component: all pins with "
            "positions/directions/voltage/current, mounting details, "
            "internal_nets, pin_groups, and configurable fields. "
            "Always read component details before using it in a design."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "component_id": {
                    "type": "string",
                    "description": "Component ID from the catalog (e.g. 'led_5mm_red')",
                },
            },
            "required": ["component_id"],
        },
    },
    {
        "name": "submit_design",
        "description": (
            "Submit a complete device design for validation. If validation "
            "fails, you'll receive error details — fix and resubmit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "components": {
                    "type": "array",
                    "description": "Component instances to use.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "catalog_id": {
                                "type": "string",
                                "description": "Component ID from the catalog",
                            },
                            "instance_id": {
                                "type": "string",
                                "description": "Unique instance name (e.g. 'led_1', 'r_1')",
                            },
                            "config": {
                                "type": "object",
                                "description": "Config overrides for configurable components",
                            },
                            "mounting_style": {
                                "type": "string",
                                "description": "Override from allowed_styles",
                            },
                        },
                        "required": ["catalog_id", "instance_id"],
                    },
                },
                "nets": {
                    "type": "array",
                    "description": "Electrical nets connecting component pins.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Net name (e.g. 'VCC', 'GND')",
                            },
                            "pins": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Pin references as 'instance_id:pin_id'. "
                                    "Use 'instance_id:group_id' for MCU dynamic "
                                    "pin allocation."
                                ),
                            },
                        },
                        "required": ["id", "pins"],
                    },
                },
                "outline": {
                    "type": "array",
                    "description": (
                        "Device outline as a list of vertex objects (clockwise winding). "
                        "Each vertex has x, y (mm) and optional ease_in / ease_out "
                        "distances (mm) that round the corner. Omit both for sharp corners."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "x": {
                                "type": "number",
                                "description": "X coordinate in mm",
                            },
                            "y": {
                                "type": "number",
                                "description": "Y coordinate in mm",
                            },
                            "ease_in": {
                                "type": "number",
                                "description": (
                                    "Distance in mm along the incoming edge "
                                    "(from previous vertex) where the curve "
                                    "begins. If omitted, defaults to ease_out "
                                    "when ease_out is set, otherwise 0."
                                ),
                            },
                            "ease_out": {
                                "type": "number",
                                "description": (
                                    "Distance in mm along the outgoing edge "
                                    "(toward next vertex) where the curve "
                                    "ends. If omitted, defaults to ease_in "
                                    "when ease_in is set, otherwise 0."
                                ),
                            },
                        },
                        "required": ["x", "y"],
                    },
                },
                "ui_placements": {
                    "type": "array",
                    "description": (
                        "Positions for UI-facing components (buttons, LEDs, "
                        "switches). Only for ui_placement=true components. "
                        "Side-mount components must include edge_index."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "instance_id": {"type": "string"},
                            "x_mm": {
                                "type": "number",
                                "description": (
                                    "X position in mm. For side-mount: "
                                    "approximate position along the edge."
                                ),
                            },
                            "y_mm": {
                                "type": "number",
                                "description": (
                                    "Y position in mm. For side-mount: "
                                    "approximate position along the edge."
                                ),
                            },
                            "edge_index": {
                                "type": "integer",
                                "description": (
                                    "Required for side-mount components. "
                                    "Which outline edge (0-based) to mount on. "
                                    "Edge i goes from vertices[i] to "
                                    "vertices[(i+1) % n]. The component "
                                    "protrudes through this wall."
                                ),
                            },
                        },
                        "required": ["instance_id", "x_mm", "y_mm"],
                    },
                },
            },
            "required": ["components", "nets", "outline", "ui_placements"],
        },
    },
]

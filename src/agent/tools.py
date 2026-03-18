"""Tool definitions for the design and circuit agents."""

from __future__ import annotations

from typing import Any


# ── Shared tools (used by both agents) ────────────────────────────

_LIST_COMPONENTS = {
    "name": "list_components",
    "description": (
        "List all available components in the catalog with summary info "
        "(ID, name, pin count, mounting style, whether it needs "
        "UI placement). Already shown in your system prompt — use this "
        "only if you need a refresher."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
    },
}

_GET_COMPONENT = {
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
                "description": "Component ID from the catalog (e.g. 'led_5mm')",
            },
        },
        "required": ["component_id"],
    },
}


# ── Design agent tools ────────────────────────────────────────────

DESIGN_TOOLS: list[dict[str, Any]] = [
    _LIST_COMPONENTS,
    _GET_COMPONENT,
    {
        "name": "submit_design",
        "description": (
            "Submit a physical device design for validation. Includes the "
            "device shape, enclosure, and UI component placements. If "
            "validation fails, you'll receive error details — fix and resubmit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_description": {
                    "type": "string",
                    "description": (
                        "2-4 sentence description of what the device does, "
                        "how it is used, and what the user asked for. "
                        "This is passed to the circuit agent."
                    ),
                },
                "outline": {
                    "type": "array",
                    "description": (
                        "Device outline as a list of vertex objects (clockwise winding). "
                        "Coordinate system: screen convention — x increases rightward, "
                        "y increases downward (y=0 is the top of the device). "
                        "Each vertex has x, y (mm), optional ease_in / ease_out "
                        "(mm) for corner rounding, and optional z_top (mm) for "
                        "per-vertex ceiling height. Omit z_top to inherit from "
                        "the enclosure height_mm."
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
                            "z_top": {
                                "type": "number",
                                "description": (
                                    "Ceiling height (mm) at this vertex. "
                                    "Omit to inherit from enclosure.height_mm. "
                                    "Must be >= floor(2mm) + tallest component + ceiling(2mm)."
                                ),
                            },
                            "z_bottom": {
                                "type": "number",
                                "description": (
                                    "Floor height (mm) at this vertex. "
                                    "Omit or 0 for flat on build plate. "
                                    "Raises the floor locally — areas with z_bottom > 0 "
                                    "cannot hold traces or components."
                                ),
                            },
                        },
                        "required": ["x", "y"],
                    },
                },
                "enclosure": {
                    "type": "object",
                    "description": (
                        "3D enclosure shape descriptor. The floor is always flat. "
                        "height_mm sets the default ceiling height for vertices without "
                        "z_top, and is the minimum height everywhere. "
                        "top_surface adds an optional smooth bump over the vertex heights."
                    ),
                    "properties": {
                        "height_mm": {
                            "type": "number",
                            "description": (
                                "Default ceiling height (mm) and minimum height. "
                                "Must be >= 2 (floor) + tallest_component + 2 (ceiling). "
                                "Example: battery_holder_9v is ~30mm tall so height_mm >= 34."
                            ),
                        },
                        "top_surface": {
                            "type": "object",
                            "description": "Optional smooth bump added over the per-vertex ceiling interpolation.",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "description": "Shape type: 'flat' (default), 'dome', or 'ridge'.",
                                },
                                "peak_x_mm": {"type": "number", "description": "Dome: X of peak"},
                                "peak_y_mm": {"type": "number", "description": "Dome: Y of peak"},
                                "peak_height_mm": {"type": "number", "description": "Dome: absolute Z at peak"},
                                "base_height_mm": {"type": "number", "description": "Dome/ridge: Z level the bump rises from"},
                                "x1": {"type": "number", "description": "Ridge: crest line start X"},
                                "y1": {"type": "number", "description": "Ridge: crest line start Y"},
                                "x2": {"type": "number", "description": "Ridge: crest line end X"},
                                "y2": {"type": "number", "description": "Ridge: crest line end Y"},
                                "crest_height_mm": {"type": "number", "description": "Ridge: absolute Z at the crest"},
                                "falloff_mm": {"type": "number", "description": "Ridge: distance from crest where surface reaches base_height_mm"},
                            },
                            "required": ["type"],
                        },
                        "bottom_surface": {
                            "type": "object",
                            "description": (
                                "Optional smooth bump that raises the floor. Same structure as top_surface. "
                                "Raised floor areas cannot hold traces or components."
                            ),
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "description": "Shape type: 'flat' (default), 'dome', or 'ridge'.",
                                },
                                "peak_x_mm": {"type": "number", "description": "Dome: X of peak"},
                                "peak_y_mm": {"type": "number", "description": "Dome: Y of peak"},
                                "peak_height_mm": {"type": "number", "description": "Dome: absolute Z of raised floor peak"},
                                "base_height_mm": {"type": "number", "description": "Dome/ridge: Z level the bump rises from"},
                                "x1": {"type": "number", "description": "Ridge: crest line start X"},
                                "y1": {"type": "number", "description": "Ridge: crest line start Y"},
                                "x2": {"type": "number", "description": "Ridge: crest line end X"},
                                "y2": {"type": "number", "description": "Ridge: crest line end Y"},
                                "crest_height_mm": {"type": "number", "description": "Ridge: absolute Z of raised floor crest"},
                                "falloff_mm": {"type": "number", "description": "Ridge: distance from crest where floor returns to base"},
                            },
                            "required": ["type"],
                        },
                        "edge_top": {
                            "type": "object",
                            "description": "Profile at the wall-to-ceiling junction.",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "description": "'none' (default), 'chamfer', or 'fillet'.",
                                },
                                "size_mm": {
                                    "type": "number",
                                    "description": "Profile radius/depth in mm (default 2). Clamped to ≤ 45% of wall height.",
                                },
                            },
                            "required": ["type"],
                        },
                        "edge_bottom": {
                            "type": "object",
                            "description": (
                                "Profile at the wall-to-floor junction. "
                                "Reduces usable internal floor area near the walls."
                            ),
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "description": "'none' (default), 'chamfer', or 'fillet'.",
                                },
                                "size_mm": {
                                    "type": "number",
                                    "description": "Profile radius/depth in mm (default 2). Clamped to ≤ 45% of wall height.",
                                },
                            },
                            "required": ["type"],
                        },
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
                            "catalog_id": {
                                "type": "string",
                                "description": "Catalog component ID (e.g. 'led_5mm', 'tactile_button_6x6')",
                            },
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
                            "conform_to_surface": {
                                "type": "boolean",
                                "description": (
                                    "Whether to angle the component cutout to "
                                    "follow the local surface curvature (default: true). "
                                    "Set to false for a vertical hole regardless of "
                                    "the ceiling angle."
                                ),
                            },
                            "mounting_style": {
                                "type": "string",
                                "description": (
                                    "Override the component's default mounting style. "
                                    "Must be one of the component's allowed_styles "
                                    "(check with get_component). Set to 'side' for "
                                    "components that should protrude through a wall "
                                    "(requires edge_index). Omit to use the catalog default."
                                ),
                            },
                            "button_outline": {
                                "type": "array",
                                "description": (
                                    "Custom button shape as a list of [x, y] points "
                                    "(mm) relative to the button centre. When provided, "
                                    "a matching printable button cap is generated alongside "
                                    "the enclosure, and the ceiling hole is shaped to this "
                                    "outline. The button's top surface follows the enclosure "
                                    "curvature. Only applies to components with a switch "
                                    "actuator (e.g. tactile_button_6x6). Omit for the "
                                    "default circular button."
                                ),
                                "items": {
                                    "type": "array",
                                    "items": {"type": "number"},
                                    "minItems": 2,
                                    "maxItems": 2,
                                    "description": "[x, y] point in mm relative to button centre",
                                },
                            },
                        },
                        "required": ["instance_id", "catalog_id", "x_mm", "y_mm"],
                    },
                },
            },
            "required": ["device_description", "outline", "enclosure", "ui_placements"],
        },
    },
]


# ── Circuit agent tools ───────────────────────────────────────────

CIRCUIT_TOOLS: list[dict[str, Any]] = [
    _LIST_COMPONENTS,
    _GET_COMPONENT,
    {
        "name": "submit_circuit",
        "description": (
            "Submit a complete circuit design for validation. Includes all "
            "components (UI + internal) and the electrical net list. If "
            "validation fails, you'll receive error details — fix and resubmit."
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
            },
            "required": ["components", "nets"],
        },
    },
]


# ── Setup (firmware) agent tools ──────────────────────────────────

SETUP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "submit_firmware",
        "description": (
            "Submit a complete Arduino sketch (.ino) for the device. "
            "The code will be compiled with arduino-cli. If compilation "
            "fails, you'll receive the compiler errors — fix and resubmit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "The complete Arduino .ino file contents. Must be a "
                        "single self-contained sketch with setup() and loop()."
                    ),
                },
            },
            "required": ["code"],
        },
    },
]

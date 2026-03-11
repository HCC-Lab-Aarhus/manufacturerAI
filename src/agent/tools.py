"""Tool definitions for the Anthropic API — circuit and design tool sets."""

from __future__ import annotations

from typing import Any


# ── Shared tool schemas ────────────────────────────────────────────

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

_CSG_SHAPE_SCHEMA = {
    "type": "object",
    "description": (
        "CSG tree defining the device shape. Each node is either "
        "a primitive (with 'type') or a boolean operation (with 'op' + 'children'). "
        "Coordinate system: x=right, y=forward, z=up (mm). "
        "Primitives are centered at their 'center' position."
    ),
    "properties": {
        "type": {
            "type": "string",
            "description": (
                "Primitive type: 'box', 'cylinder', or 'sphere'. "
                "Only set for leaf nodes."
            ),
        },
        "op": {
            "type": "string",
            "description": (
                "Boolean operation: 'union', 'difference', or 'intersection'. "
                "Only set for branch nodes. 'difference' subtracts children[1..N] from children[0]."
            ),
        },
        "children": {
            "type": "array",
            "description": "Child CSG nodes (for operations).",
            "items": {"type": "object"},
        },
        "center": {
            "type": "array",
            "description": "Position [x, y, z] in mm. Default [0,0,0].",
            "items": {"type": "number"},
        },
        "size": {
            "type": "array",
            "description": (
                "Extents [x, y, z] in mm (box)."
            ),
            "items": {"type": "number"},
        },
        "size_end": {
            "type": "array",
            "description": (
                "+axis-end extents [x, y, z] in mm (tapered box). Only the "
                "two non-axis dimensions matter. `size` defines the \u2212axis "
                "end; `size_end` defines the +axis end. Omit for a regular "
                "box. Set dimensions to 0 to collapse edges."
            ),
            "items": {"type": "number"},
        },
        "radius": {
            "description": (
                "Radius in mm. A single number for uniform shape. "
                "An array [rx, ry, rz] for ellipsoid (sphere) or "
                "[ra, rb] for oval cross-section (cylinder). "
                "For tapered cylinders, this is the \u2212axis end."
            ),
        },
        "radius_end": {
            "description": (
                "+axis-end radius for tapered cylinder. `radius` defines "
                "the \u2212axis end; `radius_end` defines the +axis end. "
                "A number for uniform, [ra, rb] for oval. "
                "Set to 0 for a pointed cone. Omit for a straight cylinder."
            ),
        },
        "height": {"type": "number", "description": "Height in mm (cylinder)."},
        "axis": {
            "type": "string",
            "description": (
                "Alignment axis: 'x', 'y', or 'z' (default 'z'). "
                "The shape spans from center[axis]\u2212height/2 (−axis end) "
                "to center[axis]+height/2 (+axis end)."
            ),
        },
        "rotate": {
            "type": "array",
            "description": "Euler rotation [rx, ry, rz] in degrees, applied after shape generation.",
            "items": {"type": "number"},
        },
    },
}

_SURFACE_PLACEMENTS_SCHEMA = {
    "type": "array",
    "description": (
        "Components placed on the mesh surface. For each component, specify "
        "which face to project onto and an approximate [x, y, z] aim-point "
        "using the same coordinates as your CSG shapes. The system ray-casts "
        "to the actual surface automatically — only the two coordinates "
        "perpendicular to the face matter; the depth axis is auto-resolved. "
        "Use [0, 0, 0] for dead center. "
        "Only for UI components (buttons, LEDs, switches). "
        "Internal components (MCU, resistors, battery) will be auto-placed inside."
    ),
    "items": {
        "type": "object",
        "properties": {
            "catalog_id": {
                "type": "string",
                "description": "Component catalog ID (e.g. 'led_5mm', 'tactile_button_6x6')",
            },
            "instance_id": {"type": "string"},
            "face": {
                "type": "string",
                "description": (
                    "Which surface to place on: "
                    "'top', 'bottom', 'front', 'back', 'left', 'right'."
                ),
            },
            "at": {
                "type": "array",
                "description": (
                    "Approximate [x, y, z] aim-point in the same coordinate "
                    "system as the CSG shapes. The coordinate along the face's "
                    "depth axis is ignored (auto-projected to the surface). "
                    "Use [0, 0, 0] for dead center of the face."
                ),
                "items": {"type": "number"},
                "minItems": 3,
                "maxItems": 3,
            },
            "rotation_deg": {
                "type": "number",
                "description": "Rotation around the surface normal in degrees (default 0).",
            },
        },
        "required": ["catalog_id", "instance_id", "face"],
    },
}


# ── Design Agent Tools (Step 1) ───────────────────────────────────

DESIGN_TOOLS: list[dict[str, Any]] = [
    _LIST_COMPONENTS,
    _GET_COMPONENT,
    {
        "name": "submit_design",
        "description": (
            "Submit the physical device design (shape + UI placements + "
            "device description) for validation. The device shape is defined "
            "as a CSG tree (union, difference, intersection of "
            "box/cylinder/sphere primitives). UI components are placed "
            "on the mesh surface — the system snaps them to the actual "
            "surface. The device_description is passed to the electronics "
            "engineer who designs the circuit. If validation fails, you'll "
            "receive error details — fix and resubmit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_description": {
                    "type": "string",
                    "description": (
                        "2-4 sentence description of what the device does, "
                        "how it behaves, and what functions the UI components "
                        "serve. Read by the electronics engineer."
                    ),
                },
                "shape": _CSG_SHAPE_SCHEMA,
                "surface_placements": _SURFACE_PLACEMENTS_SCHEMA,
            },
            "required": ["device_description", "shape", "surface_placements"],
        },
    },
]


# ── Circuit Agent Tools (Step 2) ──────────────────────────────────

CIRCUIT_TOOLS: list[dict[str, Any]] = [
    _LIST_COMPONENTS,
    _GET_COMPONENT,
    {
        "name": "submit_circuit",
        "description": (
            "Submit the circuit design (components + nets) for validation. "
            "If validation fails, you'll receive error details — fix and resubmit."
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
                            "catalog_id": {"type": "string"},
                            "instance_id": {"type": "string"},
                            "config": {"type": "object"},
                            "mounting_style": {"type": "string"},
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
                            "id": {"type": "string"},
                            "pins": {
                                "type": "array",
                                "items": {"type": "string"},
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

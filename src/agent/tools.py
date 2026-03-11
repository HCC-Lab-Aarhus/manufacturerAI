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
                "Primitive type: 'box', 'cylinder', 'sphere', or 'cone'. "
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
            "description": "Box extents [width_x, depth_y, height_z] in mm.",
            "items": {"type": "number"},
        },
        "radius": {"type": "number", "description": "Radius in mm (cylinder/sphere/cone)."},
        "height": {"type": "number", "description": "Height in mm (cylinder/cone)."},
        "axis": {
            "type": "string",
            "description": "Axis for cylinder/cone: 'x', 'y', or 'z' (default 'z').",
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
        "Components placed on the mesh surface. Use face_hint + offset_mm "
        "to place relative to a detected face zone (preferred), or provide "
        "an absolute position as fallback. "
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
            "face_hint": {
                "type": "string",
                "description": (
                    "Which face to place on: "
                    "'top', 'bottom', 'front', 'back', 'left', 'right'. "
                    "Required when using offset_mm."
                ),
            },
            "offset_mm": {
                "type": "array",
                "description": (
                    "2D offset [u, v] in mm from the face zone center. "
                    "For top/bottom: [x_offset, y_offset]. "
                    "For front/back: [x_offset, z_offset]. "
                    "For left/right: [y_offset, z_offset]. "
                    "Use [0, 0] for dead center of the face. "
                    "The system resolves the depth coordinate automatically."
                ),
                "items": {"type": "number"},
                "minItems": 2,
                "maxItems": 2,
            },
            "position": {
                "type": "array",
                "description": (
                    "Fallback: absolute [x, y, z] in mm near the desired surface. "
                    "Prefer offset_mm + face_hint instead."
                ),
                "items": {"type": "number"},
            },
            "rotation_deg": {
                "type": "number",
                "description": "Rotation around the surface normal in degrees (default 0).",
            },
        },
        "required": ["catalog_id", "instance_id", "face_hint"],
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
            "box/cylinder/sphere/cone primitives). UI components are placed "
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

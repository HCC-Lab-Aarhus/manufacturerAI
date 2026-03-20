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
        "name": "edit_design",
        "description": (
            "Edit the design document by finding and replacing text. "
            "The design is a JSON document shown in your system prompt. "
            "Find the exact text you want to change (old_string) and "
            "provide the replacement (new_string). The result must be "
            "valid JSON. The design is validated and saved after every edit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "old_string": {
                    "type": "string",
                    "description": (
                        "The exact text to find in the design document. "
                        "Must match exactly one location, including "
                        "whitespace and indentation."
                    ),
                },
                "new_string": {
                    "type": "string",
                    "description": "The replacement text.",
                },
            },
            "required": ["old_string", "new_string"],
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

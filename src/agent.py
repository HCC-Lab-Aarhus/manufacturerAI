"""
Design agent — LLM-driven device designer using Anthropic API.

Uses Claude Sonnet 4.6 with extended thinking. The agent reads the
component catalog via tool calls, reasons about the design, and
submits a validated DesignSpec.

Usage:
    agent = DesignAgent(catalog, session)
    async for event in agent.run("Design a flashlight"):
        print(event)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, AsyncGenerator

import anthropic

from src.catalog import CatalogResult, _component_to_dict
from src.schema import DesignSpec, parse_design, validate_design, design_to_dict
from src.session import Session


# ── Config ─────────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 16384
THINKING_BUDGET = 16000
MAX_TURNS = 25


# ── Tool definitions (Anthropic format) ────────────────────────────

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
                    "type": "object",
                    "description": "Device outline polygon (clockwise winding).",
                    "properties": {
                        "vertices": {
                            "type": "array",
                            "description": "Polygon vertices as [x_mm, y_mm].",
                            "items": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 2,
                                "maxItems": 2,
                            },
                        },
                        "edges": {
                            "type": "array",
                            "description": (
                                "Edge styles. edges[i] = vertex[i] to "
                                "vertex[(i+1) % n]."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "style": {
                                        "type": "string",
                                        "enum": ["sharp", "round"],
                                    },
                                    "curve": {
                                        "type": "string",
                                        "enum": [
                                            "ease_in",
                                            "ease_out",
                                            "ease_in_out",
                                        ],
                                    },
                                    "radius_mm": {"type": "number"},
                                },
                                "required": ["style"],
                            },
                        },
                    },
                    "required": ["vertices", "edges"],
                },
                "ui_placements": {
                    "type": "array",
                    "description": (
                        "Positions for UI-facing components (buttons, LEDs, "
                        "switches). Only for ui_placement=true components."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "instance_id": {"type": "string"},
                            "x_mm": {"type": "number"},
                            "y_mm": {"type": "number"},
                        },
                        "required": ["instance_id", "x_mm", "y_mm"],
                    },
                },
            },
            "required": ["components", "nets", "outline", "ui_placements"],
        },
    },
]


# ── System prompt ──────────────────────────────────────────────────

def _catalog_summary(catalog: CatalogResult) -> str:
    """Build a compact table of all catalog components."""
    lines = [
        "| ID | Category | Name | Pins | UI | Mounting | Description |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in catalog.components:
        ui = "yes" if c.ui_placement else "no"
        desc = c.description
        if len(desc) > 60:
            desc = desc[:57] + "..."
        lines.append(
            f"| {c.id} | {c.category} | {c.name} | {len(c.pins)} "
            f"| {ui} | {c.mounting.style} | {desc} |"
        )
    return "\n".join(lines)


def _build_system_prompt(catalog: CatalogResult) -> str:
    """Build the full system prompt with catalog summary and design rules."""
    summary = _catalog_summary(catalog)

    return f"""You are a device designer. You design electronic devices that will be manufactured using a 3D printer (PLA enclosure) and a silver ink printer (conductive traces).

## Manufacturing Process
1. 3D printer prints the PLA enclosure shell with two pauses
2. Silver ink printer deposits conductive traces on the ironed floor surface (during pause 1)
3. Component insertion — pins poke through holes into the ink traces (during pause 2)
4. 3D printer resumes and seals the ceiling

The enclosure has: solid floor (2mm PLA), ink layer at Z=2mm (ironed surface), cavity for components, solid ceiling (2mm PLA). Components sit in pockets; their pins reach down through pinholes to contact the ink traces.

## Your Task
Given a user's device description, design it by:
1. Selecting components from the catalog
2. Defining electrical connections (nets) between component pins
3. Designing the device outline (polygon shape)
4. Placing UI components (buttons, LEDs, switches) within the outline

## Available Components
{summary}

Use `get_component` to read full pin/mounting details before using a component in your design.

## Design Rules

### Components
- `catalog_id`: must match an ID from the catalog
- `instance_id`: your unique name for this instance (e.g. "led_1", "r_1", "mcu_1")
- `config`: only for configurable components (e.g. resistor value)
- `mounting_style`: optional override from the component's `allowed_styles`

### Nets (electrical connections)
- Pin addressing: `"instance_id:pin_id"` (e.g. `"bat_1:V+"`, `"led_1:anode"`)
- For MCU GPIO: use `"instance_id:group_id"` (e.g. `"mcu_1:gpio"`) for dynamic pin allocation — the router picks the optimal physical pin
- Each pin may appear in at most ONE net
- Components with `internal_nets` have pins that are internally connected (e.g. ATmega VCC1↔AVCC) — connect at least one pin from each internal group to the appropriate net
- Each net must have at least 2 pins

### Outline (device shape)
- `vertices`: [[x,y], ...] coordinates in mm, clockwise winding
- `edges`: one per edge; `edges[i]` describes the edge from `vertices[i]` to `vertices[(i+1) % n]`
- Edge styles: `"sharp"` (straight) or `"round"` (curved, with `radius_mm` and `curve`: `"ease_in"`, `"ease_out"`, or `"ease_in_out"`)
- Must be a valid non-self-intersecting polygon with positive area

### UI Placements
- Only for components with `ui_placement=true` (buttons, LEDs, switches)
- Position them within the outline polygon
- Internal components (MCU, resistors, caps, battery) are auto-placed by the placer — do NOT give them UI placements

## Example: Simple Flashlight
```json
{{
  "components": [
    {{"catalog_id": "battery_holder_2xAAA", "instance_id": "bat_1"}},
    {{"catalog_id": "resistor_axial", "instance_id": "r_1", "config": {{"resistance_ohms": 150}}}},
    {{"catalog_id": "led_5mm_red", "instance_id": "led_1", "mounting_style": "top"}},
    {{"catalog_id": "tactile_button_6x6", "instance_id": "btn_1"}}
  ],
  "nets": [
    {{"id": "POWER", "pins": ["bat_1:V+", "r_1:1"]}},
    {{"id": "LED_DRIVE", "pins": ["r_1:2", "led_1:anode"]}},
    {{"id": "BTN_IN", "pins": ["btn_1:1", "bat_1:GND"]}},
    {{"id": "BTN_OUT", "pins": ["btn_1:3", "led_1:cathode"]}}
  ],
  "outline": {{
    "vertices": [[0,0], [30,0], [30,80], [0,80]],
    "edges": [
      {{"style": "sharp"}},
      {{"style": "sharp"}},
      {{"style": "round", "curve": "ease_in_out", "radius_mm": 8}},
      {{"style": "round", "curve": "ease_in_out", "radius_mm": 8}}
    ]
  }},
  "ui_placements": [
    {{"instance_id": "btn_1", "x_mm": 15, "y_mm": 25}},
    {{"instance_id": "led_1", "x_mm": 15, "y_mm": 65}}
  ]
}}
```

## Process
1. Analyze the user's request
2. Read component details with `get_component` for each component you plan to use
3. Design the circuit (components + nets)
4. Design the enclosure shape (outline)
5. Place UI components
6. Submit with `submit_design`
7. If validation fails, read the errors, fix, and resubmit"""


# ── Agent events ───────────────────────────────────────────────────

@dataclass
class AgentEvent:
    """Event yielded during agent execution, streamed to the UI."""
    type: str       # thinking | message | tool_call | tool_result | design | error | done
    data: dict

    def to_dict(self) -> dict:
        return {"type": self.type, "data": self.data}


# ── Design agent ───────────────────────────────────────────────────

class DesignAgent:
    """
    LLM-driven device designer.

    Uses Claude Sonnet 4.6 with extended thinking and the streaming API.
    Yields token-level deltas for thinking and text blocks so the UI
    updates in real time.

    The conversation loop follows the SeedGPT pattern:
      messages → streaming API call → yield deltas → accumulate
      content blocks → dispatch tool calls → repeat
    """

    def __init__(self, catalog: CatalogResult, session: Session):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required")
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.catalog = catalog
        self.session = session
        self.design: DesignSpec | None = None

        # Load existing conversation from session (for multi-turn)
        saved = session.read_artifact("conversation.json")
        self.messages: list[dict] = _sanitize_messages(saved) if isinstance(saved, list) else []

    def _save_conversation(self) -> None:
        """Persist the full message history to the session folder."""
        self.session.write_artifact("conversation.json", self.messages)

    async def run(self, user_prompt: str) -> AsyncGenerator[AgentEvent, None]:
        """
        Run the agent loop. Yields events for streaming to the UI.

        Event types with streaming deltas:
          thinking_start  — new thinking block begins
          thinking_delta  — incremental thinking text
          message_start   — new text block begins
          message_delta   — incremental text
          block_stop      — current block complete
          tool_call       — tool invocation (after stream completes)
          tool_result     — tool result
          design          — validated design spec
          error           — error message
          done            — agent finished
        """
        system = _build_system_prompt(self.catalog)
        self.messages.append({"role": "user", "content": user_prompt})

        for turn in range(MAX_TURNS):
            content_blocks: list[dict] = []
            stop_reason = None

            try:
                async with self.client.messages.stream(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    thinking={
                        "type": "enabled",
                        "budget_tokens": THINKING_BUDGET,
                    },
                    system=system,
                    tools=TOOLS,
                    messages=self.messages,
                ) as stream:
                    async for event in stream:
                        agent_event = self._handle_stream_event(event)
                        if agent_event:
                            yield agent_event

                    # After stream completes, get the full response
                    response = await stream.get_final_message()
                    content_blocks = _serialize_content(response.content)
                    stop_reason = response.stop_reason

            except anthropic.APIError as e:
                self._save_conversation()
                yield AgentEvent("error", {"message": f"API error: {e}"})
                return

            # ── Always append the assistant response to history ──
            self.messages.append({
                "role": "assistant",
                "content": content_blocks,
            })

            # ── Check stop reason ──
            if stop_reason == "max_tokens":
                self._save_conversation()
                yield AgentEvent("error", {
                    "message": "Response truncated — output too long"
                })
                return

            # ── Extract tool_use blocks ──
            tool_blocks = [
                b for b in content_blocks if b.get("type") == "tool_use"
            ]

            if not tool_blocks:
                self._save_conversation()
                yield AgentEvent("done", {})
                return

            # ── Handle each tool call ──
            tool_results: list[dict] = []
            design_submitted = False

            for block in tool_blocks:
                yield AgentEvent("tool_call", {
                    "name": block["name"],
                    "input": block["input"],
                })

                result_text, is_valid_design = self._handle_tool(
                    block["name"], block["input"]
                )

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": result_text,
                })

                yield AgentEvent("tool_result", {
                    "name": block["name"],
                    "content": result_text,
                    "is_error": not is_valid_design and block["name"] == "submit_design",
                })

                if is_valid_design:
                    design_submitted = True

            # ── Append tool results as user message ──
            self.messages.append({"role": "user", "content": tool_results})

            # ── If valid design was submitted, we're done ──
            if design_submitted:
                self._save_conversation()
                yield AgentEvent("design", {
                    "design": design_to_dict(self.design),
                })
                yield AgentEvent("done", {})
                return

        self._save_conversation()
        yield AgentEvent("error", {
            "message": f"Agent exceeded maximum turns ({MAX_TURNS})"
        })

    # ── Stream event handler ───────────────────────────────────────

    def _handle_stream_event(self, event) -> AgentEvent | None:
        """Convert an Anthropic stream event to an AgentEvent (or None)."""
        etype = event.type

        # Thinking block lifecycle
        if etype == "content_block_start":
            block = event.content_block
            if hasattr(block, "type"):
                if block.type == "thinking":
                    return AgentEvent("thinking_start", {})
                if block.type == "text":
                    return AgentEvent("message_start", {})
            return None

        if etype == "content_block_delta":
            delta = event.delta
            if hasattr(delta, "type"):
                if delta.type == "thinking_delta":
                    return AgentEvent("thinking_delta", {"text": delta.thinking})
                if delta.type == "text_delta":
                    return AgentEvent("message_delta", {"text": delta.text})
            return None

        if etype == "content_block_stop":
            # We don't know the block type from the stop event alone,
            # but the UI tracks state internally so a generic stop works.
            return AgentEvent("block_stop", {})

        return None

    # ── Tool handlers ──────────────────────────────────────────────

    def _handle_tool(self, name: str, input_data: dict) -> tuple[str, bool]:
        """
        Dispatch a tool call. Returns (result_text, is_valid_design).
        is_valid_design is True only when submit_design succeeds.
        """
        if name == "list_components":
            return _catalog_summary(self.catalog), False

        if name == "get_component":
            return self._tool_get_component(input_data), False

        if name == "submit_design":
            return self._tool_submit_design(input_data)

        return f"Unknown tool: {name}", False

    def _tool_get_component(self, input_data: dict) -> str:
        component_id = input_data.get("component_id", "")
        for c in self.catalog.components:
            if c.id == component_id:
                return json.dumps(_component_to_dict(c), indent=2)
        available = [c.id for c in self.catalog.components]
        return (
            f"Component '{component_id}' not found. "
            f"Available: {', '.join(available)}"
        )

    def _tool_submit_design(self, input_data: dict) -> tuple[str, bool]:
        """Parse, validate, and save a design. Returns (result, is_valid)."""
        try:
            spec = parse_design(input_data)
        except (KeyError, TypeError, ValueError, IndexError) as e:
            return f"Design parsing error: {e}", False

        errors = validate_design(spec, self.catalog)
        if errors:
            error_list = "\n".join(f"  - {e}" for e in errors)
            return f"Design validation failed:\n{error_list}", False

        # Valid! Save to session.
        self.design = spec
        self.session.write_artifact("design.json", input_data)
        self.session.pipeline_state["design"] = "complete"
        self.session.save()

        return "Design validated successfully! Saved to session.", True


# ── Helpers ────────────────────────────────────────────────────────

def _serialize_content(content: list) -> list[dict]:
    """
    Convert API response content blocks to serializable dicts.

    The Anthropic SDK returns pydantic model instances with extra fields
    (parsed_output, citations, caller, etc.) that the API rejects on
    re-submission.  We whitelist only the fields the API accepts per
    block type.
    """
    # Fields the API accepts for each content block type
    ALLOWED = {
        "thinking": {"type", "thinking", "signature"},
        "text":     {"type", "text"},
        "tool_use": {"type", "id", "name", "input"},
        "tool_result": {"type", "tool_use_id", "content", "is_error"},
    }

    result = []
    for block in content:
        if hasattr(block, "model_dump"):
            d = block.model_dump()
        elif isinstance(block, dict):
            d = block
        else:
            d = {"type": "text", "text": str(block)}

        allowed = ALLOWED.get(d.get("type"), set())
        if allowed:
            d = {k: v for k, v in d.items() if k in allowed}
        result.append(d)
    return result


def _sanitize_messages(messages: list[dict]) -> list[dict]:
    """
    Clean a saved conversation so every content block only contains
    fields the Anthropic API accepts (strips parsed_output, citations,
    caller, etc. that model_dump() may have added).
    """
    clean = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            msg = {**msg, "content": _serialize_content(content)}
        clean.append(msg)
    return clean

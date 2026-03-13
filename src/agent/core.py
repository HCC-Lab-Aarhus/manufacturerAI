"""Agent core — shared loop, DesignAgent, and CircuitAgent."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import AsyncGenerator

import anthropic

from src.catalog import CatalogResult, _component_to_dict
from src.pipeline.config import get_printer
from src.pipeline.design import parse_design, validate_design, design_to_dict
from src.session import Session

from .config import MODEL, MAX_TOKENS, THINKING_BUDGET, MAX_TURNS, TOKEN_BUDGET
from .tools import DESIGN_TOOLS, CIRCUIT_TOOLS
from .prompt import build_design_prompt, build_circuit_prompt, build_circuit_user_prompt, catalog_summary
from .messages import serialize_content, sanitize_messages, prune_messages


# ── Agent events ───────────────────────────────────────────────────

@dataclass
class AgentEvent:
    """Event yielded during agent execution, streamed to the UI."""
    type: str
    data: dict

    def to_dict(self) -> dict:
        return {"type": self.type, "data": self.data}


# ── Base agent ─────────────────────────────────────────────────────

class _BaseAgent:
    """Shared agent loop for both design and circuit agents.

    Subclasses provide tools, system prompt, and tool handlers.
    The conversation loop, streaming, and persistence are identical.
    """

    conversation_file: str = ""  # subclasses must override

    def __init__(self, catalog: CatalogResult, session: Session):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required")
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.catalog = catalog
        self.session = session

        saved = session.read_artifact(self.conversation_file)
        self.messages: list[dict] = sanitize_messages(saved) if isinstance(saved, list) else []

    def _save_conversation(self) -> None:
        self.session.write_artifact(self.conversation_file, self.messages)

    def _get_tools(self) -> list[dict]:
        raise NotImplementedError

    def _get_system_prompt(self) -> str:
        raise NotImplementedError

    def _handle_tool(self, name: str, input_data: dict) -> tuple[str, bool]:
        """Dispatch a tool call. Returns (result_text, is_terminal).

        is_terminal=True means the agent should stop after this tool.
        """
        raise NotImplementedError

    def _terminal_event(self, tool_name: str, input_data: dict) -> AgentEvent | None:
        """Return the event to emit when a terminal tool succeeds, or None."""
        return None

    async def run(self, user_prompt: str) -> AsyncGenerator[AgentEvent, None]:
        """Run the agent loop. Yields events for streaming to the UI."""
        system = self._get_system_prompt()
        tools = self._get_tools()
        self.messages.append({"role": "user", "content": user_prompt})

        for turn in range(MAX_TURNS):
            content_blocks: list[dict] = []
            stop_reason = None
            api_messages = prune_messages(self.messages)

            try:
                async with self.client.messages.stream(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    thinking={
                        "type": "enabled",
                        "budget_tokens": THINKING_BUDGET,
                    },
                    system=system,
                    tools=tools,
                    messages=api_messages,
                ) as stream:
                    async for event in stream:
                        agent_event = self._handle_stream_event(event)
                        if agent_event:
                            yield agent_event

                    response = await stream.get_final_message()
                    content_blocks = serialize_content(response.content)
                    stop_reason = response.stop_reason

            except anthropic.APIError as e:
                self._save_conversation()
                yield AgentEvent("error", {"message": f"API error: {e}"})
                return

            self.messages.append({
                "role": "assistant",
                "content": content_blocks,
            })

            # Token counting (best-effort)
            try:
                token_count = await self.client.messages.count_tokens(
                    model=MODEL,
                    messages=api_messages,
                    system=system,
                    tools=tools,
                    thinking={
                        "type": "enabled",
                        "budget_tokens": THINKING_BUDGET,
                    },
                )
                yield AgentEvent("token_usage", {
                    "input_tokens": token_count.input_tokens,
                    "budget": TOKEN_BUDGET,
                })
            except Exception:
                pass

            if stop_reason == "max_tokens":
                self._save_conversation()
                yield AgentEvent("error", {
                    "message": "Response truncated — output too long",
                })
                return

            tool_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]
            if not tool_blocks:
                self._save_conversation()
                yield AgentEvent("done", {})
                return

            tool_results: list[dict] = []
            terminal_event = None

            for block in tool_blocks:
                yield AgentEvent("tool_call", {
                    "name": block["name"],
                    "input": block["input"],
                })

                result_text, is_terminal = self._handle_tool(block["name"], block["input"])

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": result_text,
                })

                yield AgentEvent("tool_result", {
                    "name": block["name"],
                    "content": result_text,
                    "is_error": not is_terminal and block["name"] in ("submit_design", "submit_circuit"),
                })

                if is_terminal:
                    terminal_event = self._terminal_event(block["name"], block["input"])

            self.messages.append({"role": "user", "content": tool_results})

            if terminal_event:
                self._save_conversation()
                yield terminal_event
                yield AgentEvent("done", {})
                return

        self._save_conversation()
        yield AgentEvent("error", {
            "message": f"Agent exceeded maximum turns ({MAX_TURNS})",
        })

    def _handle_stream_event(self, event) -> AgentEvent | None:
        etype = event.type
        if etype == "content_block_start":
            block = event.content_block
            if hasattr(block, "type"):
                if block.type == "thinking":
                    return AgentEvent("thinking_start", {})
                if block.type == "text":
                    return AgentEvent("message_start", {})
        elif etype == "content_block_delta":
            delta = event.delta
            if hasattr(delta, "type"):
                if delta.type == "thinking_delta":
                    return AgentEvent("thinking_delta", {"text": delta.thinking})
                if delta.type == "text_delta":
                    return AgentEvent("message_delta", {"text": delta.text})
        elif etype == "content_block_stop":
            return AgentEvent("block_stop", {})
        return None

    def _tool_list_components(self) -> str:
        return catalog_summary(self.catalog)

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


# ── Design agent ───────────────────────────────────────────────────

class DesignAgent(_BaseAgent):
    """Physical device designer — outline, enclosure, UI placements."""

    conversation_file = "design_conversation.json"

    def __init__(self, catalog: CatalogResult, session: Session):
        super().__init__(catalog, session)
        self._feasibility_attempts: int = 0

    def _get_tools(self) -> list[dict]:
        return DESIGN_TOOLS

    def _get_system_prompt(self) -> str:
        printer = get_printer(self.session.printer_id)
        return build_design_prompt(self.catalog, printer=printer)

    def _handle_tool(self, name: str, input_data: dict) -> tuple[str, bool]:
        if name == "list_components":
            return self._tool_list_components(), False
        if name == "get_component":
            return self._tool_get_component(input_data), False
        if name == "submit_design":
            return self._tool_submit_design(input_data)
        if name == "check_placement_feasibility":
            return self._tool_check_feasibility(input_data), False
        return f"Unknown tool: {name}", False

    def _terminal_event(self, tool_name: str, input_data: dict) -> AgentEvent | None:
        if tool_name == "submit_design":
            return AgentEvent("design", {"design": input_data})
        return None

    _MAX_FEASIBILITY_ATTEMPTS = 3

    def _tool_check_feasibility(self, input_data: dict) -> str:
        from src.pipeline.placer.feasibility import run_feasibility_check
        self._feasibility_attempts += 1
        if self._feasibility_attempts > self._MAX_FEASIBILITY_ATTEMPTS:
            return (
                f"FEASIBILITY CHECK LIMIT REACHED ({self._MAX_FEASIBILITY_ATTEMPTS} attempts). "
                f"You have been unable to find a valid layout automatically. "
                f"Do NOT call check_placement_feasibility or submit_design again. "
                f"Instead, respond to the user explaining: which component(s) cannot "
                f"be placed, why (which UI components are blocking them), and what "
                f"the user should change (e.g. larger outline, fewer UI components, "
                f"different arrangement). Ask the user for guidance before retrying."
            )
        remaining = self._MAX_FEASIBILITY_ATTEMPTS - self._feasibility_attempts
        report = run_feasibility_check(
            self.catalog,
            input_data.get("components", []),
            input_data.get("outline", []),
            input_data.get("ui_placements", []),
            enclosure_raw=input_data.get("enclosure"),
        )
        if remaining == 0:
            report += (
                f"\n\nWARNING: This was your last allowed feasibility check. "
                f"If any component still shows [FAIL], do NOT call this tool again. "
                f"Either fix the issue and call submit_design directly, or stop "
                f"and explain the problem to the user."
            )
        else:
            report += f"\n\n({remaining} feasibility check(s) remaining before limit)"
        return report

    def _tool_submit_design(self, input_data: dict) -> tuple[str, bool]:
        """Validate and save a physical design (no components/nets)."""
        # Build a full design dict for parse_design — it expects components
        # and nets, but the design agent doesn't provide them any more.
        # We add empty lists so parsing doesn't fail.
        full_data = {**input_data}
        full_data.setdefault("components", [])
        full_data.setdefault("nets", [])

        # Convert ui_placements into components for parse_design compatibility
        ui_components = []
        for p in input_data.get("ui_placements", []):
            ui_components.append({
                "catalog_id": p.get("catalog_id", p["instance_id"]),
                "instance_id": p["instance_id"],
            })
        full_data["components"] = ui_components

        try:
            spec = parse_design(full_data)
        except (KeyError, TypeError, ValueError, IndexError) as e:
            return f"Design parsing error: {e}", False

        printer = get_printer(self.session.printer_id)
        errors = validate_design(spec, self.catalog, printer=printer)
        if errors:
            error_list = "\n".join(f"  - {e}" for e in errors)
            return f"Design validation failed:\n{error_list}", False

        # Save the clean design data (without synthesized components)
        self.session.write_artifact("design.json", input_data)
        self.session.pipeline_state["design"] = "complete"

        # Invalidate downstream: circuit, placement, and routing depend on design
        for step in ("circuit", "placement", "routing"):
            artifact = f"{step}.json"
            if self.session.has_artifact(artifact):
                self.session.delete_artifact(artifact)
            self.session.pipeline_state.pop(step, None)
        # Also clear circuit conversation
        if self.session.has_artifact("circuit_conversation.json"):
            self.session.delete_artifact("circuit_conversation.json")
        self.session.save()

        return "Design validated successfully! Saved to session.", True


# ── Circuit agent ──────────────────────────────────────────────────

class CircuitAgent(_BaseAgent):
    """Electrical design — component selection and net topology."""

    conversation_file = "circuit_conversation.json"

    def _get_tools(self) -> list[dict]:
        return CIRCUIT_TOOLS

    def _get_system_prompt(self) -> str:
        return build_circuit_prompt(self.catalog)

    def _handle_tool(self, name: str, input_data: dict) -> tuple[str, bool]:
        if name == "list_components":
            return self._tool_list_components(), False
        if name == "get_component":
            return self._tool_get_component(input_data), False
        if name == "submit_circuit":
            return self._tool_submit_circuit(input_data)
        return f"Unknown tool: {name}", False

    def _terminal_event(self, tool_name: str, input_data: dict) -> AgentEvent | None:
        if tool_name == "submit_circuit":
            return AgentEvent("circuit", {"circuit": input_data})
        return None

    def _tool_submit_circuit(self, input_data: dict) -> tuple[str, bool]:
        """Validate and save a circuit design (components + nets)."""
        # Read the design to get UI placements for validation context
        design_data = self.session.read_artifact("design.json")
        if not design_data:
            return "No design.json found — run the design agent first.", False

        ui_instance_ids = {
            p["instance_id"]
            for p in design_data.get("ui_placements", [])
        }

        components = input_data.get("components", [])
        nets = input_data.get("nets", [])

        # Basic validation
        errors: list[str] = []

        # Check that all UI components are present
        circuit_instance_ids = {c["instance_id"] for c in components}
        missing_ui = ui_instance_ids - circuit_instance_ids
        if missing_ui:
            errors.append(
                f"Missing UI components from design: {', '.join(sorted(missing_ui))}. "
                f"You must include all placed UI components."
            )

        # Check all components have catalog entries
        catalog_ids = {c.id for c in self.catalog.components}
        for comp in components:
            if comp.get("catalog_id") not in catalog_ids:
                errors.append(
                    f"Component '{comp.get('instance_id')}' references unknown "
                    f"catalog_id '{comp.get('catalog_id')}'"
                )

        # Check nets have at least 2 pins
        for net in nets:
            if len(net.get("pins", [])) < 2:
                errors.append(f"Net '{net.get('id')}' has fewer than 2 pins")

        # Check pin references point to existing instances
        for net in nets:
            for pin_ref in net.get("pins", []):
                instance = pin_ref.split(":")[0] if ":" in pin_ref else pin_ref
                if instance not in circuit_instance_ids:
                    errors.append(
                        f"Net '{net.get('id')}' references unknown instance "
                        f"'{instance}' in pin '{pin_ref}'"
                    )

        if errors:
            error_list = "\n".join(f"  - {e}" for e in errors)
            return f"Circuit validation failed:\n{error_list}", False

        # Mark which components are UI-placed
        for comp in components:
            comp["ui_placement"] = comp["instance_id"] in ui_instance_ids

        # Save circuit data
        self.session.write_artifact("circuit.json", input_data)
        self.session.pipeline_state["circuit"] = "complete"

        # Invalidate downstream
        for step in ("placement", "routing"):
            artifact = f"{step}.json"
            if self.session.has_artifact(artifact):
                self.session.delete_artifact(artifact)
            self.session.pipeline_state.pop(step, None)
        self.session.save()

        return "Circuit validated successfully! Saved to session.", True

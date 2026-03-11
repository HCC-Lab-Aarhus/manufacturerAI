"""Design agent — LLM-driven device designer core loop."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import AsyncGenerator

import anthropic

from src.catalog import CatalogResult, _component_to_dict
from src.pipeline.config import get_printer
from src.pipeline.design.models3d import DesignSpec3D
from src.pipeline.design.parsing3d import parse_design_3d
from src.pipeline.design.validation3d import validate_design_3d
from src.pipeline.design.serialization3d import design3d_to_dict
from src.session import Session

from .config import MODEL, MAX_TOKENS, THINKING_BUDGET, MAX_TURNS, TOKEN_BUDGET
from .tools import TOOLS
from .prompt import _build_system_prompt, _catalog_summary
from .messages import _serialize_content, _sanitize_messages, _prune_messages


# ── Snap feedback helpers ──────────────────────────────────────────

_HINT_VECTORS = {
    "top": (0.0, 0.0, 1.0),
    "bottom": (0.0, 0.0, -1.0),
    "front": (0.0, -1.0, 0.0),
    "back": (0.0, 1.0, 0.0),
    "left": (-1.0, 0.0, 0.0),
    "right": (1.0, 0.0, 0.0),
}


def _snap_drift(requested, snapped) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(requested, snapped)))


def _normal_alignment(face_hint: str | None, normal: tuple) -> float | None:
    if not face_hint or face_hint not in _HINT_VECTORS:
        return None
    expected = _HINT_VECTORS[face_hint]
    return sum(a * b for a, b in zip(expected, normal))


def _describe_normal(normal: tuple) -> str:
    nx, ny, nz = normal
    parts: list[str] = []
    if abs(nz) > 0.4:
        parts.append("up" if nz > 0 else "down")
    if abs(ny) > 0.4:
        parts.append("forward" if ny < 0 else "back")
    if abs(nx) > 0.4:
        parts.append("right" if nx > 0 else "left")
    return "-".join(parts) if parts else "oblique"


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
        self.design: DesignSpec3D | None = None

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
        printer = get_printer(self.session.printer_id)
        system = _build_system_prompt(self.catalog, printer=printer)
        self.messages.append({"role": "user", "content": user_prompt})

        for turn in range(MAX_TURNS):
            content_blocks: list[dict] = []
            stop_reason = None
            api_messages = _prune_messages(self.messages)

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
                    messages=api_messages,
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

            # ── Count conversation tokens (free API) ──
            try:
                token_count = await self.client.messages.count_tokens(
                    model=MODEL,
                    messages=api_messages,
                    system=system,
                    tools=TOOLS,
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
                pass  # token counting is best-effort

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
                    "design": design3d_to_dict(self.design),
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
            return AgentEvent("block_stop", {})

        return None

    # ── Tool handlers ──────────────────────────────────────────────

    def _handle_tool(self, name: str, input_data: dict) -> tuple[str, bool]:
        """Dispatch a tool call. Returns (result_text, is_valid_design)."""
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
        """Parse, validate, generate mesh, snap placements, and save a design."""
        try:
            spec = parse_design_3d(input_data)
        except (KeyError, TypeError, ValueError, IndexError) as e:
            return f"Design parsing error: {e}", False

        errors = validate_design_3d(spec, self.catalog)
        if errors:
            error_list = "\n".join(f"  - {e}" for e in errors)
            return f"Design validation failed:\n{error_list}", False

        # Generate the mesh from the CSG tree
        try:
            from src.pipeline.mesh.csg import evaluate_csg, mesh_to_glb_bytes
            mesh = evaluate_csg(spec.shape)
        except Exception as e:
            return f"CSG mesh generation failed: {e}", False

        # Detect placement zones and resolve face-relative offsets
        try:
            from src.pipeline.mesh.surface import snap_all, find_placement_zones, resolve_face_offset
            zones = find_placement_zones(mesh)
            for p in spec.surface_placements:
                if p.offset_mm is not None and p.face_hint and p.face_hint in zones:
                    p.position = resolve_face_offset(zones, p.face_hint, p.offset_mm)
        except Exception as e:
            return f"Zone detection / offset resolution failed: {e}", False

        # Snap surface placements to the mesh
        try:
            snap_all(mesh, spec.surface_placements)
        except Exception as e:
            return f"Surface placement snapping failed: {e}", False

        # Build zone summary for feedback
        zone_lines = []
        for face, z in sorted(zones.items()):
            zone_lines.append(
                f"  {face}: center={z['center']}, bounds={z['bounds']}, depth={z['depth']} (axes: {z['axes']})"
            )
        zone_report = "\n".join(zone_lines) if zone_lines else "  (no flat zones detected)"

        # Build snap report and check for placement problems
        snap_lines = []
        has_placement_errors = False
        for p in spec.surface_placements:
            if p.snapped_position is None or p.surface_normal is None:
                snap_lines.append(f"  {p.instance_id}: snap data missing")
                continue
            drift = _snap_drift(p.position, p.snapped_position)
            alignment = _normal_alignment(p.face_hint, p.surface_normal)
            normal_desc = _describe_normal(p.surface_normal)
            pos_str = "[{:.1f}, {:.1f}, {:.1f}]".format(*p.snapped_position)
            line = f"  {p.instance_id}: snapped to {pos_str}, normal={normal_desc}, drift={drift:.1f}mm"
            if alignment is not None and alignment < 0.3:
                line += " !! surface faces wrong direction for '{}' placement".format(p.face_hint)
                has_placement_errors = True
            elif alignment is not None and alignment < 0.6:
                line += " ! surface is steep for '{}' — consider flattening with a box subtraction".format(p.face_hint)
            if drift > 15:
                line += " !! large drift from requested position"
                has_placement_errors = True
            snap_lines.append(line)
        snap_report = "\n".join(snap_lines)

        if has_placement_errors:
            return (
                f"Design mesh generated ({len(mesh.vertices)} verts, {len(mesh.faces)} faces) "
                f"but some surface placements landed on unsuitable surfaces:\n\n{snap_report}\n\n"
                f"Detected placement zones:\n{zone_report}\n\n"
                f"Reshape so components land on appropriate surfaces. "
                f"Use offset_mm=[u, v] relative to a zone center — the system resolves the depth automatically.",
                False,
            )

        glb_bytes = mesh_to_glb_bytes(mesh)
        self.session.write_artifact_bytes("design_mesh.glb", glb_bytes)

        # Save the design spec
        self.design = spec
        save_data = design3d_to_dict(spec)
        self.session.write_artifact("design.json", save_data)
        self.session.pipeline_state["design"] = "complete"
        for step in ("placement", "routing"):
            artifact = f"{step}.json"
            if self.session.has_artifact(artifact):
                self.session.delete_artifact(artifact)
            self.session.pipeline_state.pop(step, None)
        self.session.save()

        return (
            f"Design validated and mesh generated successfully! "
            f"Mesh: {len(mesh.vertices)} vertices, {len(mesh.faces)} faces.\n\n"
            f"Placement zones:\n{zone_report}\n\n"
            f"Surface snap report:\n{snap_report}",
            True,
        )

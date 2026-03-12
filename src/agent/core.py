"""Agent core loop — DesignAgent (step 1, physical) and CircuitAgent (step 2, electrical)."""

from __future__ import annotations

import json
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
from .tools import CIRCUIT_TOOLS, DESIGN_TOOLS
from .prompt import build_circuit_prompt, build_design_prompt, catalog_summary
from .messages import serialize_content, sanitize_messages, prune_messages


# ── Snap feedback helpers ──────────────────────────────────────────

_HINT_VECTORS = {
    "top": (0.0, 0.0, 1.0),
    "bottom": (0.0, 0.0, -1.0),
    "front": (0.0, -1.0, 0.0),
    "back": (0.0, 1.0, 0.0),
    "left": (-1.0, 0.0, 0.0),
    "right": (1.0, 0.0, 0.0),
}


def _normal_alignment(face: str, normal: tuple) -> float | None:
    if face not in _HINT_VECTORS:
        return None
    expected = _HINT_VECTORS[face]
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
    type: str
    data: dict

    def to_dict(self) -> dict:
        return {"type": self.type, "data": self.data}


# ── Base agent loop ────────────────────────────────────────────────

class _BaseAgent:
    """Shared streaming agent loop used by both CircuitAgent and DesignAgent."""

    def __init__(self, session: Session, *, conversation_file: str):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required")
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.session = session
        self._conversation_file = conversation_file

        saved = session.read_artifact(conversation_file)
        self.messages: list[dict] = sanitize_messages(saved) if isinstance(saved, list) else []

    def _save_conversation(self) -> None:
        self.session.write_artifact(self._conversation_file, self.messages)

    # Subclasses must implement these:
    def _get_system_prompt(self) -> str:
        raise NotImplementedError

    def _get_tools(self) -> list[dict]:
        raise NotImplementedError

    def _handle_tool(self, name: str, input_data: dict) -> tuple[str, bool]:
        """Dispatch a tool call. Returns (result_text, is_success)."""
        raise NotImplementedError

    def _submit_event_type(self) -> str:
        """SSE event name emitted on successful submission."""
        raise NotImplementedError

    def _get_submit_event_data(self) -> dict:
        """Data payload for the successful submission event."""
        raise NotImplementedError

    def _submit_tool_name(self) -> str:
        """Name of the tool that triggers the 'done' flow."""
        raise NotImplementedError

    async def run(self, user_prompt: str) -> AsyncGenerator[AgentEvent, None]:
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
                    "message": "Response truncated — output too long"
                })
                return

            tool_blocks = [
                b for b in content_blocks if b.get("type") == "tool_use"
            ]

            if not tool_blocks:
                self._save_conversation()
                yield AgentEvent("done", {})
                return

            tool_results: list[dict] = []
            submitted = False

            for block in tool_blocks:
                yield AgentEvent("tool_call", {
                    "name": block["name"],
                    "input": block["input"],
                })

                result_text, is_success = self._handle_tool(
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
                    "is_error": not is_success and block["name"] == self._submit_tool_name(),
                })

                if is_success:
                    submitted = True

            self.messages.append({"role": "user", "content": tool_results})

            if submitted:
                self._save_conversation()
                yield AgentEvent(self._submit_event_type(), self._get_submit_event_data())
                yield AgentEvent("done", {})
                return

        self._save_conversation()
        yield AgentEvent("error", {
            "message": f"Agent exceeded maximum turns ({MAX_TURNS})"
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


# ── Circuit agent (electrical engineer) ────────────────────────────

class CircuitAgent(_BaseAgent):
    """Step 2: Selects all components (UI + internal) and designs nets. Runs autonomously."""

    def __init__(self, catalog: CatalogResult, session: Session):
        super().__init__(session, conversation_file="circuit_conversation.json")
        self.catalog = catalog
        self.circuit_data: dict | None = None

    def _get_system_prompt(self) -> str:
        return build_circuit_prompt(self.catalog)

    def _get_tools(self) -> list[dict]:
        return CIRCUIT_TOOLS

    def _submit_tool_name(self) -> str:
        return "submit_circuit"

    def _submit_event_type(self) -> str:
        return "circuit"

    def _get_submit_event_data(self) -> dict:
        return {"circuit": self.circuit_data}

    def _handle_tool(self, name: str, input_data: dict) -> tuple[str, bool]:
        if name == "list_components":
            return catalog_summary(self.catalog), False

        if name == "get_component":
            return self._tool_get_component(input_data), False

        if name == "submit_circuit":
            return self._tool_submit_circuit(input_data)

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

    def _tool_submit_circuit(self, input_data: dict) -> tuple[str, bool]:
        """Validate components + nets and save circuit.json."""
        from src.pipeline.design.models import ComponentInstance, Net

        components_raw = input_data.get("components", [])
        nets_raw = input_data.get("nets", [])

        errors: list[str] = []
        catalog_map = {c.id: c for c in self.catalog.components}

        # Validate components
        seen_ids: set[str] = set()
        for ci in components_raw:
            cid = ci.get("catalog_id", "")
            iid = ci.get("instance_id", "")
            if cid not in catalog_map:
                errors.append(f"Component '{iid}': unknown catalog_id '{cid}'")
            if iid in seen_ids:
                errors.append(f"Duplicate instance_id '{iid}'")
            seen_ids.add(iid)
            ms = ci.get("mounting_style")
            if ms and cid in catalog_map:
                cat = catalog_map[cid]
                if ms not in cat.mounting.allowed_styles:
                    errors.append(
                        f"Component '{iid}': mounting_style '{ms}' "
                        f"not in allowed_styles {cat.mounting.allowed_styles}"
                    )

        instance_to_catalog = {}
        for ci in components_raw:
            cid = ci.get("catalog_id", "")
            if cid in catalog_map:
                instance_to_catalog[ci["instance_id"]] = catalog_map[cid]

        # Validate nets
        for net in nets_raw:
            nid = net.get("id", "")
            pins = net.get("pins", [])
            if len(pins) < 2:
                errors.append(f"Net '{nid}': must have at least 2 pins")
            for pin_ref in pins:
                if ":" not in pin_ref:
                    errors.append(f"Net '{nid}': invalid pin reference '{pin_ref}'")
                    continue
                iid, pid = pin_ref.split(":", 1)
                if iid not in seen_ids:
                    errors.append(f"Net '{nid}': unknown instance '{iid}' in '{pin_ref}'")
                    continue
                if iid not in instance_to_catalog:
                    continue
                cat = instance_to_catalog[iid]
                pin_ids = {p.id for p in cat.pins}
                group_ids = {g.id for g in cat.pin_groups} if cat.pin_groups else set()
                if pid not in pin_ids and pid not in group_ids:
                    errors.append(
                        f"Net '{nid}': unknown pin/group '{pid}' on '{iid}' (catalog: {cat.id})"
                    )

        if errors:
            error_list = "\n".join(f"  - {e}" for e in errors)
            return f"Circuit validation failed:\n{error_list}", False

        # Enrich with catalog metadata for the design agent
        enriched = []
        for ci in components_raw:
            cid = ci.get("catalog_id", "")
            entry = {
                "catalog_id": cid,
                "instance_id": ci["instance_id"],
            }
            if ci.get("config"):
                entry["config"] = ci["config"]
            if ci.get("mounting_style"):
                entry["mounting_style"] = ci["mounting_style"]
            if cid in catalog_map:
                cat = catalog_map[cid]
                entry["ui_placement"] = cat.ui_placement
                entry["name"] = cat.name
                entry["description"] = cat.description
            enriched.append(entry)

        circuit = {
            "components": enriched,
            "nets": nets_raw,
        }

        self.circuit_data = circuit
        self.session.write_artifact("circuit.json", circuit)
        self.session.pipeline_state["circuit"] = "complete"
        for step in ("placement", "routing"):
            artifact = f"{step}.json"
            if self.session.has_artifact(artifact):
                self.session.delete_artifact(artifact)
            self.session.pipeline_state.pop(step, None)
        self.session.save()

        comp_count = len(enriched)
        net_count = len(nets_raw)
        ui_count = sum(1 for c in enriched if c.get("ui_placement"))
        return (
            f"Circuit validated! {comp_count} components ({ui_count} UI), {net_count} nets.",
            True,
        )


# ── Design agent (product designer) ───────────────────────────────

class DesignAgent(_BaseAgent):
    """Step 1: Sculpts the device shape, selects and places UI components, writes device description."""

    def __init__(self, catalog: CatalogResult, session: Session):
        super().__init__(session, conversation_file="design_conversation.json")
        self.catalog = catalog
        self.design: DesignSpec3D | None = None

    def _get_system_prompt(self) -> str:
        printer = get_printer(self.session.printer_id)
        return build_design_prompt(self.catalog, printer=printer)

    def _get_tools(self) -> list[dict]:
        return DESIGN_TOOLS

    def _submit_tool_name(self) -> str:
        return "submit_design"

    def _submit_event_type(self) -> str:
        return "design"

    def _get_submit_event_data(self) -> dict:
        return {"design": design3d_to_dict(self.design)}

    def _handle_tool(self, name: str, input_data: dict) -> tuple[str, bool]:
        if name == "list_components":
            return catalog_summary(self.catalog), False

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
        # Build component list from surface placements' catalog_ids
        components_raw = []
        for sp in input_data.get("surface_placements", []):
            components_raw.append({
                "catalog_id": sp.get("catalog_id", ""),
                "instance_id": sp["instance_id"],
            })

        merged = {
            "components": components_raw,
            "nets": [],
            "shape": input_data.get("shape", {}),
            "surface_placements": input_data.get("surface_placements", []),
        }

        try:
            spec = parse_design_3d(merged)
        except (KeyError, TypeError, ValueError, IndexError) as e:
            return f"Design parsing error: {e}", False

        errors = validate_design_3d(spec, self.catalog)
        if errors:
            error_list = "\n".join(f"  - {e}" for e in errors)
            return f"Design validation failed:\n{error_list}", False

        try:
            from src.pipeline.mesh.csg import evaluate_csg, mesh_to_glb_bytes
            mesh = evaluate_csg(spec.shape)
        except Exception as e:
            return f"CSG mesh generation failed: {e}", False

        try:
            from src.pipeline.mesh.surface import project_all
            project_all(mesh, spec.surface_placements)
        except Exception as e:
            return f"Surface projection failed: {e}", False

        bbox = mesh.bounds
        bbox_str = "[{:.1f}, {:.1f}, {:.1f}] to [{:.1f}, {:.1f}, {:.1f}]".format(
            *bbox[0], *bbox[1]
        )

        snap_lines = []
        has_placement_errors = False
        for p in spec.surface_placements:
            if p.snapped_position is None or p.surface_normal is None:
                snap_lines.append(f"  {p.instance_id}: projection failed")
                has_placement_errors = True
                continue
            alignment = _normal_alignment(p.face, p.surface_normal)
            normal_desc = _describe_normal(p.surface_normal)
            pos_str = "[{:.1f}, {:.1f}, {:.1f}]".format(*p.snapped_position)
            line = f"  {p.instance_id}: placed at {pos_str}, normal={normal_desc}"
            if alignment is not None and alignment < 0.3:
                line += f" !! surface faces wrong direction for '{p.face}'"
                has_placement_errors = True
            elif alignment is not None and alignment < 0.6:
                line += f" ! surface is steep for '{p.face}' \u2014 consider flattening with a box subtraction"
            snap_lines.append(line)
        snap_report = "\n".join(snap_lines)

        if has_placement_errors:
            return (
                f"Design mesh generated ({len(mesh.vertices)} verts, {len(mesh.faces)} faces, "
                f"bbox {bbox_str}) but some surface placements failed:\n\n{snap_report}\n\n"
                f"Adjust the 'at' coordinates or reshape so the surface exists under the target face.",
                False,
            )

        glb_bytes = mesh_to_glb_bytes(mesh)
        self.session.write_artifact_bytes("design_mesh.glb", glb_bytes)

        self.design = spec
        save_data = design3d_to_dict(spec)
        save_data["device_description"] = input_data.get("device_description", "")
        # Preserve catalog_id in surface_placements for the circuit agent
        sp_catalog_map = {
            sp["instance_id"]: sp.get("catalog_id", "")
            for sp in input_data.get("surface_placements", [])
        }
        for sp in save_data.get("surface_placements", []):
            sp["catalog_id"] = sp_catalog_map.get(sp["instance_id"], "")

        self.session.write_artifact("design.json", save_data)
        self.session.pipeline_state["design"] = "complete"
        for step in ("circuit", "placement", "routing"):
            artifact = f"{step}.json"
            if self.session.has_artifact(artifact):
                self.session.delete_artifact(artifact)
            self.session.pipeline_state.pop(step, None)
        self.session.save()

        return (
            f"Design validated and mesh generated successfully! "
            f"Mesh: {len(mesh.vertices)} vertices, {len(mesh.faces)} faces, "
            f"bbox {bbox_str}.\n\n"
            f"Surface placement report:\n{snap_report}",
            True,
        )

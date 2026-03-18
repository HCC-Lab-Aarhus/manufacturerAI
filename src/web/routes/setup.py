"""Setup (firmware generation) agent route — SSE streaming like design/circuit."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from src.agent import SetupAgent
from src.catalog import _component_to_dict
from src.pipeline.firmware.context_builder import build_firmware_context
from src.pipeline.firmware.sim_config import generate_sim_config, write_sim_config
from src.web.routes._deps import (
    get_catalog, load_session_or_404,
)
from src.web.tasks import AgentTask, get_agent_task, set_agent_task

log = logging.getLogger(__name__)

router = APIRouter(tags=["setup"])


def _build_catalog_map(catalog) -> dict[str, dict]:
    """Build a dict of catalog_id → component dict for the context builder."""
    return {c.id: _component_to_dict(c) for c in catalog.components}


async def _run_setup_background(sid: str, task: AgentTask):
    """Run the setup agent in the background, accumulating events in *task*."""
    try:
        sess = load_session_or_404(sid)
        cat = get_catalog()

        # Load required artifacts
        design = sess.read_artifact("design.json")
        circuit = sess.read_artifact("circuit.json")
        routing = sess.read_artifact("routing.json")

        if not design or not circuit or not routing:
            task.append_event("error", {"message": "Missing required artifacts (design, circuit, or routing)"})
            task.finish("error", error="Missing artifacts")
            return

        # Build the firmware context document
        catalog_map = _build_catalog_map(cat)
        firmware_context = build_firmware_context(design, circuit, routing, catalog_map)

        agent = SetupAgent(cat, sess, firmware_context)

        prompt = (
            "Write the firmware for this device. Read the device context in your "
            "system prompt carefully, then write a complete Arduino sketch that "
            "implements the described behavior. Call submit_firmware when ready."
        )

        async for event in agent.run(prompt, cancel_event=task.cancel_event):
            if event.type == "checkpoint":
                task.last_save_cursor = len(task.events)
                continue
            task.append_event(event.type, event.data or {})

        # After agent finishes, generate sim_config if firmware compiled
        setup_state = sess.pipeline_state.get("setup")
        if setup_state in ("complete", "compile_failed"):
            elf_path = sess.path / "firmware_build" / "firmware.ino.elf"
            elf_str = str(elf_path) if elf_path.exists() else None
            try:
                sim_cfg = generate_sim_config(circuit, routing, catalog_map, elf_str)
                write_sim_config(sim_cfg, sess.path)
                task.append_event("sim_config", {"ready": True})
            except Exception as e:
                log.warning("sim_config generation failed: %s", e)

        task.finish("done")
    except asyncio.CancelledError:
        task.finish("done", error="Cancelled")
    except Exception as e:
        log.exception("Setup agent background error")
        task.append_event("error", {"message": str(e)})
        task.finish("error", error=str(e))


@router.post("/sessions/{sid}/setup")
async def run_setup(sid: str, request: Request):
    """Start the setup (firmware generation) agent."""
    sess = load_session_or_404(sid)

    # Check prerequisites
    if not sess.has_artifact("routing.json"):
        raise HTTPException(400, "No routing.json — run the router first")
    if not sess.has_artifact("circuit.json"):
        raise HTTPException(400, "No circuit.json — run the circuit agent first")
    if not sess.has_artifact("design.json"):
        raise HTTPException(400, "No design.json — run the design agent first")

    # Check for MCU in circuit
    circuit = sess.read_artifact("circuit.json")
    has_mcu = any(
        "atmega" in c.get("catalog_id", "").lower() or "mcu" in c.get("instance_id", "").lower()
        for c in circuit.get("components", [])
    )
    if not has_mcu:
        return {"status": "skipped", "message": "No MCU in circuit — firmware not needed"}

    existing = get_agent_task(sid, "setup")
    if existing and existing.status == "running":
        raise HTTPException(409, "Setup agent is already running")

    task = AgentTask()
    set_agent_task(sid, "setup", task)
    task.asyncio_task = asyncio.create_task(
        _run_setup_background(sid, task)
    )
    return {"status": "running"}


@router.get("/sessions/{sid}/setup/stream")
async def stream_setup_events(sid: str, after: int = Query(0)):
    """SSE endpoint: yields buffered events starting at *after*."""
    task = get_agent_task(sid, "setup")
    if not task:
        raise HTTPException(404, "No setup agent task")

    async def event_stream():
        cursor = after
        while True:
            while cursor < len(task.events):
                ev = task.events[cursor]
                data = json.dumps(ev["data"]) if ev["data"] else "{}"
                yield f"event: {ev['type']}\ndata: {data}\n\n"
                cursor += 1

            if task.status != "running":
                break
            await asyncio.sleep(0.15)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/sessions/{sid}/setup/status")
async def setup_agent_status(sid: str):
    """Poll the setup agent status."""
    task = get_agent_task(sid, "setup")
    if not task:
        return {"status": "idle", "event_count": 0}
    return {
        "status": task.status,
        "event_count": len(task.events),
        "last_save_cursor": task.last_save_cursor,
        "error": task.error,
    }


@router.get("/sessions/{sid}/setup/firmware")
async def get_firmware(sid: str):
    """Return the generated firmware source code."""
    sess = load_session_or_404(sid)
    ino_path = sess.path / "firmware.ino"
    if not ino_path.exists():
        raise HTTPException(404, "No firmware generated yet")
    return {"code": ino_path.read_text(encoding="utf-8")}


@router.get("/sessions/{sid}/setup/conversation")
async def get_setup_conversation(sid: str):
    """Return the setup agent conversation history."""
    sess = load_session_or_404(sid)
    data = sess.read_artifact("setup_conversation.json")
    return data if isinstance(data, list) else []

"""Setup (firmware generation) agent route — SSE streaming like design/circuit."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from src.agent import SetupAgent
from src.catalog import _component_to_dict
from src.pipeline.firmware.context_builder import build_firmware_context
from src.pipeline.firmware.sim_config import generate_sim_config, write_sim_config
from src.pipeline.firmware.simulation import get_or_create_simulation, stop_simulation
from src.pipeline.firmware.arduino_cli import compile_sketch, find_arduino_cli
from src.web.routes._deps import (
    get_catalog, load_session_or_404,
)
from src.web.tasks import AgentTask, get_agent_task, set_agent_task

log = logging.getLogger(__name__)

router = APIRouter(tags=["setup"])


def _build_catalog_map(catalog) -> dict[str, dict]:
    """Build a dict of catalog_id → component dict for the context builder."""
    return {c.id: _component_to_dict(c) for c in catalog.components}


async def _run_setup_background(sid: str, prompt: str, task: AgentTask):
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

        agent = SetupAgent(
            cat, sess, firmware_context,
            circuit=circuit, routing=routing, catalog_map=catalog_map,
        )

        async for event in agent.run(prompt, cancel_event=task.cancel_event):
            if event.type == "checkpoint":
                task.last_save_cursor = len(task.events)
                continue
            task.append_event(event.type, event.data or {})

        # After agent finishes, generate sim_config if firmware compiled
        setup_state = sess.pipeline_state.get("setup")
        if setup_state in ("complete", "compile_failed", "compile_skipped"):
            elf_path = sess.artifact_path("firmware.ino").parent / "firmware_build" / "firmware.ino.elf"
            elf_rel = "firmware_build/firmware.ino.elf" if elf_path.exists() else None
            try:
                sim_cfg = generate_sim_config(circuit, routing, catalog_map, elf_rel)
                write_sim_config(sim_cfg, sess.artifact_path("sim_config.json").parent)
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

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass
    feedback = body.get("feedback")
    outline = body.get("outline")

    if feedback:
        prompt = (
            "The user has feedback on the generated firmware:\n\n"
            f"{feedback}\n\n"
            "Please address the feedback and resubmit the firmware."
        )
    elif outline:
        prompt = outline
    else:
        prompt = (
            "Write the firmware for this device. Read the device context in your "
            "system prompt carefully, then write a complete Arduino sketch that "
            "implements the described behavior. Call submit_firmware when ready."
        )

    task = AgentTask()
    set_agent_task(sid, "setup", task)
    task.asyncio_task = asyncio.create_task(
        _run_setup_background(sid, prompt, task)
    )
    return {"status": "running"}


@router.post("/sessions/{sid}/setup/stop")
async def stop_setup(sid: str):
    """Cancel the running setup agent."""
    task = get_agent_task(sid, "setup")
    if task and task.status == "running":
        task.cancel()
    return {"status": "stopped"}


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
            await asyncio.sleep(0.05)

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
    ino_path = sess.artifact_path("firmware.ino")
    if not ino_path.exists():
        raise HTTPException(404, "No firmware generated yet")
    return {"code": ino_path.read_text(encoding="utf-8")}


@router.post("/sessions/{sid}/setup/recompile")
async def recompile_firmware(sid: str):
    """Recompile existing firmware.ino and regenerate sim_config."""
    sess = load_session_or_404(sid)
    ino_path = sess.artifact_path("firmware.ino")
    if not ino_path.exists():
        raise HTTPException(400, "No firmware.ino — run the setup agent first")

    if find_arduino_cli() is None:
        raise HTTPException(
            503,
            "arduino-cli is not installed. "
            "Install it and the arduino:avr core before recompiling.",
        )

    code = ino_path.read_text(encoding="utf-8")
    result = compile_sketch(code, sess.artifact_path("firmware.ino").parent)

    if not result.success:
        sess.pipeline_state["setup"] = "compile_failed"
        sess.save()
        return {
            "status": "compile_failed",
            "stderr": result.stderr,
            "stdout": result.stdout,
        }

    sess.pipeline_state["setup"] = "complete"
    sess.save()

    # Regenerate sim_config with the new ELF
    circuit = sess.read_artifact("circuit.json")
    routing = sess.read_artifact("routing.json")
    if circuit and routing:
        cat = get_catalog()
        catalog_map = _build_catalog_map(cat)
        elf_path = sess.artifact_path("firmware.ino").parent / "firmware_build" / "firmware.ino.elf"
        elf_rel = "firmware_build/firmware.ino.elf" if elf_path.exists() else None
        try:
            sim_cfg = generate_sim_config(circuit, routing, catalog_map, elf_rel)
            write_sim_config(sim_cfg, sess.artifact_path("sim_config.json").parent)
        except Exception as e:
            log.warning("sim_config generation failed: %s", e)

    return {
        "status": "complete",
        "hex_path": str(result.hex_path) if result.hex_path else None,
        "elf_path": str(result.elf_path) if result.elf_path else None,
    }


@router.get("/sessions/{sid}/setup/sim-config")
async def get_sim_config(sid: str):
    """Return the simulation config for the device."""
    sess = load_session_or_404(sid)
    cfg_path = sess.artifact_path("sim_config.json")
    if not cfg_path.exists():
        raise HTTPException(404, "No sim_config.json — run setup first")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


@router.get("/sessions/{sid}/setup/conversation")
async def get_setup_conversation(sid: str):
    """Return the setup agent conversation history."""
    sess = load_session_or_404(sid)
    data = sess.read_artifact("setup_conversation.json")
    return data if isinstance(data, list) else []


@router.websocket("/sessions/{sid}/setup/simulate")
async def simulate_ws(websocket: WebSocket, sid: str):
    """WebSocket bridge to the simavr harness for real-time simulation."""
    await websocket.accept()

    sess = load_session_or_404(sid)
    queue: asyncio.Queue[dict] = asyncio.Queue()

    def on_event(event: dict) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

    try:
        mgr = await get_or_create_simulation(sess.artifact_path("sim_config.json").parent, sid)
        mgr.add_listener(on_event)

        if mgr.booted:
            await websocket.send_json({"event": "boot_ok"})

        # Send current state snapshot
        for iid, st in mgr.state.items():
            await websocket.send_json({
                "event": "pin_change",
                "instance_id": iid,
                "on": st.get("on", False),
            })

        async def _forward_events() -> None:
            while True:
                event = await queue.get()
                await websocket.send_json(event)

        forward_task = asyncio.create_task(_forward_events())

        try:
            while True:
                data = await websocket.receive_json()
                cmd = data.get("cmd")
                iid = data.get("instance_id", "")
                if cmd == "press":
                    await mgr.press(iid)
                elif cmd == "release":
                    await mgr.release(iid)
                elif cmd == "stop":
                    await mgr.stop()
                    await websocket.send_json({"event": "stopped"})
                elif cmd == "start":
                    try:
                        await mgr.start()
                    except Exception as exc:
                        await websocket.send_json({"event": "error", "message": str(exc)})
                elif cmd == "restart":
                    try:
                        await mgr.restart()
                    except Exception as exc:
                        await websocket.send_json({"event": "error", "message": str(exc)})
        except WebSocketDisconnect:
            pass
        finally:
            forward_task.cancel()
            mgr.remove_listener(on_event)
            await stop_simulation(sid)

    except (FileNotFoundError, ValueError, TimeoutError, RuntimeError) as exc:
        try:
            await websocket.send_json({"event": "error", "message": str(exc)})
            await websocket.close()
        except (WebSocketDisconnect, Exception):
            pass
        await stop_simulation(sid)
    except WebSocketDisconnect:
        await stop_simulation(sid)

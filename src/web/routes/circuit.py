from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from src.agent import CircuitAgent, build_circuit_user_prompt
from src.web.routes._deps import (
    get_catalog, load_session_or_404, invalidate_downstream,
    enrich_components,
)
from src.web.tasks import AgentTask, get_agent_task, set_agent_task

log = logging.getLogger(__name__)

router = APIRouter(tags=["circuit"])


async def _run_circuit_background(sid: str, prompt: str, task: AgentTask, invalidated: list[str]):
    """Run the circuit agent in the background, accumulating events in *task*."""
    try:
        sess = load_session_or_404(sid)
        cat = get_catalog()

        if invalidated:
            task.append_event("invalidated", {
                "invalidated_steps": invalidated,
                "artifacts": sess.artifacts,
                "pipeline_errors": sess.pipeline_errors,
            })

        agent = CircuitAgent(cat, sess)
        async for event in agent.run(prompt, cancel_event=task.cancel_event):
            if event.type == "checkpoint":
                task.last_save_cursor = len(task.events)
                continue
            if event.type == "circuit" and event.data:
                circuit = event.data.get("circuit")
                if circuit:
                    enrich_components(circuit.get("components", []), cat)
            task.append_event(event.type, event.data or {})
        task.finish("done")
    except asyncio.CancelledError:
        task.finish("done", error="Cancelled")
    except Exception as e:
        log.exception("Circuit agent background error")
        task.append_event("error", {"message": str(e)})
        task.finish("error", error=str(e))


@router.post("/sessions/{sid}/circuit")
async def run_circuit(sid: str, request: Request):
    sess = load_session_or_404(sid)
    design_data = sess.read_artifact("design.json")
    if design_data is None:
        raise HTTPException(400, "No design.json — run the design agent first")

    existing = get_agent_task(sid, "circuit")
    if existing and existing.status == "running":
        raise HTTPException(409, "Circuit agent is already running")

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass
    feedback = body.get("feedback")
    outline = body.get("outline")

    if feedback:
        prompt = (
            "The manufacturing pipeline failed after your circuit was submitted. "
            "Here is the error:\n\n"
            f"{feedback}\n\n"
            "Please fix the issue and resubmit the circuit."
        )
        invalidated = invalidate_downstream(sess, "circuit")
    elif outline:
        prompt = outline
        invalidated = invalidate_downstream(sess, "design")
    else:
        prompt = build_circuit_user_prompt(design_data)
        invalidated = invalidate_downstream(sess, "design")
    sess.save()

    task = AgentTask()
    set_agent_task(sid, "circuit", task)
    task.asyncio_task = asyncio.create_task(
        _run_circuit_background(sid, prompt, task, invalidated)
    )
    return {"status": "running"}


@router.post("/sessions/{sid}/circuit/stop")
async def stop_circuit(sid: str):
    task = get_agent_task(sid, "circuit")
    if not task or task.status != "running":
        raise HTTPException(404, "No running circuit agent")
    task.cancel_event.set()
    return {"status": "stopping"}


@router.get("/sessions/{sid}/circuit/stream")
async def stream_circuit_events(sid: str, after: int = Query(0)):
    """SSE endpoint: yields buffered events starting at *after*, then waits for new ones."""
    task = get_agent_task(sid, "circuit")
    if not task:
        raise HTTPException(404, "No circuit agent task")

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


@router.get("/sessions/{sid}/circuit/status")
async def circuit_agent_status(sid: str):
    task = get_agent_task(sid, "circuit")
    if not task:
        return {"status": "idle", "event_count": 0}
    return {
        "status": task.status,
        "event_count": len(task.events),
        "last_save_cursor": task.last_save_cursor,
        "error": task.error,
    }


@router.get("/sessions/{sid}/circuit")
async def get_circuit(sid: str):
    s = load_session_or_404(sid)
    data = s.read_artifact("circuit.json")
    if data is None:
        raise HTTPException(404, "No circuit yet")
    cat = get_catalog()
    enrich_components(data.get("components", []), cat)
    return data


@router.get("/sessions/{sid}/circuit/conversation")
async def get_circuit_conversation(sid: str):
    s = load_session_or_404(sid)
    data = s.read_artifact("circuit_conversation.json")
    return data if isinstance(data, list) else []

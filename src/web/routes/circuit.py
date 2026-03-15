from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.agent import CircuitAgent, build_circuit_user_prompt
from src.web.routes._deps import (
    get_catalog, load_session_or_404, invalidate_downstream,
    enrich_components,
)

router = APIRouter(tags=["circuit"])


@router.post("/sessions/{sid}/circuit")
async def run_circuit(sid: str, request: Request):
    sess = load_session_or_404(sid)
    design_data = sess.read_artifact("design.json")
    if design_data is None:
        raise HTTPException(400, "No design.json — run the design agent first")

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass
    feedback = body.get("feedback")
    outline = body.get("outline")

    cat = get_catalog()

    if feedback:
        prompt = (
            "The manufacturing pipeline failed after your circuit was submitted. "
            "Here is the error:\n\n"
            f"{feedback}\n\n"
            "Please fix the issue and resubmit the circuit."
        )
        invalidate_downstream(sess, "circuit")
    elif outline:
        prompt = outline
        invalidate_downstream(sess, "design")
    else:
        prompt = build_circuit_user_prompt(design_data)
        invalidate_downstream(sess, "design")
    sess.save()

    async def event_stream():
        try:
            agent = CircuitAgent(cat, sess)
            async for event in agent.run(prompt):
                if event.type == "circuit" and event.data:
                    circuit = event.data.get("circuit")
                    if circuit:
                        enrich_components(circuit.get("components", []), cat)
                data = json.dumps(event.data) if event.data else "{}"
                yield f"event: {event.type}\ndata: {data}\n\n"
        except Exception as e:
            data = json.dumps({"message": str(e)})
            yield f"event: error\ndata: {data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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

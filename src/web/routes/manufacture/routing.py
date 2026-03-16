from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException

from src.pipeline.design import parse_physical_design, parse_circuit
from src.pipeline.placer import assemble_full_placement
from src.pipeline.router import route_traces, routing_to_dict
from src.web.routes._deps import (
    get_catalog, load_session_or_404,
    require_design, require_circuit, require_placement,
    build_routing_response,
)
from src.web.tasks import PipelineTask, get_pipeline_task, set_pipeline_task

router = APIRouter()


@router.post("/sessions/{sid}/manufacture/routing")
async def run_routing(sid: str):
    s = load_session_or_404(sid)

    existing = get_pipeline_task(sid, "routing")
    if existing and existing.status == "running":
        return {"status": "running"}

    set_pipeline_task(sid, "routing", PipelineTask(status="running"))

    def _do():
        try:
            cat = get_catalog()
            physical = parse_physical_design(require_design(s))
            circuit = parse_circuit(require_circuit(s))
            placement_data = require_placement(s)

            full_placement = assemble_full_placement(
                placement_data, physical.outline, circuit.nets, physical.enclosure,
            )

            result = route_traces(full_placement, cat)

            if not result.ok:
                total = len({t.net_id for t in result.traces} | set(result.failed_nets))
                detail = {
                    "error": "routing_failed",
                    "reason": f"Failed to route {len(result.failed_nets)}/{total} nets: {', '.join(result.failed_nets)}",
                    "responsible_agent": "circuit",
                }
                s.set_step_error("routing", detail)
                set_pipeline_task(sid, "routing", PipelineTask(status="error", error=detail["reason"], detail=detail))
                return

            s.clear_step_error("routing")
            s.write_artifact("routing.json", routing_to_dict(result))
            if result.debug_grids:
                s.write_artifact("routing_debug.json", {"debug_grids": result.debug_grids})
            s.pipeline_state["routing"] = "complete"
            s.save()
            set_pipeline_task(sid, "routing", PipelineTask(status="done"))
        except Exception as e:
            detail = {
                "error": "routing_failed",
                "reason": str(e),
                "responsible_agent": "circuit",
            }
            s.set_step_error("routing", detail)
            set_pipeline_task(sid, "routing", PipelineTask(status="error", error=str(e), detail=detail))

    threading.Thread(target=_do, daemon=True).start()
    return {"status": "running"}


@router.get("/sessions/{sid}/manufacture/routing/status")
async def poll_routing(sid: str):
    task = get_pipeline_task(sid, "routing")
    if task:
        resp = {"status": task.status, "message": task.error or ""}
        if task.detail:
            resp["detail"] = task.detail
        return resp
    s = load_session_or_404(sid)
    if s.read_artifact("routing.json") is not None:
        return {"status": "done"}
    return {"status": "idle"}


@router.get("/sessions/{sid}/manufacture/routing")
async def get_routing(sid: str):
    s = load_session_or_404(sid)
    if s.read_artifact("routing.json") is None:
        raise HTTPException(404, "No routing yet")
    return build_routing_response(s, get_catalog())

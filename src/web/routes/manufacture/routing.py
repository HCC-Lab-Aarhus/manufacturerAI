from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.pipeline.design import parse_physical_design, parse_circuit
from src.pipeline.placer import assemble_full_placement
from src.pipeline.router import route_traces, routing_to_dict
from src.web.routes._deps import (
    get_catalog, load_session_or_404,
    require_design, require_circuit, require_placement,
    build_routing_response,
)

router = APIRouter()


@router.post("/sessions/{sid}/manufacture/routing")
async def run_routing(sid: str):
    s = load_session_or_404(sid)
    cat = get_catalog()

    physical = parse_physical_design(require_design(s))
    circuit = parse_circuit(require_circuit(s))
    placement_data = require_placement(s)

    full_placement = assemble_full_placement(
        placement_data, physical.outline, circuit.nets, physical.enclosure,
    )

    try:
        result = route_traces(full_placement, cat)
    except Exception as e:
        raise HTTPException(422, detail={
            "error": "routing_failed",
            "reason": str(e),
            "responsible_agent": "circuit",
        })

    s.write_artifact("routing.json", routing_to_dict(result))
    if result.debug_grids:
        s.write_artifact("routing_debug.json", {"debug_grids": result.debug_grids})
    s.pipeline_state["routing"] = "complete"
    s.save()
    return build_routing_response(s, cat)


@router.get("/sessions/{sid}/manufacture/routing")
async def get_routing(sid: str):
    s = load_session_or_404(sid)
    if s.read_artifact("routing.json") is None:
        raise HTTPException(404, "No routing yet")
    return build_routing_response(s, get_catalog())

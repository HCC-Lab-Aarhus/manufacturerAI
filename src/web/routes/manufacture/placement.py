from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.pipeline.design import (
    parse_physical_design, parse_circuit, build_design_spec, validate_design,
)
from src.pipeline.placer import place_components, placement_to_dict, PlacementError
from src.pipeline.config import get_printer
from src.web.routes._deps import (
    get_catalog, load_session_or_404, invalidate_downstream,
    require_design, require_circuit,
    build_placement_response,
)

router = APIRouter()


@router.post("/sessions/{sid}/manufacture/placement")
async def run_placement(sid: str):
    s = load_session_or_404(sid)
    cat = get_catalog()

    physical = parse_physical_design(require_design(s))
    circuit = parse_circuit(require_circuit(s))
    design = build_design_spec(physical, circuit)

    errors = validate_design(design, cat, printer=get_printer(s.printer_id))
    if errors:
        detail = {
            "error": "design_validation_failed",
            "reason": "; ".join(errors),
            "responsible_agent": "design",
        }
        s.set_step_error("placement", detail)
        raise HTTPException(400, detail=detail)

    try:
        result = place_components(design, cat)
    except PlacementError as e:
        detail = {
            "error": "placement_failed",
            "instance_id": e.instance_id,
            "catalog_id": e.catalog_id,
            "reason": e.reason,
            "responsible_agent": "design",
        }
        s.set_step_error("placement", detail)
        raise HTTPException(422, detail=detail)

    s.clear_step_error("placement")

    s.write_artifact("placement.json", placement_to_dict(result))
    s.pipeline_state["placement"] = "complete"
    invalidate_downstream(s, "placement")
    s.save()
    return build_placement_response(s, cat)


@router.get("/sessions/{sid}/manufacture/placement")
async def get_placement(sid: str):
    s = load_session_or_404(sid)
    if s.read_artifact("placement.json") is None:
        raise HTTPException(404, "No placement yet")
    return build_placement_response(s, get_catalog())

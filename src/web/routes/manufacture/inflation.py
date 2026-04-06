from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, HTTPException
from shapely.geometry import Polygon as ShapelyPolygon

from src.pipeline.design import parse_physical_design, parse_circuit
from src.pipeline.placer import assemble_full_placement
from src.pipeline.router import parse_routing
from src.pipeline.inflation import inflate_traces, build_obstacle_polygons, inflation_to_dict, pin_shaft_poly
from src.web.routes._deps import (
    get_catalog, load_session_or_404,
    require_design, require_circuit, require_placement, require_routing,
    build_routing_response,
)
from src.web.tasks import PipelineTask, get_pipeline_task, set_pipeline_task

log = logging.getLogger(__name__)

router = APIRouter()


@router.post("/sessions/{sid}/manufacture/inflation")
async def run_inflation(sid: str):
    s = load_session_or_404(sid)

    existing = get_pipeline_task(sid, "inflation")
    if existing and existing.status == "running":
        return {"status": "running"}

    task = PipelineTask(status="running")
    set_pipeline_task(sid, "inflation", task)

    def _do():
        try:
            s.clear_stage_artifacts("inflation")
            cat = get_catalog()
            physical = parse_physical_design(require_design(s))
            circuit = parse_circuit(require_circuit(s))
            placement_data = require_placement(s)
            routing_data = require_routing(s)

            full_placement = assemble_full_placement(
                placement_data, physical.outline, circuit.nets, physical.enclosure,
            )
            result = parse_routing(routing_data)

            outline_poly = ShapelyPolygon(physical.outline.vertices)
            catalog_map = {c.id: c for c in cat.components}
            obstacles = build_obstacle_polygons(full_placement.components, catalog_map)

            pin_positions: dict[str, tuple[float, float]] = {}
            pin_pads = {}
            for comp in full_placement.components:
                cat_comp = catalog_map.get(comp.catalog_id)
                for pid, pos in comp.pin_positions.items():
                    key = f"{comp.instance_id}:{pid}"
                    pin_positions[key] = pos
                    if cat_comp is not None:
                        pin = next((p for p in cat_comp.pins if p.id == pid), None)
                        if pin is not None:
                            pin_pads[key] = pin_shaft_poly(
                                pin, pos[0], pos[1],
                                comp.rotation_deg,
                            )

            net_pin_ids: dict[str, set[str]] = {}
            for net in full_placement.nets:
                resolved: set[str] = set()
                for pin_ref in net.pins:
                    key = f"{net.id}|{pin_ref}"
                    assigned = result.pin_assignments.get(key)
                    if assigned:
                        resolved.add(assigned)
                    else:
                        resolved.add(pin_ref)
                net_pin_ids[net.id] = resolved

            inflated = inflate_traces(
                result, outline_poly, obstacles,
                pin_positions=pin_positions,
                pin_pads=pin_pads,
                net_pin_ids=net_pin_ids,
            )

            s.write_artifact("inflation.json", inflation_to_dict(inflated))

            s.pipeline_state["inflation"] = "complete"
            s.save()
            set_pipeline_task(sid, "inflation", PipelineTask(status="done"))
        except Exception as e:
            log.exception("Inflation failed for session %s", sid)
            set_pipeline_task(sid, "inflation", PipelineTask(status="error", error=str(e)))

    threading.Thread(target=_do, daemon=True).start()
    return {"status": "running"}


@router.get("/sessions/{sid}/manufacture/inflation/status")
async def poll_inflation(sid: str):
    task = get_pipeline_task(sid, "inflation")
    if task:
        return {"status": task.status, "message": task.error or ""}
    s = load_session_or_404(sid)
    if s.pipeline_state.get("inflation") == "complete":
        return {"status": "done"}
    return {"status": "idle"}


@router.get("/sessions/{sid}/manufacture/inflation")
async def get_inflation(sid: str):
    s = load_session_or_404(sid)
    if s.read_artifact("routing.json") is None:
        raise HTTPException(404, "No routing yet")
    return build_routing_response(s, get_catalog())

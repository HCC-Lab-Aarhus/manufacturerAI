from __future__ import annotations

from fastapi import APIRouter, Query

from src.catalog import catalog_to_dict
from src.session import create_session
from src.pipeline.config import get_printer
from src.web.routes._deps import (
    get_catalog, load_session_or_404, invalidate_downstream,
)


router = APIRouter(tags=["sessions"])


@router.get("/sessions")
async def list_sessions():
    from src.session import list_sessions as _ls
    return {"sessions": _ls()}


@router.post("/sessions")
async def create_new_session(description: str = ""):
    session = create_session(description)
    session.save()
    return {"session_id": session.id, "created": session.created}


@router.get("/sessions/{sid}")
async def get_session(sid: str):
    s = load_session_or_404(sid)
    return {
        "id": s.id,
        "created": s.created,
        "last_modified": s.last_modified,
        "description": s.description,
        "name": s.name,
        "printer_id": s.printer_id,
        "pipeline_state": s.pipeline_state,
        "pipeline_errors": s.pipeline_errors,
        "artifacts": s.artifacts,
    }


@router.put("/sessions/{sid}/printer")
async def set_printer(sid: str, printer_id: str = Query(...)):
    s = load_session_or_404(sid)
    pdef = get_printer(printer_id)
    old_id = s.printer_id
    s.printer_id = pdef.id
    invalidated: list[str] = []
    if old_id != pdef.id:
        invalidated = invalidate_downstream(s, "placement")
    s.save()
    return {
        "printer_id": pdef.id,
        "label": pdef.label,
        "invalidated_steps": invalidated,
        "artifacts": s.artifacts,
        "pipeline_errors": s.pipeline_errors,
    }


@router.get("/sessions/{sid}/catalog")
async def get_session_catalog(sid: str):
    load_session_or_404(sid)
    cat = get_catalog()
    return catalog_to_dict(cat)

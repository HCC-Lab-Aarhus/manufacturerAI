from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.pipeline.scad import run_scad_step
from src.web.routes._deps import load_session_or_404, require_placement

router = APIRouter()


@router.post("/sessions/{sid}/manufacture/scad")
async def run_scad(sid: str):
    s = load_session_or_404(sid)
    require_placement(s)

    try:
        scad_path = run_scad_step(s)
    except Exception as exc:
        detail = {
            "error": "scad_failed",
            "reason": str(exc),
            "responsible_agent": "design",
        }
        s.set_step_error("scad", detail)
        raise HTTPException(422, detail=detail)

    s.clear_step_error("scad")

    scad_text = scad_path.read_text(encoding="utf-8")
    return {
        "status": "done",
        "scad_lines": scad_text.count("\n"),
        "scad_bytes": len(scad_text),
    }


@router.get("/sessions/{sid}/manufacture/scad")
async def get_scad(sid: str):
    s = load_session_or_404(sid)
    scad_path = s.path / "enclosure.scad"
    if not scad_path.exists():
        raise HTTPException(404, "No enclosure.scad yet")
    scad_text = scad_path.read_text(encoding="utf-8")
    return {
        "status": "done",
        "scad": scad_text,
        "scad_lines": scad_text.count("\n"),
        "scad_bytes": len(scad_text),
    }

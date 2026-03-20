from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException

from src.pipeline.scad import run_scad_step
from src.web.routes._deps import load_session_or_404, require_placement
from src.web.tasks import PipelineTask, get_pipeline_task, set_pipeline_task

router = APIRouter()


@router.post("/sessions/{sid}/manufacture/scad")
async def run_scad(sid: str):
    s = load_session_or_404(sid)
    require_placement(s)

    existing = get_pipeline_task(sid, "scad")
    if existing and existing.status == "running":
        return {"status": "running"}

    set_pipeline_task(sid, "scad", PipelineTask(status="running"))

    def _do():
        try:
            scad_path = run_scad_step(s)
            s.clear_step_error("scad")
            set_pipeline_task(sid, "scad", PipelineTask(status="done"))
        except Exception as exc:
            detail = {
                "error": "scad_failed",
                "reason": str(exc),
                "responsible_agent": "design",
            }
            s.set_step_error("scad", detail)
            set_pipeline_task(sid, "scad", PipelineTask(status="error", error=str(exc), detail=detail))

    threading.Thread(target=_do, daemon=True).start()
    return {"status": "running"}


@router.get("/sessions/{sid}/manufacture/scad/status")
async def poll_scad(sid: str):
    task = get_pipeline_task(sid, "scad")
    if task:
        resp = {"status": task.status, "message": task.error or ""}
        if task.detail:
            resp["detail"] = task.detail
        return resp
    s = load_session_or_404(sid)
    if s.has_artifact("enclosure.scad"):
        return {"status": "done"}
    return {"status": "idle"}


@router.get("/sessions/{sid}/manufacture/scad")
async def get_scad(sid: str):
    s = load_session_or_404(sid)
    scad_path = s.artifact_path("enclosure.scad")
    if not scad_path.exists():
        raise HTTPException(404, "No enclosure.scad yet")
    scad_text = scad_path.read_text(encoding="utf-8")
    return {
        "status": "done",
        "scad": scad_text,
        "scad_lines": scad_text.count("\n"),
        "scad_bytes": len(scad_text),
    }

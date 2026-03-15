from __future__ import annotations

import logging
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from src.pipeline.design import parse_physical_design
from src.web.routes._deps import (
    get_gcode_state, set_gcode_state,
    load_session_or_404, require_routing,
)

router = APIRouter()


@router.post("/sessions/{sid}/manufacture/gcode")
async def start_gcode(
    sid: str,
    force: bool = Query(False),
    filament: str = Query(None),
    silverink_only: bool = Query(False),
):
    s = load_session_or_404(sid)
    stl_path = s.path / "enclosure.stl"
    if not stl_path.exists():
        raise HTTPException(400, "No enclosure.stl — compile SCAD first")

    routing_data = require_routing(s)

    cur = get_gcode_state(sid)
    if not force and cur and cur["status"] == "running":
        return {"status": "running"}
    if not force and cur and cur["status"] in ("done", "error"):
        return cur

    set_gcode_state(sid, {"status": "running", "message": "Starting G-code pipeline…", "stages": []})

    def _do_gcode():
        from src.pipeline.gcode.pipeline import run_gcode_pipeline
        try:
            shell_height = None
            design_data = s.read_artifact("design.json")
            if design_data:
                try:
                    shell_height = parse_physical_design(design_data).enclosure.height_mm
                except Exception:
                    pass

            result = run_gcode_pipeline(
                stl_path=stl_path,
                output_dir=s.path,
                routing_result=routing_data,
                shell_height=shell_height,
                printer=s.printer_id,
                filament=filament,
                silverink_only=silverink_only,
            )
            if result.success:
                s.pipeline_state["gcode"] = "complete"
                s.save()
                set_gcode_state(sid, {
                    "status": "done",
                    "message": result.message,
                    "stages": result.stages,
                    "has_bgcode": result.bgcode_path is not None and Path(result.bgcode_path).exists(),
                    "gcode_bytes": (
                        Path(result.staged_gcode_path).stat().st_size
                        if result.staged_gcode_path and Path(result.staged_gcode_path).exists()
                        else 0
                    ),
                })
            else:
                set_gcode_state(sid, {"status": "error", "message": result.message, "stages": result.stages})
        except Exception as exc:
            logging.exception("G-code pipeline error")
            set_gcode_state(sid, {"status": "error", "message": str(exc), "stages": []})

    threading.Thread(target=_do_gcode, daemon=True).start()
    return {"status": "running"}


@router.get("/sessions/{sid}/manufacture/gcode")
async def poll_gcode(sid: str):
    s = load_session_or_404(sid)
    cur = get_gcode_state(sid)
    if cur:
        return cur
    staged = s.path / "enclosure_staged.gcode"
    bgcode = s.path / "enclosure_staged.bgcode"
    if staged.exists():
        return {
            "status": "done",
            "message": "G-code pipeline completed successfully.",
            "stages": [],
            "has_bgcode": bgcode.exists(),
            "gcode_bytes": staged.stat().st_size,
        }
    return {"status": "pending"}


@router.get("/sessions/{sid}/manufacture/gcode/download")
async def download_gcode(sid: str, format: str = Query("gcode")):
    s = load_session_or_404(sid)
    if format == "bgcode":
        path = s.path / "enclosure_staged.bgcode"
        fname = "enclosure_staged.bgcode"
        mime = "application/octet-stream"
    else:
        path = s.path / "enclosure_staged.gcode"
        fname = "enclosure_staged.gcode"
        mime = "text/plain"
    if not path.exists():
        raise HTTPException(404, f"No {fname} — run the G-code pipeline first")
    return FileResponse(path, media_type=mime, filename=fname,
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

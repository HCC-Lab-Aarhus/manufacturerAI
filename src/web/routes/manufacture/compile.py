from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from src.web.routes._deps import (
    get_compile_state, set_compile_state,
    load_session_or_404,
)

router = APIRouter()


@router.post("/sessions/{sid}/manufacture/compile")
async def start_compile(sid: str, force: bool = Query(False)):
    s = load_session_or_404(sid)
    scad_path = s.artifact_path("enclosure.scad")
    if not scad_path.exists():
        raise HTTPException(400, "No enclosure.scad yet — run SCAD first")

    stl_path = s.artifact_path("enclosure.stl")
    cur = get_compile_state(sid)

    if not force and stl_path.exists() and cur is None:
        if not (scad_path.stat().st_mtime > stl_path.stat().st_mtime):
            return {"status": "done", "stl_bytes": stl_path.stat().st_size}

    if cur and cur["status"] == "compiling" and not force:
        return {"status": "compiling"}

    if not force and cur and cur["status"] in ("done", "error"):
        return {"status": cur["status"], "message": cur.get("message", ""),
                "stl_bytes": stl_path.stat().st_size if stl_path.exists() else 0}

    if force and cur and cur.get("cancel"):
        cur["cancel"].set()

    cancel = threading.Event()
    set_compile_state(sid, {"status": "compiling", "cancel": cancel, "message": ""})

    def _do_compile():
        from src.pipeline.scad.compiler import compile_scad
        ok, msg, out = compile_scad(scad_path, stl_path, cancel=cancel, timeout=600)
        set_compile_state(sid, {"status": "done" if ok else "error", "message": msg, "cancel": cancel})
        if ok:
            s.pipeline_state["scad"] = "done"
            s.save()

    threading.Thread(target=_do_compile, daemon=True).start()
    return {"status": "compiling"}


@router.get("/sessions/{sid}/manufacture/compile")
async def poll_compile(sid: str):
    s = load_session_or_404(sid)
    stl_path = s.artifact_path("enclosure.stl")
    state = get_compile_state(sid)
    if state:
        out = {"status": state["status"], "message": state.get("message", "")}
        if state["status"] == "done" and stl_path.exists():
            out["stl_bytes"] = stl_path.stat().st_size
        return out
    if stl_path.exists():
        return {"status": "done", "stl_bytes": stl_path.stat().st_size}
    return {"status": "pending"}


@router.get("/sessions/{sid}/manufacture/stl")
async def download_stl(sid: str):
    s = load_session_or_404(sid)
    stl_path = s.artifact_path("enclosure.stl")
    if not stl_path.exists():
        raise HTTPException(404, "No enclosure.stl yet — compile first")
    return FileResponse(
        stl_path,
        media_type="application/octet-stream",
        filename="enclosure.stl",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )

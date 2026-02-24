"""
FastAPI web server — streaming endpoint that drives multi-turn agent.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
import traceback
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.agent.loop import run_turn
from src.scad.shell import generate_enclosure_scad, generate_battery_hatch_scad, generate_print_plate_scad, DEFAULT_HEIGHT_MM
from src.scad.cutouts import build_cutouts
from src.scad.compiler import compile_scad
from src.gcode.pipeline import run_gcode_pipeline
from src.gcode.slicer import find_prusaslicer, find_prusaslicer_gui, PRINTERS

# ── .env loader ────────────────────────────────────────────────────

def _load_env():
    root = Path(__file__).resolve().parents[2]
    for name in (".env", ".env.local"):
        p = root / name
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v

_load_env()

# ── App ────────────────────────────────────────────────────────────

app = FastAPI(title="ManufacturerAI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS_DIR = ROOT / "outputs" / "web"
STATIC_DIR = Path(__file__).resolve().parent / "static"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def _no_cache_static(request, call_next):
    """Prevent browser from caching JS / CSS during development."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response

# ── Session state (persists across requests) ───────────────────────

_conversation_history: list = []      # Gemini Content proto objects
_run_dir: Path | None = None          # Output directory for this session
_printer_id: str | None = None        # Last-used printer id ("mk3s" / "coreone")
_layout_gen: int = 0                  # bumped by update_layout; checked by emit
_pipeline_gate = threading.Event()    # clear() = paused, set() = running
_pipeline_gate.set()                  # start unblocked
_stl_rebuilding = False               # True while background STL compile is running


# ── Models ─────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    message: str


class CurveUpdateRequest(BaseModel):
    top_curve_length: float = 0.0
    top_curve_height: float = 0.0
    bottom_curve_length: float = 0.0
    bottom_curve_height: float = 0.0


class LayoutPositionUpdate(BaseModel):
    id: str
    center: list[float]
    rotation_deg: int | None = None


class LayoutUpdateRequest(BaseModel):
    positions: list[LayoutPositionUpdate]


# ── Routes ─────────────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.post("/api/reset")
def reset_session():
    """Reset conversation history and start a fresh session."""
    global _conversation_history, _run_dir, _printer_id, _layout_gen
    _conversation_history = []
    _run_dir = None
    _printer_id = None
    _layout_gen = 0
    _pipeline_gate.set()
    return {"status": "ok"}


@app.get("/api/shell_height")
def get_shell_height():
    """Return the default shell height so the UI knows the max."""
    return {"height_mm": DEFAULT_HEIGHT_MM}


@app.post("/api/update_curve")
def update_curve(req: CurveUpdateRequest):
    """Re-generate SCAD + STL with new curve params only.

    Does NOT re-run placement or routing — uses cached data.
    """
    if _run_dir is None:
        raise HTTPException(400, "No run yet — generate a design first.")

    layout_path = _run_dir / "pcb_layout.json"
    routing_path = _run_dir / "routing_result.json"
    if not layout_path.exists():
        raise HTTPException(400, "No layout data — generate a design first.")

    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    outline = layout.get("board", {}).get("outline_polygon", [])
    if not outline:
        raise HTTPException(400, "No outline in layout.")

    routing = {}
    if routing_path.exists():
        routing = json.loads(routing_path.read_text(encoding="utf-8"))

    # Rebuild cutouts + SCAD
    cutouts = build_cutouts(layout, routing)
    enclosure_scad = generate_enclosure_scad(
        outline=outline,
        cutouts=cutouts,
        top_curve_length=req.top_curve_length,
        top_curve_height=req.top_curve_height,
        bottom_curve_length=req.bottom_curve_length,
        bottom_curve_height=req.bottom_curve_height,
    )
    (_run_dir / "enclosure.scad").write_text(enclosure_scad, encoding="utf-8")

    hatch_scad = generate_battery_hatch_scad()
    (_run_dir / "battery_hatch.scad").write_text(hatch_scad, encoding="utf-8")
    plate_scad = generate_print_plate_scad()
    (_run_dir / "print_plate.scad").write_text(plate_scad, encoding="utf-8")

    # Compile STLs
    stl_results = {}
    for name in ["enclosure", "battery_hatch", "print_plate"]:
        scad_p = _run_dir / f"{name}.scad"
        stl_p = scad_p.with_suffix(".stl")
        if scad_p.exists():
            ok, msg, _ = compile_scad(scad_p, stl_p)
            stl_results[name] = {"ok": ok, "message": msg}

    model_name = "print_plate" if (_run_dir / "print_plate.stl").exists() else "enclosure"
    return {
        "status": "ok",
        "model_name": model_name,
        "stl_results": stl_results,
    }


@app.post("/api/update_layout")
def update_layout(req: LayoutUpdateRequest):
    """Move components to new positions, re-route traces, and rebuild STLs.

    Called from the Realign mode in the outline view.  Accepts a list of
    ``{id, center}`` pairs, patches the cached ``pcb_layout.json``, then
    re-routes and re-generates SCAD/STL from the updated layout.
    """
    from src.pcb.router_bridge import route_traces as _route_traces, RouterError

    if _run_dir is None:
        raise HTTPException(400, "No run yet — generate a design first.")

    layout_path = _run_dir / "pcb_layout.json"
    if not layout_path.exists():
        raise HTTPException(400, "No layout data — generate a design first.")

    layout = json.loads(layout_path.read_text(encoding="utf-8"))

    # ── Patch component positions ──────────────────────────────────
    global _layout_gen
    _layout_gen += 1   # prevent pipeline thread from overwriting
    pos_map = {p.id: p.center for p in req.positions}
    rot_map = {p.id: p.rotation_deg for p in req.positions if p.rotation_deg is not None}
    for comp in layout.get("components", []):
        if comp["id"] in pos_map:
            comp["center"] = pos_map[comp["id"]]
        if comp["id"] in rot_map:
            old_rot = comp.get("rotation_deg", 0)
            new_rot = rot_map[comp["id"]]
            if old_rot != new_rot:
                comp["rotation_deg"] = new_rot
                # Swap keepout dimensions when rotation changes
                ko = comp.get("keepout", {})
                if ko.get("type") == "rectangle":
                    ko["width_mm"], ko["height_mm"] = ko["height_mm"], ko["width_mm"]
                # Swap body dimensions too (battery, controller)
                if "body_width_mm" in comp and "body_height_mm" in comp:
                    comp["body_width_mm"], comp["body_height_mm"] = comp["body_height_mm"], comp["body_width_mm"]

    # Save updated layout
    layout_path.write_text(json.dumps(layout, indent=2), encoding="utf-8")

    # Write lock marker so pipeline thread won't overwrite our files
    (_run_dir / ".layout_lock").write_text("realigned", encoding="utf-8")

    # ── Re-route traces ────────────────────────────────────────────
    routing = {}
    routing_ok = False
    try:
        routing = _route_traces(layout, _run_dir)
        routing_ok = routing.get("success", False)
        (_run_dir / "routing_result.json").write_text(
            json.dumps(routing, indent=2), encoding="utf-8"
        )
    except (RouterError, Exception) as exc:
        # Routing may fail with the new positions — log it so we can debug.
        import logging as _log
        _log.getLogger("manufacturerAI.server").warning(
            "Re-route after realign failed: %s", exc, exc_info=True,
        )

    # Always check for debug image (router generates it even on partial failure)
    has_debug_image = (
        (_run_dir / "pcb" / "pcb_debug.png").exists()
        or (_run_dir / "pcb_debug.png").exists()
    )

    # ── Rebuild SCAD + STL in background (compile takes minutes) ───
    outline = layout.get("board", {}).get("outline_polygon", [])
    run_dir_snap = _run_dir  # snapshot for the background thread

    def _rebuild_stl():
        global _stl_rebuilding
        if not outline:
            _stl_rebuilding = False
            return
        try:
            cutouts = build_cutouts(layout, routing)
            enclosure_scad = generate_enclosure_scad(outline=outline, cutouts=cutouts)
            (run_dir_snap / "enclosure.scad").write_text(enclosure_scad, encoding="utf-8")

            hatch_scad = generate_battery_hatch_scad()
            (run_dir_snap / "battery_hatch.scad").write_text(hatch_scad, encoding="utf-8")
            plate_scad = generate_print_plate_scad()
            (run_dir_snap / "print_plate.scad").write_text(plate_scad, encoding="utf-8")

            for name in ["enclosure", "battery_hatch"]:
                scad_p = run_dir_snap / f"{name}.scad"
                stl_p = scad_p.with_suffix(".stl")
                if scad_p.exists():
                    compile_scad(scad_p, stl_p)

            # Merge into print plate
            from src.scad.compiler import merge_stl_files
            enc_stl = run_dir_snap / "enclosure.stl"
            hatch_stl = run_dir_snap / "battery_hatch.stl"
            plate_stl = run_dir_snap / "print_plate.stl"
            if enc_stl.exists() and hatch_stl.exists():
                merge_stl_files(
                    [(enc_stl, (0, 0, 0)), (hatch_stl, (80, 0, 0))],
                    plate_stl,
                )
        finally:
            _stl_rebuilding = False

    _stl_rebuilding = True
    threading.Thread(target=_rebuild_stl, daemon=True).start()

    model_name = "print_plate" if (_run_dir / "print_plate.stl").exists() else "enclosure"
    return {
        "status": "ok",
        "layout": layout,
        "model_name": model_name,
        "has_debug_image": has_debug_image,
        "stl_rebuilding": True,
    }


@app.post("/api/realign/pause")
def realign_pause():
    """Pause the pipeline while the user is in realign mode."""
    _pipeline_gate.clear()
    return {"status": "paused"}


@app.post("/api/realign/resume")
def realign_resume():
    """Resume the pipeline after exiting realign mode."""
    _pipeline_gate.set()
    return {"status": "resumed"}


@app.get("/api/stl_status")
def stl_status():
    """Check if background STL rebuild is still running."""
    model_name = None
    if _run_dir:
        if (_run_dir / "print_plate.stl").exists():
            model_name = "print_plate"
        elif (_run_dir / "enclosure.stl").exists():
            model_name = "enclosure"
    return {
        "rebuilding": _stl_rebuilding,
        "model_name": model_name,
    }


@app.post("/api/generate/stream")
async def generate_stream(req: GenerateRequest):
    """
    Streaming endpoint.  Runs one agent turn in a background thread,
    pushes SSE events to the client via a Queue.
    Conversation history is preserved across requests for multi-turn.
    """
    global _conversation_history, _run_dir, _layout_gen

    if not req.message.strip():
        raise HTTPException(400, "Empty prompt.")

    # Ensure pipeline is unblocked (may have been paused for realign mode)
    _pipeline_gate.set()

    # NOTE: we intentionally do NOT remove .layout_lock or reset
    # _layout_gen here.  If the user realigned, we want the lock to
    # persist so the pipeline won't overwrite their layout.  The lock
    # is cleared on /api/reset (new chat) only.

    # Create / reuse run dir for this session
    if _run_dir is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        _run_dir = OUTPUTS_DIR / f"run_{stamp}"
        _run_dir.mkdir(parents=True, exist_ok=True)

    queue: Queue[dict | None] = Queue()
    gen_at_start = _layout_gen      # snapshot for this generation

    def emit(event_type: str, data: dict):
        # Block while realign mode is active (pipeline pauses)
        _pipeline_gate.wait()
        # If a realign happened mid-pipeline, suppress layout/debug events
        if _layout_gen > gen_at_start and event_type in ("outline_preview", "pcb_layout", "debug_image"):
            return
        queue.put({"type": event_type, **data})

    def run_in_thread():
        global _conversation_history
        try:
            _conversation_history = run_turn(
                user_message=req.message.strip(),
                history=_conversation_history,
                emit=emit,
                output_dir=_run_dir,
            )
        except Exception as e:
            queue.put({
                "type": "error",
                "message": str(e),
                "traceback": traceback.format_exc(),
            })
        finally:
            queue.put(None)  # sentinel

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()

    async def event_generator():
        import time as _time
        _last_data = _time.monotonic()
        while True:
            try:
                item = queue.get(timeout=0.05)
            except Empty:
                # Send keepalive comment every 15 s to prevent connection drop
                if _time.monotonic() - _last_data > 15:
                    yield ": keepalive\n\n"
                    _last_data = _time.monotonic()
                await asyncio.sleep(0.05)
                continue

            if item is None:
                break

            yield f"data: {json.dumps(item)}\n\n"
            _last_data = _time.monotonic()

            if item.get("type") == "error":
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── File serving ───────────────────────────────────────────────────

@app.get("/api/model/{name}")
def get_model(name: str):
    """Serve an STL file from the current session run."""
    if _run_dir is None:
        raise HTTPException(404, "No run yet.")
    stl = _run_dir / f"{name}.stl"
    if not stl.exists():
        raise HTTPException(404, f"{name}.stl not found.")
    return Response(
        content=stl.read_bytes(),
        media_type="model/stl",
        headers={
            "Content-Disposition": f"inline; filename={name}.stl",
            "Cache-Control": "no-cache",
        },
    )


@app.get("/api/model/download/{name}")
def download_model(name: str):
    if _run_dir is None:
        raise HTTPException(404, "No run yet.")
    stl = _run_dir / f"{name}.stl"
    if not stl.exists():
        raise HTTPException(404, f"{name}.stl not found.")
    return Response(
        content=stl.read_bytes(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={name}.stl"},
    )


@app.get("/api/images/{name}")
def get_image(name: str):
    """Serve a debug image from the current session."""
    if _run_dir is None:
        raise HTTPException(404, "No run yet.")

    for candidate in [
        _run_dir / "pcb" / f"{name}.png",
        _run_dir / f"{name}.png",
        _run_dir / "pcb" / f"pcb_{name}.png",
        _run_dir / f"pcb_{name}.png",
    ]:
        if candidate.exists():
            return FileResponse(
                candidate,
                media_type="image/png",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

    raise HTTPException(404, f"Image {name} not found.")


@app.get("/api/outputs/{run_id}/{path:path}")
def get_output_file(run_id: str, path: str):
    """Serve any file from a specific run."""
    full = OUTPUTS_DIR / run_id / path
    if not full.exists():
        raise HTTPException(404)
    return FileResponse(full)


# ── Printer info ─────────────────────────────────────────────────

@app.get("/api/printers")
def list_printers():
    """Return the list of supported printers for the UI dropdown."""
    return {
        "printers": [
            {"id": p.id, "label": p.label, "bed": f"{p.bed_width:.0f}×{p.bed_depth:.0f} mm"}
            for p in PRINTERS.values()
        ]
    }


# ── G-code endpoints ──────────────────────────────────────────────

class SliceRequest(BaseModel):
    printer: str | None = None


@app.post("/api/slice")
def slice_model(req: SliceRequest | None = None):
    """Slice the enclosure STL and generate staged G-code with pauses.

    Uses the cached pcb_layout and routing_result from the current
    session's run directory.  Returns metadata about the generated
    G-code including pause points and layer numbers.
    """
    if _run_dir is None:
        raise HTTPException(400, "No run yet — generate a design first.")

    stl_path = _run_dir / "enclosure.stl"
    if not stl_path.exists():
        raise HTTPException(400, "No enclosure STL — compile a design first.")

    layout_path = _run_dir / "pcb_layout.json"
    routing_path = _run_dir / "routing_result.json"
    if not layout_path.exists():
        raise HTTPException(400, "No layout data.")

    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    routing = {}
    if routing_path.exists():
        routing = json.loads(routing_path.read_text(encoding="utf-8"))

    global _printer_id
    printer_id = req.printer if req else None
    _printer_id = printer_id

    result = run_gcode_pipeline(
        stl_path=stl_path,
        output_dir=_run_dir,
        pcb_layout=layout,
        routing_result=routing,
        printer=printer_id,
    )

    if not result.success:
        raise HTTPException(500, result.message)

    # Extract components for step-by-step guide
    components = layout.get("components", [])

    return {
        "status": "ok",
        "staged_gcode": result.staged_gcode_path.name if result.staged_gcode_path else None,
        "raw_gcode": result.raw_gcode_path.name if result.raw_gcode_path else None,
        "pause_points": {
            "ink_layer_z": result.pause_points.ink_layer_z,
            "component_insert_z": result.pause_points.component_insert_z,
            "ink_layer_number": result.pause_points.ink_layer_number,
            "component_layer_number": result.pause_points.component_layer_number,
            "total_height": result.pause_points.total_height,
            "layer_height": result.pause_points.layer_height,
        } if result.pause_points else None,
        "postprocess": {
            "total_layers": result.postprocess.total_layers,
            "ink_layer": result.postprocess.ink_layer,
            "component_layer": result.postprocess.component_layer,
            "stages": result.postprocess.stages,
        } if result.postprocess else None,
        "stages": result.stages,
        "components": components,
    }


# NOTE: Static /api/gcode/ routes MUST be defined before the
# catch-all /api/gcode/{name} route, otherwise FastAPI matches the
# path parameter first.

@app.get("/api/gcode/download-bgcode")
def download_bgcode():
    """Download the binary G-code (.bgcode) for the current session."""
    if _run_dir is None:
        raise HTTPException(404, "No run yet.")
    bgcode = _run_dir / "enclosure_staged.bgcode"
    if not bgcode.exists():
        raise HTTPException(404, "enclosure_staged.bgcode not found.")
    return Response(
        content=bgcode.read_bytes(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=enclosure_staged.bgcode"},
    )


@app.get("/api/gcode/download/{name}")
def download_gcode(name: str):
    """Download a G-code file from the current session."""
    if _run_dir is None:
        raise HTTPException(404, "No run yet.")
    gcode = _run_dir / f"{name}.gcode"
    if not gcode.exists():
        raise HTTPException(404, f"{name}.gcode not found.")
    return Response(
        content=gcode.read_bytes(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={name}.gcode"},
    )


@app.get("/api/gcode/{name}")
def get_gcode(name: str):
    """Serve a G-code file from the current session run."""
    if _run_dir is None:
        raise HTTPException(404, "No run yet.")
    gcode = _run_dir / f"{name}.gcode"
    if not gcode.exists():
        raise HTTPException(404, f"{name}.gcode not found.")
    return Response(
        content=gcode.read_bytes(),
        media_type="text/plain",
        headers={
            "Content-Disposition": f"inline; filename={name}.gcode",
            "Cache-Control": "no-cache",
        },
    )


# ── G-code preview / viewer ───────────────────────────────────────

class OpenViewerRequest(BaseModel):
    format: str = "bgcode"   # "gcode" or "bgcode"


@app.post("/api/gcode/open-viewer")
def open_gcode_viewer(req: OpenViewerRequest | None = None):
    """Launch PrusaSlicer's G-code viewer.

    Accepts ``{"format": "gcode"}`` or ``{"format": "bgcode"}``.
    """
    if _run_dir is None:
        raise HTTPException(400, "No run yet.")

    fmt = (req.format if req else "bgcode").lower()
    if fmt == "bgcode":
        target = _run_dir / "enclosure_staged.bgcode"
    else:
        target = _run_dir / "enclosure_staged.gcode"

    if not target.exists():
        raise HTTPException(400, f"{target.name} not found — slice first.")

    exe = find_prusaslicer_gui()
    if not exe:
        raise HTTPException(500, "PrusaSlicer (GUI) not found on this system.")

    try:
        cmd: list[str] = [exe, "--gcodeviewer", str(target)]
        # Tell PrusaSlicer which printer to use so the correct bed is
        # shown (e.g. "Prusa CORE One HF0.4 nozzle").
        if _printer_id and _printer_id in PRINTERS:
            native = PRINTERS[_printer_id].native_printer
            if native:
                cmd.extend(["--printer-profile", native])
        subprocess.Popen(cmd)
    except Exception as e:
        raise HTTPException(500, f"Failed to launch viewer: {e}")

    return {"status": "ok", "message": f"G-code viewer launched ({target.name})."}


@app.get("/api/gcode/preview/{name}")
def preview_gcode(name: str):
    """Return G-code metadata for the web preview: layers, pauses, line count."""
    if _run_dir is None:
        raise HTTPException(404, "No run yet.")
    gcode = _run_dir / f"{name}.gcode"
    if not gcode.exists():
        raise HTTPException(404, f"{name}.gcode not found.")

    lines = gcode.read_text(encoding="utf-8").splitlines()
    layers: list[dict] = []
    pauses: list[dict] = []
    current_z = 0.0
    layer_idx = 0

    for i, line in enumerate(lines):
        if line.startswith(";Z:"):
            try:
                current_z = float(line[3:])
            except ValueError:
                pass
            layer_idx += 1
            layers.append({"line": i + 1, "z": current_z, "layer": layer_idx})
        elif "M601" in line:
            # Find the pause label (look backwards for ; PAUSE: ...)
            label = "Pause"
            for j in range(max(0, i - 8), i):
                if lines[j].strip().startswith("; PAUSE:"):
                    label = lines[j].strip().replace("; PAUSE: ", "")
                    break
            pauses.append({"line": i + 1, "z": current_z, "layer": layer_idx, "label": label})

    return {
        "name": name,
        "total_lines": len(lines),
        "total_layers": layer_idx,
        "layers": layers,
        "pauses": pauses,
    }


# ── Entry point ────────────────────────────────────────────────────

def main(host: str = "127.0.0.1", port: int = 8000):
    import uvicorn
    uvicorn.run("src.web.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()

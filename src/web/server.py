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
from src.agent.pipeline import run_pipeline, PipelineCancelled
from src.scad.shell import DEFAULT_HEIGHT_MM
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


# ── Build manager ──────────────────────────────────────────────────

import logging as _logging
_bm_log = _logging.getLogger("manufacturerAI.build")


class BuildManager:
    """Manages pipeline execution — cancellation, background threading, status.

    * For the LLM flow, the pipeline runs in the agent thread.  Call
      :meth:`prepare_for_agent` to get a cancel event the emit wrapper
      can check.
    * For realign / curve-edit, call :meth:`start_background`.
    * Only *one* build runs at a time — starting a new one cancels
      the previous.
    """

    def __init__(self):
        self._cancel = threading.Event()
        self._bg_thread: threading.Thread | None = None
        self._status = "idle"  # idle | running | done | error
        self._error: str | None = None
        self._progress: str = ""
        self._run_dir: Path | None = None

    # ── lifecycle ──────────────────────────────────────────────────

    def prepare_for_agent(self, run_dir: Path) -> threading.Event:
        """Prepare for an LLM pipeline run.

        Cancels any background build, resets state, and returns a
        *fresh* cancel event that the SSE emit wrapper should check.
        """
        self.cancel()
        self._cancel = threading.Event()
        self._status = "running"
        self._error = None
        self._progress = ""
        self._run_dir = run_dir
        return self._cancel

    def agent_done(self, success: bool, error: str | None = None):
        """Called when the agent pipeline finishes."""
        self._status = "done" if success else "error"
        self._error = error

    def start_background(self, run_dir: Path, *, start_from: str, stop_after: str | None = None):
        """Cancel any current build and start a new pipeline in a background thread."""
        self.cancel()
        self._cancel = threading.Event()
        self._status = "running"
        self._error = None
        self._progress = ""
        self._run_dir = run_dir

        cancel = self._cancel

        def _emit(event_type: str, data: dict):
            if cancel.is_set():
                raise PipelineCancelled()
            if event_type == "progress":
                self._progress = data.get("stage", "")

        def _run():
            try:
                result = run_pipeline(
                    run_dir, _emit, cancel,
                    start_from=start_from, stop_after=stop_after,
                )
                if result.get("status") == "error":
                    self._status = "error"
                    self._error = result.get("message", "Unknown error")
                else:
                    self._status = "done"
            except PipelineCancelled:
                self._status = "idle"
            except Exception as exc:
                self._status = "error"
                self._error = str(exc)
                _bm_log.exception("Background pipeline failed")

        self._bg_thread = threading.Thread(target=_run, daemon=True)
        self._bg_thread.start()

    def cancel(self):
        """Cancel whatever is running."""
        self._cancel.set()
        if self._bg_thread and self._bg_thread.is_alive():
            self._bg_thread.join(timeout=10)
        self._bg_thread = None

    def reset(self):
        """Full reset for new session."""
        self.cancel()
        self._cancel = threading.Event()
        self._status = "idle"
        self._error = None
        self._progress = ""
        self._run_dir = None

    # ── status ────────────────────────────────────────────────────

    @property
    def status(self) -> dict:
        model = None
        if self._run_dir:
            for name in ("print_plate", "enclosure"):
                if (self._run_dir / f"{name}.stl").exists():
                    model = name
                    break
        return {
            "status": self._status,
            "progress": self._progress,
            "model_name": model,
            "error": self._error,
        }


_build = BuildManager()


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
    global _conversation_history, _run_dir, _printer_id
    _conversation_history = []
    _run_dir = None
    _printer_id = None
    _build.reset()
    return {"status": "ok"}


@app.get("/api/shell_height")
def get_shell_height():
    """Return the default shell height so the UI knows the max."""
    return {"height_mm": DEFAULT_HEIGHT_MM}


@app.post("/api/update_curve")
def update_curve(req: CurveUpdateRequest):
    """Write new curve params and rebuild SCAD + STL in background.

    The frontend's client-side Three.js preview is updated instantly;
    this endpoint just keeps the on-disk STL in sync.
    """
    if _run_dir is None:
        raise HTTPException(400, "No run yet — generate a design first.")
    if not (_run_dir / "pcb_layout.json").exists():
        raise HTTPException(400, "No layout data — generate a design first.")

    # Write curve params to disk
    (_run_dir / "curve_params.json").write_text(json.dumps({
        "top_curve_length": req.top_curve_length,
        "top_curve_height": req.top_curve_height,
        "bottom_curve_length": req.bottom_curve_length,
        "bottom_curve_height": req.bottom_curve_height,
    }, indent=2), encoding="utf-8")

    # Rebuild SCAD + STL in background (pipeline "build" step only)
    _build.start_background(_run_dir, start_from="build", stop_after="build")

    return {"status": "ok", "stl_rebuilding": True}


@app.post("/api/update_layout")
def update_layout(req: LayoutUpdateRequest):
    """Move components, re-route traces, and rebuild STLs.

    Called from the Realign mode.  Steps:
    1. Patch component positions in pcb_layout.json (synchronous)
    2. Re-route traces (synchronous — takes seconds)
    3. Start SCAD + STL rebuild in background (takes minutes)
    """
    from src.pcb.router_bridge import route_traces as _route_traces, RouterError

    if _run_dir is None:
        raise HTTPException(400, "No run yet — generate a design first.")

    layout_path = _run_dir / "pcb_layout.json"
    if not layout_path.exists():
        raise HTTPException(400, "No layout data — generate a design first.")

    layout = json.loads(layout_path.read_text(encoding="utf-8"))

    # Cancel any running pipeline (LLM or previous realign)
    _build.cancel()

    # ── Patch component positions ──────────────────────────────────
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
                ko = comp.get("keepout", {})
                if ko.get("type") == "rectangle":
                    ko["width_mm"], ko["height_mm"] = ko["height_mm"], ko["width_mm"]
                if "body_width_mm" in comp and "body_height_mm" in comp:
                    comp["body_width_mm"], comp["body_height_mm"] = comp["body_height_mm"], comp["body_width_mm"]

    # Save updated layout
    layout_path.write_text(json.dumps(layout, indent=2), encoding="utf-8")

    # Write lock so a subsequent LLM turn doesn't re-submit the design
    (_run_dir / ".layout_lock").write_text("realigned", encoding="utf-8")

    # ── Re-route traces (synchronous — fast) ───────────────────────
    routing = {}
    routing_ok = False
    try:
        routing = _route_traces(layout, _run_dir)
        (_run_dir / "routing_result.json").write_text(
            json.dumps(routing, indent=2), encoding="utf-8"
        )
        routing_ok = routing.get("success", False)
    except (RouterError, Exception) as exc:
        _bm_log.warning("Re-route after realign failed: %s", exc, exc_info=True)

    has_debug_image = (_run_dir / "pcb_debug.png").exists()

    # ── Build shell preview data for frontend ──────────────────────
    curve_path = _run_dir / "curve_params.json"
    curves = (
        json.loads(curve_path.read_text(encoding="utf-8"))
        if curve_path.exists()
        else {}
    )
    outline = layout.get("board", {}).get("outline_polygon", [])
    from src.scad.shell import DEFAULT_HEIGHT_MM as _DHM
    from src.config.hardware import hw as _hw
    shell_preview = {
        "outline": outline,
        "height_mm": _DHM,
        "wall_mm": _hw.wall_thickness,
        "top_curve_length": curves.get("top_curve_length", 0),
        "top_curve_height": curves.get("top_curve_height", 0),
        "bottom_curve_length": curves.get("bottom_curve_length", 0),
        "bottom_curve_height": curves.get("bottom_curve_height", 0),
        "components": [
            {
                "type": c.get("type"),
                "center": c["center"],
                "body_width_mm": c.get("body_width_mm", 0),
                "body_height_mm": c.get("body_height_mm", 0),
            }
            for c in layout.get("components", [])
        ],
    }

    # ── Only rebuild STL if routing succeeded ──────────────────────
    failed_nets = []
    if routing_ok:
        _build.start_background(_run_dir, start_from="build", stop_after="build")
    else:
        failed_nets = [
            f.get("netName", str(f)) if isinstance(f, dict) else str(f)
            for f in routing.get("failed_nets", [])
        ]
        _bm_log.warning("Skipping STL build — routing failed (%d failed nets)", len(failed_nets))

    model_name = (
        "print_plate" if (_run_dir / "print_plate.stl").exists()
        else "enclosure" if (_run_dir / "enclosure.stl").exists()
        else None
    )
    return {
        "status": "ok",
        "layout": layout,
        "model_name": model_name,
        "has_debug_image": has_debug_image,
        "stl_rebuilding": routing_ok,
        "routing_ok": routing_ok,
        "failed_nets": failed_nets,
        "shell_preview": shell_preview,
    }


@app.post("/api/realign/pause")
def realign_pause():
    """User entered realign mode — cancel the running pipeline."""
    _build.cancel()
    return {"status": "paused"}


@app.post("/api/realign/resume")
def realign_resume():
    """Resume after cancelling realign (no-op — pipeline was cancelled)."""
    return {"status": "resumed"}


@app.get("/api/stl_status")
def stl_status():
    """Poll endpoint for background build progress."""
    s = _build.status
    return {
        "rebuilding": s["status"] == "running",
        "model_name": s["model_name"],
        "error": s["error"],
        "progress": s.get("progress", ""),
    }


@app.post("/api/generate/stream")
async def generate_stream(req: GenerateRequest):
    """Streaming endpoint.  Runs one agent turn in a background thread,
    pushes SSE events to the client via a Queue.
    """
    global _conversation_history, _run_dir

    if not req.message.strip():
        raise HTTPException(400, "Empty prompt.")

    # Create / reuse run dir for this session
    if _run_dir is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        _run_dir = OUTPUTS_DIR / f"run_{stamp}"
        _run_dir.mkdir(parents=True, exist_ok=True)

    # Prepare build manager — cancels any background build, gives us a cancel event
    cancel = _build.prepare_for_agent(_run_dir)

    queue: Queue[dict | None] = Queue()

    def emit(event_type: str, data: dict):
        if cancel.is_set():
            raise PipelineCancelled()
        queue.put({"type": event_type, **data})

    def run_in_thread():
        global _conversation_history
        try:
            _conversation_history = run_turn(
                user_message=req.message.strip(),
                history=_conversation_history,
                emit=emit,
                output_dir=_run_dir,
                cancel=cancel,
            )
            _build.agent_done(True)
        except PipelineCancelled:
            pass  # Silently exit — realign took over
        except Exception as e:
            _build.agent_done(False, str(e))
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
    """Serve a PNG image from the current run directory.

    Files are always flat in the run dir (e.g. pcb_debug.png,
    pcb_negative.png).  The client must request the exact stem.
    """
    if _run_dir is None:
        raise HTTPException(404, "No run yet.")

    img = _run_dir / f"{name}.png"
    if not img.exists():
        raise HTTPException(404, f"Image {name}.png not found.")

    return FileResponse(
        img,
        media_type="image/png",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


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

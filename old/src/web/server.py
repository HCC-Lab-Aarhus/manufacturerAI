from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from queue import Queue
import threading
import traceback

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.core.orchestrator import Orchestrator
from src.core.printer import get_printer_status, queue_print_job
from src.design.models import RemoteParams


# Global progress tracking for SSE - per-job queues
_job_queues: Dict[str, Queue] = {}
_current_job: Dict[str, Any] = {}
_job_results: Dict[str, Dict[str, Any]] = {}  # Store job results for retrieval


def _load_env():
    root = Path(__file__).resolve().parents[2]
    env_file = root / ".env"
    if env_file.exists():
        try:
            content = env_file.read_text(encoding="utf-8")
            for line in content.splitlines():
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                         os.environ[key] = val
        except Exception:
            pass

_load_env()


class PromptRequest(BaseModel):
    message: str
    use_llm: bool = True


class ChatMessage(BaseModel):
    role: str
    content: str


class PromptResponse(BaseModel):
    messages: list[ChatMessage]
    model_url: str | None
    printer_connected: bool
    debug_images: dict | None = None  # {"debug": url, "positive": url, "negative": url}
    models: dict | None = None  # {"top": url, "bottom": url} for multi-part enclosures


app = FastAPI(title="Remote GDT Web")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS_DIR = ROOT / "outputs" / "web"
STATIC_DIR = Path(__file__).resolve().parent / "static"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


_chat_log: list[ChatMessage] = []
_latest_stl: Path | None = None
_latest_run_dir: Path | None = None
_latest_design_spec: dict | None = None  # For incremental modifications


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def _make_run_dir() -> Path:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUTS_DIR / f"run_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _summarize_params(params: RemoteParams) -> str:
    r = params.remote
    b = params.buttons
    return (
        "Parameters validated. "
        f"Remote: {r.length_mm}×{r.width_mm}×{r.thickness_mm} mm, wall {r.wall_mm} mm. "
        f"Buttons: {b.rows}×{b.cols}, {b.diam_mm} mm diameter, {b.spacing_mm} mm spacing."
    )


from src.llm.client import GeminiClient

def _is_design_request(text: str) -> bool:
    text_lower = text.lower()
    # Keywords that strongly suggest a design request
    design_keywords = {
        "remote", "button", "switch", "hole", "length", "width", "thick", "mm",
        "print", "stl", "model", "design", "make", "create", "generate", "cad", "rows", "cols",
        "add", "remove", "modify", "change", "move"
    }
    
    # If any keyword is found, assume design
    if any(kw in text_lower for kw in design_keywords):
        return True
    
    # If it contains digits (dimensions), assume design
    if any(ch.isdigit() for ch in text):
        return True

    # Otherwise, assume chat
    return False

@app.post("/api/prompt", response_model=PromptResponse)
def prompt_to_model(req: PromptRequest) -> PromptResponse:
    global _latest_stl, _latest_run_dir, _latest_design_spec

    print("\n" + "="*80)
    print("[SERVER] NEW REQUEST")
    print("="*80)
    print(f"[SERVER] Message: {req.message[:100]}{'...' if len(req.message) > 100 else ''}")
    print(f"[SERVER] use_llm: {req.use_llm}")

    if not req.message.strip():
        print("[SERVER] ✗ Empty prompt rejected")
        raise HTTPException(status_code=400, detail="Prompt is empty.")

    _chat_log.append(ChatMessage(role="user", content=req.message.strip()))

    # Check for simple chat vs design
    is_design = _is_design_request(req.message)
    print(f"[SERVER] Request type: {'DESIGN' if is_design else 'CHAT'}")
    
    if not is_design:
         # Just chat
         print("[SERVER] PATH: Chat conversation mode")
         reply = ""
         try:
             # Force reload of key from env to be safe
             api_key = os.environ.get("GEMINI_API_KEY")
             if not api_key:
                 print("[SERVER] ⚠ WARNING: GEMINI_API_KEY is missing in chat handler.")
             
             print("[SERVER] PATH: Attempting LLM chat response...")
             client = GeminiClient(api_key=api_key)
             # Attempt to use LLM for conversational reply
             chat_system_prompt = """You are ManufacturerAI, a friendly assistant that helps users design 3D-printable remote controls.

What you can help with:
- Designing custom remote controls with buttons (e.g. "make a remote with 3 buttons")
- Specifying device dimensions (e.g. "120mm long, 45mm wide")
- Choosing button labels and layout (e.g. "power button at top, volume buttons on the side")
- Modifying existing designs (e.g. "add another button", "make it wider")

Examples of things users can ask you to design:
- "A simple TV remote with 4 buttons"
- "A small remote 80x40mm with volume up/down buttons"
- "A game controller style remote with 6 buttons"
- "Add a power button to my current design"

Be helpful, concise, and guide users to describe what kind of remote they want. When users ask what you can do or for examples, explain your capabilities clearly.

Never mention portfolios, websites, or external resources - you generate designs directly.

Output JSON: { "reply": "your response" }"""
             reply_json = client.complete_json(
                 system=chat_system_prompt,
                 user=req.message
             )
             reply = reply_json.get("reply", "")
             print(f"[SERVER] ✓ LLM chat response received: {len(reply)} chars")
         except Exception as e:
             # Fallback if LLM fails (e.g. quota limit)
             print(f"[SERVER] ✗ Chat LLM failed: {e}")
             print("[SERVER] PATH: FALLBACK → Using static chat responses")
             import traceback
             traceback.print_exc()
             pass

         if not reply:
             print("[SERVER] PATH: FALLBACK → Using default greeting")
             reply = "Hello! I am ManufacturerAI. Describe a remote control (e.g., '100x50mm with 2x2 buttons'), and I will generate a 3D printable design for you."
             msg_lower = req.message.lower().strip()
             if "bye" in msg_lower:
                 reply = "Goodbye! Happy printing."

         _chat_log.append(ChatMessage(role="assistant", content=reply))
         status = get_printer_status()
         return PromptResponse(
            messages=_chat_log,
            model_url=None,
            printer_connected=status.connected,
        )

    print("[SERVER] PATH: Design generation mode")
    run_dir = _make_run_dir()
    print(f"[SERVER] Run directory: {run_dir}")
    blender_bin = os.environ.get("BLENDER_BIN")
    print(f"[SERVER] Blender binary: {blender_bin or '(not set)'}")
    print(f"[SERVER] Using parametric mode: True")
    orch = Orchestrator(blender_bin=blender_bin, use_parametric=True)
    
    # Set up progress callback
    def progress_callback(stage: str, iteration: int, max_iter: int, message: Optional[str]) -> None:
        global _current_job
        _current_job = {
            "stage": stage,
            "iteration": iteration,
            "max_iterations": max_iter,
            "message": message or "",
            "timestamp": datetime.now().isoformat()
        }
        _progress_queue.put(_current_job.copy())
    
    orch.set_progress_callback(progress_callback)

    # Check if this is a modification of existing design
    from src.llm.consultant_agent import is_modification_request
    previous_design = None
    has_previous = _latest_design_spec is not None
    is_mod_request = is_modification_request(req.message) if has_previous else False
    print(f"[SERVER] Has previous design: {has_previous}")
    print(f"[SERVER] Is modification request: {is_mod_request}")
    
    if has_previous and is_mod_request:
        print("[SERVER] PATH: MODIFICATION mode - using previous design as base")
        previous_design = _latest_design_spec
    else:
        print("[SERVER] PATH: NEW DESIGN mode - starting fresh")

    try:
        new_design_spec = orch.run_from_prompt(
            req.message, 
            run_dir, 
            use_llm=req.use_llm,
            previous_design=previous_design
        )
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        traceback.print_exc()
        if "quota" in str(e).lower() or "429" in str(e) or "exhausted" in str(e).lower():
             raise HTTPException(status_code=429, detail="Gemini API Quota Exceeded. Please try again later.")
        if "not found" in str(e).lower() or "404" in str(e):
             raise HTTPException(status_code=404, detail="Gemini Model Not Found. Invalid model name configured.")
        # Return full traceback in detail for debugging
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}\n\nTraceback:\n{tb}")

    # Update module-level variables (already declared global at function start)
    _latest_run_dir = run_dir
    _latest_design_spec = new_design_spec  # Store for future modification requests
    
    params_path = run_dir / "params_validated.json"
    
    # Handle both old Blender workflow and new parametric workflow
    print(f"[SERVER] Checking output workflow...")
    if params_path.exists():
        print("[SERVER] PATH: Legacy Blender workflow (params_validated.json found)")
        params = RemoteParams.model_validate(json.loads(params_path.read_text(encoding="utf-8")))
        assistant_text = _summarize_params(params) + " Generated STL and report."
    else:
        # New workflow - read from design_spec
        design_spec_path = run_dir / "design_spec.json"
        if design_spec_path.exists():
            print("[SERVER] PATH: New parametric workflow (design_spec.json found)")
            design_spec = json.loads(design_spec_path.read_text(encoding="utf-8"))
            device = design_spec.get("device_constraints", {})
            buttons = design_spec.get("buttons", [])
            assistant_text = (
                f"Design completed! "
                f"Remote: {device.get('length_mm', '?')}×{device.get('width_mm', '?')}mm, "
                f"{len(buttons)} buttons. "
                "Generated enclosure files."
            )
        else:
            print("[SERVER] PATH: FALLBACK → No spec files found, using generic message")
            assistant_text = "Design completed. Files generated."
    
    # Check for STL files - all models rendered for preview
    print("[SERVER] Searching for STL files...")
    models_dict = None
    
    # Check for all STL files (unified remote model)
    remote_stl = run_dir / "remote.stl"
    battery_hatch_stl = run_dir / "battery_hatch.stl"
    print_plate_stl = run_dir / "print_plate.stl"
    
    # Legacy files for backwards compatibility
    top_stl = run_dir / "top_shell.stl"
    bottom_stl = run_dir / "bottom_shell.stl"
    combined_stl = run_dir / "combined_assembly.stl"
    
    if remote_stl.exists() or print_plate_stl.exists() or battery_hatch_stl.exists():
        models_dict = {}
        if remote_stl.exists():
            models_dict["remote"] = "/api/model/remote"
            _latest_stl = remote_stl
            print(f"[SERVER] PATH: Found STL → remote.stl")
        if battery_hatch_stl.exists():
            models_dict["hatch"] = "/api/model/hatch"
            print(f"[SERVER] PATH: Found STL → battery_hatch.stl")
        if print_plate_stl.exists():
            models_dict["print_plate"] = "/api/model/print_plate"
            print(f"[SERVER] PATH: Found STL → print_plate.stl")
    elif top_stl.exists() or bottom_stl.exists() or combined_stl.exists():
        # Legacy support for old format
        models_dict = {}
        if top_stl.exists():
            models_dict["top"] = "/api/model/top"
            _latest_stl = top_stl
            print(f"[SERVER] PATH: Found legacy STL → top_shell.stl")
        if bottom_stl.exists():
            models_dict["bottom"] = "/api/model/bottom"
            if _latest_stl is None:
                _latest_stl = bottom_stl
            print(f"[SERVER] PATH: Found legacy STL → bottom_shell.stl")
        if battery_hatch_stl.exists():
            models_dict["hatch"] = "/api/model/hatch"
            print(f"[SERVER] PATH: Found STL → battery_hatch.stl")
        if combined_stl.exists():
            models_dict["combined"] = "/api/model/combined"
            print(f"[SERVER] PATH: Found legacy STL → combined_assembly.stl")
    elif (run_dir / "remote.stl").exists():
        _latest_stl = run_dir / "remote.stl"
        print(f"[SERVER] PATH: Found STL → remote.stl")
    elif (run_dir / "remote_body.stl").exists():
        _latest_stl = run_dir / "remote_body.stl"
        print(f"[SERVER] PATH: Found legacy STL → remote_body.stl")
    else:
        # Check if SCAD files exist (OpenSCAD not installed)
        scad_file = run_dir / "combined_assembly.scad"
        if scad_file.exists():
            print("[SERVER] PATH: FALLBACK → SCAD files only (OpenSCAD not installed)")
            assistant_text += " (OpenSCAD files ready - render manually or install OpenSCAD for STL)"
            _latest_stl = None
        else:
            print("[SERVER] ✗ ERROR: No model files generated!")
            raise HTTPException(status_code=500, detail="No model files were generated.")

    _chat_log.append(ChatMessage(role="assistant", content=assistant_text))

    # Build debug image URLs if they exist
    debug_images = None
    debug_file = run_dir / "pcb_debug.png"
    if debug_file.exists():
        print("[SERVER] ✓ Debug images found, adding to response")
        debug_images = {
            "debug": "/api/images/debug",
            "positive": "/api/images/positive",
            "negative": "/api/images/negative"
        }
    else:
        print("[SERVER] ⚠ No debug images found")
    
    print(f"[SERVER] Response: model_url={'yes' if _latest_stl else 'no'}, debug_images={'yes' if debug_images else 'no'}")
    print("="*80 + "\n")

    status = get_printer_status()
    return PromptResponse(
        messages=_chat_log,
        model_url="/api/model/latest" if _latest_stl else None,
        printer_connected=status.connected,
        debug_images=debug_images,
        models=models_dict,
    )


def _run_pipeline_with_events(job_id: str, message: str, use_llm: bool, run_dir: Path, previous_design: Optional[dict]):
    """Run the pipeline in a background thread, pushing events to job queue."""
    global _latest_stl, _latest_run_dir, _latest_design_spec
    
    queue = _job_queues.get(job_id)
    if not queue:
        return
    
    # Track what we've already sent to avoid duplicates
    debug_images_sent = False
    
    def send_event(event_type: str, data: dict):
        queue.put({"type": event_type, **data})
    
    def check_and_send_debug_images():
        nonlocal debug_images_sent
        if debug_images_sent:
            return
        debug_file = run_dir / "pcb_debug.png"
        if debug_file.exists():
            debug_images = {
                "debug": "/api/images/debug",
                "positive": "/api/images/positive",
                "negative": "/api/images/negative"
            }
            send_event("debug_images", {"urls": debug_images})
            debug_images_sent = True
    
    try:
        blender_bin = os.environ.get("BLENDER_BIN")
        orch = Orchestrator(blender_bin=blender_bin, use_parametric=True)
        
        # Set up progress callback that also checks for intermediate outputs
        def progress_callback(stage: str, iteration: int, max_iter: int, msg: Optional[str]) -> None:
            send_event("progress", {
                "stage": stage,
                "iteration": iteration,
                "max_iterations": max_iter,
                "message": msg or ""
            })
            
            # Check for debug images when PCB is complete
            if stage == "PCB_COMPLETE":
                check_and_send_debug_images()
        
        orch.set_progress_callback(progress_callback)
        
        # Send starting event
        send_event("status", {"message": "Starting design generation..."})
        
        try:
            new_design_spec = orch.run_from_prompt(
                message,
                run_dir,
                use_llm=use_llm,
                previous_design=previous_design
            )
        except Exception as e:
            tb = traceback.format_exc()
            send_event("error", {"message": str(e), "traceback": tb})
            return
        
        # Update global state
        _latest_run_dir = run_dir
        _latest_design_spec = new_design_spec
        
        # Check for debug images one more time (in case PCB_COMPLETE didn't fire)
        check_and_send_debug_images()
        
        # Check for print_plate.stl (the only STL we generate now)
        print_plate_stl = run_dir / "print_plate.stl"
        
        if print_plate_stl.exists():
            _latest_stl = print_plate_stl
            send_event("models", {"urls": {"print_plate": "/api/model/print_plate"}})
        
        # Build final result message
        design_spec_path = run_dir / "design_spec.json"
        if design_spec_path.exists():
            design_spec = json.loads(design_spec_path.read_text(encoding="utf-8"))
            device = design_spec.get("device_constraints", {})
            buttons = design_spec.get("buttons", [])
            result_message = (
                f"Design completed! "
                f"Remote: {device.get('length_mm', '?')}×{device.get('width_mm', '?')}mm, "
                f"{len(buttons)} buttons."
            )
        else:
            result_message = "Design completed."
        
        send_event("complete", {
            "message": result_message,
            "model_url": "/api/model/print_plate" if print_plate_stl.exists() else None,
            "debug_images": {
                "debug": "/api/images/debug",
                "positive": "/api/images/positive",
                "negative": "/api/images/negative"
            } if debug_images_sent else None
        })
        
    except Exception as e:
        tb = traceback.format_exc()
        send_event("error", {"message": str(e), "traceback": tb})


@app.post("/api/generate/stream")
async def generate_stream(req: PromptRequest):
    """
    Streaming endpoint that generates a design and sends Server-Sent Events.
    
    Events:
    - status: {"message": "..."}
    - progress: {"stage": "...", "iteration": N, "max_iterations": M, "message": "..."}
    - debug_images: {"urls": {"debug": "...", "positive": "...", "negative": "..."}}
    - models: {"urls": {"remote": "...", "hatch": "...", "print_plate": "..."}}
    - complete: {"message": "...", "models": {...}, "debug_images": {...}}
    - error: {"message": "...", "traceback": "..."}
    """
    global _latest_run_dir, _latest_design_spec
    
    # Check for simple chat vs design
    is_design = _is_design_request(req.message)
    
    if not is_design:
        # Return a simple response for chat (non-streaming)
        async def chat_response():
            msg = {"type": "chat", "message": "Please describe a remote control to generate a design."}
            yield f"data: {json.dumps(msg)}\n\n"
            yield f"data: {json.dumps({'type': 'complete', 'message': 'Ready'})}\n\n"
        
        return StreamingResponse(
            chat_response(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
        )
    
    # Create job
    job_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    _job_queues[job_id] = Queue()
    
    run_dir = _make_run_dir()
    _latest_run_dir = run_dir
    
    # Check if this is a modification of existing design
    from src.llm.consultant_agent import is_modification_request
    previous_design = None
    if _latest_design_spec and is_modification_request(req.message):
        previous_design = _latest_design_spec
    
    # Add user message to chat log
    _chat_log.append(ChatMessage(role="user", content=req.message.strip()))
    
    # Start pipeline in background thread
    thread = threading.Thread(
        target=_run_pipeline_with_events,
        args=(job_id, req.message, req.use_llm, run_dir, previous_design),
        daemon=True
    )
    thread.start()
    
    async def event_generator():
        queue = _job_queues.get(job_id)
        if not queue:
            return
        
        try:
            while True:
                # Check for events
                try:
                    # Use loop to avoid blocking async
                    for _ in range(10):  # Check multiple times per iteration
                        if not queue.empty():
                            event = queue.get_nowait()
                            yield f"data: {json.dumps(event)}\n\n"
                            
                            # If complete or error, end stream
                            if event.get("type") in ("complete", "error"):
                                return
                        else:
                            await asyncio.sleep(0.05)
                            break
                    else:
                        await asyncio.sleep(0.1)
                except Exception:
                    await asyncio.sleep(0.1)
                
                # Send keepalive every second
                await asyncio.sleep(0.1)
                
        finally:
            # Clean up job queue
            if job_id in _job_queues:
                del _job_queues[job_id]
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )


@app.get("/api/progress")
async def get_progress_stream():
    """
    Server-Sent Events endpoint for real-time pipeline progress.
    
    Returns events like:
    data: {"stage": "GENERATE_PCB", "iteration": 2, "max_iterations": 5, "message": "Creating PCB layout"}
    """
    async def event_generator():
        while True:
            try:
                # Non-blocking check with short timeout
                if not _progress_queue.empty():
                    progress = _progress_queue.get_nowait()
                    yield f"data: {json.dumps(progress)}\n\n"
                    
                    # If done or error, close stream
                    if progress.get("stage") in ("DONE", "ERROR"):
                        break
                else:
                    # Send keepalive
                    yield f": keepalive\n\n"
                
                await asyncio.sleep(0.1)
            except Exception:
                break
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.get("/api/progress/current")
def get_current_progress() -> Dict[str, Any]:
    """Get the current pipeline progress state (polling alternative to SSE)."""
    if _current_job:
        return _current_job
    return {"stage": "IDLE", "iteration": 0, "max_iterations": 0, "message": "No job running"}


from fastapi.responses import Response

@app.get("/api/model/print_plate")
def get_print_plate():
    """Serve the print plate STL (main output - remote + hatch combined)."""
    if _latest_run_dir is None:
        raise HTTPException(status_code=404, detail="No design generated yet.")
    stl_path = _latest_run_dir / "print_plate.stl"
    if not stl_path.exists():
        raise HTTPException(status_code=404, detail="Print plate STL not available.")
    
    content = stl_path.read_bytes()
    return Response(
        content=content,
        media_type="model/stl",
        headers={
            "Content-Disposition": "inline; filename=print_plate.stl",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        }
    )


@app.get("/api/model/download")
def download_model():
    """Download the print plate STL file."""
    if _latest_run_dir is None:
        raise HTTPException(status_code=404, detail="No design generated yet.")
    
    stl_path = _latest_run_dir / "print_plate.stl"
    
    if not stl_path.exists():
        raise HTTPException(status_code=404, detail="Print plate STL not available.")
    
    content = stl_path.read_bytes()
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename=print_plate.stl",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        }
    )


@app.get("/api/images/debug")
def get_debug_image() -> FileResponse:
    """Serve the PCB debug visualization image."""
    if _latest_run_dir is None:
        raise HTTPException(status_code=404, detail="No design generated yet.")
    img_path = _latest_run_dir / "pcb_debug.png"
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="Debug image not available.")
    return FileResponse(img_path, media_type="image/png", filename="pcb_debug.png")


@app.get("/api/images/positive")
def get_positive_mask() -> FileResponse:
    """Serve the PCB positive mask (conductive areas)."""
    if _latest_run_dir is None:
        raise HTTPException(status_code=404, detail="No design generated yet.")
    img_path = _latest_run_dir / "pcb_positive.png"
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="Positive mask not available.")
    return FileResponse(img_path, media_type="image/png", filename="pcb_positive.png")


@app.get("/api/images/negative")
def get_negative_mask() -> FileResponse:
    """Serve the PCB negative mask (non-conductive areas)."""
    if _latest_run_dir is None:
        raise HTTPException(status_code=404, detail="No design generated yet.")
    img_path = _latest_run_dir / "pcb_negative.png"
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="Negative mask not available.")
    return FileResponse(img_path, media_type="image/png", filename="pcb_negative.png")


class PrintRequest(BaseModel):
    model_url: str | None = None


@app.get("/api/printer/status")
def printer_status() -> dict[str, Any]:
    status = get_printer_status()
    return {"connected": status.connected, "details": status.details}


@app.post("/api/print")
def print_latest(_: PrintRequest) -> dict[str, Any]:
    status = get_printer_status()
    if not status.connected:
        raise HTTPException(status_code=409, detail="Printer not connected.")
    if _latest_stl is None or not _latest_stl.exists():
        raise HTTPException(status_code=404, detail="No model generated yet.")

    job_id = queue_print_job(_latest_stl)
    return {"ok": True, "job_id": job_id}


def main(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(
        "src.web.server:app",
        host=host,
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.core.orchestrator import Orchestrator
from src.core.printer import get_printer_status, queue_print_job
from src.design.models import RemoteParams


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


app = FastAPI(title="Remote GDT Web")

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS_DIR = ROOT / "outputs" / "web"
STATIC_DIR = Path(__file__).resolve().parent / "static"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


_chat_log: list[ChatMessage] = []
_latest_stl: Path | None = None


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
        "remote", "button", "switch", "led", "hole", "length", "width", "thick", "mm",
        "print", "stl", "model", "design", "make", "create", "generate", "cad", "rows", "cols"
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
    global _latest_stl

    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Prompt is empty.")

    _chat_log.append(ChatMessage(role="user", content=req.message.strip()))

    # Check for simple chat vs design
    if not _is_design_request(req.message):
         # Just chat
         reply = ""
         try:
             # Force reload of key from env to be safe
             api_key = os.environ.get("GEMINI_API_KEY")
             if not api_key:
                 print("WARNING: GEMINI_API_KEY is missing in chat handler.")
             
             client = GeminiClient(api_key=api_key)
             # Attempt to use LLM for conversational reply
             reply_json = client.complete_json(
                 system="You are ManufacturerAI. If the user gets chatty, be friendly and casual. If they talk about designs, be professional. Keep it brief. Output JSON: { \"reply\": \"your response\" }",
                 user=req.message
             )
             reply = reply_json.get("reply", "")
         except Exception as e:
             # Fallback if LLM fails (e.g. quota limit)
             print(f"Chat Generation Failed: {e}")
             import traceback
             traceback.print_exc()
             pass

         if not reply:
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

    run_dir = _make_run_dir()
    blender_bin = os.environ.get("BLENDER_BIN")
    print(f"Using Blender binary: {blender_bin}")
    orch = Orchestrator(blender_bin=blender_bin)

    try:
        orch.run_from_prompt(req.message, run_dir, use_llm=req.use_llm)
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

    params_path = run_dir / "params_validated.json"
    params = RemoteParams.model_validate(json.loads(params_path.read_text(encoding="utf-8")))

    _latest_stl = run_dir / "remote_body.stl"
    if not _latest_stl.exists():
        raise HTTPException(status_code=500, detail="STL was not generated.")

    assistant_text = _summarize_params(params) + " Generated STL and report."
    _chat_log.append(ChatMessage(role="assistant", content=assistant_text))

    status = get_printer_status()
    return PromptResponse(
        messages=_chat_log,
        model_url="/api/model/latest",
        printer_connected=status.connected,
    )


@app.get("/api/model/latest")
def get_latest_model() -> FileResponse:
    if _latest_stl is None or not _latest_stl.exists():
        raise HTTPException(status_code=404, detail="No model generated yet.")
    return FileResponse(_latest_stl, media_type="model/stl", filename=_latest_stl.name)


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

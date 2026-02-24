"""
Agent loop — multi-turn Gemini conversation with automatic pipeline.

The LLM handles conversation with the user and submits designs via
the ``submit_design`` tool call.  The manufacturing pipeline (validate →
place → route → SCAD → compile) runs automatically when a design is
submitted.  Errors feed back to the LLM so it can iterate.
"""

from __future__ import annotations

import json
import logging
import os
import time
import traceback
from pathlib import Path
from typing import Any, Callable

import google.generativeai as genai

log = logging.getLogger("manufacturerAI.agent")

from src.agent.prompts import build_system_prompt
from src.agent.pipeline import run_pipeline
from src.agent.tools import configure as configure_tools, EmitFn

# ── Gemini tool declarations (minimal set) ────────────────────────

_TOOL_DECLARATIONS = [
    genai.protos.FunctionDeclaration(
        name="think",
        description=(
            "Internal reasoning scratchpad. The user does NOT see this. "
            "Use freely to plan, reason about geometry, validate your "
            "math, etc."
        ),
        parameters=genai.protos.Schema(
            type=genai.protos.Type.OBJECT,
            properties={
                "reasoning": genai.protos.Schema(
                    type=genai.protos.Type.STRING,
                    description="Your internal reasoning / chain of thought.",
                ),
            },
            required=["reasoning"],
        ),
    ),
    genai.protos.FunctionDeclaration(
        name="submit_design",
        description=(
            "Submit a complete remote-control design for manufacturing. "
            "This triggers the full automated pipeline: geometry validation, "
            "component placement, trace routing, SCAD generation, and STL "
            "compilation.  The result (success or detailed error) will be "
            "returned so you can inform the user or iterate on the design."
        ),
        parameters=genai.protos.Schema(
            type=genai.protos.Type.OBJECT,
            properties={
                "outline_type": genai.protos.Schema(
                    type=genai.protos.Type.STRING,
                    description=(
                        "Shape type: 'polygon' (default — use provided vertices), "
                        "'ellipse' (generate a smooth ellipse from the bounding box), "
                        "or 'racetrack' (rectangle with semicircular ends). "
                        "For 'ellipse' and 'racetrack', the outline field is "
                        "optional — just provide width/height as a simple "
                        "rectangle [[0,0],[W,0],[W,L],[0,L]]."
                    ),
                ),
                "outline": genai.protos.Schema(
                    type=genai.protos.Type.ARRAY,
                    items=genai.protos.Schema(
                        type=genai.protos.Type.ARRAY,
                        items=genai.protos.Schema(type=genai.protos.Type.NUMBER),
                    ),
                    description=(
                        "Polygon outline as list of [x, y] vertices in mm, "
                        "counter-clockwise winding.  Min 4 vertices. "
                        "For outline_type='ellipse'/'racetrack', just provide "
                        "a bounding rectangle."
                    ),
                ),
                "button_positions": genai.protos.Schema(
                    type=genai.protos.Type.ARRAY,
                    items=genai.protos.Schema(
                        type=genai.protos.Type.OBJECT,
                        properties={
                            "id": genai.protos.Schema(type=genai.protos.Type.STRING),
                            "label": genai.protos.Schema(type=genai.protos.Type.STRING),
                            "x": genai.protos.Schema(type=genai.protos.Type.NUMBER),
                            "y": genai.protos.Schema(type=genai.protos.Type.NUMBER),
                        },
                        required=["id", "x", "y"],
                    ),
                    description="Button positions with id, label, x (mm), y (mm).",
                ),
                "top_curve_length": genai.protos.Schema(
                    type=genai.protos.Type.NUMBER,
                    description=(
                        "How far inward (mm) the rounded top edge curves "
                        "from the outer perimeter. 0 or omit for a flat top. "
                        "Typical values: 1–4 mm."
                    ),
                ),
                "top_curve_height": genai.protos.Schema(
                    type=genai.protos.Type.NUMBER,
                    description=(
                        "Vertical extent (mm) of the rounded zone measured "
                        "down from the top of the shell. 0 or omit for a flat "
                        "top. Typical values: 2–6 mm."
                    ),
                ),
                "bottom_curve_length": genai.protos.Schema(
                    type=genai.protos.Type.NUMBER,
                    description=(
                        "How far inward (mm) the rounded bottom edge curves "
                        "from the outer perimeter. 0 or omit for a flat bottom. "
                        "Typical values: 1–3 mm."
                    ),
                ),
                "bottom_curve_height": genai.protos.Schema(
                    type=genai.protos.Type.NUMBER,
                    description=(
                        "Vertical extent (mm) of the rounded zone measured "
                        "up from the bottom of the shell. 0 or omit for a flat "
                        "bottom. Typical values: 1–3 mm."
                    ),
                ),
            },
            required=["outline", "button_positions"],
        ),
    ),
]


# ── API call logger ────────────────────────────────────────────────

class _ApiLog:
    """Appends every API interaction to a JSONL file for debugging."""

    def __init__(self, path: Path):
        self._path = path
        self._turn = 0
        self._path.touch()

    def _write(self, entry: dict) -> None:
        entry["ts"] = time.time()
        entry["turn"] = self._turn
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def log(self, role: str, **kwargs) -> None:
        self._write({"role": role, **kwargs})

    def next_turn(self) -> None:
        self._turn += 1


# ── Main entry point ──────────────────────────────────────────────

MAX_TURNS = 20  # safety limit per single user message


def run_turn(
    user_message: str,
    history: list,
    emit: EmitFn,
    output_dir: str | Path,
    model_name: str = "gemini-2.5-pro",
) -> list:
    """
    Run a single conversational turn.

    Args:
        user_message: The new message from the user.
        history: List of prior Content proto objects (conversation so far).
        emit: Callback(event_type, data_dict) for streaming events to UI.
        output_dir: Directory for pipeline outputs.
        model_name: Gemini model to use.

    Returns:
        Updated history list (includes this turn's exchange).
    """
    # ── Setup ──────────────────────────────────────────────────────
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY or GOOGLE_API_KEY environment variable.")

    genai.configure(api_key=api_key)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = output_dir.name

    configure_tools(emit, output_dir, run_id)

    api_log = _ApiLog(output_dir / "api_calls.jsonl")
    if not log.handlers:
        log.setLevel(logging.DEBUG)

    emit("progress", {"stage": "Thinking..."})

    # ── Create model + chat with history ───────────────────────────
    tool_proto = genai.protos.Tool(function_declarations=_TOOL_DECLARATIONS)

    model = genai.GenerativeModel(
        model_name=model_name,
        tools=[tool_proto],
        system_instruction=build_system_prompt(),
    )

    chat = model.start_chat(history=history)

    # ── Send user message ──────────────────────────────────────────
    api_log.log("user", text=user_message)
    response = _safe_send(chat, user_message)
    api_log.next_turn()

    # ── Process model responses (tool-call loop) ───────────────────
    empty_retries = 0
    _pipeline_done = False          # True once submit_design succeeds
    _pipeline_attempts = 0          # cap retries to avoid parallel outlines
    for _turn_idx in range(MAX_TURNS):
        is_empty = isinstance(response, _EmptyResponse)
        function_calls = _extract_function_calls(response)
        text = _extract_text(response)

        # ── Case 1: empty response (SDK IndexError / no candidates) ──
        if is_empty and not function_calls and not text:
            empty_retries += 1
            if empty_retries > 3:
                log.warning("Too many empty responses, stopping.")
                break
            log.info("Empty response from model, nudging to continue...")
            try:
                response = _safe_send(
                    chat,
                    "Continue with the next step. If you have enough information, call submit_design now.",
                )
                api_log.next_turn()
            except Exception as e:
                api_log.log("error", message=str(e))
                emit("error", {"message": f"Gemini API error: {e}"})
                break
            continue

        empty_retries = 0  # reset on any real response

        # ── Case 2: text-only, no function calls ── send to user ──
        if not function_calls:
            if text:
                emit("chat", {"role": "assistant", "text": text})
                api_log.log("model_text", text=text)
            break

        # ── Case 3: function calls (possibly with text) ─────────────
        # If model sent text alongside function calls, show it
        if text:
            emit("chat", {"role": "assistant", "text": text})
            api_log.log("model_text", text=text)

        # Dispatch function calls, collect responses
        fn_response_parts = []

        for fc in function_calls:
            name = fc.name
            args = _proto_to_dict(fc.args)
            api_log.log("model_call", name=name, args=args)

            if name == "think":
                reasoning = args.get("reasoning", "")
                emit("thinking", {"text": reasoning})
                result = {"status": "ok"}

            elif name == "submit_design":
                _pipeline_attempts += 1
                if _pipeline_done:
                    # Pipeline already succeeded — don't run again
                    result = {
                        "status": "ok",
                        "message": "Design already submitted and built successfully.",
                    }
                elif _pipeline_attempts > 2:
                    # Cap retries to prevent multiple outlines
                    result = {
                        "status": "error",
                        "step": "pipeline",
                        "message": (
                            "Maximum pipeline attempts reached. "
                            "Please report the final result to the user."
                        ),
                    }
                else:
                    emit("progress", {"stage": "Running manufacturing pipeline..."})
                    try:
                        result = run_pipeline(
                            outline=args.get("outline", []),
                            button_positions=args.get("button_positions", []),
                            emit=emit,
                            output_dir=output_dir,
                            outline_type=args.get("outline_type", "polygon"),
                            top_curve_length=float(args.get("top_curve_length", 0)),
                            top_curve_height=float(args.get("top_curve_height", 0)),
                            bottom_curve_length=float(args.get("bottom_curve_length", 0)),
                            bottom_curve_height=float(args.get("bottom_curve_height", 0)),
                        )
                    except Exception as e:
                        log.exception("Pipeline crashed")
                        result = {
                            "status": "error",
                            "step": "pipeline",
                            "message": str(e),
                            "traceback": traceback.format_exc(),
                        }

                    if result.get("status") == "success":
                        _pipeline_done = True

            else:
                result = {"status": "error", "message": f"Unknown tool: {name}"}

            api_log.log("tool_result", name=name, result=result)

            fn_response_parts.append(
                genai.protos.Part(
                    function_response=genai.protos.FunctionResponse(
                        name=name,
                        response={
                            "result": json.loads(
                                json.dumps(result, default=str)
                            )
                        },
                    )
                )
            )

        # Send function results back to model for next response
        try:
            response = _safe_send(
                chat,
                genai.protos.Content(parts=fn_response_parts),
            )
            api_log.next_turn()
        except Exception as e:
            api_log.log("error", message=str(e))
            emit("error", {"message": f"Gemini API error: {e}"})
            break

    else:
        emit("error", {"message": "Agent reached maximum turn limit."})

    emit("progress", {"stage": "Ready"})

    # Return updated history for multi-turn
    return list(chat.history)


# ── Helpers ────────────────────────────────────────────────────────


def _extract_function_calls(response) -> list:
    """Extract FunctionCall objects from a Gemini response."""
    calls = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        if content is None:
            continue
        for part in getattr(content, "parts", None) or []:
            fc = getattr(part, "function_call", None)
            if fc and getattr(fc, "name", None):
                calls.append(fc)
    return calls


def _extract_text(response) -> str:
    """Extract concatenated text from a Gemini response."""
    texts = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        if content is None:
            continue
        for part in getattr(content, "parts", None) or []:
            if getattr(part, "text", None):
                texts.append(part.text)
    return "\n".join(texts)


class _EmptyResponse:
    """Sentinel when the SDK throws on empty candidates."""
    candidates = []


def _safe_send(chat, message, _max_retries: int = 3):
    """
    Wrapper around chat.send_message that:
    - catches the SDK's IndexError when the model returns empty candidates
    - retries on 429 (rate-limit) errors with exponential backoff
    """
    for attempt in range(_max_retries + 1):
        try:
            resp = chat.send_message(message)
            log.debug("Model response: candidates=%d",
                      len(getattr(resp, 'candidates', []) or []))
            return resp
        except IndexError:
            log.warning("Model returned empty candidates (IndexError). "
                        "Will retry with nudge.")
            return _EmptyResponse()
        except Exception as e:
            err_str = str(e)
            if "429" in err_str and attempt < _max_retries:
                wait = 2 ** attempt  # 1s, 2s, 4s
                log.warning("Rate-limited (429), retrying in %ds (attempt %d/%d)...",
                            wait, attempt + 1, _max_retries)
                time.sleep(wait)
                continue
            raise  # non-429 error or exhausted retries


def _proto_to_dict(proto_struct) -> dict:
    """Convert a protobuf Struct to a plain Python dict."""
    if proto_struct is None:
        return {}
    result = {}
    for key in proto_struct:
        val = proto_struct[key]
        result[key] = _convert_value(val)
    return result


def _convert_value(val: Any) -> Any:
    """Recursively convert protobuf values to Python types."""
    if isinstance(val, (str, int, float, bool)):
        return val
    if val is None:
        return None
    # Check dict-like (Struct) BEFORE iterable — Structs are iterable
    # over their keys, which would incorrectly produce a list of key names.
    if hasattr(val, 'keys'):
        return {k: _convert_value(val[k]) for k in val}
    if hasattr(val, '__iter__') and not isinstance(val, (str, dict)):
        return [_convert_value(item) for item in val]
    return val

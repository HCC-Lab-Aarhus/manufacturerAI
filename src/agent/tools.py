"""
Tool implementations for the designer agent.

Each tool is a plain Python function. The agent loop calls these based on
the Gemini function-calling response.  Every tool returns a dict that gets
sent back to the LLM as the function response.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from src.config.hardware import hw
from src.geometry.polygon import validate_outline as _validate, ensure_ccw, polygon_bounds
from src.pcb.placer import place_components as _place, build_optimization_report, PlacementError
from src.pcb.router_bridge import route_traces as _route, RouterError
from src.scad.shell import generate_enclosure_scad, generate_battery_hatch_scad, generate_print_plate_scad
from src.scad.compiler import compile_scad, check_scad


# ── Event callback type ────────────────────────────────────────────
# The web layer injects an `emit` callback so tools can stream
# intermediates.  Signature:  emit(event_type: str, data: dict)
EmitFn = Callable[[str, dict], None]

# Module-level state set by the agent loop before tool dispatch
_emit: EmitFn = lambda t, d: None
_output_dir: Path = Path("outputs/agent")
_run_id: str = ""


def configure(emit: EmitFn, output_dir: Path, run_id: str) -> None:
    global _emit, _output_dir, _run_id
    _emit = emit
    _output_dir = Path(output_dir)
    _run_id = run_id
    _output_dir.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════
# TOOL FUNCTIONS
# ═══════════════════════════════════════════════════════════════════


def think(reasoning: str) -> dict:
    """
    Internal scratchpad — use this to reason about the next step.
    The user does NOT see this. Write freely.
    """
    _emit("thinking", {"text": reasoning})
    return {"status": "ok", "note": "Your reasoning has been noted. Continue."}


def send_message(message: str) -> dict:
    """
    Send a chat message to the user. Use this to communicate
    progress, ask clarifying questions, or summarize results.
    """
    _emit("chat", {"role": "assistant", "text": message})
    return {"status": "sent"}


def send_outline_preview(
    outline: list[list[float]],
    button_positions: list[dict],
    label: str = "outline",
) -> dict:
    """
    Send a 2D outline preview to the user's browser.
    outline: list of [x, y] vertices in mm.
    button_positions: list of {id, label, x, y}.
    label: a short description shown in the UI.
    """
    _emit("outline_preview", {
        "outline": outline,
        "buttons": button_positions,
        "label": label,
    })
    return {"status": "preview_sent", "label": label}


def validate_outline(
    outline: list[list[float]],
    button_positions: list[dict],
) -> dict:
    """
    Validate a 2D polygon outline and button positions.
    Returns a list of errors (empty if valid).
    outline: list of [x, y] vertices in mm.
    button_positions: list of {id, label, x, y}.
    """
    bpos = [{"x": b["x"], "y": b["y"]} for b in button_positions]
    bounds = polygon_bounds(outline)
    width = bounds[2] - bounds[0]
    length = bounds[3] - bounds[1]
    errors = _validate(
        outline,
        width=width,
        length=length,
        button_positions=bpos,
        edge_clearance=hw.button["cap_diameter_mm"] / 2 + 2,
    )
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "bounds": {"min_x": bounds[0], "min_y": bounds[1],
                   "max_x": bounds[2], "max_y": bounds[3]},
        "width_mm": round(width, 2),
        "height_mm": round(length, 2),
    }


def place_components(
    outline: list[list[float]],
    button_positions: list[dict],
) -> dict:
    """
    Auto-place battery, controller, and IR diode inside the outline.
    Buttons are placed at the given positions.
    Returns the full PCB layout.
    """
    btn_list = [{"id": b["id"], "x": b["x"], "y": b["y"]} for b in button_positions]

    try:
        layout = _place(outline, btn_list)
    except PlacementError as e:
        return {
            "status": "error",
            "message": str(e),
            "details": e.to_dict(),
            "suggestion": e.suggestion,
        }

    # Save & emit
    path = _output_dir / "pcb_layout.json"
    path.write_text(json.dumps(layout, indent=2), encoding="utf-8")
    _emit("pcb_layout", layout)

    # Build a summary for the LLM
    summary = []
    for c in layout["components"]:
        summary.append(f"  {c['id']} ({c['type']}): ({c['center'][0]:.1f}, {c['center'][1]:.1f})")

    return {
        "status": "ok",
        "component_count": len(layout["components"]),
        "components_summary": "\n".join(summary),
        "pcb_layout_saved": str(path),
    }


def route_traces(
    outline: list[list[float]],
    button_positions: list[dict],
) -> dict:
    """
    Run the A* trace router on the current PCB layout.
    Reads pcb_layout.json from the output directory.
    Returns routing results.
    """
    layout_path = _output_dir / "pcb_layout.json"
    if not layout_path.exists():
        return {"status": "error", "message": "No pcb_layout.json — run place_components first."}

    layout = json.loads(layout_path.read_text(encoding="utf-8"))

    try:
        result = _route(layout, _output_dir)
    except RouterError as e:
        return {"status": "error", "message": str(e)}

    # Save routing result
    (path := _output_dir / "routing_result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )

    # Emit debug image if the router generated one
    pcb_debug = _output_dir / "pcb_debug.png"
    if pcb_debug.exists():
        _emit("debug_image", {"path": str(pcb_debug), "label": pcb_debug.stem})

    _emit("routing_result", result)

    # Build optimization report
    report = build_optimization_report(layout, result, outline)
    (rpath := _output_dir / "optimization_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    if not result.get("success", False):
        failed = result.get("failed_nets", [])
        return {
            "status": "routing_failed",
            "routed_count": len(result.get("traces", [])),
            "failed_nets": [f.get("netName", str(f)) if isinstance(f, dict) else str(f) for f in failed],
            "problems": report["problems"],
            "suggestion": "Try widening the outline or adjusting button positions.",
        }

    return {
        "status": "ok",
        "routed_count": len(result.get("traces", [])),
        "failed_nets": [],
    }


def generate_enclosure(
    outline: list[list[float]],
    button_positions: list[dict],
) -> dict:
    """
    Generate the full OpenSCAD enclosure from outline + PCB layout + routing.
    Produces bottom shell, top shell, battery hatch, and print plate.
    """
    layout_path = _output_dir / "pcb_layout.json"
    routing_path = _output_dir / "routing_result.json"

    if not layout_path.exists():
        return {"status": "error", "message": "No pcb_layout.json — run place_components first."}

    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    routing = None
    if routing_path.exists():
        routing = json.loads(routing_path.read_text(encoding="utf-8"))

    btn_pos = [{"id": b["id"], "x": b["x"], "y": b["y"]} for b in button_positions]

    try:
        # Solid enclosure extrusion
        enclosure_scad = generate_enclosure_scad(outline=outline)
        (p1 := _output_dir / "enclosure.scad").write_text(enclosure_scad, encoding="utf-8")

        # Battery hatch
        hatch_scad = generate_battery_hatch_scad()
        (p2 := _output_dir / "battery_hatch.scad").write_text(hatch_scad, encoding="utf-8")

        # Print plate
        plate_scad = generate_print_plate_scad()
        (p3 := _output_dir / "print_plate.scad").write_text(plate_scad, encoding="utf-8")

    except Exception as e:
        return {"status": "error", "message": f"SCAD generation failed: {e}"}

    scad_files = {
        "enclosure": str(p1),
        "battery_hatch": str(p2),
        "print_plate": str(p3),
    }

    _emit("scad_generated", scad_files)

    return {
        "status": "ok",
        "files": scad_files,
    }


def compile_models() -> dict:
    """
    Compile all SCAD files in the output directory to STL.
    """
    scad_files = list(_output_dir.glob("*.scad"))
    if not scad_files:
        return {"status": "error", "message": "No SCAD files found. Run generate_enclosure first."}

    results = {}
    stl_files = {}
    all_ok = True

    for scad_path in scad_files:
        stl_path = scad_path.with_suffix(".stl")
        ok, msg, out = compile_scad(scad_path, stl_path)
        results[scad_path.stem] = {"ok": ok, "message": msg}
        if ok and out:
            stl_files[scad_path.stem] = out
            _emit("model", {"name": scad_path.stem, "path": out})
        else:
            all_ok = False

    return {
        "status": "ok" if all_ok else "partial_failure",
        "results": results,
        "stl_files": stl_files,
    }


def finalize(summary: str) -> dict:
    """
    Finalize the design session. Provide a summary of what was built.
    """
    # Save design manifest
    manifest = {
        "run_id": _run_id,
        "summary": summary,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(_output_dir),
    }
    (p := _output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    _emit("complete", manifest)
    return {"status": "complete"}


# ═══════════════════════════════════════════════════════════════════
# TOOL REGISTRY — used by the agent loop for dispatch
# ═══════════════════════════════════════════════════════════════════

TOOLS: dict[str, Callable[..., dict]] = {
    "think": think,
    "send_message": send_message,
    "send_outline_preview": send_outline_preview,
    "validate_outline": validate_outline,
    "place_components": place_components,
    "route_traces": route_traces,
    "generate_enclosure": generate_enclosure,
    "compile_models": compile_models,
    "finalize": finalize,
}

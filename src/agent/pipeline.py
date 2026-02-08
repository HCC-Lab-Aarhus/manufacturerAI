"""
Manufacturing pipeline — runs all steps automatically.

Called when the LLM submits a design via ``submit_design``.
Each step streams progress events to the UI.  If any step fails,
the error is returned to the LLM so it can iterate.
"""

from __future__ import annotations

import json
import logging
import traceback
from pathlib import Path
from typing import Any, Callable

from src.config.hardware import hw
from src.geometry.polygon import (
    validate_outline as _validate_geometry,
    ensure_ccw,
    polygon_bounds,
)
from src.pcb.placer import place_components as _place, build_optimization_report, PlacementError
from src.pcb.router_bridge import route_traces as _route, RouterError, build_pin_mapping
from src.scad.shell import (
    generate_enclosure_scad,
    generate_battery_hatch_scad,
    generate_print_plate_scad,
)
from src.scad.compiler import compile_scad

log = logging.getLogger("manufacturerAI.pipeline")

EmitFn = Callable[[str, dict], None]

# ── Printer limits ─────────────────────────────────────────────────

def _load_printer_limits() -> dict:
    p = Path(__file__).resolve().parents[2] / "configs" / "printer_limits.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"max_width_mm": 70, "max_length_mm": 240}

_PRINTER_LIMITS = _load_printer_limits()


def _normalize_outline(
    outline: list[list[float]],
    button_positions: list[dict],
) -> tuple[list[list[float]], list[dict]]:
    """
    Normalize outline and buttons so the bottom-left is at the origin.
    Also strips duplicate closing vertex (if first == last).
    """
    # Strip duplicate closing vertex
    if len(outline) >= 2 and outline[0] == outline[-1]:
        outline = outline[:-1]

    # Find minimum x, y
    min_x = min(v[0] for v in outline)
    min_y = min(v[1] for v in outline)

    # Shift to origin
    outline = [[v[0] - min_x, v[1] - min_y] for v in outline]
    button_positions = [
        {**b, "x": b["x"] - min_x, "y": b["y"] - min_y}
        for b in button_positions
    ]

    return outline, button_positions


def run_pipeline(
    outline: list[list[float]],
    button_positions: list[dict],
    emit: EmitFn,
    output_dir: Path,
) -> dict:
    """
    Execute the full manufacturing pipeline.

    Steps:
        1. Validate outline geometry
        2. Send outline preview to UI
        3. Place components (battery, controller, IR diode)
        4. Route PCB traces
        5. Generate OpenSCAD enclosure files
        6. Compile SCAD → STL

    Returns:
        Result dict with ``status`` ("success" or "error") and details.
        On error, includes ``step`` (which step failed) and ``message``.
    """
    output_dir = Path(output_dir)

    # ── 0. Normalize outline to origin & clean up ──────────────────
    bpos = [
        {
            "id": b.get("id", f"btn_{i}"),
            "label": b.get("label", b.get("id", f"btn_{i}")),
            "x": b["x"],
            "y": b["y"],
        }
        for i, b in enumerate(button_positions)
    ]
    outline, bpos = _normalize_outline(outline, bpos)

    # ── 1. Validate outline ────────────────────────────────────────
    emit("progress", {"stage": "Validating outline..."})
    log.info("Pipeline step 1: validate outline (%d vertices, %d buttons)",
             len(outline), len(bpos))

    bounds = polygon_bounds(outline)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]

    # Check against printer limits FIRST
    max_w = _PRINTER_LIMITS["max_width_mm"]
    max_l = _PRINTER_LIMITS["max_length_mm"]
    dim_errors = []
    if width > max_w:
        dim_errors.append(
            f"Outline is {width:.1f}mm wide — max printable width is {max_w}mm."
        )
    if height > max_l:
        dim_errors.append(
            f"Outline is {height:.1f}mm long — max printable length is {max_l}mm."
        )

    errors = _validate_geometry(
        outline,
        width=width,
        length=height,
        button_positions=bpos,
        edge_clearance=hw.button["cap_diameter_mm"] / 2 + 2,
    )
    errors = dim_errors + errors

    if errors:
        return {
            "status": "error",
            "step": "validate",
            "message": "Outline validation failed.",
            "errors": errors,
            "bounds": {"width_mm": round(width, 2), "height_mm": round(height, 2)},
            "suggestion": (
                "Fix the listed errors and resubmit.  Common issues: "
                "vertices not CCW, buttons too close to edge, outline "
                "self-intersecting, or area too small."
            ),
        }

    # ── 2. Send outline preview ────────────────────────────────────
    emit("outline_preview", {
        "outline": outline,
        "buttons": bpos,
        "label": "Design submitted",
    })

    # ── 3. Place components ────────────────────────────────────────
    emit("progress", {"stage": "Placing components..."})
    log.info("Pipeline step 3: place components")

    try:
        layout = _place(outline, bpos)
    except PlacementError as e:
        log.warning("Placement failed: %s", e)
        return {
            "status": "error",
            "step": "placement",
            "message": str(e),
            "details": e.to_dict(),
            "suggestion": e.suggestion,
        }
    except Exception as e:
        log.exception("Component placement failed")
        return {
            "status": "error",
            "step": "placement",
            "message": f"Component placement failed: {e}",
            "suggestion": (
                "The outline may be too small or oddly shaped for the "
                "required components.  Try making it wider or taller."
            ),
        }

    # Save layout
    layout_path = output_dir / "pcb_layout.json"
    layout_path.write_text(json.dumps(layout, indent=2), encoding="utf-8")
    emit("pcb_layout", layout)

    # ── 4. Route traces ────────────────────────────────────────────
    emit("progress", {"stage": "Routing traces..."})
    log.info("Pipeline step 4: route traces")

    try:
        routing_result = _route(layout, output_dir)
    except RouterError as e:
        return {
            "status": "error",
            "step": "routing",
            "message": f"Trace routing failed: {e}",
            "suggestion": (
                "Routing can fail if components are too close together. "
                "Try widening the outline or spacing buttons further apart."
            ),
        }
    except Exception as e:
        log.exception("Routing crashed")
        return {
            "status": "error",
            "step": "routing",
            "message": f"Trace routing error: {e}",
        }

    # Save routing result
    routing_path = output_dir / "routing_result.json"
    routing_path.write_text(json.dumps(routing_result, indent=2), encoding="utf-8")

    # Emit debug image if the router generated one
    pcb_debug = output_dir / "pcb_debug.png"
    if pcb_debug.exists():
        emit("debug_image", {"path": str(pcb_debug), "label": pcb_debug.stem})

    # Check routing success
    if not routing_result.get("success", False):
        failed = routing_result.get("failed_nets", [])
        failed_names = [
            f.get("netName", str(f)) if isinstance(f, dict) else str(f)
            for f in failed
        ]
        report = build_optimization_report(layout, routing_result, outline)
        return {
            "status": "error",
            "step": "routing",
            "message": "Some traces could not be routed.",
            "routed_count": len(routing_result.get("traces", [])),
            "failed_nets": failed_names,
            "problems": report.get("problems", []),
            "suggestion": (
                "Try widening the outline or adjusting button positions "
                "to give the router more space."
            ),
        }

    emit("routing_result", routing_result)

    # ── 5. Generate SCAD ───────────────────────────────────────────
    emit("progress", {"stage": "Generating enclosure..."})
    log.info("Pipeline step 5: generate SCAD")

    try:
        enclosure_scad = generate_enclosure_scad(outline=outline)
        (p1 := output_dir / "enclosure.scad").write_text(
            enclosure_scad, encoding="utf-8"
        )

        hatch_scad = generate_battery_hatch_scad()
        (p2 := output_dir / "battery_hatch.scad").write_text(
            hatch_scad, encoding="utf-8"
        )

        plate_scad = generate_print_plate_scad()
        (p3 := output_dir / "print_plate.scad").write_text(
            plate_scad, encoding="utf-8"
        )
    except Exception as e:
        log.exception("SCAD generation failed")
        return {
            "status": "error",
            "step": "scad_generation",
            "message": f"SCAD generation failed: {e}",
        }

    scad_files = {
        "enclosure": str(p1),
        "battery_hatch": str(p2),
        "print_plate": str(p3),
    }
    emit("scad_generated", scad_files)

    # ── 6. Compile STL ─────────────────────────────────────────────
    emit("progress", {"stage": "Compiling STL models..."})
    log.info("Pipeline step 6: compile STL")

    stl_results = {}
    stl_files = {}
    all_ok = True

    for scad_path in output_dir.glob("*.scad"):
        stl_path = scad_path.with_suffix(".stl")
        try:
            ok, msg, out = compile_scad(scad_path, stl_path)
        except Exception as e:
            ok, msg, out = False, str(e), None

        stl_results[scad_path.stem] = {"ok": ok, "message": msg}
        if ok and out:
            stl_files[scad_path.stem] = str(out)
            emit("model", {"name": scad_path.stem, "path": str(out)})
        else:
            all_ok = False

    if not all_ok:
        return {
            "status": "error",
            "step": "compile",
            "message": "Some SCAD files failed to compile.",
            "results": stl_results,
        }

    # ── Success ────────────────────────────────────────────────────
    emit("progress", {"stage": "Pipeline complete!"})
    log.info("Pipeline complete — all steps succeeded")

    # Save manifest
    manifest = {
        "outline_vertices": len(outline),
        "button_count": len(bpos),
        "stl_files": stl_files,
        "scad_files": scad_files,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    # Build pin mapping so the LLM can report wiring to the user
    pin_mapping = build_pin_mapping(layout, bpos)

    return {
        "status": "success",
        "stl_files": list(stl_files.keys()),
        "component_count": len(layout.get("components", [])),
        "routed_traces": len(routing_result.get("traces", [])),
        "pin_mapping": pin_mapping,
        "message": (
            f"Design manufactured successfully! "
            f"{len(stl_files)} STL models generated."
        ),
    }

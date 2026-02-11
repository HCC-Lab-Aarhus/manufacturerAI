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
from src.pcb.placer import (
    place_components_optimal,
    build_optimization_report,
    PlacementError,
)
from src.pcb.router_bridge import route_traces as _route, RouterError, build_pin_mapping
from src.pcb.routability import score_placement, detect_crossings, format_feedback
from src.scad.shell import (
    generate_enclosure_scad,
    generate_battery_hatch_scad,
    generate_print_plate_scad,
)
from src.scad.cutouts import build_cutouts
from src.scad.compiler import compile_scad

log = logging.getLogger("manufacturerAI.pipeline")

EmitFn = Callable[[str, dict], None]

# ── Printer limits ─────────────────────────────────────────────────

def _load_printer_limits() -> dict:
    p = Path(__file__).resolve().parents[2] / "configs" / "printer_limits.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"max_width_mm": 200, "max_length_mm": 200}

_PRINTER_LIMITS = _load_printer_limits()


def _save_winning_result(
    layout: dict,
    routing_result: dict,
    output_dir: Path,
    emit: EmitFn,
) -> None:
    """Persist and emit a successful layout+routing pair."""
    (output_dir / "pcb_layout.json").write_text(
        json.dumps(layout, indent=2), encoding="utf-8"
    )
    (output_dir / "routing_result.json").write_text(
        json.dumps(routing_result, indent=2), encoding="utf-8"
    )
    emit("pcb_layout", layout)
    pcb_debug = output_dir / "pcb_debug.png"
    if pcb_debug.exists():
        emit("debug_image", {"path": str(pcb_debug), "label": pcb_debug.stem})


def _place_and_route(
    outline: list[list[float]],
    bpos: list[dict],
    output_dir: Path,
    emit: EmitFn,
) -> tuple[dict | None, dict | None]:
    """
    Place components with optimal spacing, then route once.

    The placer generates several candidate layouts (varying battery /
    controller position preferences) and picks the single one that
    maximizes the minimum gap between all components and the polygon
    boundary.  That layout is then routed once with the full rip-up
    budget.
    """
    # ── Optimal placement (instant, pure geometry) ───────────────
    emit("progress", {"stage": "Optimizing component placement..."})
    log.info("Finding optimal component placement...")

    try:
        layout = place_components_optimal(outline, bpos)
    except PlacementError:
        layout = None

    if layout is None:
        return (None, None)

    # Save placement result
    (output_dir / "pcb_layout.json").write_text(
        json.dumps(layout, indent=2), encoding="utf-8"
    )
    emit("pcb_layout", layout)

    # ── Route traces (single attempt, full budget) ───────────────
    emit("progress", {"stage": "Routing traces..."})
    log.info("Routing traces...")

    try:
        routing_result = _route(layout, output_dir)
    except (RouterError, Exception) as e:
        log.warning("Routing failed: %s", e)
        return (layout, None)

    if routing_result.get("success", False):
        _save_winning_result(layout, routing_result, output_dir, emit)

    return (layout, routing_result)


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
    *,
    top_curve_length: float = 0.0,
    top_curve_height: float = 0.0,
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

    Parameters
    ----------
    top_curve_length : float
        Inward extent (mm) of the rounded top edge.  0 = no rounding.
    top_curve_height : float
        Vertical extent (mm) of the rounded zone from the top.  0 = no rounding.

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

    # ── 3 + 4. Place components & route traces ─────────────────────
    #
    # Optimal placement picks the single layout with the best global
    # spacing, then routes it once.
    emit("progress", {"stage": "Placing components & routing traces..."})
    log.info("Pipeline step 3+4: optimal placement + single route")

    layout, routing_result = _place_and_route(
        outline, bpos, output_dir, emit,
    )

    if layout is None:
        # Total placement failure — not even one candidate
        return {
            "status": "error",
            "step": "placement",
            "message": "No valid component placement found.",
            "suggestion": (
                "The outline is too small or oddly shaped for the "
                "required components (battery 25×48mm, controller "
                "10×36mm).  Widen the outline or make it taller."
            ),
        }

    if not routing_result or not routing_result.get("success", False):
        # Routing failed for every candidate — return structured feedback
        failed = (routing_result or {}).get("failed_nets", [])
        failed_names = [
            f.get("netName", str(f)) if isinstance(f, dict) else str(f)
            for f in failed
        ]
        # Run crossing analysis for actionable feedback
        crossings = detect_crossings(layout)
        _, bottlenecks = score_placement(layout, outline)
        feedback = format_feedback(
            bottlenecks=bottlenecks,
            crossings=crossings,
            tried_placements=1,
            best_routed=len((routing_result or {}).get("traces", [])),
            total_nets=(
                len((routing_result or {}).get("traces", []))
                + len(failed)
            ),
        )
        return {
            "status": "error",
            "step": "routing",
            "message": "Traces could not be routed with any placement.",
            "routed_count": feedback["best_routed"],
            "failed_nets": failed_names,
            "tried_placements": feedback["tried_placements"],
            "bottlenecks": feedback["bottlenecks"],
            "problems": feedback["problems"],
            "suggestion": feedback["suggestion"],
        }

    emit("routing_result", routing_result)

    # ── 5. Generate SCAD ───────────────────────────────────────────
    emit("progress", {"stage": "Generating enclosure..."})
    log.info("Pipeline step 5: generate SCAD")

    try:
        cutouts = build_cutouts(layout, routing_result)
        log.info("Built %d cutouts for shell subtraction", len(cutouts))
        enclosure_scad = generate_enclosure_scad(
            outline=outline,
            cutouts=cutouts,
            top_curve_length=top_curve_length,
            top_curve_height=top_curve_height,
        )
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

    # Compile in deterministic order: enclosure & battery_hatch first,
    # then print_plate last (it imports the other STLs).
    scad_order = ["enclosure", "battery_hatch", "print_plate"]
    ordered_scads = []
    for name in scad_order:
        p = output_dir / f"{name}.scad"
        if p.exists():
            ordered_scads.append(p)
    for extra in sorted(output_dir.glob("*.scad")):
        if extra not in ordered_scads:
            ordered_scads.append(extra)

    for scad_path in ordered_scads:
        stl_path = scad_path.with_suffix(".stl")
        try:
            ok, msg, out = compile_scad(scad_path, stl_path)
        except Exception as e:
            ok, msg, out = False, str(e), None

        stl_results[scad_path.stem] = {"ok": ok, "message": msg}
        if ok and out:
            stl_files[scad_path.stem] = str(out)
        else:
            all_ok = False

    # Emit print_plate as the 3D preview (shows enclosure + battery
    # hatch side by side, ready for printing).  Fall back to enclosure
    # if print_plate failed.
    if "print_plate" in stl_files:
        emit("model", {
            "name": "print_plate",
            "path": stl_files["print_plate"],
            "top_curve_length": top_curve_length,
            "top_curve_height": top_curve_height,
        })
    elif "enclosure" in stl_files:
        emit("model", {
            "name": "enclosure",
            "path": stl_files["enclosure"],
            "top_curve_length": top_curve_length,
            "top_curve_height": top_curve_height,
        })

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
        "top_curve_length": top_curve_length,
        "top_curve_height": top_curve_height,
        "message": (
            f"Design manufactured successfully! "
            f"{len(stl_files)} STL models generated."
        ),
    }

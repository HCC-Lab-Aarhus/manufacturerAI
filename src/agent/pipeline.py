"""
Manufacturing pipeline — discrete steps, enterable at any point.

Each step reads inputs from run_dir/, writes outputs to run_dir/.
The pipeline can be entered at any step (e.g. "route" after realign,
"build" after a curve edit) and cancelled between steps.

Steps in order:
    validate → place → route → build → slice → firmware
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable

from src.config.hardware import hw
from src.geometry.polygon import (
    validate_outline as _validate_geometry,
    ensure_ccw,
    polygon_bounds,
    smooth_polygon,
    generate_ellipse,
    generate_racetrack,
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
    DEFAULT_HEIGHT_MM,
)
from src.scad.cutouts import build_cutouts
from src.scad.compiler import compile_scad, merge_stl_files
from src.gcode.pipeline import run_gcode_pipeline
from firmware.firmware_generator import generate_firmware, generate_pin_assignment_report

log = logging.getLogger("manufacturerAI.pipeline")

EmitFn = Callable[[str, dict], None]


# ── Public API ─────────────────────────────────────────────────────

STEPS = ("validate", "place", "route", "build", "slice", "firmware")


class PipelineCancelled(BaseException):
    """Raised when the pipeline is cancelled (e.g. user realigned).

    Inherits from BaseException so it won't be caught by generic
    ``except Exception`` handlers in the pipeline or agent loop.
    """
    pass


def run_pipeline(
    run_dir: Path,
    emit: EmitFn,
    cancel: threading.Event | None = None,
    *,
    start_from: str = "validate",
    stop_after: str | None = None,
    # Only needed when start_from="validate":
    outline: list[list[float]] | None = None,
    buttons: list[dict] | None = None,
    outline_type: str = "polygon",
    curve_params: dict | None = None,
) -> dict:
    """Run the manufacturing pipeline from any step onwards.

    All intermediate data is read from / written to *run_dir/*.

    Args:
        run_dir: Output directory for this session.
        emit: Callback ``(event_type, data_dict)`` for UI events.
        cancel: Set this event to cancel the pipeline.
        start_from: First step to execute (one of :data:`STEPS`).
        stop_after: Last step to execute (inclusive). ``None`` = run to end.
        outline: Polygon ``[[x,y], ...]`` — only needed for ``"validate"``.
        buttons: Button list — only needed for ``"validate"``.
        outline_type: ``"polygon"`` | ``"ellipse"`` | ``"racetrack"``.
        curve_params: ``dict`` with top/bottom curve length/height.

    Returns:
        Result dict with ``"status"`` (``"success"`` or ``"error"``).
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    start_idx = STEPS.index(start_from)
    stop_idx = STEPS.index(stop_after) if stop_after else len(STEPS) - 1
    steps_to_run = STEPS[start_idx : stop_idx + 1]

    # Persist initial design input if starting from validate
    if "validate" in steps_to_run and outline is not None:
        (run_dir / "design_input.json").write_text(
            json.dumps(
                {
                    "outline": outline,
                    "buttons": buttons or [],
                    "outline_type": outline_type,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # Persist curve params if provided
    if curve_params:
        (run_dir / "curve_params.json").write_text(
            json.dumps(curve_params, indent=2), encoding="utf-8"
        )

    # Execute steps
    for step_name in steps_to_run:
        _check(cancel)
        log.info("Pipeline step: %s", step_name)
        result = _STEP_DISPATCH[step_name](run_dir, emit, cancel)
        if result.get("status") == "error":
            return result

    emit("progress", {"stage": "Pipeline complete!"})
    log.info("Pipeline complete")

    return _build_success_result(run_dir, steps_to_run)


# ── Printer limits ─────────────────────────────────────────────────

def _load_printer_limits() -> dict:
    p = Path(__file__).resolve().parents[2] / "configs" / "printer_limits.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"max_width_mm": 200, "max_length_mm": 200}

_PRINTER_LIMITS = _load_printer_limits()


# ── Helper: cancel check ──────────────────────────────────────────

def _check(cancel: threading.Event | None) -> None:
    """Raise *PipelineCancelled* if the cancel event is set."""
    if cancel is not None and cancel.is_set():
        raise PipelineCancelled()


# ── Step: validate ─────────────────────────────────────────────────

def _step_validate(run_dir: Path, emit: EmitFn, cancel: threading.Event | None) -> dict:
    """Normalize and validate the outline geometry.

    Reads ``design_input.json``, writes back the normalized version.
    """
    emit("progress", {"stage": "Validating outline..."})

    design = json.loads((run_dir / "design_input.json").read_text(encoding="utf-8"))
    outline = design["outline"]
    buttons = design.get("buttons", [])
    outline_type = design.get("outline_type", "polygon")

    # Function → display label mapping
    _FUNC_LABELS = {
        "power": "Power",
        "vol_up": "Vol +",
        "vol_down": "Vol −",
        "ch1": "Ch 1",
        "ch2": "Ch 2",
        "ch3": "Ch 3",
        "ch4": "Ch 4",
        "ch5": "Ch 5",
        "brand": "Brand",
    }

    def _button_label(b: dict, i: int) -> str:
        """Get display label: explicit label > function name > fallback."""
        if b.get("label"):
            return b["label"]
        func = b.get("function", "")
        if func and func in _FUNC_LABELS:
            return _FUNC_LABELS[func]
        return b.get("id", f"Button {i + 1}")

    # Normalize button format (preserve function field)
    bpos = [
        {
            "id": b.get("id", f"btn_{i}"),
            "label": _button_label(b, i),
            "x": b["x"],
            "y": b["y"],
            "function": b.get("function", ""),
        }
        for i, b in enumerate(buttons)
    ]

    # Normalize outline (smooth, shift to origin, parametric shapes)
    outline, bpos = _normalize_outline(outline, bpos, outline_type=outline_type)

    # Validate
    bounds = polygon_bounds(outline)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]

    log.info(
        "Validating outline: %d vertices, %d buttons, %.1f×%.1f mm",
        len(outline), len(bpos), width, height,
    )

    max_w = _PRINTER_LIMITS["max_width_mm"]
    max_l = _PRINTER_LIMITS["max_length_mm"]
    dim_errors: list[str] = []
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

    # Write normalized data back to disk
    design["outline"] = outline
    design["buttons"] = bpos
    (run_dir / "design_input.json").write_text(
        json.dumps(design, indent=2), encoding="utf-8"
    )

    # Emit outline preview
    emit("outline_preview", {
        "outline": outline,
        "buttons": bpos,
        "label": "Design submitted",
    })

    return {"status": "ok"}


# ── Step: place ────────────────────────────────────────────────────

def _step_place(run_dir: Path, emit: EmitFn, cancel: threading.Event | None) -> dict:
    """Place components optimally within the outline.

    Reads ``design_input.json``, writes ``pcb_layout.json``.
    """
    emit("progress", {"stage": "Placing components..."})

    design = json.loads((run_dir / "design_input.json").read_text(encoding="utf-8"))
    outline = design["outline"]
    bpos = design.get("buttons", [])

    try:
        layout = place_components_optimal(outline, bpos)
    except PlacementError:
        layout = None

    if layout is None:
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

    # Write layout to disk
    (run_dir / "pcb_layout.json").write_text(
        json.dumps(layout, indent=2), encoding="utf-8"
    )
    emit("pcb_layout", layout)

    # Emit shell preview so the 3-D preview + curve editor appear
    # instantly — before routing (which can take 15-30 s or fail).
    outline = layout.get("board", {}).get("outline_polygon", [])
    curve_path = run_dir / "curve_params.json"
    curves = (
        json.loads(curve_path.read_text(encoding="utf-8"))
        if curve_path.exists()
        else {}
    )
    _emit_shell_preview(layout, outline, curves, emit)

    log.info("Placed %d components", len(layout.get("components", [])))
    return {"status": "ok"}


# ── Step: route ────────────────────────────────────────────────────

def _step_route(run_dir: Path, emit: EmitFn, cancel: threading.Event | None) -> dict:
    """Route PCB traces between components.

    Reads ``pcb_layout.json``, writes ``routing_result.json`` + PNG images.
    """
    emit("progress", {"stage": "Routing traces..."})

    layout = json.loads((run_dir / "pcb_layout.json").read_text(encoding="utf-8"))
    outline = layout.get("board", {}).get("outline_polygon", [])

    try:
        routing_result = _route(layout, run_dir, cancel=cancel)
    except (RouterError, Exception) as e:
        log.warning("Routing failed: %s", e)
        routing_result = {"success": False, "failed_nets": [], "traces": []}

    # Write routing result
    (run_dir / "routing_result.json").write_text(
        json.dumps(routing_result, indent=2), encoding="utf-8"
    )

    if not routing_result.get("success", False):
        failed = routing_result.get("failed_nets", [])
        failed_names = [
            f.get("netName", str(f)) if isinstance(f, dict) else str(f)
            for f in failed
        ]
        crossings = detect_crossings(layout)
        _, bottlenecks = score_placement(layout, outline)
        feedback = format_feedback(
            bottlenecks=bottlenecks,
            crossings=crossings,
            tried_placements=1,
            best_routed=len(routing_result.get("traces", [])),
            total_nets=len(routing_result.get("traces", [])) + len(failed),
        )
        # Still emit debug image (router generates it even on partial failure)
        _emit_debug_image(run_dir, emit)
        return {
            "status": "error",
            "step": "routing",
            "message": "Traces could not be routed.",
            "routed_count": feedback["best_routed"],
            "failed_nets": failed_names,
            "tried_placements": feedback["tried_placements"],
            "bottlenecks": feedback["bottlenecks"],
            "problems": feedback["problems"],
            "suggestion": feedback["suggestion"],
        }

    emit("routing_result", routing_result)
    _emit_debug_image(run_dir, emit)

    log.info("Routed %d traces", len(routing_result.get("traces", [])))
    return {"status": "ok"}


# ── Step: build ────────────────────────────────────────────────────

def _step_build(run_dir: Path, emit: EmitFn, cancel: threading.Event | None) -> dict:
    """Generate SCAD enclosure files and compile to STL.

    Reads ``pcb_layout.json``, ``routing_result.json``, ``curve_params.json``.
    Writes ``.scad`` and ``.stl`` files.
    """
    emit("progress", {"stage": "Generating enclosure..."})

    layout = json.loads((run_dir / "pcb_layout.json").read_text(encoding="utf-8"))
    routing_path = run_dir / "routing_result.json"
    routing = (
        json.loads(routing_path.read_text(encoding="utf-8"))
        if routing_path.exists()
        else {}
    )
    curve_path = run_dir / "curve_params.json"
    curves = (
        json.loads(curve_path.read_text(encoding="utf-8"))
        if curve_path.exists()
        else {}
    )

    outline = layout.get("board", {}).get("outline_polygon", [])
    if not outline:
        return {"status": "error", "step": "build", "message": "No outline in layout."}

    # ── Generate SCAD ──────────────────────────────────────────────
    try:
        cutouts = build_cutouts(layout, routing)
        log.info("Built %d cutouts for shell subtraction", len(cutouts))

        enclosure_scad = generate_enclosure_scad(
            outline=outline,
            cutouts=cutouts,
            **{k: curves.get(k, 0.0) for k in (
                "top_curve_length", "top_curve_height",
                "bottom_curve_length", "bottom_curve_height",
            )},
        )
        (run_dir / "enclosure.scad").write_text(enclosure_scad, encoding="utf-8")

        hatch_scad = generate_battery_hatch_scad()
        (run_dir / "battery_hatch.scad").write_text(hatch_scad, encoding="utf-8")

        plate_scad = generate_print_plate_scad()
        (run_dir / "print_plate.scad").write_text(plate_scad, encoding="utf-8")
    except Exception as e:
        log.exception("SCAD generation failed")
        return {
            "status": "error",
            "step": "build",
            "message": f"SCAD generation failed: {e}",
        }

    # ── Emit shell preview (instant client-side 3-D) ───────────────
    _emit_shell_preview(layout, outline, curves, emit)

    # ── Compile STL ────────────────────────────────────────────────
    emit("progress", {"stage": "Compiling STL models..."})
    _check(cancel)

    stl_files: dict[str, str] = {}
    all_ok = True

    for name in ("enclosure", "battery_hatch"):
        scad_p = run_dir / f"{name}.scad"
        if not scad_p.exists():
            continue
        stl_p = scad_p.with_suffix(".stl")
        try:
            ok, msg, out = compile_scad(scad_p, stl_p, cancel=cancel)
        except Exception as e:
            ok, msg, out = False, str(e), None

        if ok and out:
            stl_files[name] = str(out)
        else:
            all_ok = False
            log.warning("STL compile failed for %s: %s", name, msg)

        _check(cancel)

    # Merge enclosure + hatch into print_plate
    if "enclosure" in stl_files and "battery_hatch" in stl_files:
        plate_stl = run_dir / "print_plate.stl"
        merged = merge_stl_files(
            [
                (Path(stl_files["enclosure"]), (0.0, 0.0, 0.0)),
                (Path(stl_files["battery_hatch"]), (80.0, 0.0, 0.0)),
            ],
            plate_stl,
        )
        if merged and plate_stl.exists():
            stl_files["print_plate"] = str(plate_stl)

    # Emit model
    model_name = (
        "print_plate" if "print_plate" in stl_files
        else "enclosure" if "enclosure" in stl_files
        else None
    )
    if model_name:
        emit("model", {
            "name": model_name,
            "path": stl_files[model_name],
            **{k: curves.get(k, 0.0) for k in (
                "top_curve_length", "top_curve_height",
                "bottom_curve_length", "bottom_curve_height",
            )},
        })

    if not all_ok:
        return {
            "status": "error",
            "step": "compile",
            "message": "Some SCAD files failed to compile.",
        }

    return {"status": "ok", "stl_files": stl_files}


# ── Step: slice ────────────────────────────────────────────────────

def _step_slice(run_dir: Path, emit: EmitFn, cancel: threading.Event | None) -> dict:
    """Slice the enclosure STL and generate staged G-code."""
    stl_path = run_dir / "enclosure.stl"
    if not stl_path.exists():
        log.info("No enclosure.stl — skipping slice step")
        return {"status": "ok"}

    emit("progress", {"stage": "Slicing & generating custom G-code..."})
    _check(cancel)

    layout = json.loads((run_dir / "pcb_layout.json").read_text(encoding="utf-8"))
    routing_path = run_dir / "routing_result.json"
    routing = (
        json.loads(routing_path.read_text(encoding="utf-8"))
        if routing_path.exists()
        else {}
    )

    try:
        gcode_result = run_gcode_pipeline(
            stl_path=stl_path,
            output_dir=run_dir,
            pcb_layout=layout,
            routing_result=routing,
        )
        if gcode_result.success:
            emit("gcode_ready", {
                "staged_gcode": str(gcode_result.staged_gcode_path),
                "raw_gcode": str(gcode_result.raw_gcode_path),
                "ink_layer_z": gcode_result.pause_points.ink_layer_z,
                "component_z": gcode_result.pause_points.component_insert_z,
                "ink_layer": gcode_result.postprocess.ink_layer,
                "component_layer": gcode_result.postprocess.component_layer,
                "total_layers": gcode_result.postprocess.total_layers,
                "stages": gcode_result.stages,
            })
        else:
            log.warning("G-code pipeline failed: %s", gcode_result.message)
            emit("progress", {"stage": f"G-code generation failed: {gcode_result.message}"})
    except Exception as e:
        log.warning("G-code pipeline error (non-fatal): %s", e)
        emit("progress", {"stage": f"G-code generation skipped: {e}"})

    return {"status": "ok"}


# ── Step: firmware ─────────────────────────────────────────────────

def _step_firmware(run_dir: Path, emit: EmitFn, cancel: threading.Event | None) -> dict:
    """Generate firmware with PCB routing pin assignments."""
    layout_path = run_dir / "pcb_layout.json"
    if not layout_path.exists():
        return {"status": "ok"}

    layout = json.loads(layout_path.read_text(encoding="utf-8"))

    # Get button data — prefer design_input, fall back to layout components
    design_path = run_dir / "design_input.json"
    if design_path.exists():
        design = json.loads(design_path.read_text(encoding="utf-8"))
        bpos = design.get("buttons", [])
    else:
        bpos = [
            {"id": c["id"], "x": c["center"][0], "y": c["center"][1]}
            for c in layout.get("components", [])
            if c.get("type") == "button"
        ]

    pin_mapping = build_pin_mapping(layout, bpos)

    firmware_path = run_dir / "firmware" / "UniversalIRRemote.ino"
    try:
        generate_firmware(pin_mapping, firmware_path)
        log.info("Firmware generated: %s", firmware_path)

        report_path = run_dir / "firmware" / "PIN_ASSIGNMENT_REPORT.txt"
        report_path.write_text(
            generate_pin_assignment_report(pin_mapping), encoding="utf-8"
        )
    except Exception as e:
        log.warning("Firmware generation failed: %s", e)

    return {"status": "ok", "pin_mapping": pin_mapping}


# ── Emit helpers ───────────────────────────────────────────────────

def _emit_debug_image(run_dir: Path, emit: EmitFn) -> None:
    """Emit debug_image event if pcb_debug.png exists."""
    pcb_debug = run_dir / "pcb_debug.png"
    if pcb_debug.exists():
        emit("debug_image", {"path": str(pcb_debug), "label": pcb_debug.stem})


def _emit_shell_preview(
    layout: dict, outline: list, curves: dict, emit: EmitFn,
) -> None:
    """Emit shell_preview event for client-side 3-D preview."""
    comps = []
    for c in layout.get("components", []):
        comps.append({
            "type": c.get("type"),
            "center": c["center"],
            "body_width_mm": c.get("body_width_mm", 0),
            "body_height_mm": c.get("body_height_mm", 0),
        })
    emit("shell_preview", {
        "outline": outline,
        "height_mm": DEFAULT_HEIGHT_MM,
        "wall_mm": hw.wall_thickness,
        "top_curve_length": curves.get("top_curve_length", 0),
        "top_curve_height": curves.get("top_curve_height", 0),
        "bottom_curve_length": curves.get("bottom_curve_length", 0),
        "bottom_curve_height": curves.get("bottom_curve_height", 0),
        "components": comps,
    })


# ── Outline normalization ─────────────────────────────────────────

def _normalize_outline(
    outline: list[list[float]],
    button_positions: list[dict],
    *,
    outline_type: str = "polygon",
) -> tuple[list[list[float]], list[dict]]:
    """Normalize outline + buttons: origin-shift, smooth, parametric shapes."""
    # Strip duplicate closing vertex
    if len(outline) >= 2 and outline[0] == outline[-1]:
        outline = outline[:-1]

    otype = (outline_type or "polygon").lower().strip()
    if otype in ("ellipse", "racetrack"):
        min_x = min(v[0] for v in outline)
        min_y = min(v[1] for v in outline)
        max_x = max(v[0] for v in outline)
        max_y = max(v[1] for v in outline)
        width = max_x - min_x
        length = max_y - min_y

        if otype == "ellipse":
            outline = generate_ellipse(width, length, n=32)
        else:
            outline = generate_racetrack(width, length, n_cap=16)

        button_positions = [
            {**b, "x": b["x"] - min_x, "y": b["y"] - min_y}
            for b in button_positions
        ]
        return outline, button_positions

    # Standard polygon: auto-smooth coarse curves
    outline = smooth_polygon(outline)

    min_x = min(v[0] for v in outline)
    min_y = min(v[1] for v in outline)

    outline = [[v[0] - min_x, v[1] - min_y] for v in outline]
    button_positions = [
        {**b, "x": b["x"] - min_x, "y": b["y"] - min_y}
        for b in button_positions
    ]

    return outline, button_positions


# ── Success result builder ─────────────────────────────────────────

def _build_success_result(run_dir: Path, steps_run: tuple[str, ...]) -> dict:
    """Build the final success-result dict and write manifest."""
    result: dict[str, Any] = {"status": "success"}

    # STL files
    stl_files: dict[str, str] = {}
    for name in ("enclosure", "battery_hatch", "print_plate"):
        p = run_dir / f"{name}.stl"
        if p.exists():
            stl_files[name] = str(p)
    result["stl_files"] = list(stl_files.keys())

    # Component / routing info
    layout_path = run_dir / "pcb_layout.json"
    if layout_path.exists():
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        result["component_count"] = len(layout.get("components", []))

    routing_path = run_dir / "routing_result.json"
    if routing_path.exists():
        routing = json.loads(routing_path.read_text(encoding="utf-8"))
        result["routed_traces"] = len(routing.get("traces", []))

    # Curve params
    curve_path = run_dir / "curve_params.json"
    if curve_path.exists():
        result.update(json.loads(curve_path.read_text(encoding="utf-8")))

    # Pin mapping + firmware
    design_path = run_dir / "design_input.json"
    if layout_path.exists():
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        bpos = []
        if design_path.exists():
            design = json.loads(design_path.read_text(encoding="utf-8"))
            bpos = design.get("buttons", [])
        result["pin_mapping"] = build_pin_mapping(layout, bpos)

    firmware_path = run_dir / "firmware" / "UniversalIRRemote.ino"
    if firmware_path.exists():
        result["firmware_path"] = str(firmware_path)

    result["message"] = (
        f"Design manufactured successfully! "
        f"{len(stl_files)} STL models generated."
    )
    if firmware_path.exists():
        result["message"] += " Firmware generated with PCB pin assignments."

    # Write manifest
    manifest = {
        "steps_run": list(steps_run),
        "stl_files": stl_files,
        "outline_vertices": result.get("component_count", 0),
        "button_count": 0,
    }
    if design_path.exists():
        design = json.loads(design_path.read_text(encoding="utf-8"))
        manifest["outline_vertices"] = len(design.get("outline", []))
        manifest["button_count"] = len(design.get("buttons", []))
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    return result


# ── Step dispatch table ────────────────────────────────────────────

_STEP_DISPATCH = {
    "validate": _step_validate,
    "place": _step_place,
    "route": _step_route,
    "build": _step_build,
    "slice": _step_slice,
    "firmware": _step_firmware,
}

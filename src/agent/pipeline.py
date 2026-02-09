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
    place_components as _place,
    generate_placement_candidates,
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

# Module-level counter so the pipeline result can report how many
# placement candidates were tried in the most recent run.
_last_tried_count: int = 1


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


def _multi_placement_route(
    outline: list[list[float]],
    bpos: list[dict],
    output_dir: Path,
    emit: EmitFn,
) -> tuple[dict | None, dict | None]:
    """
    Try multiple component placements and route each, returning the
    first successful (layout, routing_result).  If none succeed,
    return the best partial result.

    The candidates are scored by an estimated routability metric
    (channel-width analysis) and routed best-first.
    """
    global _last_tried_count

    # Generate candidates with varied battery/controller positions
    candidates = generate_placement_candidates(outline, bpos)
    log.info("Generated %d placement candidates", len(candidates))

    if not candidates:
        # Fallback: try the legacy single-placement path
        try:
            layout = _place(outline, bpos)
            candidates = [layout]
        except PlacementError:
            _last_tried_count = 0
            return (None, None)

    # Score each candidate by estimated routability
    scored: list[tuple[float, dict]] = []
    for layout in candidates:
        try:
            score, _bottlenecks = score_placement(layout, outline)
        except Exception:
            score = -999
        scored.append((score, layout))

    # Sort best-first (highest score = most likely to route)
    scored.sort(key=lambda s: s[0], reverse=True)
    log.info(
        "Candidate scores: %s",
        [round(s, 1) for s, _ in scored],
    )

    best_layout: dict | None = None
    best_routing: dict | None = None
    best_routed_count = -1

    # --- Phase A: Fast screen with limited rip-up budget ----------
    # Use a low maxAttempts to quickly check each candidate (~10-15s
    # each instead of ~260s).  This finds candidates that route easily.
    SCREEN_ATTEMPTS = 8
    _last_tried_count = 0
    screen_ranking: list[tuple[int, int, dict, dict]] = []  # (routed, idx, layout, result)

    for i, (score, layout) in enumerate(scored):
        _last_tried_count += 1

        # Skip obviously bad candidates
        if score < -20 and i > 2:
            log.info("Skipping candidate %d (score %.1f)", i, score)
            continue

        log.info("Screening candidate %d/%d (score=%.1f)...",
                 i + 1, len(scored), score)
        emit("progress", {
            "stage": f"Screening placement {i + 1}/{len(scored)}..."
        })

        try:
            routing_result = _route(layout, output_dir, max_attempts=SCREEN_ATTEMPTS)
        except (RouterError, Exception) as e:
            log.warning("Screening candidate %d failed: %s", i, e)
            if best_layout is None:
                best_layout = layout
            continue

        routed_count = len(routing_result.get("traces", []))

        if routing_result.get("success", False):
            # Winner found during fast screen!
            log.info("Candidate %d succeeded in fast screen! (%d traces)", i, routed_count)
            _save_winning_result(layout, routing_result, output_dir, emit)
            return (layout, routing_result)

        screen_ranking.append((routed_count, i, layout, routing_result))
        if routed_count > best_routed_count:
            best_routed_count = routed_count
            best_layout = layout
            best_routing = routing_result

    # --- Phase B: Thorough routing on top candidates ---------------
    # Take the top 2 candidates from screening and give them the full
    # rip-up budget (default 25 attempts incl. round-robin).
    screen_ranking.sort(key=lambda t: t[0], reverse=True)
    thorough_candidates = screen_ranking[:2]

    for routed, idx, layout, _screen_result in thorough_candidates:
        log.info("Thorough routing candidate %d (screened %d traces)...", idx, routed)
        emit("progress", {
            "stage": f"Thorough routing placement {idx + 1}..."
        })

        try:
            routing_result = _route(layout, output_dir)  # full budget (25)
        except (RouterError, Exception) as e:
            log.warning("Thorough routing candidate %d failed: %s", idx, e)
            continue

        routed_count = len(routing_result.get("traces", []))

        if routing_result.get("success", False):
            log.info("Candidate %d succeeded with thorough routing! (%d traces)",
                     idx, routed_count)
            _save_winning_result(layout, routing_result, output_dir, emit)
            return (layout, routing_result)

        if routed_count > best_routed_count:
            best_routed_count = routed_count
            best_layout = layout
            best_routing = routing_result

    # No candidate succeeded — save best partial result
    if best_layout:
        (output_dir / "pcb_layout.json").write_text(
            json.dumps(best_layout, indent=2), encoding="utf-8"
        )
        emit("pcb_layout", best_layout)
    if best_routing:
        (output_dir / "routing_result.json").write_text(
            json.dumps(best_routing, indent=2), encoding="utf-8"
        )
        pcb_debug = output_dir / "pcb_debug.png"
        if pcb_debug.exists():
            emit("debug_image", {"path": str(pcb_debug), "label": pcb_debug.stem})

    return (best_layout, best_routing)


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

    # ── 3 + 4. Place components & route traces ─────────────────────
    #
    # Instead of a single placement attempt, we generate multiple
    # placement candidates (varying battery/controller position),
    # score each by estimated routability, and route them best-first.
    # This avoids burning LLM turns on mechanically retrying
    # unroutable placements.
    emit("progress", {"stage": "Placing components & routing traces..."})
    log.info("Pipeline step 3+4: multi-placement routing")

    layout, routing_result = _multi_placement_route(
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
            tried_placements=_last_tried_count,
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
        enclosure_scad = generate_enclosure_scad(outline=outline, cutouts=cutouts)
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

    # Emit a single model event for the 3D viewer (enclosure is the
    # primary preview; other STLs are available via the download API).
    if "enclosure" in stl_files:
        emit("model", {"name": "enclosure", "path": stl_files["enclosure"]})

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

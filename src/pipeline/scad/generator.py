"""generator.py — top-level SCAD generation step.

Reads session artifacts, runs per-component resolvers, and writes
``enclosure.scad`` (and optionally ``enclosure.stl``) to the session folder.

Public entry point
------------------
    from src.pipeline.scad import run_scad_step
    scad_path = run_scad_step(session)
    scad_path = run_scad_step(session, compile_stl=True)
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.catalog.loader import load_catalog
from src.catalog.models import Component
from src.pipeline.config import CAVITY_START_MM, CEILING_MM
from src.pipeline.design.parsing import parse_physical_design, parse_circuit, build_design_spec
from src.pipeline.design.height_field import blended_height, blended_bottom_height, sample_height_grid
from src.pipeline.placer.serialization import assemble_full_placement
from src.pipeline.router.models import RoutingResult
from src.pipeline.router.serialization import parse_routing
from src.session import Session

from .outline import tessellate_outline
from .layers import shell_body_lines
from .emit import generate_scad
from .compiler import compile_scad
from .traces import build_trace_fragments, build_jumper_fragments
from .resolver import resolve_component, ResolverContext
from .fragment import ScadFragment
from .buttons import build_button_configs, generate_all_buttons_scad

log = logging.getLogger(__name__)


def run_scad_step(
    session: Session,
    compile_stl: bool = False,
) -> Path:
    """Generate ``enclosure.scad`` for the session.

    Parameters
    ----------
    session     : Session  The active session (must have placement + routing).
    compile_stl : bool     If True, also invoke OpenSCAD to render the STL.

    Returns the path to the written ``enclosure.scad``.

    Raises
    ------
    RuntimeError  If required upstream artifacts are missing.
    """

    # ── 1. Load artifacts ──────────────────────────────────────────
    placement_raw = session.read_artifact("placement.json")
    routing_raw   = session.read_artifact("routing.json")
    design_raw    = session.read_artifact("design.json")
    circuit_raw   = session.read_artifact("circuit.json")

    if placement_raw is None:
        raise RuntimeError("placement.json not found — run the placer step first.")
    if design_raw is None:
        raise RuntimeError("design.json not found — run the design step first.")

    physical  = parse_physical_design(design_raw)
    circuit   = parse_circuit(circuit_raw or {})
    design    = build_design_spec(physical, circuit)

    outline   = physical.outline
    enclosure = physical.enclosure
    placement = assemble_full_placement(placement_raw, outline, circuit.nets, enclosure)

    if routing_raw is not None:
        routing = parse_routing(routing_raw)
    else:
        log.warning(
            "routing.json not found — generating enclosure without trace channels."
        )
        routing = RoutingResult(traces=[], pin_assignments={}, failed_nets=[])
    catalog = load_catalog()

    if not catalog.ok:
        for err in catalog.errors:
            log.warning("Catalog validation: %s", err)

    log.info(
        "SCAD step: %d components  %d nets  base_height=%.1f mm",
        len(placement.components), len(placement.nets), enclosure.height_mm,
    )

    # ── 2. Tessellate footprint polygon ───────────────────────────
    flat_pts = tessellate_outline(outline)
    log.info("Footprint: %d vertices", len(flat_pts))

    # ── 3. Compute per-vertex ceiling heights ─────────────────────
    top_zs = [
        blended_height(x, y, outline, enclosure)
        for x, y in flat_pts
    ]
    z_min = min(top_zs)
    z_max = max(top_zs)
    variable_height = (z_max - z_min) >= 0.1
    log.info(
        "Ceiling heights: min=%.2f  max=%.2f mm  variable=%s",
        z_min, z_max, variable_height,
    )

    # ── 3b. Compute per-vertex floor heights ────────────────────
    bottom_zs = [
        blended_bottom_height(x, y, outline, enclosure)
        for x, y in flat_pts
    ]
    bz_min = min(bottom_zs)
    bz_max = max(bottom_zs)
    variable_bottom = (bz_max - bz_min) >= 0.1 or bz_max >= 0.1
    log.info(
        "Floor heights: min=%.2f  max=%.2f mm  variable=%s",
        bz_min, bz_max, variable_bottom,
    )

    # ── 4. Compute shell body layers ──────────────────────────
    body_lines = shell_body_lines(outline, enclosure, flat_pts, top_zs=top_zs, bottom_zs=bottom_zs)
    log.info("Shell body: %d SCAD lines", len(body_lines))

    # ── 5. Resolve per-component fragments ────────────────────────
    base_h = enclosure.height_mm
    ceil_start = base_h - CEILING_MM
    cavity_depth = ceil_start - CAVITY_START_MM

    ctx = ResolverContext(
        outline=outline,
        enclosure=enclosure,
        base_h=base_h,
        ceil_start=ceil_start,
        cavity_depth=cavity_depth,
        blended_height_fn=blended_height,
    )

    cat_index: dict[str, Component] = {c.id: c for c in catalog.components}
    all_fragments: list[ScadFragment] = []

    # Copy button outlines from UI placements to placed components
    ui_outline_map = {
        up.instance_id: up.button_outline
        for up in physical.ui_placements
        if up.button_outline is not None
    }
    for comp in placement.components:
        if comp.instance_id in ui_outline_map:
            comp.button_outline = ui_outline_map[comp.instance_id]

    for comp in placement.components:
        cat = cat_index.get(comp.catalog_id)
        if cat is None:
            log.warning("Unknown catalog entry '%s' — skipping", comp.catalog_id)
            continue
        frags = resolve_component(comp, cat, ctx)
        all_fragments.extend(frags)
        log.debug("Component %s: %d fragments", comp.instance_id, len(frags))

    # ── 6. Trace channel fragments ────────────────────────────────
    trace_frags = build_trace_fragments(routing, ceil_start)
    all_fragments.extend(trace_frags)

    # ── 6b. Jumper wire pinhole fragments ─────────────────────────
    jumper_frags = build_jumper_fragments(routing, ceil_start)
    all_fragments.extend(jumper_frags)

    log.info("Fragments: %d component + %d trace + %d jumper = %d total",
             len(all_fragments) - len(trace_frags) - len(jumper_frags),
             len(trace_frags), len(jumper_frags), len(all_fragments))

    # ── 7. Compute metadata for header comment ────────────────────
    height_grid = sample_height_grid(outline, enclosure, resolution_mm=2.0)
    max_h = z_max
    for row in height_grid["grid"]:
        for h in row:
            if h is not None and h > max_h:
                max_h = h

    metadata = {
        "components":       len(placement.components),
        "traces":           len(routing.traces),
        "fragments":        len(all_fragments),
        "base_height_mm":   enclosure.height_mm,
        "max_height_mm":    round(max_h, 1),
        "footprint_verts":  len(flat_pts),
        "variable_height":  variable_height,
    }

    # ── 8. Emit SCAD string ───────────────────────────────────────
    scad_str = generate_scad(
        body_lines, all_fragments,
        session_id=session.id,
        metadata=metadata,
        outline_pts=flat_pts,
    )

    # ── 8b. Generate custom buttons (printed next to enclosure) ───
    button_configs = build_button_configs(
        placement.components, cat_index, outline, enclosure, ceil_start,
    )
    if button_configs:
        # Compute enclosure bounding box for button placement
        enc_max_x = max(p[0] for p in flat_pts)
        enc_min_y = min(p[1] for p in flat_pts)
        buttons_scad = generate_all_buttons_scad(
            button_configs, enc_max_x, enc_min_y,
        )
        scad_str += buttons_scad
        log.info("Custom buttons: %d generated", len(button_configs))

    # ── 9. Write to session folder ────────────────────────────────
    scad_path: Path = session.path / "enclosure.scad"
    scad_path.write_text(scad_str, encoding="utf-8")

    log.info(
        "Wrote %s (%.1f kB, %d lines)",
        scad_path.name,
        len(scad_str.encode()) / 1024,
        scad_str.count("\n"),
    )

    session.pipeline_state["scad"] = "done"
    session.save()

    # ── 10. Optional: compile to STL ──────────────────────────────
    if compile_stl:
        stl_path = session.path / "enclosure.stl"
        ok, msg, out = compile_scad(scad_path, stl_path)
        if ok:
            log.info("STL rendered: %s", stl_path.name)
            session.pipeline_state["stl"] = "done"
        else:
            log.error("STL render failed: %s", msg)
            session.pipeline_state["stl"] = "error"
        session.save()

    return scad_path

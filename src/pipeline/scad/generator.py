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
from src.pipeline.config import CAVITY_START_MM, CEILING_MM, SPLIT_OVERLAP_MM
from src.pipeline.design.parsing import parse_physical_design, parse_circuit, build_design_spec
from src.pipeline.design.height_field import blended_height, blended_bottom_height, sample_height_grid
from src.pipeline.design.models import Outline
from src.pipeline.placer.serialization import assemble_full_placement
from src.pipeline.router.models import RoutingResult
from src.pipeline.router.serialization import parse_routing
from src.pipeline.gcode.pause_points import (
    ComponentPauseInfo, pause_z_for_component,
)
from src.session import Session

from .outline import tessellate_outline
from .layers import shell_body_lines
from .emit import generate_scad
from .compiler import compile_scad
from .traces import build_trace_fragments
from .resolver import resolve_component, ResolverContext
from .fragment import ScadFragment, PolygonGeometry
from .buttons import build_button_configs, generate_all_buttons_scad
from .extras import collect_and_generate_extras
from .split import compute_split_z
from .snap_fit import compute_snap_positions, snap_post_fragments, snap_clip_fragments

log = logging.getLogger(__name__)


def run_scad_step(
    session: Session,
    compile_stl: bool = False,
    enclosure_style_override: str | None = None,
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

    if enclosure_style_override and enclosure_style_override in ("solid", "two_part"):
        enclosure.enclosure_style = enclosure_style_override

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

    # ── Branch: two-part enclosure mode ───────────────────────────
    if enclosure.enclosure_style == "two_part":
        return _generate_two_part(
            session, physical, outline, enclosure, placement, routing,
            catalog, flat_pts, top_zs, bottom_zs,
            z_max=z_max, variable_height=variable_height,
            compile_stl=compile_stl,
        )

    # ── 4. Compute shell body layers (solid mode) ─────────────────
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

    # Build component pause info for multi-stage pause grouping
    comp_pause_infos: list[ComponentPauseInfo] = []
    for comp in placement.components:
        cat = cat_index.get(comp.catalog_id)
        if cat is not None:
            comp_pause_infos.append(ComponentPauseInfo(
                instance_id=comp.instance_id,
                body_height_mm=cat.protrusion_height_mm,
                mounting_style=comp.mounting_style or cat.mounting.style,
                pin_length_mm=cat.pin_length_mm,
            ))

    # Tessellate button shapes from UI placements into point-list outlines
    from src.pipeline.design.shape2d import tessellate_shape
    ui_shape_map = {
        up.instance_id: up.button_shape
        for up in physical.ui_placements
        if up.button_shape is not None
    }
    for comp in placement.components:
        shape = ui_shape_map.get(comp.instance_id)
        if shape is not None:
            outline_obj = tessellate_shape(shape)
            comp.button_outline = [[v.x, v.y] for v in outline_obj.points]

    for comp in placement.components:
        cat = cat_index.get(comp.catalog_id)
        if cat is None:
            log.warning("Unknown catalog entry '%s' — skipping", comp.catalog_id)
            continue
        # Set per-component pause_z so pin grooves are capped
        ctx.pause_z = pause_z_for_component(
            cat.protrusion_height_mm, base_h,
            mounting_style=comp.mounting_style or cat.mounting.style,
            pin_length_mm=cat.pin_length_mm,
        )
        frags = resolve_component(comp, cat, ctx)
        all_fragments.extend(frags)
        log.debug("Component %s: %d fragments (pause_z=%.1f)", comp.instance_id, len(frags), ctx.pause_z)

    # ── 6. Trace channel fragments ────────────────────────────────
    trace_frags = build_trace_fragments(routing, ceil_start)
    all_fragments.extend(trace_frags)

    # ── 6b. Outline holes ───────────────────────────────────────
    # Holes are built directly into the shell body polyhedron by
    # shell_body_lines() — no cutout fragments needed.

    log.info("Fragments: %d component + %d trace = %d total",
             len(all_fragments) - len(trace_frags),
             len(trace_frags), len(all_fragments))

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

    # ── 8b. Generate extra parts (buttons, hatches, etc.) ───────────
    extras_scad = collect_and_generate_extras(
        placement.components, cat_index, outline, enclosure,
        ceil_start,
    )

    # ── 9. Write to session folder ────────────────────────────────
    scad_path: Path = session.artifact_path("enclosure.scad")
    scad_path.parent.mkdir(parents=True, exist_ok=True)
    scad_path.write_text(scad_str, encoding="utf-8")

    log.info(
        "Wrote %s (%.1f kB, %d lines)",
        scad_path.name,
        len(scad_str.encode()) / 1024,
        scad_str.count("\n"),
    )

    extras_path: Path | None = None
    if extras_scad:
        extras_path = session.artifact_path("extras.scad")
        extras_path.write_text(extras_scad, encoding="utf-8")
        log.info(
            "Wrote %s (%.1f kB, %d lines)",
            extras_path.name,
            len(extras_scad.encode()) / 1024,
            extras_scad.count("\n"),
        )

    session.pipeline_state["scad"] = "done"
    session.save()

    # ── 10. Optional: compile to STL ──────────────────────────────
    if compile_stl:
        stl_path = session.artifact_path("enclosure.stl")
        ok, msg, out = compile_scad(scad_path, stl_path)
        if ok:
            log.info("STL rendered: %s", stl_path.name)
            session.pipeline_state["stl"] = "done"
        else:
            log.error("STL render failed: %s", msg)
            session.pipeline_state["stl"] = "error"

        if extras_path is not None:
            extras_stl = session.artifact_path("extras.stl")
            ok_e, msg_e, _ = compile_scad(extras_path, extras_stl)
            if ok_e:
                log.info("Extras STL rendered: %s", extras_stl.name)
            else:
                log.error("Extras STL render failed: %s", msg_e)

        session.save()

    return scad_path


# ── Two-part enclosure generation ──────────────────────────────────────────────


def _resolve_components_for_part(
    part: str,
    placement, physical, outline, enclosure,
    routing, cat_index, flat_pts, top_zs,
    base_h, ceil_start, cavity_depth,
):
    """Resolve component fragments for a specific part ('bottom' or 'top')."""
    ctx = ResolverContext(
        outline=outline,
        enclosure=enclosure,
        base_h=base_h,
        ceil_start=ceil_start,
        cavity_depth=cavity_depth,
        blended_height_fn=blended_height,
        part=part,
    )

    # Tessellate button shapes
    from src.pipeline.design.shape2d import tessellate_shape
    ui_shape_map = {
        up.instance_id: up.button_shape
        for up in physical.ui_placements
        if up.button_shape is not None
    }
    for comp in placement.components:
        shape = ui_shape_map.get(comp.instance_id)
        if shape is not None:
            outline_obj = tessellate_shape(shape)
            comp.button_outline = [[v.x, v.y] for v in outline_obj.points]

    all_fragments: list[ScadFragment] = []
    for comp in placement.components:
        cat = cat_index.get(comp.catalog_id)
        if cat is None:
            continue
        ctx.pause_z = pause_z_for_component(
            cat.protrusion_height_mm, base_h,
            mounting_style=comp.mounting_style or cat.mounting.style,
            pin_length_mm=cat.pin_length_mm,
        )
        frags = resolve_component(comp, cat, ctx)
        all_fragments.extend(frags)

    return all_fragments


def _generate_two_part(
    session: Session,
    physical, outline, enclosure, placement, routing,
    catalog, flat_pts, top_zs, bottom_zs,
    *,
    z_max: float,
    variable_height: bool,
    compile_stl: bool,
) -> Path:
    """Generate ``enclosure_bottom.scad`` and ``enclosure_top.scad``.

    Called when ``enclosure.enclosure_style == "two_part"``.
    """
    base_h = enclosure.height_mm
    ceil_start = base_h - CEILING_MM
    cavity_depth = ceil_start - CAVITY_START_MM
    cat_index: dict[str, Component] = {c.id: c for c in catalog.components}

    # ── Compute split height ──────────────────────────────────────
    split_z = compute_split_z(enclosure, placement.components, cat_index)

    # ── Snap-fit positions ────────────────────────────────────────
    snap_positions = compute_snap_positions(flat_pts)

    # ── BOTTOM part ───────────────────────────────────────────────
    bottom_top_zs = [split_z] * len(flat_pts)

    bottom_body_lines = shell_body_lines(
        outline, enclosure, flat_pts,
        top_zs=bottom_top_zs, bottom_zs=bottom_zs,
        skip_edge_top=True,
        open_top=True,
    )
    log.info("Bottom shell body: %d SCAD lines", len(bottom_body_lines))

    # Bottom fragments: floor-level stuff + support platforms
    bottom_frags = _resolve_components_for_part(
        "bottom", placement, physical, outline, enclosure,
        routing, cat_index, flat_pts, top_zs,
        base_h, ceil_start, cavity_depth,
    )

    # Add trace channels (they live in the floor)
    trace_frags = build_trace_fragments(routing, ceil_start)
    bottom_frags.extend(trace_frags)

    # Add snap posts
    bottom_frags.extend(snap_post_fragments(snap_positions, split_z))

    log.info("Bottom fragments: %d total", len(bottom_frags))

    height_grid = sample_height_grid(outline, enclosure, resolution_mm=2.0)
    max_h = z_max
    for row in height_grid["grid"]:
        for h in row:
            if h is not None and h > max_h:
                max_h = h

    bottom_metadata = {
        "components":       len(placement.components),
        "traces":           len(routing.traces),
        "fragments":        len(bottom_frags),
        "base_height_mm":   enclosure.height_mm,
        "max_height_mm":    round(split_z, 1),
        "footprint_verts":  len(flat_pts),
        "variable_height":  False,
        "part":             "bottom",
        "split_z_mm":       round(split_z, 2),
    }

    bottom_scad = generate_scad(
        bottom_body_lines, bottom_frags,
        session_id=session.id,
        metadata=bottom_metadata,
        outline_pts=flat_pts,
    )

    # ── TOP part ──────────────────────────────────────────────────
    top_bottom_zs = [split_z - SPLIT_OVERLAP_MM] * len(flat_pts)

    top_body_lines = shell_body_lines(
        outline, enclosure, flat_pts,
        top_zs=top_zs, bottom_zs=top_bottom_zs,
        skip_edge_bottom=True,
        open_bottom=True,
    )
    log.info("Top shell body: %d SCAD lines", len(top_body_lines))

    # Top fragments: ceiling cutouts only
    top_frags = _resolve_components_for_part(
        "top", placement, physical, outline, enclosure,
        routing, cat_index, flat_pts, top_zs,
        base_h, ceil_start, cavity_depth,
    )

    # Add snap clips
    top_frags.extend(snap_clip_fragments(snap_positions, split_z))

    log.info("Top fragments: %d total", len(top_frags))

    top_metadata = {
        "components":       len(placement.components),
        "traces":           0,
        "fragments":        len(top_frags),
        "base_height_mm":   enclosure.height_mm,
        "max_height_mm":    round(max_h, 1),
        "footprint_verts":  len(flat_pts),
        "variable_height":  variable_height,
        "part":             "top",
        "split_z_mm":       round(split_z, 2),
    }

    top_scad = generate_scad(
        top_body_lines, top_frags,
        session_id=session.id,
        metadata=top_metadata,
        outline_pts=flat_pts,
    )

    # ── Generate extras (buttons, hatches — same for both modes) ──
    extras_scad = collect_and_generate_extras(
        placement.components, cat_index, outline, enclosure,
        ceil_start,
    )

    # ── Write files ───────────────────────────────────────────────
    out_dir = session.artifact_path("enclosure_bottom.scad").parent
    out_dir.mkdir(parents=True, exist_ok=True)

    bottom_path = session.artifact_path("enclosure_bottom.scad")
    bottom_path.write_text(bottom_scad, encoding="utf-8")
    log.info("Wrote %s (%.1f kB)", bottom_path.name, len(bottom_scad.encode()) / 1024)

    top_path = session.artifact_path("enclosure_top.scad")
    top_path.write_text(top_scad, encoding="utf-8")
    log.info("Wrote %s (%.1f kB)", top_path.name, len(top_scad.encode()) / 1024)

    extras_path: Path | None = None
    if extras_scad:
        extras_path = session.artifact_path("extras.scad")
        extras_path.write_text(extras_scad, encoding="utf-8")
        log.info("Wrote %s (%.1f kB)", extras_path.name, len(extras_scad.encode()) / 1024)

    session.pipeline_state["scad"] = "done"
    session.save()

    # ── Optional: compile to STL ──────────────────────────────────
    if compile_stl:
        for scad_p, stl_name in [
            (bottom_path, "enclosure_bottom.stl"),
            (top_path, "enclosure_top.stl"),
        ]:
            stl_path = session.artifact_path(stl_name)
            ok, msg, _ = compile_scad(scad_p, stl_path)
            if ok:
                log.info("STL rendered: %s", stl_path.name)
            else:
                log.error("STL render failed for %s: %s", stl_name, msg)

        if extras_path is not None:
            extras_stl = session.artifact_path("extras.stl")
            ok_e, msg_e, _ = compile_scad(extras_path, extras_stl)
            if ok_e:
                log.info("Extras STL rendered: %s", extras_stl.name)
            else:
                log.error("Extras STL render failed: %s", msg_e)

        session.pipeline_state["stl"] = "done"
        session.save()

    return bottom_path

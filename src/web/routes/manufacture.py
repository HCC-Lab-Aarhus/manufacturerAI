from __future__ import annotations

import base64
import io
import json
import logging
import threading
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from src.pipeline.design import parse_design, validate_design
from src.pipeline.placer import place_components, placement_to_dict, parse_placement, PlacementError
from src.pipeline.router import route_traces, routing_to_dict, write_trace_bitmap
from src.pipeline.config import TRACE_RULES, BITMAP_CONFIG, get_printer
from src.pipeline.scad import run_scad_step
from src.web.routes._deps import (
    stl_compile, gcode_state,
    get_catalog, load_session_or_404, invalidate_downstream,
    enrich_components, attach_pcb_contour, enrich_placement,
)

router = APIRouter(tags=["manufacture"])


# ── Placement ──

@router.post("/sessions/{sid}/manufacture/placement")
async def run_placement(sid: str):
    s = load_session_or_404(sid)
    design_data = s.read_artifact("design.json")
    if design_data is None:
        raise HTTPException(400, "No design.json — run the design agent first")
    circuit_data = s.read_artifact("circuit.json")
    if circuit_data is None:
        raise HTTPException(400, "No circuit.json — run the circuit agent first")

    merged = {**design_data}
    merged["components"] = circuit_data.get("components", [])
    merged["nets"] = circuit_data.get("nets", [])

    cat = get_catalog()
    design = parse_design(merged)

    errors = validate_design(design, cat, printer=get_printer(s.printer_id))
    if errors:
        raise HTTPException(400, f"Design validation failed: {'; '.join(errors)}")

    try:
        result = place_components(design, cat)
    except PlacementError as e:
        raise HTTPException(
            422,
            detail={
                "error": "placement_failed",
                "instance_id": e.instance_id,
                "catalog_id": e.catalog_id,
                "reason": e.reason,
            },
        )

    data = placement_to_dict(result)
    s.write_artifact("placement.json", data)
    s.pipeline_state["placement"] = "complete"
    invalidate_downstream(s, "placement")
    s.save()
    return enrich_placement(data, cat)


@router.get("/sessions/{sid}/manufacture/placement")
async def get_placement(sid: str):
    s = load_session_or_404(sid)
    data = s.read_artifact("placement.json")
    if data is None:
        raise HTTPException(404, "No placement yet")
    cat = get_catalog()
    return enrich_placement(data, cat)


# ── Routing ──

@router.post("/sessions/{sid}/manufacture/routing")
async def run_routing(sid: str):
    s = load_session_or_404(sid)
    placement_data = s.read_artifact("placement.json")
    if placement_data is None:
        raise HTTPException(400, "No placement.json — run the placer first")

    cat = get_catalog()
    placement = parse_placement(placement_data)

    try:
        result = route_traces(placement, cat)
    except Exception as e:
        raise HTTPException(
            422,
            detail={"error": "routing_failed", "reason": str(e)},
        )

    data = routing_to_dict(result)
    data["outline"] = placement_data.get("outline", [])
    data["components"] = placement_data.get("components", [])
    data["nets"] = placement_data.get("nets", [])
    data["enclosure"] = placement_data.get("enclosure", {"height_mm": 25})
    data["trace_width_mm"] = TRACE_RULES.trace_width_mm

    enrich_components(data.get("components", []), cat)
    attach_pcb_contour(data)

    s.write_artifact("routing.json", data)
    s.pipeline_state["routing"] = "complete"
    s.save()
    return data


@router.get("/sessions/{sid}/manufacture/routing")
async def get_routing(sid: str):
    s = load_session_or_404(sid)
    data = s.read_artifact("routing.json")
    if data is None:
        raise HTTPException(404, "No routing yet")
    cat = get_catalog()
    for comp in data.get("components", []):
        if "body" not in comp or "pins" not in comp:
            enrich_components([comp], cat)
    attach_pcb_contour(data)
    return data


# ── Bitmap ──

@router.post("/sessions/{sid}/manufacture/bitmap")
async def generate_bitmap(sid: str):
    s = load_session_or_404(sid)
    placement_data = s.read_artifact("placement.json")
    routing_data = s.read_artifact("routing.json")
    if placement_data is None:
        raise HTTPException(400, "No placement.json — run the placer first")
    if routing_data is None:
        raise HTTPException(400, "No routing.json — run the router first")

    merged = {**placement_data}
    merged["components"] = placement_data.get("components", [])
    merged["nets"] = placement_data.get("nets", [])
    cat = get_catalog()
    design = parse_design(merged)

    from src.pipeline.router import parse_routing
    result = parse_routing(routing_data)

    pdef = get_printer(s.printer_id)
    outline_verts = design.outline.vertices
    xs = [v[0] for v in outline_verts]
    ys = [v[1] for v in outline_verts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    part_width = max_x - min_x
    part_depth = max_y - min_y

    part_origin_x = pdef.bed_width / 2 - (part_width / 2)
    part_origin_y = pdef.bed_depth / 2 - (part_depth / 2)

    write_trace_bitmap(
        result,
        TRACE_RULES.trace_width_mm,
        s.path / "trace_bitmap.txt",
        printer=pdef,
        origin_x=min_x,
        origin_y=min_y,
        part_width_mm=part_width,
        part_depth_mm=part_depth,
    )

    from src.pipeline.manifest import generate_manifest, write_manifest
    manifest = generate_manifest(
        part_origin_x_mm=part_origin_x,
        part_origin_y_mm=part_origin_y,
        part_width_mm=part_width,
        part_depth_mm=part_depth,
        printer=pdef,
    )
    write_manifest(manifest, s.path / "print_job.json")

    return {"status": "done"}


@router.get("/sessions/{sid}/manufacture/bitmap")
async def get_bitmap(sid: str):
    s = load_session_or_404(sid)
    bitmap_path = s.path / "trace_bitmap.txt"
    if not bitmap_path.exists():
        raise HTTPException(404, "No trace_bitmap.txt — run the router first")

    placement_data = s.read_artifact("placement.json")
    routing_data = s.read_artifact("routing.json")
    pdef = get_printer(s.printer_id)

    outline = placement_data.get("outline", []) if placement_data else []
    components = placement_data.get("components", []) if placement_data else []
    traces = routing_data.get("traces", []) if routing_data else []
    trace_width_mm = routing_data.get("trace_width_mm", TRACE_RULES.trace_width_mm) if routing_data else TRACE_RULES.trace_width_mm

    if placement_data:
        cat = get_catalog()
        enrich_components(components, cat)

    outline_verts = [[p["x"], p["y"]] for p in outline] if outline else []
    if outline_verts:
        model_cx = (min(v[0] for v in outline_verts) + max(v[0] for v in outline_verts)) / 2
        model_cy = (min(v[1] for v in outline_verts) + max(v[1] for v in outline_verts)) / 2
        bed_offset_x = pdef.bed_width / 2 - model_cx
        bed_offset_y = pdef.bed_depth / 2 - model_cy
    else:
        bed_offset_x = 0.0
        bed_offset_y = 0.0

    bitmap_cfg = BITMAP_CONFIG
    raw = bitmap_path.read_text(encoding="utf-8")
    rows = raw.splitlines()

    num_rows = len(rows)
    cols = len(rows[0]) if rows else bitmap_cfg.cols
    byte_cols = (cols + 7) // 8
    packed = bytearray(num_rows * byte_cols)
    for ri, line in enumerate(rows):
        offset = ri * byte_cols
        for ci in range(min(len(line), cols)):
            if line[ci] == '1':
                packed[offset + ci // 8] |= 1 << (7 - ci % 8)
    bitmap_b64 = base64.b64encode(bytes(packed)).decode('ascii')

    return {
        "bitmap_cols": cols,
        "bitmap_rows": num_rows,
        "bitmap_b64": bitmap_b64,
        "bed_width": pdef.bed_width,
        "bed_depth": pdef.bed_depth,
        "bed_offset_x": bed_offset_x,
        "bed_offset_y": bed_offset_y,
        "outline": outline,
        "components": components,
        "traces": traces,
        "trace_width_mm": trace_width_mm,
    }


# ── SCAD ──

@router.post("/sessions/{sid}/manufacture/scad")
async def run_scad(sid: str):
    s = load_session_or_404(sid)
    if s.read_artifact("placement.json") is None:
        raise HTTPException(400, "No placement.json — run the placer first")

    try:
        scad_path = run_scad_step(s)
    except Exception as exc:
        raise HTTPException(
            422,
            detail={"error": "scad_failed", "reason": str(exc)},
        )

    scad_text = scad_path.read_text(encoding="utf-8")
    return {
        "status": "done",
        "scad_lines": scad_text.count("\n"),
        "scad_bytes": len(scad_text),
    }


@router.get("/sessions/{sid}/manufacture/scad")
async def get_scad(sid: str):
    s = load_session_or_404(sid)
    scad_path = s.path / "enclosure.scad"
    if not scad_path.exists():
        raise HTTPException(404, "No enclosure.scad yet")
    scad_text = scad_path.read_text(encoding="utf-8")
    return {
        "status": "done",
        "scad": scad_text,
        "scad_lines": scad_text.count("\n"),
        "scad_bytes": len(scad_text),
    }


# ── Compile (STL) ──

@router.post("/sessions/{sid}/manufacture/compile")
async def start_compile(sid: str, force: bool = Query(False)):
    s = load_session_or_404(sid)
    scad_path = s.path / "enclosure.scad"
    if not scad_path.exists():
        raise HTTPException(400, "No enclosure.scad yet — run SCAD first")

    stl_path = s.path / "enclosure.stl"

    if not force and stl_path.exists() and sid not in stl_compile:
        scad_newer = scad_path.stat().st_mtime > stl_path.stat().st_mtime
        if not scad_newer:
            return {"status": "done", "stl_bytes": stl_path.stat().st_size}

    cur = stl_compile.get(sid)
    if cur and cur["status"] == "compiling" and not force:
        return {"status": "compiling"}

    if not force and cur and cur["status"] in ("done", "error"):
        return {"status": cur["status"], "message": cur.get("message", ""),
                "stl_bytes": stl_path.stat().st_size if stl_path.exists() else 0}

    if force and cur and cur.get("cancel"):
        cur["cancel"].set()

    cancel = threading.Event()
    stl_compile[sid] = {"status": "compiling", "cancel": cancel, "message": ""}

    def _do_compile():
        from src.pipeline.scad.compiler import compile_scad
        ok, msg, out = compile_scad(scad_path, stl_path, cancel=cancel, timeout=600)
        stl_compile[sid] = {
            "status": "done" if ok else "error",
            "message": msg,
            "cancel": cancel,
        }
        if ok:
            s.pipeline_state["scad"] = "done"
            s.save()

    threading.Thread(target=_do_compile, daemon=True).start()
    return {"status": "compiling"}


@router.get("/sessions/{sid}/manufacture/compile")
async def poll_compile(sid: str):
    s = load_session_or_404(sid)
    stl_path = s.path / "enclosure.stl"
    state = stl_compile.get(sid)
    if state:
        out = {"status": state["status"], "message": state.get("message", "")}
        if state["status"] == "done" and stl_path.exists():
            out["stl_bytes"] = stl_path.stat().st_size
        return out
    if stl_path.exists():
        return {"status": "done", "stl_bytes": stl_path.stat().st_size}
    return {"status": "pending"}


@router.get("/sessions/{sid}/manufacture/stl")
async def download_stl(sid: str):
    s = load_session_or_404(sid)
    stl_path = s.path / "enclosure.stl"
    if not stl_path.exists():
        raise HTTPException(404, "No enclosure.stl yet — compile first")
    response = FileResponse(
        stl_path,
        media_type="application/octet-stream",
        filename="enclosure.stl",
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response


# ── G-code ──

@router.post("/sessions/{sid}/manufacture/gcode")
async def start_gcode(
    sid: str,
    force: bool = Query(False),
    filament: str = Query(None),
    silverink_only: bool = Query(False),
):
    s = load_session_or_404(sid)
    stl_path = s.path / "enclosure.stl"
    if not stl_path.exists():
        raise HTTPException(400, "No enclosure.stl — compile SCAD first")

    routing_data = s.read_artifact("routing.json")
    if routing_data is None:
        raise HTTPException(400, "No routing.json — run routing first")

    cur = gcode_state.get(sid)
    if not force and cur and cur["status"] == "running":
        return {"status": "running"}
    if not force and cur and cur["status"] in ("done", "error"):
        return cur

    gcode_state[sid] = {"status": "running", "message": "Starting G-code pipeline…", "stages": []}

    def _do_gcode():
        from src.pipeline.gcode.pipeline import run_gcode_pipeline
        from src.pipeline.design.parsing import parse_design as _pd
        try:
            shell_height = None
            design_data = s.read_artifact("design.json")
            if design_data:
                try:
                    shell_height = _pd(design_data).enclosure.height_mm
                except Exception:
                    pass

            result = run_gcode_pipeline(
                stl_path=stl_path,
                output_dir=s.path,
                routing_result=routing_data,
                shell_height=shell_height,
                printer=s.printer_id,
                filament=filament,
                silverink_only=silverink_only,
            )
            if result.success:
                s.pipeline_state["gcode"] = "complete"
                s.save()
                gcode_state[sid] = {
                    "status": "done",
                    "message": result.message,
                    "stages": result.stages,
                    "has_bgcode": result.bgcode_path is not None and Path(result.bgcode_path).exists(),
                    "gcode_bytes": (
                        Path(result.staged_gcode_path).stat().st_size
                        if result.staged_gcode_path and Path(result.staged_gcode_path).exists()
                        else 0
                    ),
                }
            else:
                gcode_state[sid] = {
                    "status": "error",
                    "message": result.message,
                    "stages": result.stages,
                }
        except Exception as exc:
            logging.exception("G-code pipeline error")
            gcode_state[sid] = {"status": "error", "message": str(exc), "stages": []}

    threading.Thread(target=_do_gcode, daemon=True).start()
    return {"status": "running"}


@router.get("/sessions/{sid}/manufacture/gcode")
async def poll_gcode(sid: str):
    s = load_session_or_404(sid)
    cur = gcode_state.get(sid)
    if cur:
        return cur
    staged = s.path / "enclosure_staged.gcode"
    bgcode = s.path / "enclosure_staged.bgcode"
    if staged.exists():
        return {
            "status": "done",
            "message": "G-code pipeline completed successfully.",
            "stages": [],
            "has_bgcode": bgcode.exists(),
            "gcode_bytes": staged.stat().st_size,
        }
    return {"status": "pending"}


@router.get("/sessions/{sid}/manufacture/gcode/download")
async def download_gcode(sid: str, format: str = Query("gcode")):
    s = load_session_or_404(sid)
    if format == "bgcode":
        path = s.path / "enclosure_staged.bgcode"
        fname = "enclosure_staged.bgcode"
        mime = "application/octet-stream"
    else:
        path = s.path / "enclosure_staged.gcode"
        fname = "enclosure_staged.gcode"
        mime = "text/plain"
    if not path.exists():
        raise HTTPException(404, f"No {fname} — run the G-code pipeline first")
    return FileResponse(
        path,
        media_type=mime,
        filename=fname,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# ── Bundle / Print-Job ──

@router.get("/sessions/{sid}/manufacture/bundle")
async def download_bundle(sid: str):
    s = load_session_or_404(sid)
    files = {
        "enclosure_staged.gcode": s.path / "enclosure_staged.gcode",
        "trace_bitmap.txt": s.path / "trace_bitmap.txt",
        "print_job.json": s.path / "print_job.json",
    }
    missing = [name for name, path in files.items() if not path.exists()]
    if missing:
        raise HTTPException(404, f"Missing manufacturing files: {', '.join(missing)}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, path in files.items():
            zf.write(path, name)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{s.name or s.id}_bundle.zip"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@router.get("/sessions/{sid}/manufacture/print-job")
async def download_print_job(sid: str):
    s = load_session_or_404(sid)
    path = s.path / "print_job.json"
    if not path.exists():
        raise HTTPException(404, "print_job.json not found")
    return FileResponse(path, filename="print_job.json", media_type="application/json")

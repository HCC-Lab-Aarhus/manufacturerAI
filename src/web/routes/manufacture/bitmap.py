from __future__ import annotations

import base64
import threading

from fastapi import APIRouter, HTTPException

from src.pipeline.design import parse_physical_design
from src.pipeline.router import generate_trace_bitmap, parse_routing
from src.pipeline.config import TRACE_RULES, sweep_grid, get_printer
from src.web.routes._deps import (
    get_catalog, load_session_or_404,
    require_design, require_placement, require_routing,
    enrich_components,
)
from src.web.tasks import PipelineTask, get_pipeline_task, set_pipeline_task

router = APIRouter()


def _bed_center_offset(outline_verts: list, pdef) -> tuple[float, float]:
    xs = [v[0] for v in outline_verts]
    ys = [v[1] for v in outline_verts]
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    return pdef.nominal_bed_width / 2 - cx, pdef.nominal_bed_depth / 2 - cy


def _generate_and_save_bitmap(session) -> None:
    """Generate trace bitmap and write it to trace_bitmap.txt in the session folder."""
    routing_data = require_routing(session)
    physical = parse_physical_design(require_design(session))
    result = parse_routing(routing_data)
    pdef = get_printer(session.printer_id)
    grid = sweep_grid(pdef)
    model_to_bed = _bed_center_offset(physical.outline.vertices, pdef)
    lines = generate_trace_bitmap(result, TRACE_RULES.trace_width_mm,
                                  grid=grid, model_to_bed=model_to_bed)
    path = session.artifact_path("trace_bitmap.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines), encoding='utf-8')
    session.save()


def _read_bitmap_lines(session) -> list[str]:
    """Read the stored trace_bitmap.txt from the session folder."""
    path = session.artifact_path("trace_bitmap.txt")
    if not path.exists():
        raise HTTPException(404, "No trace_bitmap.txt — run the bitmap step first")
    return path.read_text(encoding='utf-8').split('\n')


@router.post("/sessions/{sid}/manufacture/bitmap")
async def run_bitmap(sid: str):
    s = load_session_or_404(sid)

    existing = get_pipeline_task(sid, "bitmap")
    if existing and existing.status == "running":
        return {"status": "running"}

    set_pipeline_task(sid, "bitmap", PipelineTask(status="running"))

    def _do():
        try:
            require_placement(s)
            require_routing(s)
            _generate_and_save_bitmap(s)
            set_pipeline_task(sid, "bitmap", PipelineTask(status="done"))
        except Exception as e:
            set_pipeline_task(sid, "bitmap", PipelineTask(status="error", error=str(e)))

    threading.Thread(target=_do, daemon=True).start()
    return {"status": "running"}


@router.get("/sessions/{sid}/manufacture/bitmap/status")
async def poll_bitmap(sid: str):
    task = get_pipeline_task(sid, "bitmap")
    if task:
        return {"status": task.status, "message": task.error or ""}
    s = load_session_or_404(sid)
    if s.has_artifact("trace_bitmap.txt"):
        return {"status": "done"}
    return {"status": "idle"}


@router.get("/sessions/{sid}/manufacture/bitmap")
async def get_bitmap(sid: str):
    s = load_session_or_404(sid)

    design_data = require_design(s)
    placement_data = require_placement(s)
    routing_data = require_routing(s)
    pdef = get_printer(s.printer_id)

    from src.web.routes._deps import _read_outline
    outline = _read_outline(s)
    components = placement_data.get("components", [])
    traces = routing_data.get("traces", [])

    if components:
        enrich_components(components, get_catalog())

    outline_verts = [[p["x"], p["y"]] for p in outline] if outline else []
    bed_offset_x, bed_offset_y = _bed_center_offset(outline_verts, pdef) if outline_verts else (0.0, 0.0)

    rows = _read_bitmap_lines(s)
    num_rows = len(rows)
    cols = len(rows[0]) if rows else 0
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
        "nominal_bed_width": pdef.nominal_bed_width,
        "nominal_bed_depth": pdef.nominal_bed_depth,
        "inkjet_offset_x": pdef.inkjet_offset_x,
        "inkjet_offset_y": pdef.inkjet_offset_y,
        "bed_offset_x": bed_offset_x,
        "bed_offset_y": bed_offset_y,
        "outline": outline,
        "components": components,
        "traces": traces,
        "trace_width_mm": TRACE_RULES.trace_width_mm,
    }

from __future__ import annotations

import base64

from fastapi import APIRouter, HTTPException

from src.pipeline.design import parse_physical_design, parse_circuit, build_design_spec
from src.pipeline.router import write_trace_bitmap, parse_routing
from src.pipeline.config import TRACE_RULES, sweep_grid, get_printer
from src.web.routes._deps import (
    get_catalog, load_session_or_404,
    require_design, require_circuit, require_placement, require_routing,
    enrich_components,
)

router = APIRouter()


def _bed_center_offset(outline_verts: list, pdef) -> tuple[float, float]:
    xs = [v[0] for v in outline_verts]
    ys = [v[1] for v in outline_verts]
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    return pdef.nominal_bed_width / 2 - cx, pdef.nominal_bed_depth / 2 - cy


@router.post("/sessions/{sid}/manufacture/bitmap")
async def generate_bitmap(sid: str):
    s = load_session_or_404(sid)
    require_placement(s)
    routing_data = require_routing(s)

    physical = parse_physical_design(require_design(s))
    result = parse_routing(routing_data)

    pdef = get_printer(s.printer_id)
    grid = sweep_grid(pdef)
    model_to_bed = _bed_center_offset(physical.outline.vertices, pdef)

    write_trace_bitmap(result, TRACE_RULES.trace_width_mm,
                       s.path / "trace_bitmap.txt", grid=grid, model_to_bed=model_to_bed)
    s.pipeline_state["bitmap"] = "complete"
    s.save()
    return {"status": "done"}


@router.get("/sessions/{sid}/manufacture/bitmap")
async def get_bitmap(sid: str):
    s = load_session_or_404(sid)
    bitmap_path = s.path / "trace_bitmap.txt"
    if not bitmap_path.exists():
        raise HTTPException(404, "No trace_bitmap.txt — run the bitmap step first")

    design_data = s.read_artifact("design.json")
    placement_data = s.read_artifact("placement.json")
    routing_data = s.read_artifact("routing.json")
    pdef = get_printer(s.printer_id)

    outline = design_data.get("outline", []) if design_data else []
    components = placement_data.get("components", []) if placement_data else []
    traces = routing_data.get("traces", []) if routing_data else []

    if components:
        enrich_components(components, get_catalog())

    outline_verts = [[p["x"], p["y"]] for p in outline] if outline else []
    bed_offset_x, bed_offset_y = _bed_center_offset(outline_verts, pdef) if outline_verts else (0.0, 0.0)

    raw = bitmap_path.read_text(encoding="utf-8")
    rows = raw.splitlines()

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

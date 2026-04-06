from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import get_printer, bed_bitmap, FLOOR_MM
from ._common import DEBUG_CONFIG, load_slicer_params, run_debug_pipeline
from .spacing import spacing_plate_width_px, _spacing_trace_paths, SPACING_TRACE_W_PX
from .width import width_plate_width_px, _width_trace_paths

router = APIRouter()

_BOX_GAP: float = 3.0
_COMB_MAX: int = 28
_COMB_SPLIT: int = _COMB_MAX // 2


def _build_combined_scad(
    plates: list[tuple[float, float, float, float]],
    plate_z: float,
) -> str:
    lines = [
        "$fn = 32;",
        "",
        "union() {",
    ]
    for px, py, pw, ph in plates:
        lines.append(f"  translate([{px:.3f}, {py:.3f}, 0])")
        lines.append(f"    cube([{pw:.3f}, {ph:.3f}, {plate_z:.3f}]);")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


@router.post("/combined")
async def generate_combined(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
) -> dict[str, Any]:
    pdef = get_printer(printer)
    grid = bed_bitmap(pdef)
    sp = load_slicer_params(printer)

    plate_z = max(sp.layer_height * 20, FLOOR_MM + sp.layer_height)
    plate_h = DEBUG_CONFIG.landscape_height
    px_mm = grid.pixel_size_mm

    all_widths = list(range(1, _COMB_MAX + 1))
    w_full = width_plate_width_px(widths=all_widths) * px_mm
    sp_full = spacing_plate_width_px(min_gap=1, max_gap=_COMB_MAX) * px_mm

    x_origin = 0.0
    y_cursor = 0.0

    plates: list[tuple[float, float, float, float]] = []
    all_trace_paths: list[list[tuple[float, float]]] = []

    plates.append((x_origin, y_cursor, w_full, plate_h))
    all_trace_paths.extend(_width_trace_paths(w_full, plate_h, px_mm, widths=all_widths))
    y_cursor += plate_h + _BOX_GAP

    plates.append((x_origin, y_cursor, w_full, plate_h))
    paths = _width_trace_paths(w_full, plate_h, px_mm, widths=all_widths)
    for p in paths:
        all_trace_paths.append([(x, y + y_cursor) for x, y in p])
    y_cursor += plate_h + _BOX_GAP

    plates.append((x_origin, y_cursor, sp_full, plate_h))
    paths = _spacing_trace_paths(sp_full, plate_h, px_mm, min_gap=1, max_gap=_COMB_MAX)
    for p in paths:
        all_trace_paths.append([(x, y + y_cursor) for x, y in p])
    y_cursor += plate_h + _BOX_GAP

    plates.append((x_origin, y_cursor, sp_full, plate_h))
    paths = _spacing_trace_paths(sp_full, plate_h, px_mm, min_gap=1, max_gap=_COMB_MAX)
    for p in paths:
        all_trace_paths.append([(x, y + y_cursor) for x, y in p])

    bb_x2 = max(px + pw for px, _, pw, _ in plates)
    bb_y2 = max(py + ph for _, py, _, ph in plates)
    model_center = (bb_x2 / 2, bb_y2 / 2)

    scad_src = _build_combined_scad(plates, plate_z)

    return run_debug_pipeline(
        scad_src, all_trace_paths, model_center,
        printer, filament,
        shell_height=plate_z,
        extra_overrides=[
            "fill_density = 100%\n"
            "fill_pattern = rectilinear\n"
            "top_solid_layers = 10\n"
            "bottom_solid_layers = 10\n",
        ],
    )

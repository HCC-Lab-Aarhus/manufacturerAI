from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import (
    get_printer, SweepGrid, sweep_grid,
    FLOOR_MM,
)
from src.pipeline.gcode.filaments import get_filament
from src.pipeline.manifest import generate_manifest
from src.pipeline.scad.compiler import compile_scad
from src.pipeline.gcode.slicer import slice_stl
from src.pipeline.gcode.postprocessor import postprocess_gcode

from ._common import (
    DEBUG_CONFIG, load_slicer_params, render_bitmap,
    DEBUG_OVERRIDE, _inject_silverink_marker,
)
from .spacing import spacing_box_ink_cells, spacing_plate_width_px
from .width import width_all_ink_cells, width_plate_width_px

router = APIRouter()

_ROW_GAP: float = 5.0
_BOX_GAP: float = 3.0
_COMB_MAX: int = 28
_COMB_SPLIT: int = _COMB_MAX // 2


def _combined_bitmap(
    grid: SweepGrid,
    boxes: list[tuple[str, tuple[float, float, float, float], dict]],
) -> str:
    """Render ink bitmap for all combined boxes."""
    ink_cells: set[tuple[int, int]] = set()

    for kind, (bx, by, bw, bh), kw in boxes:
        if kind == "spacing":
            ink_cells |= spacing_box_ink_cells(grid, bx, by, bw, bh, **kw)
        else:
            ink_cells |= width_all_ink_cells(grid, bx, by, bw, bh, **kw)

    return render_bitmap(grid.data_rows, grid.data_cols, ink_cells)


def _build_combined_scad(
    plates: list[tuple[float, float, float, float]],
    plate_z: float,
) -> str:
    lines = [
        "// manufacturerAI — combined debug test (auto-generated)",
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
    """Generate combined trace width + trace clearance test on one plate."""
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)
    sp = load_slicer_params(printer)

    plate_z = sp.layer_height * 20
    pad = DEBUG_CONFIG.padding
    plate_h_mm = DEBUG_CONFIG.landscape_height
    px_mm = grid.pixel_size_mm

    all_widths = list(range(1, _COMB_MAX + 1))
    w_full = width_plate_width_px(widths=all_widths) * px_mm
    sp_full = spacing_plate_width_px(min_gap=1, max_gap=_COMB_MAX) * px_mm

    x_origin = pad
    y_cursor = abs(pdef.inkjet_offset_y) + pad

    box_defs: list[tuple[str, tuple[float, float, float, float], dict]] = []

    box_defs.append(("width", (x_origin, y_cursor, w_full, plate_h_mm), {"widths": all_widths}))
    y_cursor += plate_h_mm + _BOX_GAP

    box_defs.append(("width", (x_origin, y_cursor, w_full, plate_h_mm), {"widths": all_widths}))
    y_cursor += plate_h_mm + _BOX_GAP

    box_defs.append(("spacing", (x_origin, y_cursor, sp_full, plate_h_mm), {"min_gap": 1, "max_gap": _COMB_MAX}))
    y_cursor += plate_h_mm + _BOX_GAP

    box_defs.append(("spacing", (x_origin, y_cursor, sp_full, plate_h_mm), {"min_gap": 1, "max_gap": _COMB_MAX}))

    all_plates = [b for _, b, _ in box_defs]

    scad_src = _build_combined_scad(all_plates, plate_z)
    bitmap = _combined_bitmap(grid, box_defs)

    bb_x = min(px for px, _, _, _ in all_plates)
    bb_y = min(py for _, py, _, _ in all_plates)
    bb_x2 = max(px + pw for px, _, pw, _ in all_plates)
    bb_y2 = max(py + ph for _, py, _, ph in all_plates)
    center = ((bb_x + bb_x2) / 2, (bb_y + bb_y2) / 2)

    with tempfile.TemporaryDirectory(prefix="debug_combined_") as tmpdir:
        tmp = Path(tmpdir)
        scad_path = tmp / "combined.scad"
        scad_path.write_text(scad_src, encoding="utf-8")

        ok, msg, stl_path = compile_scad(scad_path)
        if not ok or stl_path is None:
            raise RuntimeError(f"OpenSCAD compilation failed: {msg}")

        comp_override = tmp / "combined_override.ini"
        comp_override.write_text(
            "fill_density = 100%\n"
            "fill_pattern = rectilinear\n"
            "top_solid_layers = 10\n"
            "bottom_solid_layers = 10\n",
            encoding="utf-8",
        )
        overrides: list[Path] = []
        if DEBUG_OVERRIDE.exists():
            overrides.append(DEBUG_OVERRIDE)
        overrides.append(comp_override)

        slicer_gcode = tmp / "slicer_output.gcode"
        ok, msg, _ = slice_stl(
            stl_path,
            output_gcode=slicer_gcode,
            printer=printer,
            filament=fdef.id,
            center=center,
            extra_overrides=overrides,
        )
        if not ok:
            raise RuntimeError(f"PrusaSlicer failed: {msg}")

        final_gcode = tmp / "combined.gcode"
        postprocess_gcode(
            gcode_path=slicer_gcode,
            output_path=final_gcode,
            ink_z=FLOOR_MM,
            trace_segments=[],
        )

        gcode = _inject_silverink_marker(
            final_gcode.read_text(encoding="utf-8")
        )

    manifest = generate_manifest(
        grid=grid,
        part_origin_x_mm=bb_x,
        part_origin_y_mm=bb_y,
        part_width_mm=bb_x2 - bb_x,
        part_depth_mm=bb_y2 - bb_y,
        gcode_file="combined.gcode",
        bitmap_file="combined.txt",
        printer=pdef,
    )

    return {
        "gcode": gcode,
        "bitmap": bitmap,
        "contract": manifest.to_dict(),
    }

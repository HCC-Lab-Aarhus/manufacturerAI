from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from src.pipeline.config import (
    get_printer, SweepGrid, sweep_grid,
    FLOOR_MM, TRACE_HEIGHT_MM,
)
from src.pipeline.gcode.filaments import get_filament
from src.pipeline.manifest import generate_manifest
from src.pipeline.scad.fragment import ScadFragment, SegmentGeometry
from src.pipeline.scad.traces import TRACE_WIDTH as SCAD_TRACE_WIDTH
from src.pipeline.scad.compiler import compile_scad
from src.pipeline.gcode.slicer import slice_stl
from src.pipeline.gcode.postprocessor import postprocess_gcode

from ._common import (
    DEBUG_CONFIG, load_slicer_params, render_bitmap,
    DEBUG_OVERRIDE,
)
from .components import (
    compute_component_layout, frag_scad_lines, CompLayout,
    component_ink_cells,
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
    layouts: list[CompLayout],
) -> str:
    """Render ink bitmap for all combined boxes.

    Each entry in *boxes* is (type, (x, y, w, h), kwargs) where type is
    "spacing" or "width" and kwargs are passed to the corresponding ink-cell
    function.
    """
    ink_cells: set[tuple[int, int]] = set()

    for kind, (bx, by, bw, bh), kw in boxes:
        if kind == "spacing":
            ink_cells |= spacing_box_ink_cells(grid, bx, by, bw, bh, **kw)
        else:
            ink_cells |= width_all_ink_cells(grid, bx, by, bw, bh, **kw)

    ink_cells |= component_ink_cells(grid, layouts)

    return render_bitmap(grid.data_rows, grid.data_cols, ink_cells)


def _build_combined_scad(
    plates: list[tuple[float, float, float, float]],
    layouts: list[CompLayout],
    all_frags: list[ScadFragment],
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

    cutouts = [f for f in all_frags if f.type == "cutout"]
    additions = [f for f in all_frags if f.type == "addition"]

    if cutouts:
        lines.append("  difference() {")
        lines.append("    union() {")
        body_indent = "      "
    elif additions:
        lines.append("  union() {")
        body_indent = "    "
    else:
        body_indent = "  "

    for ly in layouts:
        lines.append(f"{body_indent}translate([{ly.plate_x:.3f}, {ly.plate_y:.3f}, 0])")
        lines.append(f"{body_indent}  cube([{ly.plate_w:.3f}, {ly.plate_h:.3f}, {plate_z:.3f}]);")
        lines.append(f"{body_indent}translate([{ly.block_x:.3f}, {ly.block_y:.3f}, 0])")
        lines.append(f"{body_indent}  cube([{ly.block_w:.3f}, {ly.block_h:.3f}, {ly.block_z_top:.3f}]);")

    for a in additions:
        fl = frag_scad_lines(a)
        lines.extend(f"{body_indent}{l}" for l in fl)

    if cutouts:
        lines.append("    }")
        lines.append("")
        for c in cutouts:
            if c.label:
                lines.append(f"    // {c.label}")
            fl = frag_scad_lines(c)
            lines.extend(f"    {l}" for l in fl)
        lines.append("  }")
    elif additions:
        lines.append("  }")

    lines.append("}")
    lines.append("")
    return "\n".join(lines)


@router.post("/combined")
async def generate_combined(
    printer: str = Query("coreone"),
    filament: str = Query("prusament_pla"),
) -> dict[str, Any]:
    """Generate combined spacing + width + component test on one plate."""
    pdef = get_printer(printer)
    fdef = get_filament(filament)
    grid = sweep_grid(pdef)
    sp = load_slicer_params(printer)

    plate_z = FLOOR_MM
    pad = DEBUG_CONFIG.padding
    plate_h_mm = DEBUG_CONFIG.landscape_height
    px_mm = grid.pixel_size_mm

    ref_bx, _ = grid.bed_to_bitmap(0.0, 0.0)
    x_left = -ref_bx

    sp1_w = spacing_plate_width_px(min_gap=1, max_gap=_COMB_SPLIT) * px_mm
    sp2_w = spacing_plate_width_px(min_gap=_COMB_SPLIT + 1, max_gap=_COMB_MAX) * px_mm
    w1_widths = list(range(1, _COMB_SPLIT + 1))
    w2_widths = list(range(_COMB_SPLIT + 1, _COMB_MAX + 1))
    w1_w = width_plate_width_px(widths=w1_widths) * px_mm
    w2_w = width_plate_width_px(widths=w2_widths) * px_mm

    nom_w = pdef.nominal_bed_width
    row_w = sp1_w + sp2_w + w1_w + w2_w + 3 * _BOX_GAP
    x_origin = max(0, min(x_left, nom_w - row_w - pad))
    x_cursor = x_origin
    y_bottom = abs(pdef.inkjet_offset_y) + pad

    box_defs: list[tuple[str, tuple[float, float, float, float], dict]] = []

    sp1_box = (x_cursor, y_bottom, sp1_w, plate_h_mm)
    box_defs.append(("spacing", sp1_box, {"min_gap": 1, "max_gap": _COMB_SPLIT}))
    x_cursor += sp1_w + _BOX_GAP

    sp2_box = (x_cursor, y_bottom, sp2_w, plate_h_mm)
    box_defs.append(("spacing", sp2_box, {"min_gap": _COMB_SPLIT + 1, "max_gap": _COMB_MAX}))
    x_cursor += sp2_w + _BOX_GAP

    w1_box = (x_cursor, y_bottom, w1_w, plate_h_mm)
    box_defs.append(("width", w1_box, {"widths": w1_widths}))
    x_cursor += w1_w + _BOX_GAP

    w2_box = (x_cursor, y_bottom, w2_w, plate_h_mm)
    box_defs.append(("width", w2_box, {"widths": w2_widths}))

    y_cursor = y_bottom + plate_h_mm + _ROW_GAP

    layouts = compute_component_layout(pdef, pad, sp.layer_height, y_start=y_cursor, x_start=x_origin)

    all_frags: list[ScadFragment] = []
    trace_segs: list[tuple[float, float, float, float]] = []
    for ly in layouts:
        all_frags.extend(ly.fragments)
        for pin_x, pin_y, _hr in ly.pins:
            all_frags.append(ScadFragment(
                type="cutout",
                geometry=SegmentGeometry(
                    ly.plate_x, pin_y, pin_x, pin_y, SCAD_TRACE_WIDTH,
                ),
                z_base=FLOOR_MM,
                depth=TRACE_HEIGHT_MM,
                label=f"trace {ly.catalog.id}",
            ))
            trace_segs.append((ly.plate_x, pin_y, pin_x, pin_y))

    scad_src = _build_combined_scad(
        [b for _, b, _ in box_defs], layouts, all_frags, plate_z,
    )
    bitmap = _combined_bitmap(grid, box_defs, layouts)

    all_plates = [b for _, b, _ in box_defs]
    bb_x = min(min(px for px, _, _, _ in all_plates), min(ly.plate_x for ly in layouts))
    bb_y = min(min(py for _, py, _, _ in all_plates), min(ly.plate_y for ly in layouts))
    bb_x2 = max(
        max(px + pw for px, _, pw, _ in all_plates),
        max(ly.plate_x + ly.plate_w for ly in layouts),
    )
    bb_y2 = max(
        max(py + ph for _, py, _, ph in all_plates),
        max(ly.plate_y + ly.plate_h for ly in layouts),
    )
    center = ((bb_x + bb_x2) / 2, (bb_y + bb_y2) / 2)

    with tempfile.TemporaryDirectory(prefix="debug_combined_") as tmpdir:
        tmp = Path(tmpdir)
        scad_path = tmp / "combined.scad"
        scad_path.write_text(scad_src, encoding="utf-8")

        ok, msg, stl_path = compile_scad(scad_path)
        if not ok or stl_path is None:
            raise RuntimeError(f"OpenSCAD compilation failed: {msg}")

        comp_override = tmp / "combined_ironing.ini"
        comp_override.write_text("top_solid_layers = 3\n", encoding="utf-8")
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
            trace_segments=trace_segs,
        )

        gcode = final_gcode.read_text(encoding="utf-8")

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

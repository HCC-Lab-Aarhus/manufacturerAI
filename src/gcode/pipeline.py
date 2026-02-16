"""
G-code pipeline orchestrator — runs the full slice → post-process flow.

This is the single entry point that the main manufacturing pipeline
and web server call.  It:

1. Slices the enclosure STL via PrusaSlicer CLI
2. Computes pause Z-heights from the enclosure geometry
3. Generates conductive-ink toolpath G-code from routing data
4. Post-processes the slicer G-code to inject pauses and ink paths
5. Returns the final staged G-code path and metadata
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.gcode.slicer import slice_stl, get_printer
from src.gcode.pause_points import compute_pause_points, PausePoints
from src.gcode.ink_traces import generate_ink_gcode, extract_trace_segments
from src.gcode.postprocessor import postprocess_gcode, PostProcessResult

log = logging.getLogger("manufacturerAI.gcode.pipeline")


@dataclass
class GcodePipelineResult:
    """Full result of the G-code generation pipeline."""

    success: bool
    message: str
    raw_gcode_path: Path | None = None
    staged_gcode_path: Path | None = None
    pause_points: PausePoints | None = None
    postprocess: PostProcessResult | None = None
    stages: list[str] = field(default_factory=list)


def run_gcode_pipeline(
    stl_path: Path,
    output_dir: Path,
    pcb_layout: dict,
    routing_result: dict,
    *,
    shell_height: float | None = None,
    layer_height: float = 0.2,
    slicer_profile: Path | None = None,
    printer: str | None = None,
) -> GcodePipelineResult:
    """Run the full G-code pipeline: slice → inject pauses → output.

    Parameters
    ----------
    stl_path : Path
        The enclosure STL to slice (typically ``enclosure.stl``).
    output_dir : Path
        Directory for all output files.
    pcb_layout : dict
        The ``pcb_layout.json`` data (board outline + components).
    routing_result : dict
        The routing result (traces with grid-coordinate paths).
    shell_height : float, optional
        Total enclosure height.  If *None*, uses the default.
    layer_height : float
        Slicer layer height in mm.
    slicer_profile : Path, optional
        Custom PrusaSlicer ``.ini`` profile.
    printer : str, optional
        Printer id (``"mk3s"`` or ``"coreone"``).

    Returns
    -------
    GcodePipelineResult
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stages: list[str] = []

    pdef = get_printer(printer)
    stages.append(f"Printer: {pdef.label} (bed {pdef.bed_width:.0f}×{pdef.bed_depth:.0f} mm)")

    # ── 1. Compute pause points ────────────────────────────────────
    log.info("Computing pause points...")
    pauses = compute_pause_points(
        shell_height=shell_height,
        layer_height=layer_height,
    )
    stages.append(
        f"Pause points: ink @ Z={pauses.ink_layer_z:.2f} (layer {pauses.ink_layer_number}), "
        f"components @ Z={pauses.component_insert_z:.2f} (layer {pauses.component_layer_number})"
    )
    log.info(
        "Pause points: ink Z=%.2f (L%d), components Z=%.2f (L%d)",
        pauses.ink_layer_z, pauses.ink_layer_number,
        pauses.component_insert_z, pauses.component_layer_number,
    )

    # ── 1b. Prefer print_plate.stl (enclosure + battery hatch) ──
    print_plate = stl_path.parent / "print_plate.stl"
    if print_plate.exists():
        log.info("Found print_plate.stl — slicing combined model")
        stl_path = print_plate
        stages.append("Using print_plate.stl (enclosure + battery hatch)")

    # ── 2. Slice STL ──────────────────────────────────────────────
    raw_gcode = output_dir / "enclosure_raw.gcode"
    log.info("Slicing %s → %s", stl_path, raw_gcode)
    stages.append(f"Slicing {stl_path.name} with PrusaSlicer...")

    ok, msg, gcode_path = slice_stl(
        stl_path,
        output_gcode=raw_gcode,
        profile_path=slicer_profile,
        printer=printer,
    )
    if not ok:
        log.error("Slicing failed: %s", msg)
        return GcodePipelineResult(
            success=False,
            message=f"Slicing failed: {msg}",
            pause_points=pauses,
            stages=stages,
        )
    stages.append(f"Slicing succeeded: {gcode_path}")

    # ── 3. Generate ink G-code ────────────────────────────────────
    log.info("Generating ink deposition G-code...")
    ink_lines = generate_ink_gcode(
        routing_result=routing_result,
        pcb_layout=pcb_layout,
        ink_z=pauses.ink_layer_z,
    )
    stages.append(f"Ink G-code: {len(ink_lines)} lines for {len(routing_result.get('traces', []))} traces")

    # ── 3b. Extract trace segments for ironing filter + highlight ──
    trace_segs = extract_trace_segments(
        routing_result=routing_result,
        pcb_layout=pcb_layout,
    )
    if trace_segs:
        stages.append(f"Trace segments: {len(trace_segs)} segments for ironing filter")

    # ── 3c. Compute bed offset (PrusaSlicer centres model on bed) ──
    from src.gcode.postprocessor import _compute_bed_offset
    bed_offset = _compute_bed_offset(stl_path, bed_size=(pdef.bed_width, pdef.bed_depth))
    stages.append(f"Bed offset: ({bed_offset[0]:.1f}, {bed_offset[1]:.1f}) mm")

    # ── 4. Post-process ───────────────────────────────────────────
    staged_gcode = output_dir / "enclosure_staged.gcode"
    log.info("Post-processing G-code...")

    pp_result = postprocess_gcode(
        gcode_path=gcode_path,
        output_path=staged_gcode,
        ink_z=pauses.ink_layer_z,
        component_z=pauses.component_insert_z,
        ink_gcode_lines=ink_lines,
        trace_segments=trace_segs,
        bed_offset=bed_offset,
    )
    stages.extend(pp_result.stages)
    stages.append(f"Staged G-code written: {staged_gcode}")

    log.info("G-code pipeline complete: %s", staged_gcode)

    return GcodePipelineResult(
        success=True,
        message="G-code pipeline completed successfully.",
        raw_gcode_path=gcode_path,
        staged_gcode_path=staged_gcode,
        pause_points=pauses,
        postprocess=pp_result,
        stages=stages,
    )

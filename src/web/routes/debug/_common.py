from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.pipeline.config import (
    get_printer, BedBitmap, bed_bitmap,
    TRACE_RULES, FLOOR_MM,
)
from src.pipeline.router.models import RoutingResult, Trace
from src.pipeline.router.bitmap import generate_trace_bitmap
from src.pipeline.scad.compiler import compile_scad
from src.pipeline.gcode.pipeline import run_gcode_pipeline

_PROFILES_DIR = Path(__file__).resolve().parents[4] / "pipeline" / "gcode" / "profiles"
DEBUG_OVERRIDE = _PROFILES_DIR / "debug_override.ini"


@dataclass(frozen=True)
class DebugConfig:
    padding: float = 5.0
    layers: int = 10
    cal_box_size: float = 100.0
    cal_square_size: float = 5.0
    portrait_width: float = 10.0
    portrait_height: float = 20.0
    landscape_width: float = 40.0
    landscape_height: float = 20.0


DEBUG_CONFIG = DebugConfig()


def _parse_ini(path: Path) -> dict[str, str]:
    kv: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        kv[key.strip()] = val.strip()
    return kv


@dataclass(frozen=True)
class SlicerParams:
    nozzle_d: float
    filament_d: float
    layer_height: float
    first_layer_height: float
    perimeter_speed: float
    infill_speed: float
    travel_speed: float
    retract_length: float
    retract_speed: float
    retract_lift: float
    ironing_spacing: float
    ironing_flowrate: float
    ironing_speed: float
    first_layer_speed: float
    disable_fan_first_layers: int
    full_fan_speed_layer: int
    fan_always_on: bool
    min_fan_speed: int
    max_fan_speed: int
    extrusion_multiplier: float
    start_gcode: str
    end_gcode: str

    @property
    def extrusion_w(self) -> float:
        return self.nozzle_d * 1.125

    @property
    def filament_area(self) -> float:
        return math.pi * (self.filament_d / 2) ** 2

    def e_per_mm(self, z: float) -> float:
        return (z * self.extrusion_w * self.extrusion_multiplier) / self.filament_area

    @property
    def perimeter_feed(self) -> int:
        return int(self.perimeter_speed * 60)

    @property
    def infill_feed(self) -> int:
        return int(self.infill_speed * 60)

    @property
    def travel_feed(self) -> int:
        return int(self.travel_speed * 60)

    @property
    def retract_feed(self) -> int:
        return int(self.retract_speed * 60)

    @property
    def ironing_feed(self) -> int:
        return int(self.ironing_speed * 60)

    @property
    def first_layer_feed(self) -> int:
        return int(self.first_layer_speed * 60)

    def fan_pwm_for_layer(self, layer: int) -> int:
        if not self.fan_always_on:
            return 0
        if layer < self.disable_fan_first_layers:
            return 0
        full_pwm = int(self.min_fan_speed * 2.55)
        if layer >= self.full_fan_speed_layer:
            return full_pwm
        span = self.full_fan_speed_layer - self.disable_fan_first_layers
        if span <= 0:
            return full_pwm
        frac = (layer - self.disable_fan_first_layers + 1) / span
        return int(full_pwm * frac)


def load_slicer_params(printer_id: str | None = None) -> SlicerParams:
    pdef = get_printer(printer_id)
    profile_path = _PROFILES_DIR / pdef.profile_filename
    kv = _parse_ini(profile_path) if profile_path.exists() else {}

    def _f(key: str, default: float) -> float:
        raw = kv.get(key, "")
        if not raw:
            return default
        return float(raw.rstrip("%"))

    def _pct(key: str, default: float) -> float:
        raw = kv.get(key, "")
        if not raw:
            return default
        val = raw.rstrip("%")
        return float(val) / 100 if "%" in raw else float(val)

    return SlicerParams(
        nozzle_d=_f("nozzle_diameter", 0.4),
        filament_d=1.75,
        layer_height=_f("layer_height", 0.2),
        first_layer_height=_f("first_layer_height", 0.2),
        perimeter_speed=_f("perimeter_speed", 45),
        infill_speed=_f("infill_speed", 45),
        travel_speed=_f("travel_speed", 100),
        retract_length=_f("retract_length", 0.8),
        retract_speed=_f("retract_speed", 35),
        retract_lift=_f("retract_lift", 0.6),
        ironing_spacing=_f("ironing_spacing", 0.1),
        ironing_flowrate=_pct("ironing_flowrate", 0.08),
        ironing_speed=_f("ironing_speed", 15),
        first_layer_speed=_f("first_layer_speed", 20),
        disable_fan_first_layers=int(_f("disable_fan_first_layers", 1)),
        full_fan_speed_layer=int(_f("full_fan_speed_layer", 4)),
        fan_always_on=_f("fan_always_on", 1) > 0,
        min_fan_speed=int(_f("min_fan_speed", 100)),
        max_fan_speed=int(_f("max_fan_speed", 100)),
        extrusion_multiplier=_f("extrusion_multiplier", 1.0),
        start_gcode=kv.get("start_gcode", ""),
        end_gcode=kv.get("end_gcode", ""),
    )


# ── Shared debug pipeline ─────────────────────────────────────────


def _wrap_mirror(scad_src: str) -> str:
    """Wrap SCAD source in the same Y-mirror the production emit uses."""
    return f"mirror([0, 1, 0]) {{\n{scad_src}\n}}\n"


def _routing_result_from_paths(
    paths: list[list[tuple[float, float]]],
) -> RoutingResult:
    """Build a RoutingResult from bare trace paths (model-local mm)."""
    traces = [
        Trace(net_id=f"debug_{i}", path=p)
        for i, p in enumerate(paths)
    ]
    return RoutingResult(
        traces=traces,
        pin_assignments={},
        failed_nets=[],
    )


def _routing_dict_from_paths(
    paths: list[list[tuple[float, float]]],
) -> dict:
    """Build a routing_result dict (as run_gcode_pipeline expects)."""
    return {
        "traces": [
            {"net_id": f"debug_{i}", "path": [list(pt) for pt in p]}
            for i, p in enumerate(paths)
        ],
        "pin_assignments": {},
        "failed_nets": [],
    }


def run_debug_pipeline(
    scad_src: str,
    trace_paths: list[list[tuple[float, float]]],
    model_center: tuple[float, float],
    printer: str,
    filament: str,
    *,
    shell_height: float | None = None,
    extra_overrides: list[str] | None = None,
) -> dict[str, str]:
    """Run the full production pipeline on debug geometry.

    Parameters
    ----------
    scad_src : str
        OpenSCAD source in model-local coordinates (before mirror).
    trace_paths : list of trace paths
        Each path is a list of ``(x, y)`` waypoints in model-local mm.
    model_center : (float, float)
        Bounding-box centre of the un-mirrored model geometry.
        Used to compute the model-to-bed offset for the bitmap.
    printer, filament : str
        Printer and filament ids.
    shell_height : float, optional
        Total enclosure height passed to pause-point computation.
        Defaults to ``FLOOR_MM + CEILING_MM``.
    extra_overrides : list of str, optional
        Extra ``.ini`` content strings to write as slicer overrides.

    Returns
    -------
    dict with ``'gcode'`` (str) and ``'bitmap'`` (str).
    """
    from src.pipeline.config import CEILING_MM

    pdef = get_printer(printer)
    grid = bed_bitmap(pdef)

    if shell_height is None:
        shell_height = FLOOR_MM + CEILING_MM

    mirrored_scad = _wrap_mirror(scad_src)

    routing_result = _routing_result_from_paths(trace_paths)
    routing_dict = _routing_dict_from_paths(trace_paths)

    with tempfile.TemporaryDirectory(prefix="debug_pipeline_") as tmpdir:
        tmp = Path(tmpdir)

        scad_path = tmp / "debug.scad"
        scad_path.write_text(mirrored_scad, encoding="utf-8")

        ok, msg, stl_path = compile_scad(scad_path)
        if not ok or stl_path is None:
            raise RuntimeError(f"OpenSCAD compilation failed: {msg}")

        override_paths: list[Path] = []
        if DEBUG_OVERRIDE.exists():
            override_paths.append(DEBUG_OVERRIDE)
        if extra_overrides:
            for i, content in enumerate(extra_overrides):
                p = tmp / f"override_{i}.ini"
                p.write_text(content, encoding="utf-8")
                override_paths.append(p)

        gcode_result = run_gcode_pipeline(
            stl_path=stl_path,
            output_dir=tmp,
            routing_result=routing_dict,
            shell_height=shell_height,
            printer=printer,
            filament=filament,
            silverink_only=True,
            extra_overrides=override_paths or None,
        )
        if not gcode_result.success or gcode_result.gcode_path is None:
            raise RuntimeError(f"Gcode pipeline failed: {gcode_result.message}")

        gcode = gcode_result.gcode_path.read_text(encoding="utf-8")

        ucx, ucy = pdef.usable_center
        model_to_bed = (ucx - model_center[0], ucy + model_center[1])

        bitmap_lines = generate_trace_bitmap(
            routing_result,
            TRACE_RULES.trace_width_mm,
            grid=grid,
            model_to_bed=model_to_bed,
        )
        bitmap = "\n".join(bitmap_lines)

    return {"gcode": gcode, "bitmap": bitmap}

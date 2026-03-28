from __future__ import annotations

import math
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.pipeline.config import get_printer, PrinterDef
from src.pipeline.gcode.filaments import FilamentDef

_PROFILES_DIR = Path(__file__).resolve().parents[4] / "pipeline" / "gcode" / "profiles"
_DEBUG_OVERRIDE = _PROFILES_DIR / "debug_override.ini"

_Z_HOP: float = 1.0


@dataclass(frozen=True)
class DebugConfig:
    padding: float = 5.0
    layers: int = 4
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


# ── Binary STL writer ──────────────────────────────────────────────


def _write_box_stl(
    path: Path,
    boxes: list[tuple[float, float, float, float, float]],
) -> None:
    """Write a binary STL containing axis-aligned boxes.

    Each box is ``(x, y, width, height, z_height)`` in bed coordinates.
    """
    tris: list[tuple[float, ...]] = []

    for bx, by, w, h, zh in boxes:
        x0, y0, z0 = bx, by, 0.0
        x1, y1, z1 = bx + w, by + h, zh

        # Bottom (normal -Z)
        tris.append((0, 0, -1, x0, y0, z0, x1, y1, z0, x1, y0, z0))
        tris.append((0, 0, -1, x0, y0, z0, x0, y1, z0, x1, y1, z0))
        # Top (normal +Z)
        tris.append((0, 0, 1, x0, y0, z1, x1, y0, z1, x1, y1, z1))
        tris.append((0, 0, 1, x0, y0, z1, x1, y1, z1, x0, y1, z1))
        # Front (normal -Y)
        tris.append((0, -1, 0, x0, y0, z0, x1, y0, z1, x1, y0, z0))
        tris.append((0, -1, 0, x0, y0, z0, x0, y0, z1, x1, y0, z1))
        # Back (normal +Y)
        tris.append((0, 1, 0, x0, y1, z0, x1, y1, z0, x1, y1, z1))
        tris.append((0, 1, 0, x0, y1, z0, x1, y1, z1, x0, y1, z1))
        # Left (normal -X)
        tris.append((-1, 0, 0, x0, y0, z0, x0, y1, z1, x0, y1, z0))
        tris.append((-1, 0, 0, x0, y0, z0, x0, y0, z1, x0, y1, z1))
        # Right (normal +X)
        tris.append((1, 0, 0, x1, y0, z0, x1, y1, z0, x1, y1, z1))
        tris.append((1, 0, 0, x1, y0, z0, x1, y1, z1, x1, y0, z1))

    with open(path, "wb") as f:
        f.write(b"\x00" * 80)
        f.write(struct.pack("<I", len(tris)))
        for t in tris:
            f.write(struct.pack("<12fH", *t, 0))


# ── Silverink marker injection ─────────────────────────────────────


def _inject_silverink_marker(gcode: str) -> str:
    """Insert the ``;silverink`` marker before the end sequence.

    Looks for the first ``M104 S0`` (hotend off) which signals the
    start of the end sequence, and inserts the head-home + marker
    block just before it.
    """
    lines = gcode.split("\n")
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith("M104") and "S0" in stripped:
            block = [
                "",
                "G91 ; relative positioning",
                "G1 Z1 F1000 ; lift head",
                "G90 ; absolute positioning",
                "",
                "G1 X0 Y0 F6000 ; move to home",
                "",
                "G91 ; relative positioning",
                "G1 Z-1 F1000 ; lower head back down",
                "G90 ; absolute positioning",
                "",
                ";silverink",
                "",
            ]
            lines[i:i] = block
            return "\n".join(lines)
    lines.append(";silverink")
    return "\n".join(lines)


# ── PrusaSlicer-based debug box slicing ────────────────────────────


def slice_debug_boxes(
    pdef: PrinterDef,
    fdef: FilamentDef,
    boxes: list[tuple[float, float, float, float, float]],
    printer_id: str | None = None,
) -> str:
    """Slice rectangular boxes via PrusaSlicer and return G-code.

    Parameters
    ----------
    pdef : PrinterDef
        Printer definition (used for id fallback).
    fdef : FilamentDef
        Filament definition (temperature / cooling overrides).
    boxes : list of (x, y, width, height, z_height)
        Rectangle positions in **bed coordinates**.
    printer_id : str, optional
        Explicit printer id for profile resolution.

    Returns
    -------
    str
        G-code text with ``;silverink`` marker injected.
    """
    from src.pipeline.gcode.slicer import slice_stl

    all_x = [x for x, *_ in boxes] + [x + w for x, _, w, *_ in boxes]
    all_y = [y for _, y, *_ in boxes] + [y + h for _, y, _, h, _ in boxes]
    center = ((min(all_x) + max(all_x)) / 2, (min(all_y) + max(all_y)) / 2)

    with tempfile.TemporaryDirectory(prefix="debug_stl_") as tmpdir:
        tmp = Path(tmpdir)
        stl_path = tmp / "debug_boxes.stl"
        gcode_path = tmp / "debug_boxes.gcode"

        _write_box_stl(stl_path, boxes)

        ok, msg, _ = slice_stl(
            stl_path,
            output_gcode=gcode_path,
            printer=printer_id or pdef.id,
            filament=fdef.id,
            center=center,
            extra_overrides=[_DEBUG_OVERRIDE],
        )
        if not ok:
            raise RuntimeError(f"PrusaSlicer failed: {msg}")

        gcode = gcode_path.read_text(encoding="utf-8")

    return _inject_silverink_marker(gcode)

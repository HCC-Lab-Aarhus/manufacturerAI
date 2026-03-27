from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from src.pipeline.config import get_printer

_PROFILES_DIR = Path(__file__).resolve().parents[4] / "pipeline" / "gcode" / "profiles"

_Z_HOP: float = 1.0



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

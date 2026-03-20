"""
Filament definitions — temperature / bed / cooling overrides per filament.

Each filament profile contains only the PrusaSlicer keys that differ
from the base printer profile.  At slice time the overrides are written
to a temporary ``.ini`` that is ``--load``'d *after* the printer
profile so they take precedence.
"""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FilamentDef:
    """A filament with its slicer overrides."""

    id: str                   # short key, e.g. "prusament_pla"
    label: str                # human-readable, e.g. "Prusament PLA"
    overrides: dict[str, str] # PrusaSlicer key→value pairs


# ── Filament catalogue ────────────────────────────────────────────

FILAMENTS: dict[str, FilamentDef] = {
    "prusament_pla": FilamentDef(
        id="prusament_pla",
        label="Prusament PLA",
        overrides={
            "temperature":                      "210",
            "first_layer_temperature":          "210",
            "filament_temperature":             "210",
            "filament_first_layer_temperature":  "210",
            "bed_temperature":                  "50",
            "first_layer_bed_temperature":      "50",
            "fan_always_on":                    "1",
            "min_fan_speed":                    "100",
            "max_fan_speed":                    "100",
            "disable_fan_first_layers":         "0",
            "full_fan_speed_layer":             "0",
        },
    ),
    "prusament_pla_recycled": FilamentDef(
        id="prusament_pla_recycled",
        label="Prusament PLA Recycled",
        overrides={
            "temperature":                      "210",
            "first_layer_temperature":          "210",
            "filament_temperature":             "210",
            "filament_first_layer_temperature":  "210",
            "bed_temperature":                  "50",
            "first_layer_bed_temperature":      "50",
            "fan_always_on":                    "1",
            "min_fan_speed":                    "100",
            "max_fan_speed":                    "100",
            "disable_fan_first_layers":         "0",
            "full_fan_speed_layer":             "0",
        },
    ),
    "prusament_petg": FilamentDef(
        id="prusament_petg",
        label="Prusament PETG",
        overrides={
            "temperature":                      "250",
            "first_layer_temperature":          "250",
            "filament_temperature":             "250",
            "filament_first_layer_temperature":  "250",
            "bed_temperature":                  "80",
            "first_layer_bed_temperature":      "80",
            "fan_always_on":                    "1",
            "min_fan_speed":                    "50",
            "max_fan_speed":                    "50",
            "disable_fan_first_layers":         "3",
            "full_fan_speed_layer":             "4",
        },
    ),
    "prusament_petg_v0": FilamentDef(
        id="prusament_petg_v0",
        label="Prusament PETG V0 (Self-Extinguishing)",
        overrides={
            "temperature":                      "230",
            "first_layer_temperature":          "230",
            "filament_temperature":             "230",
            "filament_first_layer_temperature":  "230",
            "bed_temperature":                  "80",
            "first_layer_bed_temperature":      "80",
            "fan_always_on":                    "1",
            "min_fan_speed":                    "50",
            "max_fan_speed":                    "50",
            "disable_fan_first_layers":         "3",
            "full_fan_speed_layer":             "4",
        },
    ),
    "prusament_petg_magnetite": FilamentDef(
        id="prusament_petg_magnetite",
        label="Prusament PETG Magnetite 40%",
        overrides={
            "temperature":                      "270",
            "first_layer_temperature":          "270",
            "filament_temperature":             "270",
            "filament_first_layer_temperature":  "270",
            "bed_temperature":                  "100",
            "first_layer_bed_temperature":      "100",
            "fan_always_on":                    "1",
            "min_fan_speed":                    "15",
            "max_fan_speed":                    "20",
            "disable_fan_first_layers":         "3",
            "full_fan_speed_layer":             "4",
            "filament_max_volumetric_speed":    "11",
        },
    ),
    "prusament_asa": FilamentDef(
        id="prusament_asa",
        label="Prusament ASA",
        overrides={
            "temperature":                      "260",
            "first_layer_temperature":          "260",
            "filament_temperature":             "260",
            "filament_first_layer_temperature":  "260",
            "bed_temperature":                  "110",
            "first_layer_bed_temperature":      "110",
            "fan_always_on":                    "1",
            "min_fan_speed":                    "30",
            "max_fan_speed":                    "30",
            "disable_fan_first_layers":         "3",
            "full_fan_speed_layer":             "4",
        },
    ),
    "prusament_pc_blend": FilamentDef(
        id="prusament_pc_blend",
        label="Prusament PC Blend",
        overrides={
            "temperature":                      "275",
            "first_layer_temperature":          "275",
            "filament_temperature":             "275",
            "filament_first_layer_temperature":  "275",
            "bed_temperature":                  "110",
            "first_layer_bed_temperature":      "110",
            "fan_always_on":                    "1",
            "min_fan_speed":                    "20",
            "max_fan_speed":                    "20",
            "disable_fan_first_layers":         "3",
            "full_fan_speed_layer":             "4",
        },
    ),
    "prusament_pc_blend_cf": FilamentDef(
        id="prusament_pc_blend_cf",
        label="Prusament PC Blend Carbon Fiber",
        overrides={
            "temperature":                      "285",
            "first_layer_temperature":          "285",
            "filament_temperature":             "285",
            "filament_first_layer_temperature":  "285",
            "bed_temperature":                  "110",
            "first_layer_bed_temperature":      "110",
            "fan_always_on":                    "0",
            "min_fan_speed":                    "0",
            "max_fan_speed":                    "0",
            "disable_fan_first_layers":         "0",
        },
    ),
    "prusament_pp_cf": FilamentDef(
        id="prusament_pp_cf",
        label="Prusament PP Carbon Fiber",
        overrides={
            "temperature":                      "270",
            "first_layer_temperature":          "270",
            "filament_temperature":             "270",
            "filament_first_layer_temperature":  "270",
            "bed_temperature":                  "85",
            "first_layer_bed_temperature":      "85",
            "disable_fan_first_layers":         "3",
            "full_fan_speed_layer":             "4",
        },
    ),
    "prusament_pp_gf": FilamentDef(
        id="prusament_pp_gf",
        label="Prusament PP Glass Fiber",
        overrides={
            "temperature":                      "245",
            "first_layer_temperature":          "245",
            "filament_temperature":             "245",
            "filament_first_layer_temperature":  "245",
            "bed_temperature":                  "95",
            "first_layer_bed_temperature":      "95",
            "disable_fan_first_layers":         "3",
            "full_fan_speed_layer":             "4",
        },
    ),
    "prusament_pvb": FilamentDef(
        id="prusament_pvb",
        label="Prusament PVB",
        overrides={
            "temperature":                      "215",
            "first_layer_temperature":          "215",
            "filament_temperature":             "215",
            "filament_first_layer_temperature":  "215",
            "bed_temperature":                  "75",
            "first_layer_bed_temperature":      "75",
            "fan_always_on":                    "1",
            "min_fan_speed":                    "100",
            "max_fan_speed":                    "100",
            "disable_fan_first_layers":         "0",
            "full_fan_speed_layer":             "0",
        },
    ),
    "prusament_tpu_95a": FilamentDef(
        id="prusament_tpu_95a",
        label="Prusament TPU 95A",
        overrides={
            "temperature":                      "230",
            "first_layer_temperature":          "230",
            "filament_temperature":             "230",
            "filament_first_layer_temperature":  "230",
            "bed_temperature":                  "65",
            "first_layer_bed_temperature":      "65",
            "fan_always_on":                    "0",
            "min_fan_speed":                    "0",
            "max_fan_speed":                    "0",
            "disable_fan_first_layers":         "0",
        },
    ),
    "prusament_pa11_cf": FilamentDef(
        id="prusament_pa11_cf",
        label="Prusament Nylon (PA11) Carbon Fiber",
        overrides={
            "temperature":                      "285",
            "first_layer_temperature":          "285",
            "filament_temperature":             "285",
            "filament_first_layer_temperature":  "285",
            "bed_temperature":                  "110",
            "first_layer_bed_temperature":      "110",
            "fan_always_on":                    "0",
            "min_fan_speed":                    "0",
            "max_fan_speed":                    "0",
            "disable_fan_first_layers":         "0",
        },
    ),
    "prusament_woodfill": FilamentDef(
        id="prusament_woodfill",
        label="Prusament Woodfill",
        overrides={
            "temperature":                      "195",
            "first_layer_temperature":          "195",
            "filament_temperature":             "195",
            "filament_first_layer_temperature":  "195",
            "bed_temperature":                  "60",
            "first_layer_bed_temperature":      "60",
            "disable_fan_first_layers":         "3",
            "full_fan_speed_layer":             "4",
        },
    ),
    "overture_rockpla": FilamentDef(
        id="overture_rockpla",
        label="Overture Rock PLA",
        overrides={
            # Overture Rock PLA range 190–230 °C.
            # 200 °C — lower viscosity reduces melt-pressure that
            # caused blobs at line starts.  215 °C was still too hot;
            # 210 °C under-extruded before we added extrusion_multiplier
            # and lowered max volumetric speed, but 200 °C should flow
            # fine now with those compensations in place.
            "temperature":                      "200",
            "first_layer_temperature":          "200",
            "filament_temperature":             "200",
            "filament_first_layer_temperature":  "200",
            # Bed 60 °C normal, 65 °C first layer to fight warping.
            "bed_temperature":                  "40",
            "first_layer_bed_temperature":      "40",
            # +5 % flow to compensate for mineral particles displacing
            # plastic in the melt zone.
            "extrusion_multiplier":             "1.05",
            # Rock PLA is more viscous than pure PLA — the extruder
            # cannot sustain 24 mm³/s without grinding.  11 mm³/s
            # keeps torque and heat-creep in check with the HF 0.4.
            "filament_max_volumetric_speed":    "11",
            # --- Retraction tuning for Rock PLA ---
            # The default 0.7 mm retraction isn't enough — mineral
            # particles hold melt-pressure.  1.4 mm pulls the melt
            # far enough back to break the pressure column.  (The
            # Core One HF safe max is ~1.5 mm before cold-zone risk.)
            "filament_retract_length":          "1.4",
            # Fast retract (60 mm/s) snaps the pressure off quickly;
            # slow deretract (20 mm/s) eases filament back in so it
            # doesn't surge on restart.
            "filament_retract_speed":           "60",
            "filament_deretract_speed":         "20",
            # Negative restart extra: under-prime by 0.1 mm so the
            # residual pressure is absorbed instead of blobbing.
            "filament_retract_restart_extra":   "-0.1",
            # Retract even on very short travels (≥ 0.5 mm) — Rock
            # PLA oozes fast enough that even tiny jumps leave blobs.
            "filament_retract_before_travel":   "0.5",
            # --- Z-hop (retract lift) ---
            # Lift the nozzle 0.6 mm during travel so ooze doesn't
            # drag across the print.  Default 0.2 mm was too low —
            # the nozzle barely cleared the surface and any seeping
            # filament smeared onto the part.
            "filament_retract_lift":            "0.6",
            # --- Perimeter speeds ---
            # The built-in Core One profile runs 150–200 mm/s with
            # input shaper.  Rock PLA is too heavy and viscous for
            # that — the nozzle yanks corners up before the bead can
            # bond.  These overrides bring outer walls to 45 mm/s and
            # inner walls to 60 mm/s, which eliminates corner curl.
            "external_perimeter_speed":         "45",
            "perimeter_speed":                  "45",
            "small_perimeter_speed":            "25",
            "infill_speed":                     "45",
            "solid_infill_speed":               "45",
            # --- Acceleration ---
            # Lower acceleration = gentler deceleration into corners
            # so the nozzle doesn't overshoot and peel the bead.
            "perimeter_acceleration":           "1500",
            "external_perimeter_acceleration":  "800",
            # --- Early-layer cooling / speed ---
            # No fan for the first 3 layers — keeps plastic warm and
            # pliable so each layer bonds before cooling shrinks it.
            "disable_fan_first_layers":         "3",
            "full_fan_speed_layer":             "4",
            # Slow first layer for better squish.
            "first_layer_speed":                "15",
            # Force layers 2–3 to print slowly too: if a layer would
            # finish in under 25 s PrusaSlicer reduces speed down to
            # min_print_speed.  For the small early layers of a remote
            # enclosure this effectively caps them at ~15–20 mm/s.
            "slowdown_below_layer_time":        "25",
            "min_print_speed":                  "15",
        },
    ),
}


def get_filament(filament_id: str) -> FilamentDef:
    """Return the *FilamentDef* for *filament_id*.

    Raises ``ValueError`` if *filament_id* is empty or unknown.
    """
    if not filament_id:
        raise ValueError("filament_id is required")
    fid = filament_id.lower().strip()
    if fid not in FILAMENTS:
        raise ValueError(f"Unknown filament '{filament_id}' — available: {', '.join(FILAMENTS)}")
    return FILAMENTS[fid]


def write_filament_overrides(filament_id: str, output_dir: Path) -> Path | None:
    """Write a temporary ``.ini`` with filament overrides.

    Returns the path to the override file, or *None* if no overrides
    are needed (i.e. the filament has no overrides).
    """
    fdef = get_filament(filament_id)
    if not fdef.overrides:
        return None

    ini_path = output_dir / f"_filament_{fdef.id}.ini"
    lines = [
        f"# Filament overrides — {fdef.label}",
        f"# Auto-generated by ManufacturerAI.  Do not edit.",
        "",
    ]
    for key, val in fdef.overrides.items():
        lines.append(f"{key} = {val}")
    lines.append("")

    ini_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Wrote filament overrides: %s (%d keys)", ini_path, len(fdef.overrides))
    return ini_path

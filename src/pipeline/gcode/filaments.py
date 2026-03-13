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
            # Prusament PLA prints well at 215 °C on MK3S / MK3S+.
            # On the Core One HF nozzle 215 °C is the minimum before
            # clogs — keep it as the safe baseline.
            "temperature":                      "215",
            "first_layer_temperature":          "215",
            "filament_temperature":             "215",
            "filament_first_layer_temperature":  "215",
            "bed_temperature":                  "40",
            "first_layer_bed_temperature":      "40",
            # Standard cooling — fan on from layer 1.
            "disable_fan_first_layers":         "0",
            "full_fan_speed_layer":             "0",
            # Normal first-layer speed (let printer profile decide).
            "first_layer_speed":                "30",
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

DEFAULT_FILAMENT = "overture_rockpla"


def get_filament(filament_id: str | None = None) -> FilamentDef:
    """Return the *FilamentDef* for *filament_id* (falls back to default)."""
    fid = (filament_id or DEFAULT_FILAMENT).lower().strip()
    if fid not in FILAMENTS:
        log.warning("Unknown filament '%s' — falling back to %s", fid, DEFAULT_FILAMENT)
        fid = DEFAULT_FILAMENT
    return FILAMENTS[fid]


def write_filament_overrides(filament_id: str | None, output_dir: Path) -> Path | None:
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

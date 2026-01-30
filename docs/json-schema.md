# JSON Schema (MVP)

The canonical parameter format is defined in:
- `schemas/remote_params.schema.json` (JSON Schema)
- `src/remote_gdt/design/models.py` (Pydantic models)

Key idea: **LLM outputs parameters, not geometry**.
Blender takes validated parameters and generates STLs deterministically.

## Main fields
- remote.length_mm / width_mm / thickness_mm
- remote.wall_mm / corner_radius_mm
- buttons.rows / cols
- buttons.diam_mm / spacing_mm
- buttons.margins + hole_clearance_mm

## Derived
Validators compute whether the button grid fits and apply auto-fixes.

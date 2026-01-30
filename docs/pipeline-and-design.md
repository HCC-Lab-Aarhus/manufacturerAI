# Pipeline

This repository implements a practical pipeline for the bachelor project:

1) **Intent capture** (optional LLM): user describes a remote in natural language.
2) **Parameter extraction**: output strict JSON parameters.
3) **Validation + auto-fix**: programmatic rules clamp/repair parameters for manufacturability.
4) **Parametric CAD generation**: Blender (`bpy`) generates geometry deterministically from JSON.
5) **Export**: STL outputs + report.

## Running Blender headless

```bash
blender -b --python src/remote_gdt/blender/generate_blender.py -- <params.json> <output_dir>
```

The script generates:
- hollow rounded-rectangle shell
- top/bottom split
- button holes

## Outputs
Each run writes:
- params_raw.json (if created from prompt)
- params_validated.json
- remote_top.stl
- remote_bottom.stl
- report.md

# Design rules (MVP)

## Printability constraints (defaults)
- Min wall thickness: 1.2 mm
- Min button spacing: 1.0 mm
- Min button diameter: 5.0 mm
- Button hole clearance: 0.2–0.4 mm (radius expansion)

## Layout constraints
- usable_width = width - 2*margin_side
- usable_length = length - margin_top - margin_bottom
- grid_width = cols*diam + (cols-1)*spacing
- grid_height = rows*diam + (rows-1)*spacing

require:
- grid_width <= usable_width
- grid_height <= usable_length

## Auto-fix priority
1) increase width/length within max bounds
2) decrease button diameter down to min
3) decrease spacing down to min
4) otherwise: fail with an actionable error

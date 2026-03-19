# Pin Hole Fix Plan

## Problem

Components are extremely difficult to insert into the 3D-printed enclosures because the pin holes are too small. Even with moderate force the leads don't slide in cleanly, making assembly frustrating and risking damage to both the print and the components.

---

## Current State

### Catalog hole diameters (what the component datasheet says the pin is)

| Component | `hole_diameter_mm` | Actual lead size |
|---|---|---|
| ATmega328P DIP-28 | 0.5 mm | ~0.46 mm square leads |
| LED 5 mm | 0.5 mm | ~0.45–0.5 mm round leads |
| Resistor (axial) | 0.6 mm | ~0.5–0.6 mm round leads |
| Tactile button 6×6 | 0.75 mm | ~0.7–1.0 mm flat legs |
| Battery holder 2×AAA | 3.0 mm | wide spring tabs |

### How holes are generated today

The SCAD resolver (`src/pipeline/scad/resolver.py`) builds each pinhole as two stacked cutouts:

1. **Shaft** — from floor (`FLOOR_MM = 2.0`) up to just below the taper zone.
   - Size: `hole_diameter_mm + PINHOLE_CLEARANCE` (currently **0.3 mm**).
   - Shape: **square** `RectGeometry` even for round pins.
   
2. **Taper / funnel** — top 0.5 mm of the hole.
   - Size: shaft size + `PINHOLE_TAPER_EXTRA` (**0.4 mm** added per side).
   - Purpose: guide the pin in and provide bridging area for conductive ink.

So for a DIP pin: shaft = 0.5 + 0.3 = **0.8 mm square**, taper = 0.8 + 0.4 = **1.2 mm square**.

### Why it's too tight

1. **FDM shrinkage is severe on small holes.** FDM holes shrink by ~0.4 mm total (~0.2 mm per wall) due to bead bulge, corner rounding, and ooze. This is not linear — sub-1.5 mm holes are hit hardest. A nominally 0.8 mm square hole can print as small as **0.4–0.5 mm**, which is the same size as or smaller than the pin lead itself.

2. **Square holes + round pins.** The shaft is a square cutout. FDM nozzles deposit extra material at direction changes (corners), so corners are the tightest part of the hole — exactly where round pins try to pass.

3. **Clearance is far too small.** 0.3 mm total clearance (0.15 mm per side) is fine for CNC-milled PCBs but FDM dimensional accuracy is ±0.2 mm *per wall*. The entire clearance budget is consumed by shrinkage alone, leaving zero actual gap.

4. **Taper is too short.** The 0.5 mm funnel is only ~2–3 layers tall at 0.2 mm layer height. It gets partially filled by bridging/sagging and provides almost no guidance.

5. **No chamfer — just a step.** The transition from taper to shaft is an abrupt step, not a gradual slope. Pins catch on this ledge.

### Detailed shrinkage analysis

FDM hole undersizing for small openings (PLA, 0.2 mm layer height, 0.4 mm nozzle):

| Nominal hole | Printed hole | Shrinkage |
|---|---|---|
| 0.8 mm | ~0.4 mm | 0.4 mm |
| 1.0 mm | ~0.6 mm | 0.4 mm |
| 1.2 mm | ~0.8 mm | 0.4 mm |
| 1.5 mm | ~1.1 mm | 0.4 mm |
| 2.0 mm | ~1.6 mm | 0.4 mm |

Current actual hole sizes after printing:

| Component | Lead size | Nominal shaft | Printed shaft | Clearance/side |
|---|---|---|---|---|
| ATmega DIP | 0.46 mm | 0.8 mm | ~0.4 mm | **negative — won't fit** |
| LED 5 mm | 0.5 mm | 0.8 mm | ~0.4 mm | **negative — won't fit** |
| Resistor | 0.5 mm | 0.9 mm | ~0.5 mm | **~0 mm — barely fits** |
| Button | 0.7 mm wide | 1.05 mm | ~0.65 mm | **negative — won't fit** |

---

## Proposed Fix

### 1. Increase `PINHOLE_CLEARANCE` from 0.3 → 1.0 mm

The clearance must exceed the ~0.4 mm FDM shrinkage AND leave real room for the pin. At 1.0 mm total clearance (0.5 mm per side nominal, ~0.3 mm per side after shrinkage), pins will slide in with light finger pressure.

Resulting shaft sizes:

| Component | Lead | Nominal shaft | Printed shaft | Clearance/side |
|---|---|---|---|---|
| ATmega DIP | 0.46 mm | 1.5 mm | ~1.1 mm | **0.32 mm — easy** |
| LED 5 mm | 0.5 mm | 1.5 mm | ~1.1 mm | **0.30 mm — easy** |
| Resistor | 0.5 mm | 1.6 mm | ~1.2 mm | **0.35 mm — easy** |
| Button (width) | 0.7 mm | 1.75 mm | ~1.35 mm | **0.33 mm — easy** |

**Where:** `src/pipeline/scad/resolver.py`, constant `PINHOLE_CLEARANCE`.

### 2. Replace the single-step taper with a multi-step funnel

**This is the key usability improvement.** The current pinhole has just two layers: a tight shaft and a single wider rectangle on top. That creates a hard ledge where pins catch. Instead, replace it with a **graduated multi-step funnel** — a staircase of progressively wider rectangles that approximates a smooth cone.

#### Current (2 layers — abrupt step near ceiling):
```
z=14.5 (ceiling)
  ┌──────────┐  taper — way up near ceiling, useless
  └──┬────┬──┘
     │    │     shaft (12+ mm of open cavity — pocket already cut)
     │    │
z=3  │    │     ← CAVITY_START — pin enters solid floor here!
     │    │     (no funnel at this critical transition)
z=2  └────┘     ← FLOOR — trace layer
```

The funnel was at the ceiling (~z=14.5), but the pin enters solid material at z=3.0 (CAVITY_START_MM). The 11+ mm of cavity above is open pocket space — a funnel there does nothing.

#### Proposed (funnel at CAVITY_START — where pin enters solid floor):
```
z=14.5 (ceiling)
        │  │     shaft-width extension (open cavity, redundant with pocket)
        │  │
z=3.0   │  │     ← CAVITY_START — pin enters here
  ┌─────┘  └─────┐  step 5 — widest (cavity entrance)
  └──┬────────┬──┘  step 4
     └──┬──┬──┘    step 3
        │  │       step 2
        │  │       step 1 — narrowest (trace contact)
z=2.0   └──┘       ← FLOOR — trace layer / ink
```

The funnel spans the 1.0 mm solid floor zone (z=2.0 to z=3.0). Wide at z=3.0 where the pin enters from the component pocket, narrow at z=2.0 for snug ink-trace contact.

#### Implementation approach

Replace the current 2-fragment pinhole (shaft + taper) with a multi-fragment staircase:

- **New constant:** `PINHOLE_TAPER_STEPS = 5` — number of graduated steps in the funnel zone
- **Keep:** `PINHOLE_TAPER_EXTRA = 1.0 mm` — total extra width per side at the mouth (same as before)
- **Note:** `PINHOLE_TAPER_DEPTH` is no longer used directly — the funnel spans the actual solid floor zone: FLOOR_MM (z=2.0) to CAVITY_START_MM (z=3.0) = **1.0 mm**.

Each step `i` (0 to STEPS-1, bottom to top of funnel) gets:
- Width per side = `shaft_size + TAPER_EXTRA × (i+1)/STEPS` — narrowest at bottom, widest at top
- Height = `actual_taper / STEPS` (= 0.2 mm per step at 1.0 mm zone, ~1 print layer each)
- Z base = `FLOOR_MM + i × step_height`

For a DIP pin (shaft = 1.5 mm), the 5 funnel steps through the solid floor zone:

| Step | Extra per side | Total opening | After shrinkage | Z range |
|---|---|---|---|---|
| Step 1 (bottom) | +0.2 mm | 1.9 mm | ~1.5 mm | z=2.0–2.2 (trace layer) |
| Step 2 | +0.4 mm | 2.3 mm | ~1.9 mm | z=2.2–2.4 |
| Step 3 | +0.6 mm | 2.7 mm | ~2.3 mm | z=2.4–2.6 |
| Step 4 | +0.8 mm | 3.1 mm | ~2.7 mm | z=2.6–2.8 |
| Step 5 (top) | +1.0 mm | 3.5 mm | ~3.1 mm | z=2.8–3.0 (cavity entrance) |
| Extension | +0.0 mm | 1.5 mm | ~1.1 mm | z=3.0–ceil (open cavity) |

You drop the pins into the pocket, they hit the ~3 mm wide funnel mouth at the cavity floor, and slide through the 1 mm graduated zone to the trace layer.

#### Code change in `_pinhole_fragments()`

The change is in `src/pipeline/scad/resolver.py`, method `_pinhole_fragments()`. Instead of emitting 2 fragments (shaft + taper), emit 1 shaft + N step fragments:

```python
# Funnel spans the solid floor zone (FLOOR_MM → CAVITY_START_MM)
funnel_top = CAVITY_START_MM          # z=3.0
funnel_bottom = FLOOR_MM              # z=2.0
actual_taper = funnel_top - funnel_bottom  # 1.0 mm
step_h = actual_taper / PINHOLE_TAPER_STEPS

# Graduated funnel: narrowest at bottom (trace layer),
# widest at top (cavity entrance)
for i in range(PINHOLE_TAPER_STEPS):
    frac = (i + 1) / PINHOLE_TAPER_STEPS
    extra = PINHOLE_TAPER_EXTRA * frac
    frags.append(ScadFragment(
        type="cutout",
        geometry=RectGeometry(px, py, pin_d + extra, pin_d + extra),
        z_base=funnel_bottom + i * step_h,
        depth=step_h,
        label=f"pin funnel {self.cid}:{pin.id} step {i}",
    ))

# Shaft-width extension through the open cavity above
if z_top > funnel_top:
    frags.append(ScadFragment(
        type="cutout",
        geometry=RectGeometry(px, py, pin_d, pin_d),
        z_base=funnel_top,
        depth=z_top - funnel_top,
        label=f"pin {self.cid}:{pin.id}",
    ))
```

This stays entirely within the existing `ScadFragment` / `RectGeometry` architecture — no new geometry types needed. The emitter's Shapely merge pass will group same-z fragments efficiently.

**Where:** `src/pipeline/scad/resolver.py`, method `_pinhole_fragments()` and new constant `PINHOLE_TAPER_STEPS`.

### 3. Update the old `cutouts.py` constants and approach

The old codepath in `old scad/cutouts.py` has its own constants (`PINHOLE_CLEARANCE = 0.15`, `PINHOLE_TAPER_D = 1.4`). These should be updated for consistency:
- `PINHOLE_CLEARANCE`: 0.15 → **1.0**
- `PINHOLE_TAPER_D`: 1.4 → **3.5** (matching the new mouth size)
- `PINHOLE_TAPER_DEPTH`: keep or bump to **1.5**
- Optionally adopt the same multi-step approach if this code path is still used

The debug route in `src/web/routes/debug.py` also has `PINHOLE_TAPER_D = 1.4` that should be updated to **3.5**.

### 5. Increase catalog `hole_diameter_mm` where they're underspecified

Some catalog entries have hole diameters smaller than or equal to the actual lead gauge. These should reflect the **actual lead gauge** (the clearance does the rest):

- **ATmega328P** pins: 0.5 → **0.6 mm** (DIP leads measured at 0.46 mm, round up)
- **ATmega328P** rect shape widths: 0.5 → **0.6 mm** (match hole_diameter)
- **LED 5 mm** pins: 0.5 → **0.6 mm** (LED leads are ~0.5 mm)
- **Resistor**: 0.6 mm is fine (leads are ~0.5 mm)
- **Button**: 0.75 mm is fine (legs measured ~0.7 mm)
- **Battery**: 3.0 mm is fine

### 6. Bump the default in `loader.py`

The fallback `hole_diameter_mm` in `src/catalog/loader.py` is 0.8 mm. With 1.0 mm clearance this produces a 1.8 mm nominal shaft (~1.4 mm printed), which is generous. Consider bumping the default from 0.8 → **1.0 mm** so that any future catalog entries without explicit `hole_diameter_mm` also get adequately sized holes.

---

## Summary of constant changes

| Constant | Current | Proposed | Location |
|---|---|---|---|
| `PINHOLE_CLEARANCE` | 0.3 mm | **1.0 mm** | `resolver.py` |
| `PINHOLE_TAPER_EXTRA` | 0.4 mm | **1.0 mm** | `resolver.py` |
| `PINHOLE_TAPER_DEPTH` | 0.5 mm | **1.5 mm** | `resolver.py` |
| `PINHOLE_TAPER_STEPS` | (new) | **5** | `resolver.py` |
| `PINHOLE_CLEARANCE` (old) | 0.15 mm | **1.0 mm** | `old scad/cutouts.py` |
| `PINHOLE_TAPER_D` (old) | 1.4 mm | **3.5 mm** | `old scad/cutouts.py`, `debug.py` |
| `PINHOLE_TAPER_DEPTH` (old) | 0.5 mm | **1.5 mm** | `old scad/cutouts.py` |
| ATmega `hole_diameter_mm` | 0.5 mm | **0.6 mm** | `catalog/atmega328p_dip28.json` |
| ATmega shape `width_mm` | 0.5 mm | **0.6 mm** | `catalog/atmega328p_dip28.json` |
| LED `hole_diameter_mm` | 0.5 mm | **0.6 mm** | `catalog/led_5mm.json` |
| `loader.py` default | 0.8 mm | **1.0 mm** | `src/catalog/loader.py` |

## Resulting hole sizes after fix (DIP pin example)

| | Before | After |
|---|---|---|
| Nominal shaft size | 0.8 mm | **1.6 mm** |
| Printed shaft (after ~0.4 mm FDM shrinkage) | ~0.4 mm | **~1.2 mm** |
| Funnel shape | 1 abrupt step (ledge) | **5 graduated steps (smooth cone)** |
| Nominal funnel mouth | 1.2 mm | **3.5 mm** |
| Printed funnel mouth (after shrinkage) | ~0.8 mm | **~3.1 mm** |
| Funnel depth | 0.5 mm (2–3 layers) | **1.5 mm (7–8 layers, 5 steps × 0.3 mm)** |
| Effective clearance per side on 0.46 mm lead | ~0.0 mm — **won't fit** | **~0.37 mm — slides in easily** |

---

## Risks & things to verify

- **Trace routing between same-component pins.** Wider holes eat into the gap between adjacent pins. For the ATmega DIP at 2.54 mm pitch with 1.6 mm nominal shafts, the gap is 2.54 - 1.6 = **0.94 mm**. However, this is not a real concern because:
  - The router already uses `pin_clearance_mm = 1.5 mm` so it doesn't route traces between pins of the same IC
  - The ATmega has `keepout_margin_mm = 5.0`; traces go around, not between pins
  - Between pins of *different* components, the placer ensures large spacing

- **Conductive ink contact.** Wider holes mean the pin has more room to move. Two mitigations:
  1. DIP pins have a slight spring/bend — they'll still press against at least one wall
  2. The ink trace channel meets the pinhole at the floor — the pin rests on ink regardless of lateral play
  3. If a specific component needs tighter fit, override via its catalog `hole_diameter_mm` (lower value = tighter hole for that part only)

- **Print test.** After changing constants, print a test piece with:
  - At least one DIP IC (ATmega or similar) — the hardest to insert (28 pins in two rows)
  - One LED (round leads in square holes)
  - One resistor
  - Verify pins slide in with light finger pressure, no tools needed
  - Verify pins don't fall out when the board is flipped — some friction should remain

- **Pin bridge width.** `PIN_BRIDGE_WIDTH` is 1.2 mm — the cutout connecting trace channels to pin holes. May need to increase to match the wider pinholes so the trace channel cleanly meets the shaft. Consider bumping to **1.6 mm** to match the new shaft widths.

- **If holes are TOO loose.** The ink contact concern is the main risk. If after testing the pins have zero friction and don't make reliable ink contact, dial `PINHOLE_CLEARANCE` back to 0.8 mm as a compromise. But err on the side of too loose — it's much easier to add a drop of solder/glue to a loose pin than to drill out a too-tight hole in a plastic enclosure.

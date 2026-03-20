# Cutout Fix Plan — Implemented

## Summary

This document describes the cutout-system changes that were implemented to
fix component insertion ergonomics, nozzle collision safety, and pin-hole
geometry.  The changes span catalog data, the SCAD resolver, the G-code
pause-point system, the postprocessor, and the web route.

---

## 1. Measured Component Dimensions

Physical measurements taken with calipers.  "Total height" = from absolute
bottom of device (pin tips on floor) to top of component.

| Component | Total height (from Z=0) | Pin length | Body height (catalog) | Installed top above cavity floor |
|---|---|---|---|---|
| ATmega328P DIP-28 | **9.1 mm** | 5.6 mm | 3.3 mm | **6.1 mm** |
| Tactile button 6×6 | **11.3 mm** (incl. cap) | 4.2 mm | 3.3 mm | **8.7 mm** (actuator + cap) |
| Battery holder plates | — | — | 13.5 mm | **13.5 mm** (full cavity) |

---

## 2. Catalog Corrections

### ATmega body width
Changed `body.width_mm` from **7.2 → 8.7 mm** to match the real chip.
The old value left a 0.2 mm gap between the body pocket edge (±3.6 mm) and
the inner edge of each pin shaft (±3.8 mm).  This gap was too thin for the
slicer — it filled it in, creating visible thin walls.  With 8.7 mm the
body pocket (±4.35 mm) overlaps the pin shafts (centered at ±4.6 mm),
merging into one continuous cutout.

### `installed_height_mm` field
New optional field on `Mounting`.  For through-hole DIP components the body
sits elevated on its pins, so the installed top is higher than
`body.height_mm` alone.  The ATmega's installed height above the cavity
floor is **6.1 mm** (= 9.1 mm absolute − 3.0 mm cavity start).

### `pause_z_mm` field
New optional field on `Mounting`.  Specifies the **exact Z height** (from
build plate) at which the print should pause for this component to be
inserted.  This replaces the old computed grouping system.

| Component | `pause_z_mm` |
|---|---|
| ATmega328P | **12.0 mm** |
| Tactile button | **12.0 mm** |
| Battery holder | **16.0 mm** |

### `protrusion_height_mm` property
New computed property on `Component`.  Returns the tallest point above the
cavity floor including cap/actuator and installed height.  Used by the
pause system to verify nozzle clearance.

```
protrusion = max(installed_height or body_height,
                 actuator.total_height + cap.height)
```

| Component | `protrusion_height_mm` |
|---|---|
| ATmega | 6.1 mm (from installed_height_mm) |
| Button | 8.7 mm (actuator 4.7 + cap 4.0) |

---

## 3. Multi-Stage Pause System

### Overview

The print has **3 pauses** (was 2: ink + one component pause):

| # | Label | Z height | What to insert |
|---|-------|----------|----------------|
| 1 | **Ink** | **2.0 mm** | Remove, iron floor, deposit conductive ink, return |
| 2 | **Components** | **12.0 mm** | ATmega + button switches |
| 3 | **Components** | **16.0 mm** | Battery contact plates |

### How pauses are determined

Each catalog entry declares its own `pause_z_mm`.  Components sharing the
same Z are merged into a single pause.  If a component has no
`pause_z_mm`, a fallback formula computes one:

```
z = CAVITY_START_MM + protrusion_height_mm + PAUSE_NOZZLE_CLEARANCE_MM
```

### Nozzle clearance at each pause

| Pause | Tallest component top (from Z=0) | Pause Z | Clearance |
|-------|----------------------------------|---------|-----------|
| Components @ 12.0 | Button: 11.3 mm | 12.0 mm | **0.7 mm** |
| Components @ 16.0 | Battery plate top: ~16.5 mm | 16.0 mm | — (plates inserted from above into channels) |

### Files changed

- **`src/pipeline/config.py`** — Added `PAUSE_NOZZLE_CLEARANCE_MM = 2.0`,
  `EARLY_GROUP_MAX_TOTAL_HEIGHT_MM = 5.0`,
  `MID_GROUP_MAX_TOTAL_HEIGHT_MM = 10.0` (the latter two are now unused
  but retained for backward compat)
- **`src/pipeline/gcode/pause_points.py`** — Full rewrite:
  - `ComponentPauseInfo(instance_id, body_height_mm, pause_z_mm)` — carries
    explicit pause Z from catalog
  - `PausePoint(z, layer_number, label, components)` — one pause
  - `PausePoints(pauses, total_height, layer_height)` — all pauses
  - `pause_z_for_component()` — uses `pause_z_mm` when set, else fallback
  - `compute_pause_points()` — groups components by resolved Z, deduplicates
- **`src/pipeline/gcode/pipeline.py`** — Accepts `component_infos`, passes
  to `compute_pause_points()`, forwards `component_pauses` list to
  postprocessor
- **`src/pipeline/gcode/postprocessor.py`** — `component_z` param replaced
  with `component_pauses: list[tuple[float, str, list[str]]]`;  injects N
  pause G-code blocks (one per group) with per-pause component ID lists
- **`src/web/routes/manufacture/gcode.py`** — Builds `ComponentPauseInfo`
  from placement + catalog, passes to pipeline

---

## 4. Pin Hole Geometry

### Current design (as implemented)

Each pin gets two vertical zones:

1. **Graduated funnel** (FLOOR_MM → CAVITY_START_MM, i.e. z=2.0 → z=3.0):
   5-step staircase widening upward for easy pin insertion.  The bottom
   sits on the ironed floor surface so ink contact is preserved.

2. **Full-height shaft** (CAVITY_START_MM → ceil_start):  shaft-width
   hole through the full cavity so the pinhole is never blocked by
   material.

### What was tried and reverted

During implementation, several approaches to capping pin holes at
`pause_z` were tried and abandoned:

1. **Sub-floor shaft (z=0 → FLOOR_MM)** — Intended to give long pin tips
   room below the floor.  Reverted because it cut through the bottom of
   the enclosure.

2. **Trace contact zone (z=1.6 → 2.0)** — A separate cutout to ensure
   pins contact ink.  Reverted because it broke the ironed surface.

3. **Pin holes capped at pause_z** — Holes only extended to the pause
   height instead of full cavity.  Reverted because the slicer filled the
   remaining space with solid filament, blocking the holes.

4. **Pin bridges capped at pause_z** — Bridges (connecting channels
   between adjacent pins) were shortened.  Reverted because the 0.2 mm
   gap between body pocket and pin shaft was too thin — the slicer filled
   it in, creating a small wall.

### Final decision

Pin holes and bridges both extend **full cavity height**.  The multi-stage
pause system is the primary mechanism for easier insertion — the component
goes in when walls are at an appropriate height.  Capping geometry was
counterproductive in every variant tested.

### Constants

```python
PINHOLE_CLEARANCE    = 1.0   # mm added to pin diameter for shaft width
PINHOLE_TAPER_EXTRA  = 1.0   # mm extra width at funnel mouth
PINHOLE_TAPER_STEPS  = 5     # number of graduated funnel steps
PIN_BRIDGE_WIDTH     = 1.6   # mm width of connecting channels
```

### ATmega pin geometry

- 28 pins at ±4.6 mm from center (two rows of 14, 2.54 mm pitch)
- Pin shape: rect 0.6 × 0.3 mm
- Shaft: 1.6 × 1.3 mm (after clearance)
- Body pocket: 8.7 × 35.2 mm (overlaps pin shafts at ±4.35 mm)
- Pin shaft outer edges: ±5.4 mm → total cutout width = **10.8 mm**

---

## 5. Ironed Floor Surface

The ironed floor at z=2.0 mm (FLOOR_MM) is preserved.  Pin funnels start
at FLOOR_MM and do **not** extend below it.  Trace channels cut from z=2.0
down to z=1.6 (TRACE_HEIGHT_MM = 0.4) where they overlap with pin
positions.  Ink pools at the funnel bottom where it meets the trace
channel, ensuring pin-to-ink contact.

---

## 6. Z-Layer Stack Reference

```
z = 0.0    Build plate
z = 2.0    FLOOR_MM — ironed surface, ink deposited here
z = 2.4    Trace channel bottom (FLOOR_MM - TRACE_HEIGHT_MM)
z = 3.0    CAVITY_START_MM — component bodies begin
z = 12.0   Component pause (ATmega + buttons)
z = 16.0   Component pause (battery plates)
z = 17.0   ceil_start (shell_height - CEILING_MM)
z = 19.0   Shell top
```

---

## 7. Print Walk-Through (typical flashlight design)

### Z = 0 → 2.0 mm: Print solid floor
Standard floor layers.  Last layer is ironed.

### Z = 2.0 mm: **Ink pause**
Remove from printer.  Deposit conductive ink traces.  Return to printer.

### Z = 2.0 → 12.0 mm: Print lower/mid cavity walls
- Floor zone (2.0–3.0) has pin funnels and trace channels.
- Above 3.0: cavity walls grow around component pockets.
- Pin holes and body pockets are open from the start.

### Z = 12.0 mm: **Component pause** (ATmega + buttons)
- Walls are 12 mm tall.
- Insert ATmega: 28 pins drop into funnels.  Body top at 9.1 mm,
  well below the 12 mm walls.
- Insert button switches: button+cap top at ~11.3 mm, just below
  the 12 mm walls.

### Z = 12.0 → 16.0 mm: Print upper cavity walls
- Material grows around the seated ATmega and buttons.
- Battery plate channels remain open (full cavity height).

### Z = 16.0 mm: **Component pause** (battery plates)
- Slide the three metal contact plates into their channels from above.
- Channels are 13.1–13.5 mm deep, fully open at this point.

### Z = 16.0 → 19.0 mm: Print ceiling
- Solid ceiling layers close over everything.
- Ceiling holes remain open for button caps.

---

## 8. Files Modified (complete list)

| File | Change |
|------|--------|
| `catalog/atmega328p_dip28.json` | `body.width_mm` 7.2→8.7, added `installed_height_mm: 6.1`, `pause_z_mm: 12.0` |
| `catalog/tactile_button_6x6.json` | Added `pause_z_mm: 12.0` |
| `catalog/battery_holder_2xAAA.json` | Added `pause_z_mm: 16.0` |
| `src/catalog/models.py` | Added `installed_height_mm` and `pause_z_mm` to `Mounting`; added `protrusion_height_mm` property to `Component` |
| `src/catalog/loader.py` | Parse `installed_height_mm` and `pause_z_mm` |
| `src/catalog/serialization.py` | Serialize `installed_height_mm` and `pause_z_mm` |
| `src/pipeline/config.py` | Added `PAUSE_NOZZLE_CLEARANCE_MM`, `EARLY_GROUP_MAX_TOTAL_HEIGHT_MM`, `MID_GROUP_MAX_TOTAL_HEIGHT_MM` |
| `src/pipeline/gcode/pause_points.py` | Full rewrite — explicit `pause_z_mm` grouping, N pauses |
| `src/pipeline/gcode/pipeline.py` | Accepts `component_infos`, N-pause support |
| `src/pipeline/gcode/postprocessor.py` | N-pause injection loop |
| `src/pipeline/scad/resolver.py` | Pin holes/bridges full height, `ResolverContext.pause_z` field (currently unused) |
| `src/pipeline/scad/generator.py` | Builds `ComponentPauseInfo` with `protrusion_height_mm` and `pause_z_mm` |
| `src/web/routes/manufacture/gcode.py` | Builds `ComponentPauseInfo` from placement + catalog |

### 4. Shortened pin extensions may leave small voids

If the pin extension is cut short at z = 7.4 mm instead of z = 17 mm, the cavity above z = 7.4 mm around the pin locations fills in with solid material. This is desired — it eliminates the grooves. But if the infill doesn't fully close over the shortened channel, there could be a small void. At 0.2 mm layer height the first layer above the channel will bridge across the ~1.6 mm wide channel — well within normal bridging distance.

**Mitigation:** No special handling needed. The pin channel is narrow enough for a single-width bridge.

### 5. Pin tips extending below the trace layer

With measured pin lengths (ATmega 5.6 mm, button 4.2 mm), the pin tips extend well below the trace surface at z=2.0 into the solid floor. The pinhole now includes a **sub-floor blind shaft** from z=0 up to z=2.0 — every pin gets this regardless of length. The shaft is the bare shaft width (no funnel taper) which keeps the pin snug at the trace contact level (z=1.6→2.0) where the ink was deposited.

The sub-floor shaft is a narrow blind hole in solid PLA — it doesn't exit the enclosure bottom. The pin tip rests inside it. The trace contact happens at z=1.6→2.0 where the trace channel merges with the pinhole and ink pools around the pin wire.

**Mitigation:** The shaft from z=0→2.0 is a single `ScadFragment` emitted by `_pinhole_fragments()`. No per-pin depth computation needed — z=0 accommodates any pin length up to the full floor thickness. The trace channel overlap guarantees ink is present at the shaft walls where the pin passes through.

### 6. Backward compatibility

Designs with only internal-mount components and no battery holder might not need the late or mid pause at all. The system should detect this and omit any pause group that has no components. Similarly, if there are no Group-1 components, skip the early pause.

---

## Summary of changes

| What | Current | Proposed |
|---|---|---|
| Component insertion pauses | 1 (at ceil_start) | 3: early (~7.4 mm) for MC/resistors, mid (~12.8 mm) for buttons, late (ceil_start) for battery/LED |
| ATmega pin extension height | floor → ceil_start (14 mm grooves) | floor → pause_z (~4.4 mm channels) |
| Pin bridge height | Full cavity depth | Capped at pause Z for the component |
| `PausePoints` dataclass | 2 fixed fields | List of `PausePoint` with component assignments |
| `_pinhole_fragments()` | `z_top = _component_z_top()` | `z_top = pin_extension_top` (body height or pause Z based) |
| G-code pause injection | 2 hardcoded pauses | N pauses from `PausePoints.pauses` list |
| Catalog schema | `body.height_mm` only | Add `total_height_mm` and `pin_vertical_mm` from measurements |
| Pin hole depth | Stops at z=2.0 (FLOOR_MM) | Shaft from z=0 to z=2.0 (sub-floor + trace contact), funnel z=2.0→3.0, extension above |
| Trace-to-pin contact | Incidental (pin passes through ink level) | Deliberate: shaft-width (snug) zone at z=1.6→2.0 where ink pools from trace channels |

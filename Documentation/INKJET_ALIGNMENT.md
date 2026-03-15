# Inkjet–PLA Alignment: Working Configuration

This document records the calibrated constants and coordinate transforms that produce correct ink-on-plastic alignment between **manufacturerAI** (bitmap generation) and **silver3dprinter** (inkjet execution).

All values below are verified working as of 2026-03-14.

---

## Hardware Constants

| Parameter | Value | Unit | Source |
|---|---|---|---|
| Nozzle count | 128 | nozzles | Xaar 128 datasheet |
| Nozzle pitch | 0.1371 | mm (137.1 µm) | Xaar 128 datasheet |
| DPI | 185.27 | dots/inch | 25.4 / 0.1371 |
| Pixel size | 0.1371 × 0.1371 | mm (square) | = nozzle pitch |
| px/mm | 7.294 | — | 1 / 0.1371 |
| Printhead width | 17.5488 | mm | 128 × 0.1371 |
| Lane step | 32 | nozzles | Chosen for 4x overlap |
| Lane width | 4.3872 | mm | 32 × 0.1371 |
| Overlap | 96 | nozzles (13.16 mm) | 128 − 32 |

## Printer Definitions

All printers share the same inkjet mount, so the mechanical offsets and calibration corrections are identical. The bed depth differs for CoreOne.

| Parameter | MK3S / MK3S+ | CoreOne | Unit |
|---|---|---|---|
| Nominal bed width | 250.0 | 250.0 | mm |
| Nominal bed depth | 210.0 | 250.0 | mm |
| Inkjet offset X | −57.6 | −57.6 | mm |
| Inkjet offset Y | −32.0 | −32.0 | mm |
| Calibration offset X | −1.8 | −1.8 | mm |
| Calibration offset Y | +2.7 | +2.7 | mm |
| Effective inkjet width | 192.4 | 192.4 | mm |
| Effective inkjet depth | 178.0 | 218.0 | mm |

The inkjet nozzle array is mounted **57.6 mm to the left** and **32 mm to the front** of the PLA nozzle on the same carriage. These offsets were determined empirically by iterating calibration prints until the ink squares landed on top of the PLA squares.

### Why −57.6 and not ~30?

The initial estimate was "about 30 mm". The measured value is 57.6 mm. This is the distance from the PLA nozzle to the center of the Xaar 128 nozzle array. The `inkjet_offset_x` field uses the sign convention: **negative = inkjet is to the left of PLA** (in the −X direction).

The value 57.6 also happens to equal `X_START` in sweep_generator.py. This is not a coincidence — `X_START` was originally set so that the inkjet's leftmost nozzle could reach the left edge of the bed (nozzle 0 at bed X = 0 when PLA is at X = 57.6).

## Calibration Offsets

After the geometric offset is applied, a small residual error remains from mechanical tolerances, firmware timing delays, and mounting imprecision. These are stored as `calibration_offset_x` / `calibration_offset_y` on each `PrinterDef`:

| Parameter | Value | Unit | Effect |
|---|---|---|---|
| calibration_offset_x | −1.8 | mm | Shifts ink 1.8 mm in −X |
| calibration_offset_y | +2.7 | mm | Shifts ink 2.7 mm in +Y |

These were measured by printing the calibration pattern (3-corner alignment squares with the top-right corner omitted for orientation) and measuring the misalignment between PLA squares and ink squares.

## Sweep Grid

The sweep grid is defined in **PLA nozzle coordinates** (= Marlin G-code coordinates). `G1 X57.6` means the PLA nozzle (and therefore the carriage) is at X = 57.6.

| Parameter | Value | Unit |
|---|---|---|
| X_START | 57.6 | mm |
| X_END | 250.0 | mm |
| X_INCREMENT | 4.3872 | mm (= 32 × 0.1371) |
| Y_START | 32.0 | mm |
| Y_END | 210.0 / 250.0 | mm (MK3S / CoreOne) |
| SLOW_FEED | 2000 | mm/min |
| FAST_FEED | 3000 | mm/min |
| Z_HEIGHT | 1.0 | mm (relative lift) |
| Total sweep lanes | 44 | — |

### Lane numbering ↔ bed position

At lane *i*, the PLA nozzle is at `X = 57.6 + i × 4.3872`. The inkjet nozzle 0 is at bed `X = 57.6 + i × 4.3872 + (−57.6) = i × 4.3872`. The full 128-nozzle array spans `[i × 4.3872, i × 4.3872 + 17.5488]` mm on the bed.

## Sliding-Window Overlap (4x Coverage)

The 128-nozzle head is logically divided into 4 groups of 32 nozzles. Between consecutive sweep lanes, the head advances by 32 nozzles (one group width), creating a 96-nozzle overlap. `rasp_main.py` implements this as a sliding-window over 32-pixel-wide strips:

1. The bitmap (width = N × 32) is chopped into N strips of 32 columns each.
2. 3 blank strips are prepended and 3 appended → total strips = N + 6.
3. `combine_slices()` creates combined slices using a window of 4 consecutive strips.
4. Combined slice *i* = strips {*i*, *i+1*, *i+2*, *i+3*} concatenated → 128 columns.
5. Total combined slices = (N + 6) − 3 = N + 3 = sweep lanes.

Each data strip appears in **exactly 4 combined slices**, fired through 4 different physical nozzle groups. This gives every pixel 4x physical coverage, averaging out individual nozzle variation.

```
Combined slice 0: [blank₀][blank₁][blank₂][D₀]         D₀ at nozzles 96–127
Combined slice 1: [blank₁][blank₂][D₀][D₁]             D₀ at nozzles 64–95
Combined slice 2: [blank₂][D₀][D₁][D₂]                 D₀ at nozzles 32–63
Combined slice 3: [D₀][D₁][D₂][D₃]                     D₀ at nozzles 0–31
Combined slice 4: [D₁][D₂][D₃][D₄]                     D₀ is gone
```

The 3 prepended blank strips ensure the first data strip gets all 4 passes. The 3 appended blank strips ensure the last data strip gets all 4 passes. No data pixels have partial coverage.

## Bitmap Dimensions

These values are computed per-printer by `sweep_grid()` in `config.py`, which returns a `SweepGrid` dataclass. There are no module-level bitmap constants — everything is derived from the `PrinterDef` and `PrintheadConfig`.

| Parameter | MK3S / MK3S+ | CoreOne | Derivation |
|---|---|---|---|
| Sweep lanes | 44 | 44 | `1 + floor((250 − 57.6) / 4.3872)` |
| Padding strips | 3 | 3 | Blank strips prepended by rasp_main.py |
| `data_cols` | 1312 | 1312 | (44 − 3) × 32 = 41 × 32 |
| `data_rows` | 1299 | 1591 | ⌈(210 − 32) / 0.1371⌉ or ⌈(250 − 32) / 0.1371⌉ |
| `_data_x_start_mm` | 70.7616 | 70.7616 | 57.6 + 3 × 32 × 0.1371 |

### Why 44 − 3 and not 44 − 6?

Only the **leading** 3 padding strips consume extra sweep lanes. The trailing 3 are consumed by the sliding window ending — the last combined slice uses the last 3 blank strips plus the last data strip. The total combined slices = data strips + 3 = sweep lanes, so `data_strips = sweep_lanes − 3`.

### Physical coverage (MK3S example)

- Bitmap column 0 maps to bed X = 70.7616 mm (data position after the 3 blank-strip offset).
- Bitmap column 1311 maps to bed X = 70.7616 + 1311 × 0.1371 = 250.64 mm.
- Bitmap row 0 maps to bed Y = 32.0 mm.
- Bitmap row 1298 maps to bed Y = 32.0 + 1298 × 0.1371 = 209.97 mm.

CoreOne extends to row 1590 → bed Y = 32.0 + 1590 × 0.1371 = 249.99 mm.

Note: bitmap columns extend slightly past X_END = 250 mm. This is correct — the last few columns fall in the last sweep lane's nozzle span and are trimmed naturally by the physical bed edge.

## Coordinate Transform (`SweepGrid.bed_to_bitmap()`)

Traces start in **model-local coordinates** (origin at model bounding-box lower-left). The pipeline passes a `model_to_bed` tuple `(dx, dy)` that translates model coords to absolute bed coords. The full transform to bitmap pixel indices is:

```
bed_x = trace_x + model_to_bed_x
bed_y = trace_y + model_to_bed_y

# SweepGrid.bed_to_bitmap():
bitmap_x = bed_x − _data_x_start_mm − inkjet_offset_x + calibration_offset_x
bitmap_y = bed_y − _y_start_mm       − inkjet_offset_y + calibration_offset_y

bitmap_col = floor(bitmap_x / pixel_size)
bitmap_row = floor(bitmap_y / pixel_size)
```

With the working values:

```
bitmap_x = bed_x − 70.7616 − (−57.6) + (−1.8)  =  bed_x − 14.9616
bitmap_y = bed_y − 32.0 − (−32.0) + 2.7         =  bed_y + 2.7
```

The coordinate transform is centralised in `SweepGrid.bed_to_bitmap()` (in `config.py`) and used by both `bitmap.py` (trace rendering) and `debug.py` (calibration squares).

### Why subtracting a negative offset adds to the position

The inkjet is at `PLA_pos + offset`. To convert from bed coordinates (PLA frame) to inkjet nozzle coordinates, we need to *undo* the offset: `inkjet_pos = bed_pos − offset`. Since `offset = −57.6`, this becomes `bed_pos − (−57.6) = bed_pos + 57.6`, which shifts the bitmap data leftward by 57.6 mm — compensating for the physical head position.

## Timing (silver3dprinter only)

These values are **not in manufacturerAI** — they belong exclusively to silver3dprinter.

| Parameter | Value | Unit | Derivation |
|---|---|---|---|
| Sweep speed | 2000 | mm/min | Empirically tuned |
| Sweep speed | 33.33 | mm/s | 2000 / 60 |
| Row fire rate | 243.1 | Hz | 33.33 / 0.1371 |
| Time per row | 4.113 | ms | 1 / 243.1 |
| Serial budget | 1.476 | ms | 1 / (115200 / 170) |
| Feasible | Yes | — | 243 Hz < min(5500, 678) Hz |

The row fire rate defines how frequently the Arduino fires the printhead as the carriage sweeps in Y. For square pixels, the Y pixel pitch must equal the nozzle pitch (0.1371 mm), so the fire rate is fully determined by the sweep speed.

The speed was reduced from 2180 to **2000 mm/min** during calibration for more reliable ink deposition.

## Separation of Concerns

### manufacturerAI knows:
- Nozzle geometry (pitch, count, lane step)
- Inkjet offset (per PrinterDef)
- Calibration offsets (per `PrinterDef`)
- Sweep grid extents (computed by `sweep_grid()` → `SweepGrid`)
- How to produce a correctly-positioned bitmap

### manufacturerAI does NOT know:
- Sweep speed, fire rate, time_per_row
- Serial protocol, baud rate, packet format
- How rasp_main.py executes the sweep

### silver3dprinter knows:
- All of the above
- How to slice the bitmap into 32-col strips
- How to combine strips into 128-col combined slices
- How to time the serial row transmission
- How to fire the printhead synchronously with carriage motion

### The contract between them:
1. A **text bitmap** (`trace_bitmap.txt`) with width divisible by 32 and the convention: `1` = ink, `0` = no ink, one character per nozzle position, one line per sweep-direction pixel.
2. The **`;silverink`** marker in the G-code, triggering the ink deposition pause.
3. The sweep gcode matches the bitmap dimensions: `combined_slices = bitmap_cols/32 + 3 = sweep_lanes`.

## Architecture (current)

### config.py

- `PrintheadConfig` — geometry only (nozzle count, pitch, lane step + derived properties)
- `PrinterDef` — per-printer static definition including `inkjet_offset_x/y` and `calibration_offset_x/y`
- `SweepGrid` — frozen dataclass with 3 public fields (`data_cols`, `data_rows`, `pixel_size_mm`) and a `bed_to_bitmap()` method that encapsulates the full coordinate transform
- `sweep_grid(pdef, printhead)` — derives a `SweepGrid` from a `PrinterDef` + `PrintheadConfig`

### bitmap.py

- `generate_trace_bitmap(result, trace_width_mm, *, grid, model_to_bed)` — renders routed traces using the `SweepGrid` coordinate transform
- Takes a `model_to_bed` tuple instead of separate origin/part_origin params

### debug.py

- `_calibration_bitmap(pdef, grid, box, pad, sq)` — generates calibration squares using the same `SweepGrid.bed_to_bitmap()` as the real pipeline

### manufacture.py

- Computes `grid = sweep_grid(pdef)` and `model_to_bed = (part_origin_x, part_origin_y)`, passes both to `write_trace_bitmap()`

### manifest.py

- `generate_manifest(*, grid, ...)` — takes `SweepGrid` for bitmap dimensions; optional convenience file

## Change History

### Initial alignment (commits 876cdb9..de55342)

- `nozzle_pitch_mm`: 0.13625 → **0.1371**
- Removed all timing/serial properties from `PrintheadConfig`
- `inkjet_offset_x`: 31.0 → **−57.6**, `inkjet_offset_y`: 32.0 → **−32.0**
- Added `BitmapCalibration` with offset_x=−1.8, offset_y=+2.7
- Added module-level sweep grid constants and bitmap dimension constants

### Refactor (2026-03-15)

- Removed `BitmapConfig`, `BitmapCalibration`, and all module-level sweep/bitmap constants
- Moved calibration offsets into `PrinterDef.calibration_offset_x/y` (per-printer)
- Introduced `SweepGrid` dataclass and `sweep_grid()` function to derive dimensions per-printer
- Centralised coordinate transform in `SweepGrid.bed_to_bitmap()`
- Replaced vestigial bitmap.py parameters with clean `grid` + `model_to_bed` API
- Added CoreOne printer definition (250×250 bed → 1591 bitmap rows)
- Fixed CoreOne Y_END bug (was hardcoded to 210, now derived from `nominal_bed_depth`)
- Replaced fragile float-stepping lane count with integer arithmetic
- Added clipping warning when traces fall outside the sweep grid
- Added 30 unit tests in `tests/test_sweep_bitmap.py` covering SweepGrid, bitmap generation, and calibration bitmap regression

### silver3dprinter (uncommitted + gcode regenerated)

- SLOW_FEED: F2100 → **F2000** (44 sweep lines)
- `X_INCREMENT = 4.3872`, `NOZZLE_PITCH_MM = 0.1371`, `SWEEP_SPEED_MM_MIN = 2000`
- `TIME_PER_ROW_S` derived: `0.1371 / (2000/60) = 0.004113 s`

## Calibration Procedure

1. Run the `/debug/calibrate` endpoint with the target printer ID.
2. This generates: G-code with 3 PLA alignment squares (top-right omitted), a bitmap with 3 ink squares at the same bed positions, and a manifest.
3. Print the G-code. At the `;silverink` pause, the inkjet deposits ink.
4. Measure the offset between PLA squares and ink squares.
5. Adjust `calibration_offset_x/y` on the `PrinterDef` in `config.py` by the measured error.
6. Repeat until ink lands on PLA.
7. If the offset is large (> ~5 mm), adjust `inkjet_offset_x/y` in `PrinterDef` instead.

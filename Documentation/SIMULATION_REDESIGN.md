# Full Simulation Redesign Plan

## Goal

Replace the current fake client-side simulation with actual firmware execution via **simavr** (cycle-accurate ATmega328P emulator). The frontend `DeviceSimulator` currently toggles all outputs ON when any button is pressed — this must be replaced with real pin-state updates from the running firmware binary. All legacy and fallback code that supported the old template-based firmware generation must be removed.

---

## Current Architecture

### Data Flow (Setup Stage)

```
POST /sessions/{sid}/setup
  → Load design.json, circuit.json, routing.json
  → Build firmware context (context_builder.py)
  → SetupAgent writes .ino via LLM
  → validate_firmware() — static pin checks
  → arduino-cli compile → .hex + .elf
  → generate_sim_config() → sim_config.json
  → SSE event: sim_config { ready: true }

Frontend:
  SetupPanel.tsx
    → useSetupAgent() subscribes to SSE
    → On done: getSimConfig() + getPlacementResult()
    → Renders DeviceSimulator(placement, simConfig)
    → DeviceSimulator: FAKE button→output toggle logic
```

### What Exists Today

| Component | File | Status |
|-----------|------|--------|
| LLM firmware generation | `src/agent/core.py` (SetupAgent) | ✅ Working |
| Static validation | `src/pipeline/firmware/validate_firmware.py` | ✅ Working |
| Compilation | `src/pipeline/firmware/arduino_cli.py` | ✅ Working |
| sim_config generation | `src/pipeline/firmware/sim_config.py` | ✅ Working |
| sim_config API endpoint | `src/web/routes/setup.py` GET /sim-config | ✅ Working |
| Frontend 3D viewport | `DeviceSimulator.tsx` | ⚠️ Fake logic |
| simavr C harness | — | ❌ Missing |
| Simulation manager | — | ❌ Missing |
| WebSocket bridge | — | ❌ Missing |
| Real pin state updates | — | ❌ Missing |

---

## Legacy Code to Remove

### 1. `firmware_generator.py` — Legacy functions and data

**File:** `src/pipeline/firmware/firmware_generator.py`

The following are dead code from the old template-based firmware generation. None are called from any live code path (only from `__main__` CLI and documentation/README):

| What | Lines | Reason |
|------|-------|--------|
| `TEMPLATE_INO` constant (line 19) | `FIRMWARE_DIR / "UniversalIRRemote.ino"` | Old template reference |
| `FUNCTION_TO_FIRMWARE` dict (lines 43-53) | Button label → firmware variable mapping | Only used by legacy `generate_firmware()` |
| `BUTTON_LABELS` dict (lines 56-79) | Fallback label mapping | Only used by `normalize_button_label()` |
| `normalize_button_label()` (lines 95-98) | Label lookup | Only used by `generate_firmware()` |
| `generate_firmware()` (lines 101-205) | Template substitution engine | Replaced by LLM agent |
| `_build_pin_definitions()` (lines 208-259) | #define block builder | Used only by `generate_firmware()` |
| `_replace_pin_definitions()` (lines 262-278) | Template regex replacer | Used only by `generate_firmware()` |
| `build_pin_mapping()` (lines 302-396) | Routing→legacy pin_mapping converter | Used only by `generate_firmware()` |
| `generate_pin_assignment_report()` (lines 398-444) | Human-readable pin report | Not called from live code |
| `__main__` CLI block (lines 447-466) | Standalone testing | Legacy CLI |

**Keep in this file:** `ATMEGA_TO_ARDUINO`, `ARDUINO_TO_PHYSICAL`, `PWM_PINS`, `atmega_port_to_arduino_pin()`, `arduino_pin_to_physical()`, `is_pwm_pin()` — these are imported and used by `validate_firmware.py`, `context_builder.py`, and `sim_config.py`.

**Consider renaming** `firmware_generator.py` → `pin_mappings.py` since it would only contain pin mapping constants and utility functions after cleanup.

### 2. `__init__.py` — Update exports

**File:** `src/pipeline/firmware/__init__.py`

Remove exports: `build_pin_mapping`, `generate_firmware`, `generate_pin_assignment_report`, `TEMPLATE_INO`.

### 3. `UniversalIRRemote.ino` — Delete template file

**File:** `src/pipeline/firmware/UniversalIRRemote.ino`

This template is no longer used. The LLM agent generates firmware from scratch.

### 4. `README.md` and `STANDALONE_GUIDE.md` — Legacy docs

**Files:**
- `src/pipeline/firmware/README.md` — References old template workflow
- `src/pipeline/firmware/STANDALONE_GUIDE.md` — References UniversalIRRemote.ino

These should be updated or removed since they describe a workflow that no longer exists.

### 5. `DeviceSimulator.tsx` — Fake simulation logic

**File:** `manufacturerAI-Frontend/src/components/viewport/DeviceSimulator.tsx`

**Remove (lines 297-321):** The `simulateButtonPress` callback that does:
```typescript
const anyPressed = simMapping.current.buttons.some(...)
for (const out of simMapping.current.outputs) {
    next[out.instance_id] = { ...next[out.instance_id], on: anyPressed }
}
```
This fake logic will be replaced with WebSocket-driven state updates from simavr.

---

## New Components to Build

### 1. simavr C Harness (`sim_harness.c`)

**Location:** `src/pipeline/firmware/harness/sim_harness.c`

A ~300-line C program that:
- Loads the compiled `.elf` binary into simavr
- Reads `sim_config.json` to attach virtual peripherals at the correct ports/pins
- Communicates via **stdin/stdout JSON** line protocol:
  - **Inbound (stdin):** `{"cmd":"press","instance_id":"btn_power"}`, `{"cmd":"release","instance_id":"btn_power"}`
  - **Outbound (stdout):** `{"event":"pin_change","port":"C","pin":5,"high":true}`, `{"event":"boot_ok"}`
- Runs the AVR simulation loop in a separate thread
- Attaches IRQ callbacks on output pins to detect LED/IR state changes
- Maps sim_config peripheral definitions to simavr port/pin IRQs

**Dependencies:** `simavr` library (C), `libelf`. Must be compiled to a standalone binary.

**Build:** Provide a `Makefile` or `CMakeLists.txt` in the harness directory. The binary should be compiled once and reused across sessions.

### 2. Simulation Manager (`simulation.py`)

**Location:** `src/pipeline/firmware/simulation.py`

A Python class that:
- Spawns the C harness as a subprocess: `subprocess.Popen(["./sim_harness", session_dir + "/sim_config.json"])`
- Reads stdout for pin state events (JSON lines)
- Writes stdin for button commands
- Tracks current peripheral states
- Provides async methods: `press(instance_id)`, `release(instance_id)`, `get_state()`, `start()`, `stop()`
- Handles harness boot verification: wait for `{"event":"boot_ok"}` within a timeout
- Kills subprocess on cleanup

### 3. WebSocket Endpoint

**Location:** `src/web/routes/setup.py` (add to existing file)

New endpoint: `WS /api/v2/sessions/{sid}/setup/simulate`

Protocol:
```
Client → Server: {"cmd":"press","instance_id":"btn_power"}
Client → Server: {"cmd":"release","instance_id":"btn_power"}
Server → Client: {"event":"pin_change","instance_id":"led_top","on":true}
Server → Client: {"event":"pin_change","instance_id":"ir_led","on":false}
Server → Client: {"event":"boot_ok"}
Server → Client: {"event":"error","message":"..."}
```

The WebSocket handler:
- On connect: starts `SimulationManager` for the session (if not already running)
- Forwards button press/release commands to the harness stdin
- Reads harness stdout events and maps port/pin changes back to instance_ids using sim_config
- Broadcasts pin state changes to the WebSocket client

### 4. Frontend WebSocket Hook (`useSimulation.ts`)

**Location:** `manufacturerAI-Frontend/src/hooks/useSimulation.ts`

A React hook that:
- Opens WebSocket to `ws://localhost:8000/api/v2/sessions/{sid}/setup/simulate`
- Sends press/release commands
- Receives pin state update events
- Maintains `peripheralState` record
- Returns: `{ peripheralState, press(id), release(id), connected, booted }`

### 5. Update `DeviceSimulator.tsx`

Replace the fake `simulateButtonPress` with the WebSocket hook:
- Remove `simMapping` ref and the fake toggle logic
- Use `useSimulation(sessionId, simConfig)` hook
- Button raycasting calls `press(instance_id)` / `release(instance_id)` via WebSocket
- LED glow state driven by `peripheralState` from WebSocket events
- Show connection status (connecting/connected/booted/error) in status bar

### 6. Update `SetupPanel.tsx`

- Pass `sessionId` to `DeviceSimulator` so it can establish the WebSocket connection
- Show simulation status indicator (booted, error, no ELF)

---

## sim_config.json Changes

The current `sim_config.json` structure is already correctly designed for the harness. No schema changes needed:

```json
{
    "mcu": "atmega328p",
    "frequency": 8000000,
    "elf_path": "firmware_build/firmware.ino.elf",
    "peripherals": [
        {"instance_id": "led_top", "type": "led", "port": "C", "pin": 5, "pwm": false},
        {"instance_id": "btn_power", "type": "button", "port": "B", "pin": 1, "active_low": true}
    ]
}
```

The C harness reads this directly and attaches peripherals. No modifications required to `sim_config.py`.

---

## Prerequisite: `.elf` Binary

The simulation requires a successfully compiled `.elf` file. Currently:
- If `arduino-cli` is not installed, the code is saved but not compiled, and `elf_path` is `null`
- If compilation fails, `elf_path` is `null`

**Required change:** The simulator tab / WebSocket connection should only be available when `elf_path` is non-null and the ELF file actually exists. The frontend already conditionally shows the simulator tab — we need to also check that `simConfig.elf_path` is truthy.

---

## Implementation Order

### Phase 1: Clean Up Legacy
1. Remove dead code from `firmware_generator.py` (keep `ATMEGA_TO_ARDUINO`, `ARDUINO_TO_PHYSICAL`, `PWM_PINS` and their helper functions)
2. Consider renaming `firmware_generator.py` → `pin_mappings.py` and updating all imports
3. Update `__init__.py` exports
4. Delete `UniversalIRRemote.ino` template
5. Update or remove `README.md` and `STANDALONE_GUIDE.md`

### Phase 2: C Harness
1. Write `sim_harness.c` with simavr integration
2. Create build system (Makefile)
3. Test standalone: load an ELF, press virtual button, verify LED pin goes high

### Phase 3: Backend Integration
1. Write `simulation.py` — subprocess manager
2. Add WebSocket endpoint to `setup.py`
3. Map port/pin events to instance_ids using sim_config peripherals

### Phase 4: Frontend Integration
1. Write `useSimulation.ts` WebSocket hook
2. Update `DeviceSimulator.tsx` — remove fake logic, wire up WebSocket
3. Update `SetupPanel.tsx` — pass session ID, show simulation status
4. Handle ELF availability (disable simulator when no ELF)

### Phase 5: Verification
1. Boot verification: confirm MCU reaches `loop()` without hanging
2. Scenario smoke test: press button → verify expected LED state
3. Error handling: harness crash, WebSocket disconnect, timeout

---

## Files Modified Summary

| Action | File |
|--------|------|
| **HEAVY EDIT** | `src/pipeline/firmware/firmware_generator.py` — remove all legacy functions, keep pin mapping constants |
| **EDIT** | `src/pipeline/firmware/__init__.py` — update exports |
| **DELETE** | `src/pipeline/firmware/UniversalIRRemote.ino` |
| **DELETE or UPDATE** | `src/pipeline/firmware/README.md` |
| **DELETE or UPDATE** | `src/pipeline/firmware/STANDALONE_GUIDE.md` |
| **ADD** | `src/pipeline/firmware/harness/sim_harness.c` |
| **ADD** | `src/pipeline/firmware/harness/Makefile` |
| **ADD** | `src/pipeline/firmware/simulation.py` |
| **EDIT** | `src/web/routes/setup.py` — add WebSocket endpoint |
| **HEAVY EDIT** | `manufacturerAI-Frontend/src/components/viewport/DeviceSimulator.tsx` — replace fake logic with WebSocket |
| **ADD** | `manufacturerAI-Frontend/src/hooks/useSimulation.ts` |
| **EDIT** | `manufacturerAI-Frontend/src/components/pipeline/SetupPanel.tsx` — pass sessionId, status |
| **EDIT** | `manufacturerAI-Frontend/src/lib/api/setup.ts` — no changes needed (SimConfig types already correct) |

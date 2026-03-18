# MC Code Plan — Verifying Firmware Without Real Hardware

## The Problem

The Setup Agent writes Arduino firmware for an ATmega328P. The code compiles — but does it actually *work*? Right now there is no way to know without flashing it onto a real chip. We need automated verification that runs entirely in software.

---

## Strategy Overview

Three layers, each catching different kinds of bugs:

```
Layer 1:  Static Analysis          (instant, catches dumb mistakes)
Layer 2:  Cycle-Accurate Emulation (simavr — runs the real .elf binary)
Layer 3:  Scenario Test Harness    (scripted button presses → expected outputs)
```

All three run automatically after every successful compile. If any layer fails, the Setup Agent gets the error and can retry.

---

## Layer 1 — Static Analysis (Pre-Compile Checks)

**What it catches:** Wrong pin numbers, missing includes, using pins that don't exist, PWM on a non-PWM pin, conflicting pin assignments.

**How it works:**
1. After the LLM writes the `.ino` file, parse it with regex before compiling
2. Extract all `#define` pin numbers and `pinMode()` calls
3. Cross-check against the pin assignments from `routing.json`:
   - Every routed component pin must appear in the code
   - No pin numbers should appear that aren't in the routing
   - IR send pins must be PWM-capable (3, 5, 6, 9, 10, 11)
   - No two functions should share the same pin
4. Check that required libraries are `#include`d for the components in the circuit (e.g., if there's an IR LED, `IRremote.hpp` must be included)

**Effort:** Small — pure Python string analysis, no external tools needed.

**Feedback loop:** If checks fail, feed the specific error back to the LLM: *"You used pin 3 for IR_SEND_PIN but routing assigns ir_led_1 to Arduino pin 9 (PB1). Fix it."*

---

## Layer 2 — Cycle-Accurate Emulation with simavr

**What it catches:** Runtime crashes, infinite loops, wrong timer configs, interrupts that never fire, code that hangs on startup.

**How it works:**

### 2a. Build the simavr harness (one-time setup)

simavr is a C library that emulates the full ATmega328P instruction set cycle-by-cycle. We write a small C "harness" program that:

- Loads the `.elf` file produced by arduino-cli
- Creates virtual peripherals (buttons, LEDs) based on `sim_config.json`
- Attaches IRQ callbacks to the AVR's GPIO ports to monitor pin state changes
- Runs the emulation for N cycles or T milliseconds of simulated time

The harness is compiled once and reused for every session — only the `.elf` and `sim_config.json` change.

### 2b. Boot test (automatic)

After compile, run the harness for 500ms of simulated time with no button presses:
- **PASS criteria:** MCU reaches `loop()` without crashing, no watchdog reset, no infinite loop in `setup()`
- **FAIL criteria:** CPU stuck on same PC for >10ms, stack overflow, illegal opcode

This alone catches a large class of bugs (wrong clock config, bad interrupt setup, missing initialization).

### 2c. Pin-state logging

The harness logs every GPIO state change with a cycle-accurate timestamp:
```
[  0.000ms] PB0 configured INPUT_PULLUP
[  0.000ms] PD4 configured INPUT_PULLUP  
[  0.000ms] PD2 configured INPUT_PULLUP
[  0.000ms] PB1 configured OUTPUT
[  0.012ms] Setup complete, entering loop
```

This log is machine-parseable and can verify that `setup()` configures the correct pins.

### 2d. Interactive simulation (frontend)

This is what the DeviceSimulator component already does — the user clicks buttons in the 3D view and sees LEDs respond. simavr runs as a subprocess, communicating via stdin/stdout or a WebSocket.

**Effort:** Medium — requires writing ~300 lines of C for the harness, plus a Python subprocess manager. simavr itself is a well-maintained open-source project.

**Dependencies:** `simavr` (install via package manager or build from source), `avr-gcc` (already installed for arduino-cli).

---

## Layer 3 — Scenario Test Harness

**What it catches:** Wrong behavior — code runs but does the wrong thing. Button press doesn't trigger the right IR code. LED doesn't light on the right event. Auto-repeat doesn't work.

**How it works:**

### 3a. Auto-generate test scenarios from the design

The circuit + design description implicitly define expected behaviors. We can generate test cases:

```
Test: "Power button sends IR"
  Given: Device is idle
  When:  BTN_POWER (PB0) is pressed for 100ms
  Then:  IR_LED (PB1) should pulse at 38kHz within 200ms

Test: "Volume up auto-repeats"
  Given: Device is idle
  When:  BTN_VOL_UP (PD4) is held for 1000ms
  Then:  IR_LED (PB1) should pulse at least 2 times

Test: "No output when idle"
  Given: No buttons pressed
  When:  500ms passes
  Then:  IR_LED (PB1) should remain LOW
```

### 3b. Run scenarios against simavr

Each scenario translates to simavr commands:
1. Reset the MCU
2. Wait for setup to complete
3. Drive the button pin LOW at timestamp T1
4. Drive the button pin HIGH at timestamp T2
5. Check that the output pin changed state between T1 and T2+timeout

### 3c. Let the LLM generate scenarios too

The Setup Agent can also be asked to produce test scenarios for its own code. This gives a secondary check — if the agent can't describe what its code should do in testable terms, the code is probably wrong.

**Effort:** Medium-large — the scenario runner needs to interface with simavr's cycle stepping. The scenario *generation* from design.json is a separate LLM call or a rule-based system.

---

## Recommended Implementation Order

### Phase 1 — Quick wins (do first)
1. **Static pin validation** — Parse the generated `.ino`, extract pin defines, cross-check against routing.json. Reject and retry if wrong. This alone would have caught the pin 3 vs pin 9 bug.
2. **Library include check** — Verify required `#include` statements match the components.
3. **Feed errors back to agent** — Wire validation failures into the compile-retry loop so the LLM self-corrects.

### Phase 2 — Real emulation
4. **Build the simavr harness** — C program that loads an ELF, runs for N cycles, logs GPIO changes.
5. **Boot smoke test** — Automatic pass/fail after every compile.
6. **Pin configuration verification** — Parse the GPIO log to confirm `setup()` configures the right pins in the right modes.

### Phase 3 — Behavioral testing
7. **Scenario format** — Define a simple JSON schema for test scenarios.
8. **Scenario runner** — Drive simavr with scripted inputs, assert expected outputs.
9. **Auto-generated scenarios** — Derive basic "press button → see output" tests from the circuit topology.

### Phase 4 — Frontend integration
10. **Live sim in browser** — WebSocket bridge: frontend sends button presses, backend drives simavr, returns pin states in real-time. (This connects to the existing DeviceSimulator component.)

---

## What Each Phase Catches

| Bug Type                         | Phase 1 | Phase 2 | Phase 3 |
|----------------------------------|---------|---------|---------|
| Wrong pin number                 | ✅      | ✅      | ✅      |
| Missing #include                 | ✅      |         |         |
| PWM on non-PWM pin               | ✅      | ✅      |         |
| Code crashes on boot             |         | ✅      |         |
| Infinite loop in setup()         |         | ✅      |         |
| Wrong timer/interrupt config     |         | ✅      | ✅      |
| Button press does nothing        |         |         | ✅      |
| Wrong IR command sent            |         |         | ✅      |
| Auto-repeat doesn't work         |         |         | ✅      |
| Sleep/wake broken                |         | ✅      | ✅      |

---

## Key Principle

**Phase 1 is the highest value per effort.** A simple Python script that cross-checks the generated code against routing.json would have caught today's bug (pin 3 vs pin 9) with zero external dependencies. Start there, then build up to full emulation.

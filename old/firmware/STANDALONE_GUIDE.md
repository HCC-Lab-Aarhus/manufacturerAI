# Standalone ATmega328 IR Remote - Complete Guide

## Overview

This guide covers:
1. Programming the ATmega328 chip using Arduino as ISP
2. Standalone wiring with battery power
3. Configurable pin assignments for custom PCB designs

---

## Part 1: Parts Required

### For Programming
| Component                 | Quantity | Notes                                     |
|---------------------------|----------|-------------------------------------------|
| ATmega328P-PU             | 1        | DIP-28 package (easier to work with)      |
| Arduino Uno / Elegoo R3   | 1        | Used as programmer                        |
| 16 MHz crystal            | 1        | Optional: can use internal 8MHz           |
| 22pF capacitors           | 2        | Only needed with external crystal         |
| 10kΩ resistor             | 1        | For RESET pull-up                         |
| Breadboard                | 1        |                                           |
| Jumper wires              | ~15      |                                           |

### For Final Standalone Circuit
| Component                 | Quantity | Notes                                     |
|---------------------------|----------|-------------------------------------------|
| ATmega328P-PU             | 1        | Pre-programmed                            |
| IR LED (850nm/940nm)      | 1        | TSHG6200 or similar                       |
| Status LED                | 1        | Any color                                 |
| 47-68Ω resistor           | 1        | For IR LED (depends on voltage)           |
| 220-330Ω resistor         | 1        | For status LED                            |
| 10kΩ resistor             | 1        | RESET pull-up                             |
| Push buttons              | 1-9      | As needed                                 |
| Battery holder            | 1        | 3×AAA (4.5V) recommended                  |
| 100nF capacitor           | 1        | Decoupling (optional but recommended)     |

---

## Part 2: ATmega328 Pinout Reference

```
                    ATmega328P-PU (DIP-28)
                    ┌──────────────────────┐
         (RESET)  1 │ PC6          PC5     │ 28  (A5/SCL)
           (RX)   2 │ PD0          PC4     │ 27  (A4/SDA)
           (TX)   3 │ PD1          PC3     │ 26  (A3)
          (D2)    4 │ PD2          PC2     │ 25  (A2)
          (D3~)   5 │ PD3          PC1     │ 24  (A1)
          (D4)    6 │ PD4          PC0     │ 23  (A0)
          (VCC)   7 │ VCC          GND     │ 22
          (GND)   8 │ GND          AREF    │ 21
        (XTAL1)   9 │ PB6          AVCC    │ 20
        (XTAL2)  10 │ PB7          PB5     │ 19  (D13/SCK)
          (D5~)  11 │ PD5          PB4     │ 18  (D12/MISO)
          (D6~)  12 │ PD6          PB3     │ 17  (D11~/MOSI)
          (D7)   13 │ PD7          PB2     │ 16  (D10~)
          (D8)   14 │ PB0          PB1     │ 15  (D9~)
                    └──────────────────────┘

Arduino Pin → ATmega Physical Pin:
  D0  → Pin 2      D7  → Pin 13     A0 → Pin 23
  D1  → Pin 3      D8  → Pin 14     A1 → Pin 24
  D2  → Pin 4      D9  → Pin 15     A2 → Pin 25
  D3  → Pin 5      D10 → Pin 16     A3 → Pin 26
  D4  → Pin 6      D11 → Pin 17     A4 → Pin 27
  D5  → Pin 11     D12 → Pin 18     A5 → Pin 28
  D6  → Pin 12     D13 → Pin 19
```

---

## Part 3: Programming the ATmega328

### Step 1: Set Up Arduino as ISP Programmer

1. Connect your Arduino Uno to the computer
2. Open Arduino IDE
3. Open **File → Examples → 11.ArduinoISP → ArduinoISP**
4. Upload this sketch to the Arduino Uno
5. The Arduino is now a programmer

### Step 2: Wire ATmega328 for Programming

Connect the ATmega328 on breadboard to Arduino:

| ATmega328 Pin | ATmega Physical | Arduino Pin | Purpose       |
|---------------|-----------------|-------------|---------------|
| RESET (PC6)   | 1               | D10         | Reset control |
| VCC           | 7               | 5V          | Power         |
| GND           | 8               | GND         | Ground        |
| AVCC          | 20              | 5V          | Analog power  |
| GND           | 22              | GND         | Ground        |
| MOSI (PB3)    | 17              | D11         | Programming   |
| MISO (PB4)    | 18              | D12         | Programming   |
| SCK (PB5)     | 19              | D13         | Programming   |

**Optional: External 16MHz Crystal**
| Crystal Pin | ATmega Physical | Component      |
|-------------|-----------------|----------------|
| XTAL1       | 9               | Crystal leg 1  |
| XTAL2       | 10              | Crystal leg 2  |
| XTAL1       | 9               | 22pF cap → GND |
| XTAL2       | 10              | 22pF cap → GND |

### Programming Wiring Diagram

```
                        BREADBOARD
    ─────────────────────────────────────────────────────
     Arduino Uno                    ATmega328P
    ┌───────────┐                 ┌─────────────────────┐
    │           │                 │  1  ○─────────────○ 28 │
    │  D10 ─────┼────────────────►│ RESET          PC5 │
    │           │                 │  2               27 │
    │           │     ┌──────────►│ VCC            PC4 │
    │  5V ──────┼─────┤           │  7               26 │
    │           │     │    ┌─────►│ VCC            PC3 │
    │           │     │    │      │ 20               25 │
    │           │     │    │      │                  24 │
    │  GND ─────┼─────┼────┼──┬──►│ GND(8)         PC1 │
    │           │     │    │  └──►│ GND(22)        PC0 │
    │           │     │    │      │                  23 │
    │  D11 ─────┼─────┼────┼─────►│ MOSI(17)           │
    │  D12 ◄────┼─────┼────┼──────│ MISO(18)           │
    │  D13 ─────┼─────┼────┼─────►│ SCK(19)            │
    │           │     │    │      │                    │
    └───────────┘     │    │      └────────────────────┘
                      │    │
                     5V   5V
                   (to 7) (to 20)

    10kΩ resistor from Pin 1 (RESET) to 5V (pull-up)
```

### Breadboard Layout for Programming

```
         BREADBOARD (programming setup)
    ─────────────────────────────────────────
    Row  a   b   c   d   e │ f   g   h   i   j
    ─────────────────────────────────────────
     1                     │ [10kΩ to 5V rail]
     2   [ATmega328 Pin 1 ─┼─ RESET ─────────→ Arduino D10
     3    ATmega328        │
     4    ...              │
     5    Pin 7 VCC ───────┼─────────────────→ 5V rail
     6    Pin 8 GND ───────┼─────────────────→ GND rail
     ...                   │  
    15    Pin 17 MOSI ─────┼─────────────────→ Arduino D11
    16    Pin 18 MISO ─────┼─────────────────→ Arduino D12
    17    Pin 19 SCK ──────┼─────────────────→ Arduino D13
    18    Pin 20 AVCC ─────┼─────────────────→ 5V rail
    19    Pin 22 GND ──────┼─────────────────→ GND rail
    ─────────────────────────────────────────
```

### Step 3: Burn Bootloader (First Time Only)

1. In Arduino IDE:
   - **Tools → Board → Arduino Uno** (or "ATmega328 on breadboard")
   - **Tools → Programmer → Arduino as ISP**
2. Click **Tools → Burn Bootloader**
3. Wait ~1-2 minutes for completion

### Step 4: Upload the IR Remote Code

1. Open **UniversalIRRemote.ino**
2. **Tools → Board → Arduino Uno**
3. **Tools → Programmer → Arduino as ISP**
4. Click **Sketch → Upload Using Programmer** (NOT regular Upload!)
5. Wait for "Done uploading"

---

## Part 4: Standalone Circuit (Battery Powered)

### Wiring Diagram

```
                    STANDALONE ATmega328 CIRCUIT
    
    Battery 4.5V (3×AAA)
         (+)────────┬──────────────────────────────────────┐
                    │                                      │
                    │  ┌─[100nF]─┐                         │
                    │  │         │                         │
                    └──┼────┬────┼─────────────────────────┤
                       │    │    │                         │
                      VCC  VCC  AVCC                       │
                     Pin 7    Pin 20                       │
                       │    │    │                         │
              ┌────────┴────┴────┴────────┐                │
              │        ATmega328          │                │
              │                           │                │
     [10kΩ]───│ Pin 1 (RESET)             │                │
        │     │                           │                │
        └─────┼───────────────────────────┼────────────────┘
              │                           │               (+)
              │ Pin 5 (D3) ───[47-68Ω]────┼───► IR LED ────┘
              │                           │       │
              │ Pin 19 (D13)──[220Ω]──────┼───► Status LED
              │                           │       │
              │ Pin 4 (D2) ───[Button]────┼───────┤
              │ Pin 6 (D4) ───[Button]────┼───────┤ (Power, Vol+, etc.)
              │ Pin 11 (D5)───[Button]────┼───────┤
              │    ...                    │       │
              │                           │       │
              │ Pin 8 (GND)───────────────┼───────┴─────────┐
              │ Pin 22 (GND)──────────────┼─────────────────┤
              └───────────────────────────┘                 │
                                                            │
    Battery (-)─────────────────────────────────────────────┘
```

### Standalone Breadboard Layout

```
         STANDALONE CIRCUIT (battery-powered)
    ──────────────────────────────────────────────────────
    Row  a   b   c   d   e │ f   g   h   i   j    RAIL
    ──────────────────────────────────────────────────────
         + (4.5V from battery) ───────────────────► (+)
         - (GND from battery) ────────────────────► (-)
    ──────────────────────────────────────────────────────
     1   [10kΩ resistor to + rail]                 (+)
     2   │                 │                        │
     3   ATmega Pin 1 ─────┴─ (RESET)              │
     4   ATmega Pin 2 (D0)                         │
     5   ATmega Pin 3 (D1)                         │
     6   ATmega Pin 4 (D2) ────── POWER BUTTON ────► (-)
     7   ATmega Pin 5 (D3) ─[68Ω]──IR LED──────────► (-)
     8   ATmega Pin 6 (D4) ────── VOL+ BUTTON ─────► (-)
     9   ATmega Pin 7 (VCC) ───────────────────────► (+)
    10   ATmega Pin 8 (GND) ───────────────────────► (-)
    11   ATmega Pin 9 (XTAL1) - leave empty
    12   ATmega Pin 10 (XTAL2) - leave empty
    13   ATmega Pin 11 (D5) ────── VOL- BUTTON ────► (-)
    14   ATmega Pin 12 (D6) ────── CH1 BUTTON ─────► (-)
        ...
    17   ATmega Pin 19 (D13) ─[220Ω]─Status LED────► (-)
    18   ATmega Pin 20 (AVCC) ─────────────────────► (+)
    19   /* 100nF capacitor between pin 20 and GND */
    20   ATmega Pin 22 (GND) ──────────────────────► (-)
    ──────────────────────────────────────────────────────
```

---

## Part 5: Pin Assignment Table

Current default pin assignments:

| Function     | Arduino Pin | ATmega Physical Pin | ATmega Port |
|--------------|-------------|---------------------|-------------|
| IR LED       | D3          | Pin 5               | PD3         |
| Power Button | D2          | Pin 4               | PD2         |
| Volume Up    | D4          | Pin 6               | PD4         |
| Volume Down  | D5          | Pin 11              | PD5         |
| Channel 1    | D6          | Pin 12              | PD6         |
| Channel 2    | D7          | Pin 13              | PD7         |
| Channel 3    | D8          | Pin 14              | PB0         |
| Channel 4    | D9          | Pin 15              | PB1         |
| Channel 5    | D10         | Pin 16              | PB2         |
| Brand Select | D12         | Pin 18              | PB4         |
| Status LED   | D13         | Pin 19              | PB5         |

### To Change Pin Assignments

Edit these lines in `UniversalIRRemote.ino`:

```cpp
// ============== PIN DEFINITIONS ==============
#define IR_SEND_PIN     3   // MUST be PWM pin (3, 5, 6, 9, 10, 11)
#define POWER_BTN       2
#define VOL_UP_BTN      4
#define VOL_DOWN_BTN    5
#define CH1_BTN         6
#define CH2_BTN         7
#define CH3_BTN         8
#define CH4_BTN         9
#define CH5_BTN         10
#define BRAND_BTN       12
#define STATUS_LED      13
```

**Important constraints:**
- `IR_SEND_PIN` **must** be a PWM-capable pin: **3, 5, 6, 9, 10, or 11**
- Other pins can be any digital pin (D2-D13, A0-A5)
- Avoid pins 0, 1 if you want serial debugging

---

## Part 6: Resistor Values by Battery Voltage

| Battery Config | Voltage | IR LED Resistor | Status LED Resistor |
|----------------|---------|-----------------|---------------------|
| 3×AAA          | 4.5V    | 56-68Ω          | 150-220Ω            |
| 3×AA           | 4.5V    | 56-68Ω          | 150-220Ω            |
| 4×AAA          | 6.0V    | 82-100Ω         | 220-330Ω            |
| 2×AAA          | 3.0V    | 22-33Ω          | 68-100Ω             |
| 1×LiPo         | 3.7V    | 33-47Ω          | 100-150Ω            |

---

## Part 7: Using Internal 8MHz Clock (No Crystal)

To avoid needing an external crystal:

1. **Before programming**, burn a different bootloader:
   - **Tools → Board → ATmega328 on a breadboard (8 MHz internal clock)**
   - **Tools → Burn Bootloader**

2. Then upload your sketch normally

**Advantages:** Fewer components, simpler circuit
**Disadvantages:** Slightly less accurate timing (usually fine for IR)

---

## Troubleshooting

| Problem                          | Solution                                              |
|----------------------------------|-------------------------------------------------------|
| "Programmer not responding"      | Check all 6 programming wires, RESET pull-up          |
| "Invalid device signature"       | Wrong chip orientation, check VCC/GND connections     |
| Chip runs but no IR output       | Check IR_SEND_PIN is PWM capable (3,5,6,9,10,11)      |
| Works on Arduino, not standalone | Check RESET pull-up resistor, both VCC and AVCC      |
| Short battery life               | Add sleep mode to code (see power optimization)       |

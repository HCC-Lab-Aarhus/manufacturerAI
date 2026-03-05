# Universal IR Remote for ATmega328

A 3D-printable universal TV remote control supporting 10 major brands with auto-scan functionality.

**ONE FIRMWARE FOR ALL REMOTES** - No recompilation needed! Just connect the buttons you need.

## Integration with manufacturerAI

This firmware is automatically updated by the manufacturerAI pipeline when a PCB is routed. The `firmware_generator.py` module:

1. Takes the pin mapping from PCB routing
2. Updates `UniversalIRRemote.ino` with correct pin assignments
3. Generates a `PIN_ASSIGNMENT_REPORT.txt` showing which ATmega pins connect to which buttons

### Manual Firmware Generation

```python
from firmware import generate_firmware, generate_pin_assignment_report

# pin_mapping comes from router_bridge.build_pin_mapping()
pin_mapping = [
    {"button_id": "BTN_POWER", "label": "POWER", "controller_pin": "PD2"},
    {"button_id": "BTN_VOL_UP", "label": "VOL+", "controller_pin": "PD4"},
    {"component_id": "IR_LED", "type": "IR diode", "controller_pin": "PD3"},
    # ...
]

# Generate updated firmware
generate_firmware(pin_mapping, output_path=Path("output/firmware/UniversalIRRemote.ino"))

# Print human-readable report
print(generate_pin_assignment_report(pin_mapping))
```

### Command Line Usage

```bash
python firmware/firmware_generator.py pin_mapping.json [output.ino]
```

## Supported TV Brands

1. Samsung
2. LG
3. Sony
4. Panasonic
5. Philips
6. Toshiba
7. Sharp
8. Vizio
9. TCL
10. Hisense

## Features

- **Combined Power/Scan Button**: Quick press = power, hold 5 seconds = auto-scan
- **Auto-Scan Mode**: Cycles through all brands, press any button to confirm when TV responds
- **Manual Brand Selection**: Cycle through brands with dedicated button
- **EEPROM Storage**: Selected brand persists after power off
- **Flexible Button Count**: Use 1-9 buttons - unconnected pins are ignored
- **Visual Feedback**: Status LED indicates current operation

## Hardware Requirements

| Component | Quantity | Notes |
|-----------|----------|-------|
| ATmega328P | 1 | Arduino Uno/Nano compatible |
| IR LED (940nm) | 1 | 5mm recommended |
| Resistor 100-150Ω | 1 | For IR LED |
| Tactile Buttons | 1-9 | 6x6mm recommended (see pin table) |
| LED (any color) | 1 | Status indicator (optional) |
| Resistor 220-330Ω | 1 | For status LED |
| Battery Holder | 1 | 2xAAA or 3.7V LiPo |

## Pin Assignments

```
Pin 2  - Power / Auto-Scan (quick=power, hold 5s=scan)
Pin 3  - IR LED Output (PWM)
Pin 4  - Volume Up Button   (optional)
Pin 5  - Volume Down Button (optional)
Pin 6  - Channel 1 Button   (optional)
Pin 7  - Channel 2 Button   (optional)
Pin 8  - Channel 3 Button   (optional)
Pin 9  - Channel 4 Button   (optional)
Pin 10 - Channel 5 Button   (optional)
Pin 11 - (free/unused)
Pin 12 - Brand Select Button (optional)
Pin 13 - Status LED         (optional)
```

## Wiring Diagram

```
                     ATmega328 (Arduino Nano)
                     ┌──────────────────────┐
                     │                      │
  Power/Scan Btn ────┤D2               D13 ├───── Status LED (+)
                     │                      │            │
      IR LED (+) ────┤D3 (PWM)         D12 ├───── Brand Btn
           │         │                      │
         [100Ω]      │                 D11 ├───── (free)
           │         │                      │
         GND         │                 D10 ├───── CH5 Btn
                     │                      │
      Vol+ Btn ──────┤D4                D9 ├───── CH4 Btn
                     │                      │
      Vol- Btn ──────┤D5                D8 ├───── CH3 Btn
                     │                      │
      CH1 Btn ───────┤D6                D7 ├───── CH2 Btn
                     │                      │
                     │       GND     VCC   │
                     └────────┼───────┼────┘
                              │       │
                             GND    3.3V/5V

Button Wiring (all buttons same):
    ┌─────┐
    │ BTN │
    └──┬──┘
       │
  Pin ─┴─ GND

IR LED Wiring:
    D3 ──[100Ω]──(+)IR LED(-)── GND
```

## Installation

1. **Install Arduino IDE** (if not already installed)
   - Download from: https://www.arduino.cc/en/software

2. **Install IRremote Library**
   - Open Arduino IDE
   - Go to: Tools → Manage Libraries
   - Search for "IRremote"
   - Install "IRremote by shirriff, z3t0, ArminJo"

3. **Upload the Sketch**
   - Open `UniversalIRRemote.ino`
   - Select Board: Tools → Board → Arduino Nano (or Uno)
   - Select Port: Tools → Port → COMx
   - Click Upload

## Usage

### Auto-Scan Mode (Recommended for first setup)
1. Point the remote at your TV
2. **Hold the POWER button for 5 seconds** until LED starts blinking
3. Watch your TV - it will receive power commands from each brand
4. When your TV turns ON or OFF, **press any button** to confirm
5. The brand is saved automatically

### Manual Brand Selection
1. Press the **BRAND button** to cycle through brands
2. LED will blink N times to indicate brand number (1-10)
3. Brand is saved automatically to EEPROM

### Normal Operation
- **Power** (quick press): Turn TV on/off
- **Vol+/Vol-**: Adjust volume
- **CH1-CH5**: Quick access to channels 1-5 (if connected)

## Example Button Configurations

| Remote Type | Buttons | Pins Used |
|-------------|---------|-----------|
| Minimal (1 btn) | Power only | 2 |
| Simple (2 btn) | Power, Brand | 2, 12 |
| Volume (4 btn) | Power, Vol+, Vol-, Brand | 2, 4, 5, 12 |
| Standard (6 btn) | + CH1, CH2 | 2, 4, 5, 6, 7, 12 |
| Full (9 btn) | + CH3, CH4, CH5 | 2, 4, 5, 6-10, 12 |

## LED Indicators

| Pattern | Meaning |
|---------|---------|
| 3 quick blinks | Startup complete |
| N blinks (fast) | Current brand number during scan |
| N blinks (medium) | Brand changed to number N |
| 1 long blink | Brand confirmed and saved |
| 5 slow blinks | Auto-scan complete, no match |

## 3D Printed Remote Design Tips

1. **Button Wells**: Design 6.5mm x 6.5mm wells for 6x6mm tactile switches
2. **IR Window**: Clear/transparent section at front for IR LED
3. **Spring Contacts**: Use conductive 3D printing filament or metal contacts
4. **Battery Compartment**: 
   - 2xAAA: ~52mm x 24mm x 11mm
   - LiPo: Custom size with JST connector access
5. **Status LED**: Small hole or light pipe for visibility

## Troubleshooting

| Problem | Solution |
|---------|----------|
| TV not responding | Try auto-scan or manual brand cycle |
| Wrong function executed | Some TV models have different codes |
| Remote works intermittently | Check IR LED and resistor connections |
| Brand not saved | Check EEPROM write (may need replacement) |

## Customizing IR Codes

If your TV doesn't respond or uses different codes:

1. Use an IR receiver (like TSOP38238) to capture your original remote's codes
2. Update the `brandCodes[]` array in the sketch
3. Re-upload the modified sketch

## License

Open source - feel free to modify and share!

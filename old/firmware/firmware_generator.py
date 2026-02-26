"""
Firmware Generator — updates UniversalIRRemote.ino with PCB routing pin assignments.

This module takes the pin mapping from the PCB router and generates an updated
Arduino sketch with the correct pin definitions based on how the traces were routed.
"""

from __future__ import annotations
import re
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("manufacturerAI.firmware")

# Path to the template firmware
FIRMWARE_DIR = Path(__file__).parent
TEMPLATE_INO = FIRMWARE_DIR / "UniversalIRRemote.ino"

# ATmega328 port name → Arduino pin number mapping
ATMEGA_TO_ARDUINO: dict[str, int] = {
    # Port D (digital 0-7)
    "PD0": 0, "PD1": 1, "PD2": 2, "PD3": 3,
    "PD4": 4, "PD5": 5, "PD6": 6, "PD7": 7,
    # Port B (digital 8-13)
    "PB0": 8, "PB1": 9, "PB2": 10, "PB3": 11, "PB4": 12, "PB5": 13,
    # Port C (analog 0-5, can be used as digital 14-19)
    "PC0": 14, "PC1": 15, "PC2": 16, "PC3": 17, "PC4": 18, "PC5": 19,
}

# Arduino pin number → ATmega physical DIP-28 pin
ARDUINO_TO_PHYSICAL: dict[int, int] = {
    0: 2, 1: 3, 2: 4, 3: 5, 4: 6, 5: 11, 6: 12, 7: 13,
    8: 14, 9: 15, 10: 16, 11: 17, 12: 18, 13: 19,
    14: 23, 15: 24, 16: 25, 17: 26, 18: 27, 19: 28,
}

# PWM-capable Arduino pins (required for IR LED)
PWM_PINS = {3, 5, 6, 9, 10, 11}

# Button function → firmware variable mapping (primary source)
FUNCTION_TO_FIRMWARE: dict[str, str] = {
    "power": "POWER_BTN",
    "vol_up": "VOL_UP_BTN",
    "vol_down": "VOL_DOWN_BTN",
    "ch1": "CH1_BTN",
    "ch2": "CH2_BTN",
    "ch3": "CH3_BTN",
    "ch4": "CH4_BTN",
    "ch5": "CH5_BTN",
    "brand": "BRAND_BTN",
}

# Default button label → firmware variable mapping (fallback for legacy data)
BUTTON_LABELS: dict[str, str] = {
    "POWER": "POWER_BTN",
    "PWR": "POWER_BTN",
    "VOL+": "VOL_UP_BTN",
    "VOL UP": "VOL_UP_BTN",
    "VOLUME UP": "VOL_UP_BTN",
    "VOL-": "VOL_DOWN_BTN",
    "VOL DOWN": "VOL_DOWN_BTN",
    "VOLUME DOWN": "VOL_DOWN_BTN",
    "CH1": "CH1_BTN",
    "CH2": "CH2_BTN",
    "CH3": "CH3_BTN",
    "CH4": "CH4_BTN",
    "CH5": "CH5_BTN",
    "1": "CH1_BTN",
    "2": "CH2_BTN",
    "3": "CH3_BTN",
    "4": "CH4_BTN",
    "5": "CH5_BTN",
    "BRAND": "BRAND_BTN",
    "MODE": "BRAND_BTN",
}


def atmega_port_to_arduino_pin(port_name: str) -> Optional[int]:
    """Convert ATmega port name (e.g., 'PD3') to Arduino pin number (e.g., 3)."""
    return ATMEGA_TO_ARDUINO.get(port_name.upper())


def arduino_pin_to_physical(arduino_pin: int) -> Optional[int]:
    """Convert Arduino pin number to ATmega328 DIP-28 physical pin number."""
    return ARDUINO_TO_PHYSICAL.get(arduino_pin)


def is_pwm_pin(arduino_pin: int) -> bool:
    """Check if an Arduino pin is PWM-capable (required for IR LED)."""
    return arduino_pin in PWM_PINS


def normalize_button_label(label: str) -> Optional[str]:
    """Normalize a button label to the firmware variable name."""
    normalized = label.upper().strip()
    return BUTTON_LABELS.get(normalized)


def generate_firmware(
    pin_mapping: list[dict],
    output_path: Optional[Path] = None,
    *,
    status_led_pin: Optional[int] = None,
) -> str:
    """
    Generate updated firmware with pin assignments from PCB routing.

    Parameters
    ----------
    pin_mapping : list[dict]
        List of mappings from router_bridge.build_pin_mapping().
        Each dict has: button_id, label, signal_net, controller_pin (ATmega port name)
        For IR diode: component_id, type, signal_net, controller_pin
    
    output_path : Path, optional
        Where to write the generated .ino file. If None, returns string only.
    
    status_led_pin : int, optional
        Arduino pin for status LED. Defaults to 13 if available.

    Returns
    -------
    str
        The generated Arduino sketch content.
    """
    if not TEMPLATE_INO.exists():
        raise FileNotFoundError(f"Template firmware not found at {TEMPLATE_INO}")

    template = TEMPLATE_INO.read_text(encoding="utf-8")

    # Parse pin mapping
    assignments: dict[str, int] = {}
    ir_pin: Optional[int] = None
    used_pins: set[int] = set()

    for mapping in pin_mapping:
        port_name = mapping.get("controller_pin", "unrouted")
        if port_name == "unrouted" or port_name == "NC":
            continue

        arduino_pin = atmega_port_to_arduino_pin(port_name)
        if arduino_pin is None:
            log.warning("Unknown ATmega port: %s", port_name)
            continue

        # Check if this is the IR diode
        if mapping.get("type") == "IR diode":
            if not is_pwm_pin(arduino_pin):
                log.error(
                    "IR LED assigned to pin %d (%s) which is not PWM-capable! "
                    "IR transmission will not work. PWM pins are: %s",
                    arduino_pin, port_name, sorted(PWM_PINS)
                )
            ir_pin = arduino_pin
            used_pins.add(arduino_pin)
            continue

        # Button assignment
        # Priority: 1) function field, 2) label, 3) button_id patterns
        fw_var = None
        
        # First, try to get function from the function field (new approach)
        func = mapping.get("function", "")
        if func:
            fw_var = FUNCTION_TO_FIRMWARE.get(func.lower().strip())
            if fw_var:
                log.debug("Button function '%s' → %s", func, fw_var)
        
        # Fallback: try label-based mapping
        if fw_var is None:
            label = mapping.get("label", mapping.get("button_id", ""))
            fw_var = normalize_button_label(label)
        
        if fw_var is None:
            # Try to match by button_id pattern
            btn_id = mapping.get("button_id", "")
            if "POWER" in btn_id.upper():
                fw_var = "POWER_BTN"
            elif "VOL" in btn_id.upper() and ("UP" in btn_id.upper() or "+" in btn_id):
                fw_var = "VOL_UP_BTN"
            elif "VOL" in btn_id.upper() and ("DOWN" in btn_id.upper() or "-" in btn_id):
                fw_var = "VOL_DOWN_BTN"
            elif "BRAND" in btn_id.upper() or "MODE" in btn_id.upper():
                fw_var = "BRAND_BTN"
            else:
                # Try to extract channel number
                match = re.search(r"(\d+)", btn_id)
                if match:
                    ch_num = int(match.group(1))
                    if 1 <= ch_num <= 5:
                        fw_var = f"CH{ch_num}_BTN"

        if fw_var:
            assignments[fw_var] = arduino_pin
            used_pins.add(arduino_pin)
            log.info("Button %s → Arduino pin %d (%s)", fw_var, arduino_pin, port_name)

    # Determine status LED pin (use 13 if not used, otherwise find another)
    if status_led_pin is None:
        if 13 not in used_pins:
            status_led_pin = 13
        else:
            # Find an unused pin for status LED
            for p in [12, 11, 10, 9, 8, 7, 6, 5, 4]:
                if p not in used_pins and p not in PWM_PINS:
                    status_led_pin = p
                    break

    # Build the new pin definitions block
    pin_defs = _build_pin_definitions(ir_pin, assignments, status_led_pin)

    # Replace the pin definitions in the template
    updated = _replace_pin_definitions(template, pin_defs)

    # Write output if path specified
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(updated, encoding="utf-8")
        log.info("Generated firmware written to %s", output_path)

    return updated


def _build_pin_definitions(
    ir_pin: Optional[int],
    button_assignments: dict[str, int],
    status_led_pin: Optional[int],
) -> str:
    """Build the #define block for pin assignments."""
    lines = [
        "// ============== PIN DEFINITIONS ==============",
        "// AUTO-GENERATED from PCB routing — do not edit manually",
        "// Re-run firmware generator after routing changes",
        "//",
        "// IMPORTANT: IR_SEND_PIN must be PWM-capable: 3, 5, 6, 9, 10, or 11",
        "//",
        "// Arduino Pin → ATmega328 Physical Pin:",
        "//   D2→Pin4   D3→Pin5   D4→Pin6   D5→Pin11  D6→Pin12  D7→Pin13",
        "//   D8→Pin14  D9→Pin15  D10→Pin16 D11→Pin17 D12→Pin18 D13→Pin19",
        "//   A0→Pin23  A1→Pin24  A2→Pin25  A3→Pin26  A4→Pin27  A5→Pin28",
        "",
    ]

    # IR LED pin
    if ir_pin is not None:
        phys = ARDUINO_TO_PHYSICAL.get(ir_pin, "?")
        pwm_note = "" if is_pwm_pin(ir_pin) else " // WARNING: Not PWM!"
        lines.append(f"#define IR_SEND_PIN     {ir_pin:<3} // ATmega Pin {phys}{pwm_note}")
    else:
        lines.append("#define IR_SEND_PIN     3   // ATmega Pin 5  - DEFAULT (not routed)")

    # Button pins with defaults for unassigned
    defaults = {
        "POWER_BTN": (2, 4, "Dual: quick=power, hold=scan"),
        "VOL_UP_BTN": (4, 6, "Optional"),
        "VOL_DOWN_BTN": (5, 11, "Optional"),
        "CH1_BTN": (6, 12, "Optional"),
        "CH2_BTN": (7, 13, "Optional"),
        "CH3_BTN": (8, 14, "Optional"),
        "CH4_BTN": (9, 15, "Optional"),
        "CH5_BTN": (10, 16, "Optional"),
        "BRAND_BTN": (12, 18, "Optional"),
    }

    for var_name, (default_pin, default_phys, comment) in defaults.items():
        if var_name in button_assignments:
            pin = button_assignments[var_name]
            phys = ARDUINO_TO_PHYSICAL.get(pin, "?")
            lines.append(f"#define {var_name:<15} {pin:<3} // ATmega Pin {phys} - {comment}")
        else:
            lines.append(f"#define {var_name:<15} {default_pin:<3} // ATmega Pin {default_phys} - {comment} (default)")

    # Status LED
    if status_led_pin is not None:
        phys = ARDUINO_TO_PHYSICAL.get(status_led_pin, "?")
        lines.append(f"#define STATUS_LED      {status_led_pin:<3} // ATmega Pin {phys} - Optional")
    else:
        lines.append("#define STATUS_LED      13  // ATmega Pin 19 - Optional (default)")

    return "\n".join(lines)


def _replace_pin_definitions(template: str, new_defs: str) -> str:
    """Replace the PIN DEFINITIONS block in the template."""
    # Pattern to match from "// ============== PIN DEFINITIONS" 
    # to the line before "// ============== CONSTANTS"
    pattern = r"(// ============== PIN DEFINITIONS ==============.*?)(?=\n// ============== CONSTANTS)"
    
    replacement = new_defs + "\n"
    
    updated, count = re.subn(pattern, replacement, template, flags=re.DOTALL)
    
    if count == 0:
        log.warning("Could not find PIN DEFINITIONS block in template")
        return template
    
    return updated


def generate_pin_assignment_report(pin_mapping: list[dict]) -> str:
    """
    Generate a human-readable report of pin assignments.
    
    Useful for documentation and debugging.
    """
    lines = [
        "=" * 70,
        "PCB ROUTING → FIRMWARE PIN ASSIGNMENT REPORT",
        "=" * 70,
        "",
        f"{'Component':<15} {'Label':<12} {'Function':<10} {'ATmega Port':<12} {'Arduino Pin':<12} {'Physical Pin'}",
        "-" * 70,
    ]

    for mapping in pin_mapping:
        comp_type = mapping.get("type", "button")
        comp_id = mapping.get("button_id", mapping.get("component_id", "?"))
        label = mapping.get("label", comp_id)
        function = mapping.get("function", "-")
        port = mapping.get("controller_pin", "unrouted")
        
        arduino = atmega_port_to_arduino_pin(port) if port != "unrouted" else None
        physical = arduino_pin_to_physical(arduino) if arduino else None
        
        arduino_str = str(arduino) if arduino is not None else "N/A"
        physical_str = str(physical) if physical is not None else "N/A"
        
        lines.append(
            f"{comp_id:<15} {label:<12} {function:<10} {port:<12} {arduino_str:<12} {physical_str}"
        )

    lines.extend([
        "",
        "=" * 70,
        "PWM-capable pins (for IR LED): 3, 5, 6, 9, 10, 11",
        "",
        "FUNCTION KEY:",
        "  power    → Power on/off (also auto-scan with long press)",
        "  vol_up   → Volume up",
        "  vol_down → Volume down",
        "  ch1-ch5  → Channel 1-5",
        "  brand    → Cycle TV brand",
        "=" * 70,
    ])

    return "\n".join(lines)


# CLI interface for standalone testing
if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("Usage: python firmware_generator.py <pin_mapping.json> [output.ino]")
        sys.exit(1)

    with open(sys.argv[1], "r") as f:
        mapping = json.load(f)

    output = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    
    result = generate_firmware(mapping, output)
    
    if output is None:
        print(result)
    else:
        print(f"Generated: {output}")

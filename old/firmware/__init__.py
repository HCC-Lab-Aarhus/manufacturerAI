"""
Firmware module — generates ATmega328 firmware from PCB routing results.
"""

from .firmware_generator import (
    generate_firmware,
    generate_pin_assignment_report,
    atmega_port_to_arduino_pin,
    arduino_pin_to_physical,
    is_pwm_pin,
    FIRMWARE_DIR,
    TEMPLATE_INO,
)

__all__ = [
    "generate_firmware",
    "generate_pin_assignment_report",
    "atmega_port_to_arduino_pin",
    "arduino_pin_to_physical",
    "is_pwm_pin",
    "FIRMWARE_DIR",
    "TEMPLATE_INO",
]

"""
Shared hardware configuration loader.

Single source of truth for all base remote hardware constants.
Every module should import from here instead of hardcoding values.
"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List


_CONFIG_PATH = Path(__file__).parent.parent.parent / "configs" / "base_remote.json"
_config_cache = None


def _load_raw() -> dict:
    """Load raw JSON config, cached."""
    global _config_cache
    if _config_cache is None:
        _config_cache = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return _config_cache


def reload():
    """Force reload of config (for testing)."""
    global _config_cache
    _config_cache = None


# ── Accessor functions ──────────────────────────────────────────────


def board() -> dict:
    return _load_raw()["board"]


def footprints() -> dict:
    return _load_raw()["footprints"]


def manufacturing() -> dict:
    return _load_raw()["manufacturing"]


def enclosure() -> dict:
    return _load_raw()["enclosure"]


def controller_pins() -> dict:
    return _load_raw()["controller_pins"]


# ── Convenience: router-format footprints (for TS bridge) ──────────


def router_footprints() -> dict:
    """Return footprints in the format the TS router expects."""
    fp = footprints()
    return {
        "button": {
            "pinSpacingX": fp["button"]["pin_spacing_x_mm"],
            "pinSpacingY": fp["button"]["pin_spacing_y_mm"],
        },
        "controller": {
            "pinSpacing": fp["controller"]["pin_spacing_mm"],
            "rowSpacing": fp["controller"]["row_spacing_mm"],
        },
        "battery": {
            "padSpacing": fp["battery"]["pad_spacing_mm"],
        },
        "diode": {
            "padSpacing": fp["led"]["pad_spacing_mm"],
        },
    }


def router_manufacturing() -> dict:
    """Return manufacturing constraints in the format the TS router expects."""
    m = manufacturing()
    return {
        "traceWidth": m["trace_width_mm"],
        "traceClearance": m["trace_clearance_mm"],
    }


def grid_resolution() -> float:
    return board()["grid_resolution_mm"]


# ── Convenience: generate controller pin assignments ────────────────


def generate_pin_assignments(button_count: int, led_count: int = 0) -> Dict[str, str]:
    """Generate controller pin→net assignments for the given button/LED count.
    
    Returns dict mapping pin names (e.g. 'PD0') to net names (e.g. 'SW1_SIG', 'GND').
    """
    cp = controller_pins()
    pins = dict(cp["power"])
    
    # Mark unused pins
    for pin in cp["unused"]:
        pins[pin] = "NC"
    
    # Assign buttons sequentially
    digital = list(cp["digital_order"])
    assigned = 0
    for i, pin in enumerate(digital):
        if pin in pins:
            continue  # Already assigned (power/unused)
        if assigned < button_count:
            assigned += 1
            pins[pin] = f"SW{assigned}_SIG"
        elif assigned < button_count + led_count:
            assigned += 1
            led_idx = assigned - button_count
            pins[pin] = f"LED{led_idx}_SIG"
        else:
            pins[pin] = "NC"
    
    return pins

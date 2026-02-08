"""
Hardware configuration — single source of truth for all base remote constants.

Loads configs/base_remote.json once and exposes typed accessors.
"""

from __future__ import annotations
import json
from pathlib import Path
from functools import lru_cache


_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "base_remote.json"


@lru_cache(maxsize=1)
def _load() -> dict:
    return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))


class _HW:
    """Typed accessor for hardware config."""

    # ── raw section accessors ───────────────────────────────────────
    @property
    def board(self) -> dict:
        return _load()["board"]

    @property
    def footprints(self) -> dict:
        return _load()["footprints"]

    @property
    def manufacturing(self) -> dict:
        return _load()["manufacturing"]

    # ── board ───────────────────────────────────────────────────────
    @property
    def wall_clearance(self) -> float:
        return _load()["board"]["enclosure_wall_clearance_mm"]

    @property
    def pcb_thickness(self) -> float:
        return _load()["board"]["pcb_thickness_mm"]

    @property
    def grid_resolution(self) -> float:
        return _load()["board"]["grid_resolution_mm"]

    @property
    def component_margin(self) -> float:
        return _load()["board"]["component_margin_mm"]

    # ── footprints ──────────────────────────────────────────────────
    @property
    def button(self) -> dict:
        return _load()["footprints"]["button"]

    @property
    def controller(self) -> dict:
        return _load()["footprints"]["controller"]

    @property
    def battery(self) -> dict:
        return _load()["footprints"]["battery"]

    @property
    def diode(self) -> dict:
        return _load()["footprints"]["diode"]

    # ── manufacturing ───────────────────────────────────────────────
    @property
    def trace_width(self) -> float:
        return _load()["manufacturing"]["trace_width_mm"]

    @property
    def trace_clearance(self) -> float:
        return _load()["manufacturing"]["trace_clearance_mm"]

    @property
    def trace_channel_depth(self) -> float:
        return _load()["manufacturing"]["trace_channel_depth_mm"]

    @property
    def pinhole_depth(self) -> float:
        return _load()["manufacturing"]["pinhole_depth_mm"]

    @property
    def pinhole_diameter(self) -> float:
        return _load()["manufacturing"]["pinhole_diameter_mm"]

    # ── enclosure ───────────────────────────────────────────────────
    @property
    def enclosure(self) -> dict:
        return _load()["enclosure"]

    @property
    def wall_thickness(self) -> float:
        return _load()["enclosure"]["wall_thickness_mm"]

    @property
    def floor_thickness(self) -> float:
        return _load()["enclosure"]["bottom_thickness_mm"]

    @property
    def ceil_thickness(self) -> float:
        return _load()["enclosure"]["top_thickness_mm"]

    @property
    def corner_radius(self) -> float:
        return _load()["enclosure"]["corner_radius_mm"]

    @property
    def shell_height(self) -> float:
        return _load()["enclosure"]["shell_height_mm"]

    # ── controller pins ─────────────────────────────────────────────
    @property
    def controller_pins(self) -> dict:
        return _load()["controller_pins"]

    # ── TS-router-format helpers ────────────────────────────────────
    def router_footprints(self) -> dict:
        fp = _load()["footprints"]
        return {
            "button": {
                "pinSpacingX": fp["button"]["pin_spacing_x_mm"],
                "pinSpacingY": fp["button"]["pin_spacing_y_mm"],
            },
            "controller": {
                "pinSpacing": fp["controller"]["pin_spacing_mm"],
                "rowSpacing": fp["controller"]["row_spacing_mm"],
            },
            "battery": {"padSpacing": fp["battery"]["pad_spacing_mm"]},
            "diode": {"padSpacing": fp["diode"]["pad_spacing_mm"]},
        }

    def router_manufacturing(self) -> dict:
        m = _load()["manufacturing"]
        return {
            "traceWidth": m["trace_width_mm"],
            "traceClearance": m["trace_clearance_mm"],
        }

    def pin_assignments(self, button_count: int, diode_count: int = 0) -> dict[str, str]:
        """Generate controller pin → net assignments."""
        cp = self.controller_pins
        pins: dict[str, str] = dict(cp["power"])
        for pin in cp["unused"]:
            pins[pin] = "NC"
        assigned = 0
        for pin in cp["digital_order"]:
            if pin in pins:
                continue
            if assigned < button_count:
                assigned += 1
                pins[pin] = f"SW{assigned}_SIG"
            elif assigned < button_count + diode_count:
                assigned += 1
                pins[pin] = f"LED{assigned - button_count}_SIG"
            else:
                pins[pin] = "NC"
        return pins


hw = _HW()

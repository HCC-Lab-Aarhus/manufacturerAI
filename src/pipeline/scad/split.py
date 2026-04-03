"""split.py — compute the Z split height for two-part enclosures."""

from __future__ import annotations

import logging

from src.catalog.models import Component
from src.pipeline.config import CAVITY_START_MM, CEILING_MM, component_z_range
from src.pipeline.design.models import Enclosure
from src.pipeline.placer.models import PlacedComponent

log = logging.getLogger(__name__)


def compute_split_z(
    enclosure: Enclosure,
    components: list[PlacedComponent],
    cat_index: dict[str, Component],
) -> float:
    """Compute the Z height where bottom and top halves meet.

    If ``enclosure.split_z_mm`` is explicitly set, use that (clamped).
    Otherwise place the split above the tallest component body in the
    cavity zone, with margin for snap posts.

    Returns the split Z in mm from the build plate.
    """
    base_h = enclosure.height_mm
    ceil_start = base_h - CEILING_MM

    # Auto-compute from component heights
    max_body_top = CAVITY_START_MM
    for comp in components:
        cat = cat_index.get(comp.catalog_id)
        if cat is None:
            continue
        style = comp.mounting_style or cat.mounting.style
        if style in ("top", "internal"):
            _, body_top = component_z_range(
                style, cat.body.height_mm, cat.pin_length_mm, ceil_start,
            )
            max_body_top = max(max_body_top, body_top)
        elif style in ("bottom", "side"):
            _, body_top = component_z_range(
                style, cat.body.height_mm, cat.pin_length_mm, ceil_start,
            )
            max_body_top = max(max_body_top, body_top)

    auto_z = max(CAVITY_START_MM + 5.0, max_body_top + 1.0)

    if enclosure.split_z_mm is not None:
        split_z = enclosure.split_z_mm
    else:
        split_z = auto_z

    # Clamp: at least CAVITY_START_MM + 2 above floor, at least 3 mm below ceiling
    lo = CAVITY_START_MM + 2.0
    hi = ceil_start - 3.0
    split_z = max(lo, min(split_z, hi))

    log.info(
        "Two-part split Z: %.2f mm (auto=%.2f, ceil_start=%.2f, max_body_top=%.2f)",
        split_z, auto_z, ceil_start, max_body_top,
    )
    return split_z

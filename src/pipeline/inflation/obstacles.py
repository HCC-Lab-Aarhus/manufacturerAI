"""Obstacle polygon construction for trace inflation."""

from __future__ import annotations

from shapely.geometry import Polygon


def build_obstacle_polygons(
    placement_components: list,
    catalog_map: dict,
) -> list[Polygon]:
    """Build obstacle polygons from placed components.

    Each component with ``blocks_routing=True`` becomes a rectangle
    (body + keepout margin) that traces cannot inflate into.
    """
    from src.pipeline.placer.geometry import footprint_halfdims

    obstacles: list[Polygon] = []
    for pc in placement_components:
        cat = catalog_map.get(pc.catalog_id)
        if cat is None or not cat.mounting.blocks_routing:
            continue
        hw, hh = footprint_halfdims(cat, pc.rotation_deg)
        keepout = cat.mounting.keepout_margin_mm
        hw += keepout
        hh += keepout
        box = Polygon([
            (pc.x_mm - hw, pc.y_mm - hh),
            (pc.x_mm + hw, pc.y_mm - hh),
            (pc.x_mm + hw, pc.y_mm + hh),
            (pc.x_mm - hw, pc.y_mm + hh),
        ])
        obstacles.append(box)
    return obstacles

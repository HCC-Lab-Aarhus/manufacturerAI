"""Shared state and helpers used by both v1 and v2 route modules."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import HTTPException

from src.catalog import load_catalog, catalog_to_dict, CatalogResult
from src.session import load_session, create_session, Session


# compile state: session_id -> {status, message, cancel}
stl_compile: dict[str, dict] = {}

# gcode pipeline state: session_id -> {status, message, stages, ...}
gcode_state: dict[str, dict] = {}


# ── Catalog (auto-reloads when any catalog/*.json changes on disk) ──

_catalog_result: CatalogResult | None = None
_catalog_mtime: float = 0.0


def _catalog_dir_mtime() -> float:
    from src.catalog.loader import CATALOG_DIR
    try:
        return max((p.stat().st_mtime for p in CATALOG_DIR.glob("*.json")), default=0.0)
    except OSError:
        return 0.0


def get_catalog() -> CatalogResult:
    global _catalog_result, _catalog_mtime
    mtime = _catalog_dir_mtime()
    if _catalog_result is None or mtime > _catalog_mtime:
        _catalog_result = load_catalog()
        _catalog_mtime = mtime
    return _catalog_result


def reload_catalog() -> CatalogResult:
    global _catalog_result, _catalog_mtime
    _catalog_result = load_catalog()
    _catalog_mtime = _catalog_dir_mtime()
    return _catalog_result


# ── Session helpers ──

def load_session_or_404(sid: str) -> Session:
    s = load_session(sid)
    if s is None:
        raise HTTPException(404, f"Session '{sid}' not found")
    return s


def resolve_session(session_id: str | None) -> Session:
    if session_id:
        return load_session_or_404(session_id)
    return create_session()


def invalidate_downstream(session: Session, current_step: str) -> list[str]:
    return session.invalidate_downstream(current_step)


# ── Enrichment helpers ──

def enrich_components(components: list, cat) -> None:
    cat_map = {c.id: c for c in cat.components}
    for comp in components:
        c = cat_map.get(comp.get("catalog_id"))
        if not c:
            continue
        comp["body"] = {
            "shape": c.body.shape,
            "width_mm": c.body.width_mm,
            "length_mm": c.body.length_mm,
            "diameter_mm": c.body.diameter_mm,
            "height_mm": c.body.height_mm,
        }
        pp = comp.get("pin_positions", {})
        comp["pins"] = [
            {
                "id": p.id,
                "position_mm": list(p.position_mm),
                **({"world_mm": list(pp[p.id])} if p.id in pp else {}),
            }
            for p in c.pins
        ]
        comp["ui_placement"] = c.ui_placement
        if c.mounting and c.mounting.cap:
            comp["cap_diameter_mm"] = c.mounting.cap.diameter_mm
            comp["cap_clearance_mm"] = c.mounting.cap.hole_clearance_mm


_design_3d_cache: dict[str, dict] = {}


def _design_3d_cache_key(outline_data, enclosure_data) -> str:
    raw = json.dumps({"o": outline_data, "e": enclosure_data}, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


def enrich_design_3d(data: dict) -> None:
    from src.pipeline.design.parsing import _parse_outline, _parse_enclosure
    from src.pipeline.design.height_field import (
        sample_height_grid, sample_bottom_height_grid,
        surface_normal_at, blended_height,
        pcb_contour_from_bottom_grid,
    )
    from src.pipeline.config import FLOOR_MM

    outline_data = data.get("outline", [])
    enclosure_data = data.get("enclosure", {})
    if not outline_data:
        return

    try:
        outline = _parse_outline(outline_data)
        enclosure = _parse_enclosure(enclosure_data)
    except Exception:
        return

    cache_key = _design_3d_cache_key(outline_data, enclosure_data)
    cached = _design_3d_cache.get(cache_key)
    if cached is not None:
        grid = cached["height_grid"]
        data["height_grid"] = grid
        if "bottom_height_grid" in cached:
            data["bottom_height_grid"] = cached["bottom_height_grid"]
        if "pcb_contour" in cached:
            data["pcb_contour"] = cached["pcb_contour"]
    else:
        grid = sample_height_grid(outline, enclosure, resolution_mm=1.0)
        data["height_grid"] = grid
        to_cache: dict = {"height_grid": grid}

        bottom_grid = sample_bottom_height_grid(outline, enclosure, resolution_mm=1.0)
        if bottom_grid is not None:
            data["bottom_height_grid"] = bottom_grid
            to_cache["bottom_height_grid"] = bottom_grid
            contour = pcb_contour_from_bottom_grid(
                bottom_grid, outline, threshold_mm=FLOOR_MM,
            )
            if contour is not None:
                data["pcb_contour"] = contour
                to_cache["pcb_contour"] = contour

        _design_3d_cache[cache_key] = to_cache

    for up in data.get("ui_placements", []):
        x, y = up.get("x_mm", 0), up.get("y_mm", 0)
        try:
            z = blended_height(x, y, outline, enclosure)
            normal = surface_normal_at(x, y, grid)
            up["z_at_position"] = round(z, 3)
            up["surface_normal"] = [round(n, 4) for n in normal]
        except Exception:
            pass


def attach_pcb_contour(data: dict) -> None:
    if "pcb_contour" in data:
        return
    outline_data = data.get("outline", [])
    enclosure_data = data.get("enclosure", {})
    if not outline_data:
        return
    try:
        from src.pipeline.design.parsing import _parse_outline, _parse_enclosure
        from src.pipeline.design.height_field import (
            sample_bottom_height_grid, pcb_contour_from_bottom_grid,
        )
        from src.pipeline.config import FLOOR_MM

        outline = _parse_outline(outline_data)
        enclosure = _parse_enclosure(enclosure_data)
        bottom_grid = sample_bottom_height_grid(outline, enclosure, resolution_mm=1.0)
        if bottom_grid is None:
            return
        contour = pcb_contour_from_bottom_grid(
            bottom_grid, outline, threshold_mm=FLOOR_MM,
        )
        if contour is not None:
            data["pcb_contour"] = contour
    except Exception:
        pass


def enrich_placement(data: dict, cat) -> dict:
    enrich_components(data.get("components", []), cat)
    attach_pcb_contour(data)
    return data

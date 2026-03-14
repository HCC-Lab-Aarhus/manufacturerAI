from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.pipeline.config import PRINTERS
from src.pipeline.gcode.filaments import FILAMENTS
from src.web.routes._deps import get_catalog, reload_catalog

router = APIRouter(tags=["catalog"])


@router.get("/catalog")
async def catalog():
    cat = get_catalog()
    from src.catalog import catalog_to_dict
    return catalog_to_dict(cat)


@router.post("/catalog/reload")
async def catalog_reload():
    cat = reload_catalog()
    from src.catalog import catalog_to_dict
    return catalog_to_dict(cat)


@router.get("/catalog/{component_id}")
async def catalog_component(component_id: str):
    cat = get_catalog()
    for c in cat.components:
        if c.id == component_id:
            from src.catalog import component_to_dict
            return component_to_dict(c)
    raise HTTPException(404, f"Component '{component_id}' not found")


@router.get("/printers")
async def list_printers():
    return {
        "printers": [
            {"id": p.id, "label": p.label, "bed_width": p.bed_width, "bed_depth": p.bed_depth, "max_z_mm": p.max_z_mm}
            for p in PRINTERS.values()
        ]
    }


@router.get("/filaments")
async def list_filaments():
    return {
        "filaments": [
            {"id": f.id, "label": f.label}
            for f in FILAMENTS.values()
        ]
    }

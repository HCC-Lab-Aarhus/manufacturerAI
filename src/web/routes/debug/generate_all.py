from __future__ import annotations

from fastapi import APIRouter, Query

from .calibration import generate_calibration
from .combined import generate_combined
from .components import generate_components
from .spacing import generate_spacing
from .surface_test import generate_surface_test
from .width import generate_width

router = APIRouter()

FILAMENT_FOLDERS: dict[str, str] = {
    "prusament_pla": "PLA",
    "generic_abs": "ABS",
    "prusament_petg": "PETG",
    "prusament_tpu_95a": "TPU",
    "generic_nylon": "NYLON",
}


@router.post("/generate-all")
async def generate_all_tests(
    printer: str = Query("mk3s"),
    filaments: str = Query("prusament_pla,generic_abs,prusament_petg,prusament_tpu_95a,generic_nylon"),
):
    """Generate all integration test files and return as JSON."""
    filament_ids = [f.strip() for f in filaments.split(",") if f.strip()]

    files: dict[str, str] = {}

    for fil_id in filament_ids:
        folder = FILAMENT_FOLDERS.get(fil_id, fil_id)

        cal = await generate_calibration(printer=printer, filament=fil_id)
        files[f"{folder}/calibration.gcode"] = cal["gcode"]
        files[f"{folder}/calibration.txt"] = cal["bitmap"]

        r = await generate_combined(printer=printer, filament=fil_id)
        files[f"{folder}/combined.gcode"] = r["gcode"]
        files[f"{folder}/combined.txt"] = r["bitmap"]

        r = await generate_components(printer=printer, filament=fil_id)
        files[f"{folder}/components.gcode"] = r["gcode"]
        files[f"{folder}/components.txt"] = r["bitmap"]

        r = await generate_spacing(printer=printer, filament=fil_id)
        files[f"{folder}/spacing.gcode"] = r["gcode"]
        files[f"{folder}/spacing.txt"] = r["bitmap"]

        r = await generate_width(printer=printer, filament=fil_id)
        files[f"{folder}/width.gcode"] = r["gcode"]
        files[f"{folder}/width.txt"] = r["bitmap"]

    r = await generate_surface_test(printer=printer)
    files["surface_test.txt"] = r["bitmap"]

    return {"files": files}

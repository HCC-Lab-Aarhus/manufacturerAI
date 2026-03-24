from __future__ import annotations

from fastapi import APIRouter, Query

from .calibration import generate_calibration
from .components import generate_components
from .layers import generate_layers
from .spacing import generate_spacing
from .solid_squares import generate_solid_squares
from .width import generate_width

router = APIRouter()

FILAMENT_FOLDERS: dict[str, str] = {
    "prusament_pla": "PLA",
    "generic_abs": "ABS",
    "prusament_petg": "PETG",
    "prusament_tpu_95a": "TPU",
}


@router.post("/generate-all")
async def generate_all_tests(
    printer: str = Query("mk3s"),
    filaments: str = Query("prusament_pla,generic_abs,prusament_petg,prusament_tpu_95a"),
):
    """Generate all integration test files and return as JSON."""
    filament_ids = [f.strip() for f in filaments.split(",") if f.strip()]

    files: dict[str, str] = {}

    cal = await generate_calibration(
        printer=printer, filament=filament_ids[0],
        box_size=100, padding=5, square_size=5,
    )
    files["calibration.gcode"] = cal["gcode"]
    files["calibration_bitmap.txt"] = cal["bitmap"]

    for fil_id in filament_ids:
        folder = FILAMENT_FOLDERS.get(fil_id, fil_id)

        r = await generate_components(printer=printer, filament=fil_id, padding=5)
        files[f"{folder}/components.gcode"] = r["gcode"]
        files[f"{folder}/components.txt"] = r["bitmap"]

        r = await generate_layers(
            printer=printer, filament=fil_id, padding=5,
            rect_width=10, rect_height=20, layers=4,
        )
        files[f"{folder}/layers.gcode"] = r["gcode"]
        files[f"{folder}/layers_1.txt"] = r["bitmap_1"]
        files[f"{folder}/layers_2.txt"] = r["bitmap_2"]
        files[f"{folder}/layers_3.txt"] = r["bitmap_3"]

        r = await generate_spacing(
            printer=printer, filament=fil_id, padding=5,
            rect_width=40, rect_height=20, layers=4,
        )
        files[f"{folder}/spacing.gcode"] = r["gcode"]
        files[f"{folder}/spacing.txt"] = r["bitmap"]

        r = await generate_width(
            printer=printer, filament=fil_id, padding=5,
            rect_width=40, rect_height=20, layers=4,
        )
        files[f"{folder}/width.gcode"] = r["gcode"]
        files[f"{folder}/width.txt"] = r["bitmap"]

        r = await generate_solid_squares(
            printer=printer, filament=fil_id, padding=5,
            rect_width=10, rect_height=20, layers=10,
        )
        files[f"{folder}/solid_squares.gcode"] = r["gcode"]
        files[f"{folder}/solid_squares.txt"] = r["bitmap"]

    return {"files": files}

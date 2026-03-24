"""Debug / calibration routes.

Generates alignment G-code and bitmap files used to measure and verify
the inkjet-to-PLA nozzle offset.

Follows the exact same coordinate conventions as the real pipeline:
- G-code is in nominal-bed coordinates (PrusaSlicer bed_shape).
- Part (calibration box) is centred on the nominal bed.
- Bitmap spans the full sweep grid in absolute bed coordinates.
- Bitmap transposition matches bitmap.py: lines = X sweep (high→low),
  chars = Y nozzle (low→high).
- Manifest records part_origin in absolute nominal-bed coordinates.
"""

from __future__ import annotations

from fastapi import APIRouter

from .calibration import router as _calibration
from .silverink import router as _silverink
from .components import router as _components
from .layers import router as _layers
from .spacing import router as _spacing
from .width import router as _width
from .squares import router as _squares
from .generate_all import router as _generate_all

router = APIRouter(prefix="/debug", tags=["debug"])

router.include_router(_calibration)
router.include_router(_silverink)
router.include_router(_components)
router.include_router(_layers)
router.include_router(_spacing)
router.include_router(_width)
router.include_router(_squares)
router.include_router(_generate_all)

from __future__ import annotations
from pydantic import BaseModel, Field

class RemoteSpec(BaseModel):
    length_mm: float = Field(ge=80, le=240)
    width_mm: float = Field(ge=25, le=70)
    thickness_mm: float = Field(ge=10, le=35)
    wall_mm: float = Field(ge=1.2, le=4.0)
    corner_radius_mm: float = Field(ge=0, le=15)

class ButtonSpec(BaseModel):
    rows: int = Field(ge=1, le=12)
    cols: int = Field(ge=1, le=8)
    diam_mm: float = Field(ge=5.0, le=18.0)
    spacing_mm: float = Field(ge=1.0, le=10.0)
    margin_top_mm: float = Field(ge=5.0, le=80.0)
    margin_bottom_mm: float = Field(ge=5.0, le=80.0)
    margin_side_mm: float = Field(ge=3.0, le=30.0)
    hole_clearance_mm: float = Field(ge=0.0, le=1.0)

class RemoteParams(BaseModel):
    remote: RemoteSpec
    buttons: ButtonSpec

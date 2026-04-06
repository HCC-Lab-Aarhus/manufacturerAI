"""Trace inflation — expands routed traces into filled polygons."""

from .inflater import inflate_traces
from .obstacles import build_obstacle_polygons
from .serialization import inflation_to_dict, parse_inflation
from src.pipeline.pin_geometry import pin_pad_poly, pin_shaft_poly

__all__ = [
    "inflate_traces",
    "pin_pad_poly",
    "pin_shaft_poly",
    "build_obstacle_polygons",
    "inflation_to_dict",
    "parse_inflation",
]

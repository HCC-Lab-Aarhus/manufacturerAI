"""Trace inflation — expands routed traces into filled polygons."""

from .inflater import inflate_traces
from .obstacles import build_obstacle_polygons
from .serialization import inflation_to_dict, parse_inflation

__all__ = [
    "inflate_traces",
    "build_obstacle_polygons",
    "inflation_to_dict",
    "parse_inflation",
]

"""Router — Manhattan trace routing between component pads.

Submodules:
  models        Output dataclasses and configuration constants.
  grid          Discretized routing grid (free/blocked cells).
  pathfinder    A* pathfinding (point-to-point and point-to-tree).
  pins          Pin resolution and dynamic pin allocation.
  engine        Main routing algorithm (grid-search with rip-up).
  serialization JSON conversion (routing_to_dict, parse_routing).
"""

from .models import Trace, RoutingResult, RouterConfig
from .engine import route_traces
from .serialization import routing_to_dict, parse_routing

__all__ = [
    # Models
    "Trace", "RoutingResult", "RouterConfig",
    # Engine
    "route_traces",
    # Serialization
    "routing_to_dict", "parse_routing",
]

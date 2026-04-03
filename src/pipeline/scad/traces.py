"""Trace channel fragment builders.

Produces ScadFragment objects for trace channels using the same
stadium geometry (Shapely LineString.buffer) as the bitmap and
routing grid so that all pipeline stages agree on the exact trace shape.
"""

from __future__ import annotations

from src.pipeline.config import FLOOR_MM, TRACE_HEIGHT_MM, TRACE_RULES
from src.pipeline.router.models import RoutingResult
from src.pipeline.trace_geometry import trace_path_polygon

from .fragment import ScadFragment, PolygonGeometry


def build_trace_fragments(
    routing: RoutingResult,
    ceil_start: float,
) -> list[ScadFragment]:
    """Build trace channel fragments for every routed trace.

    Each trace path is buffered into a stadium-shaped polygon (rectangle
    with semicircular endcaps at every waypoint) using the shared
    ``trace_path_polygon`` function, then emitted as a single cutout
    fragment per net.
    """
    channel_depth = TRACE_HEIGHT_MM
    trace_w = TRACE_RULES.trace_width_mm
    frags: list[ScadFragment] = []

    for trace in routing.traces:
        path = [(float(x), float(y)) for x, y in trace.path]
        poly = trace_path_polygon(path, trace_w)
        if poly is None:
            continue

        from shapely.geometry import MultiPolygon
        geoms = list(poly.geoms) if isinstance(poly, MultiPolygon) else [poly]

        for geom in geoms:
            coords = list(geom.exterior.coords)[:-1]
            pts = [[x, y] for x, y in coords]
            holes = None
            if geom.interiors:
                holes = [
                    [[x, y] for x, y in ring.coords[:-1]]
                    for ring in geom.interiors
                ]

            frags.append(ScadFragment(
                type="cutout",
                geometry=PolygonGeometry(pts, holes),
                z_base=FLOOR_MM,
                depth=channel_depth,
                label=f"trace {trace.net_id}",
            ))

    return frags

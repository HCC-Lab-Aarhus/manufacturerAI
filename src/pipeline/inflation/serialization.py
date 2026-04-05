"""Inflation artifact serialization — JSON conversion."""

from __future__ import annotations

from shapely.geometry import Polygon

from src.pipeline.router.models import InflatedTrace


def inflation_to_dict(inflated: list[InflatedTrace]) -> dict:
    """Serialize inflation results to a JSON-safe dict for inflation.json."""
    return {
        "inflated_traces": [
            {
                "net_id": it.net_id,
                "centreline": [list(p) for p in it.centreline],
                "polygon": list(it.polygon.exterior.coords),
                "holes": [
                    list(ring.coords)
                    for ring in it.polygon.interiors
                ] if it.polygon.interiors else [],
            }
            for it in inflated
        ],
    }


def parse_inflation(data: dict) -> list[InflatedTrace]:
    """Parse an inflation.json dict back into a list of InflatedTrace."""
    inflated: list[InflatedTrace] = []
    for it_data in data.get("inflated_traces", []):
        shell = [tuple(p) for p in it_data["polygon"]]
        holes = [
            [tuple(p) for p in ring]
            for ring in it_data.get("holes", [])
        ]
        poly = Polygon(shell, holes) if holes else Polygon(shell)
        inflated.append(InflatedTrace(
            net_id=it_data["net_id"],
            centreline=[tuple(p) for p in it_data["centreline"]],
            polygon=poly,
        ))
    return inflated

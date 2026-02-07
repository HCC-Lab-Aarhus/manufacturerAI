"""
PCB Python Bridge Package

This package provides Python interfaces to the TypeScript PCB tools.
All PCB routing and visualization is handled by TypeScript - Python only
provides a thin bridge layer.

Components:
- PCBAgent: Generates pcb_layout.json from design_spec.json (component placement)
- TSPCBRouter: Bridge to TypeScript CLI for routing and visualization
"""

from src.pcb_python.pcb_agent import PCBAgent
from src.pcb_python.ts_router_bridge import (
    TSPCBRouter,
    RouterError,
    RouterNotFoundError,
    RoutingFailedError,
    route_pcb,
    ATMEGA328P_PINOUT,
)

__all__ = [
    "PCBAgent",
    "TSPCBRouter",
    "RouterError",
    "RouterNotFoundError",
    "RoutingFailedError",
    "route_pcb",
    "ATMEGA328P_PINOUT",
]

"""
State machine for the manufacturing pipeline.

States:
- COLLECT_REQUIREMENTS: Consultant agent normalizes user input
- GENERATE_PCB: PCB agent creates layout from design spec
- CHECK_PCB_FEASIBILITY: Feasibility tool validates layout
- ITERATE_PCB: Apply fixes and regenerate (loop if needed)
- GENERATE_ENCLOSURE: 3D agent builds parametric enclosure
- FINAL_VERIFY: Optional final checks
- DONE: Pipeline complete
"""

from __future__ import annotations
from enum import Enum, auto
from pathlib import Path
from dataclasses import dataclass
import json
from typing import Optional

class PipelineState(Enum):
    COLLECT_REQUIREMENTS = auto()
    GENERATE_PCB = auto()
    CHECK_PCB_FEASIBILITY = auto()
    ITERATE_PCB = auto()
    GENERATE_ENCLOSURE = auto()
    FINAL_VERIFY = auto()
    DONE = auto()
    ERROR = auto()

@dataclass
class PipelineContext:
    """Shared context across pipeline stages."""
    run_dir: Path
    design_spec: Optional[dict] = None
    pcb_layout: Optional[dict] = None
    feasibility_report: Optional[dict] = None
    routing_result: Optional[dict] = None  # Traces for conductive filament channels
    iteration: int = 0
    max_iterations: int = 5
    
    def save_design_spec(self) -> None:
        if self.design_spec:
            path = self.run_dir / "design_spec.json"
            path.write_text(json.dumps(self.design_spec, indent=2), encoding="utf-8")
    
    def save_pcb_layout(self, version: Optional[int] = None) -> None:
        if self.pcb_layout:
            ver = version or self.iteration
            path = self.run_dir / f"pcb_layout_v{ver}.json"
            path.write_text(json.dumps(self.pcb_layout, indent=2), encoding="utf-8")
    
    def save_feasibility_report(self, version: Optional[int] = None) -> None:
        if self.feasibility_report:
            ver = version or self.iteration
            path = self.run_dir / f"feasibility_v{ver}.json"
            path.write_text(json.dumps(self.feasibility_report, indent=2), encoding="utf-8")
    
    def save_routing_result(self, version: Optional[int] = None) -> None:
        if self.routing_result:
            ver = version or self.iteration
            path = self.run_dir / f"routing_result_v{ver}.json"
            path.write_text(json.dumps(self.routing_result, indent=2), encoding="utf-8")
    
    def load_design_spec(self) -> dict:
        path = self.run_dir / "design_spec.json"
        self.design_spec = json.loads(path.read_text(encoding="utf-8"))
        return self.design_spec
    
    def load_pcb_layout(self, version: Optional[int] = None) -> dict:
        ver = version or self.iteration
        path = self.run_dir / f"pcb_layout_v{ver}.json"
        self.pcb_layout = json.loads(path.read_text(encoding="utf-8"))
        return self.pcb_layout

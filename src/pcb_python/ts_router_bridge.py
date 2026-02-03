"""
TypeScript PCB Router Integration

This module provides a Python wrapper to call the TypeScript PCB router
for trace routing between components after placement is complete.
"""

from __future__ import annotations
import json
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any


class TSPCBRouter:
    """
    Wrapper for the TypeScript PCB router.
    
    The TS router handles:
    - Grid-based pathfinding for traces
    - Net extraction and MST optimization
    - DRC-aware routing with clearances
    - Output generation (PNG, geometry)
    """
    
    def __init__(self, ts_router_dir: Optional[Path] = None):
        if ts_router_dir is None:
            ts_router_dir = Path(__file__).parent.parent / "pcb"
        self.ts_router_dir = ts_router_dir
        self._ensure_built()
    
    def _ensure_built(self) -> None:
        """Ensure the TypeScript router is compiled."""
        dist_dir = self.ts_router_dir / "dist"
        if not dist_dir.exists():
            # Need to compile
            self._run_npm_build()
    
    def _run_npm_build(self) -> None:
        """Run npm install and build."""
        try:
            subprocess.run(
                ["npm", "install"],
                cwd=self.ts_router_dir,
                capture_output=True,
                check=True,
                shell=True
            )
            subprocess.run(
                ["npm", "run", "build"],
                cwd=self.ts_router_dir,
                capture_output=True,
                check=True,
                shell=True
            )
        except subprocess.CalledProcessError as e:
            print(f"Warning: Could not build TS router: {e}")
            print(f"STDOUT: {e.stdout.decode() if e.stdout else ''}")
            print(f"STDERR: {e.stderr.decode() if e.stderr else ''}")
    
    def convert_pcb_layout_to_router_input(self, pcb_layout: dict) -> dict:
        """
        Convert pcb_layout.json format to the TS router input format.
        
        The TS router expects:
        - board: { boardWidth, boardHeight, gridResolution }
        - manufacturing: { traceWidth, traceClearance }
        - footprints: { button, controller, battery, diode }
        - placement: { buttons, controllers, batteries, diodes }
        """
        # Extract board dimensions
        outline = pcb_layout["board"]["outline_polygon"]
        xs = [p[0] for p in outline]
        ys = [p[1] for p in outline]
        board_width = max(xs) - min(xs)
        board_height = max(ys) - min(ys)
        
        # Build placement from components
        buttons = []
        controllers = []
        batteries = []
        diodes = []
        
        for comp in pcb_layout.get("components", []):
            comp_type = comp.get("type")
            x, y = comp["center"]
            comp_id = comp["id"]
            
            if comp_type == "button":
                buttons.append({
                    "id": comp_id,
                    "x": x,
                    "y": y,
                    "signalNet": f"{comp_id}_SIG"
                })
            elif comp_type == "controller":
                # Build controller pins
                pins = self._generate_controller_pins(len(buttons))
                controllers.append({
                    "id": comp_id,
                    "x": x,
                    "y": y,
                    "pins": pins
                })
            elif comp_type == "battery":
                batteries.append({
                    "id": comp_id,
                    "x": x,
                    "y": y
                })
            elif comp_type == "led":
                diodes.append({
                    "id": comp_id,
                    "x": x,
                    "y": y,
                    "signalNet": f"{comp_id}_SIG"
                })
        
        return {
            "board": {
                "boardWidth": board_width,
                "boardHeight": board_height,
                "gridResolution": 0.5
            },
            "manufacturing": {
                "traceWidth": 1.5,
                "traceClearance": 2.0
            },
            "footprints": {
                "button": {"pinSpacingX": 9.0, "pinSpacingY": 6.0},
                "controller": {"pinSpacing": 2.5, "rowSpacing": 10.0},
                "battery": {"padSpacing": 6.0},
                "diode": {"padSpacing": 5.0}
            },
            "placement": {
                "buttons": buttons,
                "controllers": controllers,
                "batteries": batteries,
                "diodes": diodes
            }
        }
    
    def _generate_controller_pins(self, button_count: int) -> Dict[str, str]:
        """Generate controller pin assignments for buttons."""
        pins = {
            "VCC": "VCC",
            "GND1": "GND",
            "GND2": "GND",
            "AVCC": "VCC",
            "AREF": "NC"
        }
        
        # Available I/O pins
        io_pins = ["PD0", "PD1", "PD2", "PD3", "PD4", "PD5", "PD6", "PD7",
                   "PB0", "PB1", "PB2", "PB3", "PB4", "PB5",
                   "PC0", "PC1", "PC2", "PC3", "PC4", "PC5"]
        
        # Assign pins to buttons
        for i, pin in enumerate(io_pins):
            if i < button_count:
                pins[pin] = f"SW{i+1}_SIG"
            else:
                pins[pin] = "NC"
        
        # Add unused pins
        for pin in ["PC6", "PB6", "PB7"]:
            pins[pin] = "NC"
        
        return pins
    
    def route(self, pcb_layout: dict, output_dir: Path) -> dict:
        """
        Run the TS router on the given PCB layout.
        
        Args:
            pcb_layout: pcb_layout.json dict
            output_dir: Directory for output files
        
        Returns:
            Routing result with traces and any failed nets
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Convert to TS router format
        router_input = self.convert_pcb_layout_to_router_input(pcb_layout)
        
        # Run the TS router CLI via stdin/stdout
        try:
            cli_path = self.ts_router_dir / "dist" / "cli.js"
            if not cli_path.exists():
                # Fall back to index.js if cli.js doesn't exist
                cli_path = self.ts_router_dir / "dist" / "index.js"
            
            output_path = output_dir / "pcb"
            
            result = subprocess.run(
                ["node", str(cli_path), "--output", str(output_path)],
                cwd=self.ts_router_dir,
                input=json.dumps(router_input),
                capture_output=True,
                text=True,
                check=False,  # Don't raise on non-zero exit
                shell=True
            )
            
            # Parse JSON result from stdout
            if result.stdout.strip():
                try:
                    routing_result = json.loads(result.stdout)
                    return {
                        "success": routing_result.get("success", False),
                        "output_dir": str(output_dir),
                        "traces": routing_result.get("traces", []),
                        "failed_nets": routing_result.get("failedNets", [])
                    }
                except json.JSONDecodeError:
                    pass
            
            # Fallback if JSON parsing fails
            print(f"TS Router output:\n{result.stdout}")
            if result.stderr:
                print(f"TS Router stderr:\n{result.stderr}")
            
            return {
                "success": result.returncode == 0,
                "output_dir": str(output_dir),
                "traces": [],
                "failed_nets": []
            }
            
        except subprocess.CalledProcessError as e:
            print(f"TS Router failed: {e}")
            print(f"STDERR: {e.stderr}")
            return {
                "success": False,
                "error": str(e),
                "stderr": e.stderr
            }
        except FileNotFoundError:
            print("Node.js not found. TS router requires Node.js to be installed.")
            return {
                "success": False,
                "error": "Node.js not found"
            }
    
    def route_from_file(self, pcb_layout_path: Path, output_dir: Path) -> dict:
        """Route from a pcb_layout.json file."""
        pcb_layout = json.loads(pcb_layout_path.read_text(encoding="utf-8"))
        return self.route(pcb_layout, output_dir)


def integrate_routing_into_layout(pcb_layout: dict, routing_result: dict) -> dict:
    """
    Add routing information to the PCB layout.
    
    This enriches the pcb_layout with trace paths from the router.
    """
    if not routing_result.get("success"):
        return pcb_layout
    
    # Add traces to layout
    pcb_layout["traces"] = routing_result.get("traces", [])
    pcb_layout["routing_metadata"] = {
        "routed": True,
        "failed_nets": routing_result.get("failed_nets", [])
    }
    
    return pcb_layout

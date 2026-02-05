"""
TypeScript PCB Router Bridge

Single clean interface to the TypeScript PCB router CLI.
No Python fallback - errors are raised if the TS router fails.
"""

from __future__ import annotations
import json
import subprocess
from pathlib import Path
from typing import Dict, List


# ATMega328P-PU 28-pin DIP Pinout
# Pin numbers go counter-clockwise from top-left (notch at top)
ATMEGA328P_PINOUT = {
    1: ("PC6", "RESET", ["Digital I/O"]),
    2: ("PD0", "RXD", ["USART0 RX", "Digital I/O"]),
    3: ("PD1", "TXD", ["USART0 TX", "Digital I/O"]),
    4: ("PD2", "INT0", ["External Interrupt 0", "Digital I/O"]),
    5: ("PD3", "INT1", ["External Interrupt 1", "PWM", "Digital I/O"]),
    6: ("PD4", "XCK/T0", ["Timer0 External Clock", "Digital I/O"]),
    7: ("VCC", "VCC", ["Supply Voltage"]),
    8: ("GND", "GND", ["Ground"]),
    9: ("PB6", "XTAL1", ["Crystal Oscillator", "Digital I/O"]),
    10: ("PB7", "XTAL2", ["Crystal Oscillator", "Digital I/O"]),
    11: ("PD5", "T1", ["Timer1 Input", "PWM", "Digital I/O"]),
    12: ("PD6", "AIN0", ["Analog Comparator", "PWM", "Digital I/O"]),
    13: ("PD7", "AIN1", ["Analog Comparator", "Digital I/O"]),
    14: ("PB0", "ICP1", ["Timer1 Input Capture", "Digital I/O"]),
    15: ("PB1", "OC1A", ["PWM", "Digital I/O"]),
    16: ("PB2", "SS/OC1B", ["SPI Slave Select", "PWM", "Digital I/O"]),
    17: ("PB3", "MOSI/OC2A", ["SPI MOSI", "PWM", "Digital I/O"]),
    18: ("PB4", "MISO", ["SPI MISO", "Digital I/O"]),
    19: ("PB5", "SCK", ["SPI Clock", "Digital I/O"]),
    20: ("AVCC", "AVCC", ["ADC Supply Voltage"]),
    21: ("AREF", "AREF", ["ADC Reference Voltage"]),
    22: ("GND", "GND", ["Ground"]),
    23: ("PC0", "ADC0", ["Analog Input 0", "Digital I/O"]),
    24: ("PC1", "ADC1", ["Analog Input 1", "Digital I/O"]),
    25: ("PC2", "ADC2", ["Analog Input 2", "Digital I/O"]),
    26: ("PC3", "ADC3", ["Analog Input 3", "Digital I/O"]),
    27: ("PC4", "ADC4/SDA", ["Analog Input 4", "I2C Data", "Digital I/O"]),
    28: ("PC5", "ADC5/SCL", ["Analog Input 5", "I2C Clock", "Digital I/O"]),
}


class RouterError(Exception):
    """Raised when the PCB router fails."""
    pass


class RouterNotFoundError(RouterError):
    """Raised when the TypeScript CLI is not found."""
    pass


class RoutingFailedError(RouterError):
    """Raised when routing fails for one or more nets."""
    def __init__(self, message: str, failed_nets: List[Dict]):
        super().__init__(message)
        self.failed_nets = failed_nets


class TSPCBRouter:
    """
    Bridge to the TypeScript PCB router CLI.
    
    The TS router handles:
    - Grid-based A* pathfinding for traces
    - Net extraction and MST optimization  
    - DRC-aware routing with clearances
    - Rip-up and reroute for failed nets
    """
    
    def __init__(self, ts_router_dir: Path = None):
        if ts_router_dir is None:
            ts_router_dir = Path(__file__).parent.parent / "pcb"
        self.ts_router_dir = ts_router_dir
        self._cli_path = None
    
    @property
    def cli_path(self) -> Path:
        """Get the CLI path, building if necessary."""
        if self._cli_path is None:
            self._cli_path = self._find_or_build_cli()
        return self._cli_path
    
    def _find_or_build_cli(self) -> Path:
        """Find the CLI or build it if needed."""
        cli_path = self.ts_router_dir / "dist" / "cli.js"
        
        if not cli_path.exists():
            print("[TSPCBRouter] CLI not found, building...")
            self._build()
            
        if not cli_path.exists():
            raise RouterNotFoundError(
                f"TypeScript router CLI not found at {cli_path}. "
                "Run 'npm install && npm run build' in src/pcb/"
            )
        
        return cli_path
    
    def _build(self) -> None:
        """Build the TypeScript router."""
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
            print("[TSPCBRouter] Build completed")
        except subprocess.CalledProcessError as e:
            raise RouterError(f"Failed to build TypeScript router: {e.stderr.decode() if e.stderr else str(e)}")
    
    def route(self, pcb_layout: dict, output_dir: Path) -> dict:
        """
        Route traces for the given PCB layout.
        
        Args:
            pcb_layout: PCB layout dict with board, components, etc.
            output_dir: Directory for output files
            
        Returns:
            Dict with 'success', 'traces', 'failed_nets'
            
        Raises:
            RouterNotFoundError: If CLI not found
            RouterError: If routing fails completely
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Convert to TS router format
        router_input = self._convert_layout(pcb_layout)
        
        # Save input for debugging
        input_file = output_dir / "ts_router_input.json"
        input_file.write_text(json.dumps(router_input, indent=2), encoding="utf-8")
        
        # Run CLI
        try:
            result = subprocess.run(
                ["node", str(self.cli_path), "--output", str(output_dir / "pcb")],
                cwd=self.ts_router_dir,
                input=json.dumps(router_input),
                capture_output=True,
                text=True,
                check=False,
                shell=True
            )
        except FileNotFoundError:
            raise RouterNotFoundError("Node.js not found. Install Node.js to use the PCB router.")
        
        # Save raw output
        (output_dir / "ts_router_stdout.txt").write_text(result.stdout or "", encoding="utf-8")
        (output_dir / "ts_router_stderr.txt").write_text(result.stderr or "", encoding="utf-8")
        
        # Parse result
        if not result.stdout.strip():
            raise RouterError(f"Router produced no output. stderr: {result.stderr}")
        
        try:
            routing_result = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise RouterError(f"Failed to parse router output: {e}\nOutput: {result.stdout[:500]}")
        
        # Save parsed result
        (output_dir / "ts_router_result.json").write_text(
            json.dumps(routing_result, indent=2), encoding="utf-8"
        )
        
        return {
            "success": routing_result.get("success", False),
            "traces": routing_result.get("traces", []),
            "failed_nets": routing_result.get("failedNets", [])
        }
    
    def _convert_layout(self, pcb_layout: dict) -> dict:
        """Convert pcb_layout to TS router input format."""
        # Extract board dimensions
        outline = pcb_layout["board"]["outline_polygon"]
        xs = [p[0] for p in outline]
        ys = [p[1] for p in outline]
        board_width = max(xs) - min(xs)
        board_height = max(ys) - min(ys)
        
        # Count buttons and LEDs for controller pin assignment
        components = pcb_layout.get("components", [])
        total_buttons = sum(1 for c in components if c.get("type") == "button")
        led_components = [c for c in components if c.get("type") == "led"]
        
        # Build placement lists
        buttons = []
        controllers = []
        batteries = []
        diodes = []
        
        for comp in components:
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
                controllers.append({
                    "id": comp_id,
                    "x": x,
                    "y": y,
                    "pins": self._generate_controller_pins(total_buttons, led_components)
                })
            elif comp_type == "battery":
                batteries.append({"id": comp_id, "x": x, "y": y})
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
                "button": {"pinSpacingX": 14.5, "pinSpacingY": 5.0},
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
    
    def _generate_controller_pins(self, button_count: int, led_components: List[dict] = None) -> Dict[str, str]:
        """Generate controller pin assignments for buttons and LEDs."""
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
        
        pin_index = 0
        
        # Assign pins to buttons first
        for i in range(button_count):
            if pin_index < len(io_pins):
                pins[io_pins[pin_index]] = f"SW{i+1}_SIG"
                pin_index += 1
        
        # Assign pins to LEDs
        if led_components:
            for led in led_components:
                if pin_index < len(io_pins):
                    led_id = led.get("id", f"LED{pin_index}")
                    pins[io_pins[pin_index]] = f"{led_id}_SIG"
                    pin_index += 1
        
        # Mark remaining pins as NC
        while pin_index < len(io_pins):
            pins[io_pins[pin_index]] = "NC"
            pin_index += 1
        
        # Unused pins
        for pin in ["PC6", "PB6", "PB7"]:
            pins[pin] = "NC"
        
        return pins


def route_pcb(pcb_layout: dict, output_dir: Path) -> dict:
    """
    Convenience function to route a PCB layout.
    
    Args:
        pcb_layout: PCB layout dict
        output_dir: Output directory for files
        
    Returns:
        Routing result dict
        
    Raises:
        RouterError: If routing fails
    """
    router = TSPCBRouter()
    return router.route(pcb_layout, output_dir)

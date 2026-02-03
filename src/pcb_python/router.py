"""
PCB Router - A* pathfinding for single-layer PCB trace routing.

Mirrors the TypeScript implementation in src/pcb/src/router.ts
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Set
from enum import Enum
import heapq
import math


class CellState(Enum):
    FREE = 0
    BLOCKED = 1


@dataclass
class GridCoordinate:
    x: int
    y: int
    
    def __hash__(self):
        return hash((self.x, self.y))
    
    def __eq__(self, other):
        if isinstance(other, GridCoordinate):
            return self.x == other.x and self.y == other.y
        return False


@dataclass
class Pad:
    component_id: str
    pin_name: str
    center: GridCoordinate
    net: str
    component_center: Optional[GridCoordinate] = None


@dataclass
class Net:
    name: str
    source: GridCoordinate
    sink: GridCoordinate
    net_type: str  # 'GND', 'VCC', or 'SIGNAL'


@dataclass
class Trace:
    net: str
    path: List[GridCoordinate]


@dataclass
class FailedNet:
    net_name: str
    source_pin: str
    destination_pin: str
    reason: str


@dataclass
class RoutingResult:
    success: bool
    traces: List[Trace]
    failed_nets: List[FailedNet]


# ATMega328P-PU 28-pin DIP Pinout
# Pin numbers go counter-clockwise from top-left (notch at top)
ATMEGA328P_PINOUT = {
    # Pin Number: (Pin Name, Primary Function, Alternate Functions)
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

# Arduino Uno pin mapping (Digital pin -> ATMega328P pin number)
ARDUINO_TO_ATMEGA = {
    "D0": 2,   # PD0 (RXD)
    "D1": 3,   # PD1 (TXD)
    "D2": 4,   # PD2 (INT0)
    "D3": 5,   # PD3 (INT1/PWM)
    "D4": 6,   # PD4
    "D5": 11,  # PD5 (PWM)
    "D6": 12,  # PD6 (PWM)
    "D7": 13,  # PD7
    "D8": 14,  # PB0
    "D9": 15,  # PB1 (PWM)
    "D10": 16, # PB2 (PWM/SS)
    "D11": 17, # PB3 (PWM/MOSI)
    "D12": 18, # PB4 (MISO)
    "D13": 19, # PB5 (SCK)
    "A0": 23,  # PC0 (ADC0)
    "A1": 24,  # PC1 (ADC1)
    "A2": 25,  # PC2 (ADC2)
    "A3": 26,  # PC3 (ADC3)
    "A4": 27,  # PC4 (ADC4/SDA)
    "A5": 28,  # PC5 (ADC5/SCL)
    "RESET": 1,
    "VCC": 7,
    "GND1": 8,
    "GND2": 22,
    "AVCC": 20,
    "AREF": 21,
}


@dataclass
class Footprints:
    """Component footprint dimensions in mm."""
    button_pin_spacing_x: float = 6.5  # Tactile switch pin spacing X
    button_pin_spacing_y: float = 4.5  # Tactile switch pin spacing Y
    controller_pin_spacing: float = 2.54  # DIP-28 pin spacing (0.1 inch)
    controller_row_spacing: float = 7.62  # DIP-28 row spacing (0.3 inch = 300 mil)
    controller_pin_count: int = 28  # ATMega328P-PU DIP-28
    battery_pad_spacing: float = 25.0  # Distance between battery terminals
    diode_pad_spacing: float = 5.0


@dataclass
class ManufacturingConstraints:
    trace_width: float = 0.8  # mm
    trace_clearance: float = 0.4  # mm


@dataclass
class BoardParameters:
    board_width: float  # mm
    board_height: float  # mm
    grid_resolution: float = 1.0  # mm per cell


class Grid:
    """Grid-based representation of PCB for routing."""
    
    def __init__(self, board: BoardParameters, manufacturing: ManufacturingConstraints):
        self.resolution = board.grid_resolution
        self.width = int(math.ceil(board.board_width / board.grid_resolution))
        self.height = int(math.ceil(board.board_height / board.grid_resolution))
        
        # Calculate blocked radius based on trace width and clearance
        self.blocked_radius = int(math.ceil(
            (manufacturing.trace_width / 2 + manufacturing.trace_clearance) / board.grid_resolution
        ))
        
        # Initialize all cells as free
        self.cells: List[List[CellState]] = [
            [CellState.FREE for _ in range(self.width)]
            for _ in range(self.height)
        ]
        
        # Block board edges
        self._block_board_edges()
    
    def _block_board_edges(self):
        """Block cells near board edges."""
        for x in range(self.width):
            for r in range(self.blocked_radius):
                if r < self.height:
                    self.cells[r][x] = CellState.BLOCKED
                if self.height - 1 - r >= 0:
                    self.cells[self.height - 1 - r][x] = CellState.BLOCKED
        
        for y in range(self.height):
            for r in range(self.blocked_radius):
                if r < self.width:
                    self.cells[y][r] = CellState.BLOCKED
                if self.width - 1 - r >= 0:
                    self.cells[y][self.width - 1 - r] = CellState.BLOCKED
    
    def world_to_grid(self, world_x: float, world_y: float) -> GridCoordinate:
        """Convert world coordinates (mm) to grid coordinates."""
        return GridCoordinate(
            x=int(world_x / self.resolution),
            y=int(world_y / self.resolution)
        )
    
    def grid_to_world(self, grid_x: int, grid_y: int) -> Tuple[float, float]:
        """Convert grid coordinates to world coordinates (mm)."""
        return (
            (grid_x + 0.5) * self.resolution,
            (grid_y + 0.5) * self.resolution
        )
    
    def is_in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height
    
    def is_free(self, x: int, y: int) -> bool:
        if not self.is_in_bounds(x, y):
            return False
        return self.cells[y][x] == CellState.FREE
    
    def is_blocked(self, x: int, y: int) -> bool:
        if not self.is_in_bounds(x, y):
            return True
        return self.cells[y][x] == CellState.BLOCKED
    
    def block_cell(self, x: int, y: int):
        if self.is_in_bounds(x, y):
            self.cells[y][x] = CellState.BLOCKED
    
    def free_cell(self, x: int, y: int):
        if self.is_in_bounds(x, y):
            self.cells[y][x] = CellState.FREE
    
    def block_area(self, center_x: int, center_y: int, radius: int):
        """Block a square area around a center point."""
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                self.block_cell(center_x + dx, center_y + dy)
    
    def block_circular_area(self, center_x: int, center_y: int, radius: int):
        """Block a circular area around a center point."""
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx * dx + dy * dy <= radius * radius:
                    self.block_cell(center_x + dx, center_y + dy)
    
    def block_trace_path(self, path: List[GridCoordinate], padding: int):
        """Block cells along a trace path with padding for clearance."""
        for coord in path:
            for dy in range(-padding, padding + 1):
                for dx in range(-padding, padding + 1):
                    self.block_cell(coord.x + dx, coord.y + dy)


class Pathfinder:
    """A* pathfinding with L-shaped route preference."""
    
    def __init__(self, grid: Grid):
        self.grid = grid
    
    def find_path(
        self, 
        source: GridCoordinate, 
        sink: GridCoordinate
    ) -> Optional[List[GridCoordinate]]:
        """Find path from source to sink using L-shaped routes or A*."""
        if not self.grid.is_in_bounds(source.x, source.y):
            return None
        if not self.grid.is_in_bounds(sink.x, sink.y):
            return None
        
        if source == sink:
            return [source]
        
        # Try L-shaped routes first (faster)
        path = self._try_l_shaped_route(source, sink)
        if path:
            return path
        
        # Fall back to A*
        return self._a_star(source, sink)
    
    def _try_l_shaped_route(
        self, 
        source: GridCoordinate, 
        sink: GridCoordinate
    ) -> Optional[List[GridCoordinate]]:
        """Try simple L-shaped routes (horizontal then vertical, or vice versa)."""
        # Try horizontal first
        path = self._try_horizontal_then_vertical(source, sink)
        if path:
            return path
        
        # Try vertical first
        path = self._try_vertical_then_horizontal(source, sink)
        if path:
            return path
        
        return None
    
    def _try_horizontal_then_vertical(
        self, 
        source: GridCoordinate, 
        sink: GridCoordinate
    ) -> Optional[List[GridCoordinate]]:
        path = []
        dx = 1 if sink.x > source.x else -1
        dy = 1 if sink.y > source.y else -1
        
        x, y = source.x, source.y
        path.append(GridCoordinate(x, y))
        
        # Move horizontally
        while x != sink.x:
            x += dx
            if not self.grid.is_free(x, y):
                return None
            path.append(GridCoordinate(x, y))
        
        # Move vertically
        while y != sink.y:
            y += dy
            if not self.grid.is_free(x, y):
                return None
            path.append(GridCoordinate(x, y))
        
        return path
    
    def _try_vertical_then_horizontal(
        self, 
        source: GridCoordinate, 
        sink: GridCoordinate
    ) -> Optional[List[GridCoordinate]]:
        path = []
        dx = 1 if sink.x > source.x else -1
        dy = 1 if sink.y > source.y else -1
        
        x, y = source.x, source.y
        path.append(GridCoordinate(x, y))
        
        # Move vertically
        while y != sink.y:
            y += dy
            if not self.grid.is_free(x, y):
                return None
            path.append(GridCoordinate(x, y))
        
        # Move horizontally
        while x != sink.x:
            x += dx
            if not self.grid.is_free(x, y):
                return None
            path.append(GridCoordinate(x, y))
        
        return path
    
    def _a_star(
        self, 
        source: GridCoordinate, 
        sink: GridCoordinate
    ) -> Optional[List[GridCoordinate]]:
        """A* pathfinding with Manhattan distance and turn penalty."""
        
        # Priority queue: (f_score, counter, x, y, g_score, parent_key, direction)
        counter = 0
        open_set = []
        
        start_h = self._manhattan_distance(source, sink)
        heapq.heappush(open_set, (start_h, counter, source.x, source.y, 0, None, None))
        counter += 1
        
        closed_set: Set[Tuple[int, int]] = set()
        g_scores: Dict[Tuple[int, int], int] = {(source.x, source.y): 0}
        came_from: Dict[Tuple[int, int], Optional[Tuple[int, int]]] = {(source.x, source.y): None}
        
        directions = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        
        while open_set:
            _, _, x, y, g, parent, direction = heapq.heappop(open_set)
            
            if x == sink.x and y == sink.y:
                # Reconstruct path
                return self._reconstruct_path(came_from, (x, y))
            
            key = (x, y)
            if key in closed_set:
                continue
            closed_set.add(key)
            
            for dx, dy in directions:
                nx, ny = x + dx, y + dy
                neighbor_key = (nx, ny)
                
                if not self.grid.is_in_bounds(nx, ny):
                    continue
                
                # Allow sink even if blocked
                if not self.grid.is_free(nx, ny) and (nx, ny) != (sink.x, sink.y):
                    continue
                
                if neighbor_key in closed_set:
                    continue
                
                # Turn penalty
                new_dir = (dx, dy)
                turn_penalty = 10 if direction is not None and direction != new_dir else 0
                
                tentative_g = g + 1 + turn_penalty
                
                if neighbor_key not in g_scores or tentative_g < g_scores[neighbor_key]:
                    g_scores[neighbor_key] = tentative_g
                    came_from[neighbor_key] = key
                    h = self._manhattan_distance(GridCoordinate(nx, ny), sink)
                    f = tentative_g + h
                    heapq.heappush(open_set, (f, counter, nx, ny, tentative_g, key, new_dir))
                    counter += 1
        
        return None
    
    def _manhattan_distance(self, a: GridCoordinate, b: GridCoordinate) -> int:
        return abs(a.x - b.x) + abs(a.y - b.y)
    
    def _reconstruct_path(
        self, 
        came_from: Dict[Tuple[int, int], Optional[Tuple[int, int]]], 
        current: Tuple[int, int]
    ) -> List[GridCoordinate]:
        path = []
        while current is not None:
            path.append(GridCoordinate(current[0], current[1]))
            current = came_from[current]
        path.reverse()
        return path


class PCBRouter:
    """
    PCB Router using grid-based A* pathfinding.
    
    Mirrors the TypeScript implementation.
    """
    
    def __init__(
        self,
        board: BoardParameters,
        manufacturing: ManufacturingConstraints,
        footprints: Footprints
    ):
        self.board = board
        self.manufacturing = manufacturing
        self.footprints = footprints
        self.grid = Grid(board, manufacturing)
        self.pathfinder = Pathfinder(self.grid)
        
        self.pads: Dict[str, Pad] = {}
        self.nets: List[Net] = []
        self.traces: List[Trace] = []
        self.failed_nets: List[FailedNet] = []
        
        # Trace padding for clearance
        self.trace_padding = int(math.ceil(
            manufacturing.trace_clearance / board.grid_resolution
        ))
    
    def block_component_area(self, x: float, y: float, width: float, height: float, clearance: float = 1.0):
        """
        Block a rectangular area for a component so traces route around it.
        
        Args:
            x, y: Center of component in world coordinates (mm)
            width, height: Component dimensions (mm)
            clearance: Extra clearance around component (mm)
        """
        # Convert to grid coordinates
        half_w = (width + clearance * 2) / 2
        half_h = (height + clearance * 2) / 2
        
        # Block the rectangular area
        min_coord = self.grid.world_to_grid(x - half_w, y - half_h)
        max_coord = self.grid.world_to_grid(x + half_w, y + half_h)
        
        for gy in range(min_coord.y, max_coord.y + 1):
            for gx in range(min_coord.x, max_coord.x + 1):
                self.grid.block_cell(gx, gy)
    
    def place_button(self, button_id: str, x: float, y: float, signal_net: str):
        """Place a tactile button with 4 pins and block its area."""
        center = self.grid.world_to_grid(x, y)
        half_x = self.footprints.button_pin_spacing_x / 2
        half_y = self.footprints.button_pin_spacing_y / 2
        
        # Block the button body area (6x6mm tactile switch)
        button_size = 6.0
        self.block_component_area(x, y, button_size, button_size, clearance=0.5)
        
        # Tactile switch wiring for INPUT_PULLUP configuration:
        # - A1/A2 (left side) are internally connected → connect to MCU input
        # - B1/B2 (right side) are internally connected → connect to GND
        # When pressed, switch bridges A side to B side, pulling input LOW
        
        # Pin A1: signal to MCU (bottom-left) - only need one from A side
        a1 = self.grid.world_to_grid(x - half_x, y - half_y)
        self.pads[f"{button_id}.A1"] = Pad(
            component_id=button_id,
            pin_name="A1",
            center=a1,
            net=signal_net,
            component_center=center
        )
        
        # Pin A2: internally connected to A1, not connected to net (top-left)
        a2 = self.grid.world_to_grid(x - half_x, y + half_y)
        self.pads[f"{button_id}.A2"] = Pad(
            component_id=button_id,
            pin_name="A2",
            center=a2,
            net="NC",
            component_center=center
        )
        
        # Pin B1: GND (bottom-right) - only need one from B side for GND
        b1 = self.grid.world_to_grid(x + half_x, y - half_y)
        self.pads[f"{button_id}.B1"] = Pad(
            component_id=button_id,
            pin_name="B1",
            center=b1,
            net="GND",  # Changed from VCC to GND for INPUT_PULLUP
            component_center=center
        )
        
        # Pin B2: internally connected to B1, not connected to net (top-right)
        b2 = self.grid.world_to_grid(x + half_x, y + half_y)
        self.pads[f"{button_id}.B2"] = Pad(
            component_id=button_id,
            pin_name="B2",
            center=b2,
            net="NC",
            component_center=center
        )
    
    def place_controller(self, ctrl_id: str, x: float, y: float, pins: Dict[str, str]):
        """
        Place ATMega328P-PU 28-pin DIP controller.
        
        DIP-28 Layout (notch at top, pin 1 top-left):
        - Pins 1-14 on left side (top to bottom)
        - Pins 15-28 on right side (bottom to top)
        - Pin spacing: 2.54mm (0.1")
        - Row spacing: 7.62mm (0.3")
        """
        center = self.grid.world_to_grid(x, y)
        
        row_spacing = self.footprints.controller_row_spacing  # 7.62mm
        pin_spacing = self.footprints.controller_pin_spacing  # 2.54mm
        pins_per_side = 14  # DIP-28 has 14 pins per side
        total_height = (pins_per_side - 1) * pin_spacing  # 33.02mm
        
        # Block the IC body area (between the pin rows)
        body_width = 6.35  # DIP-28 body width
        self.block_component_area(x, y, body_width, total_height, clearance=0.5)
        
        # Place all 28 pins according to ATMega328P-PU pinout
        for pin_number in range(1, 29):
            # Get the pin info from the pinout table
            pin_info = ATMEGA328P_PINOUT.get(pin_number)
            if not pin_info:
                continue
            
            port_name, function_name, _ = pin_info
            
            # Calculate pin position
            if pin_number <= 14:
                # Left side: pins 1-14 from top to bottom
                pin_x = x - row_spacing / 2
                pin_y = y + total_height / 2 - (pin_number - 1) * pin_spacing
            else:
                # Right side: pins 15-28 from bottom to top
                pin_x = x + row_spacing / 2
                right_idx = pin_number - 15  # 0 for pin 15, 13 for pin 28
                pin_y = y - total_height / 2 + right_idx * pin_spacing
            
            pad_center = self.grid.world_to_grid(pin_x, pin_y)
            
            # Determine net assignment
            # First check if user specified a mapping for this pin
            net = "NC"  # Default to not connected
            
            # Check various possible key formats in the pins dict
            possible_keys = [
                port_name,           # e.g., "PD2"
                function_name,       # e.g., "INT0"
                f"PIN{pin_number}",  # e.g., "PIN4"
                f"D{pin_number-2}" if 2 <= pin_number <= 13 else None,  # Arduino digital pins
            ]
            
            for key in possible_keys:
                if key and key in pins:
                    net = pins[key]
                    break
            
            # Special handling for power pins
            if port_name == "VCC" and "VCC" in pins:
                net = pins["VCC"]
            elif port_name == "AVCC" and ("AVCC" in pins or "VCC" in pins):
                net = pins.get("AVCC", pins.get("VCC", "VCC"))
            elif port_name == "GND":
                net = pins.get("GND", "GND")
            elif port_name == "AREF" and "AREF" in pins:
                net = pins["AREF"]
            
            # Create unique pad identifier
            pad_key = f"{ctrl_id}.{port_name}_{pin_number}"
            
            self.pads[pad_key] = Pad(
                component_id=ctrl_id,
                pin_name=f"{port_name} ({function_name})",
                center=pad_center,
                net=net,
                component_center=center
            )
    
    def place_battery(self, bat_id: str, x: float, y: float, width: float = 12.0, height: float = 45.0):
        """Place a battery with VCC and GND terminals, blocking its area."""
        center = self.grid.world_to_grid(x, y)
        
        # Block the battery holder area
        self.block_component_area(x, y, width, height, clearance=1.0)
        
        # Place terminals at top (+) and bottom (-) of battery
        half_height = height / 2
        
        # VCC terminal (positive, at top)
        vcc_center = self.grid.world_to_grid(x, y + half_height - 5)
        self.pads[f"{bat_id}.VCC"] = Pad(
            component_id=bat_id,
            pin_name="VCC",
            center=vcc_center,
            net="VCC",
            component_center=center
        )
        
        # GND terminal (negative, at bottom)
        gnd_center = self.grid.world_to_grid(x, y - half_height + 5)
        self.pads[f"{bat_id}.GND"] = Pad(
            component_id=bat_id,
            pin_name="GND",
            center=gnd_center,
            net="GND",
            component_center=center
        )
    
    def _unblock_all_pads(self):
        """Unblock cells at pad locations so traces can reach them."""
        for pad in self.pads.values():
            self.grid.free_cell(pad.center.x, pad.center.y)
            # Also free a small area around the pad for routing access
            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    self.grid.free_cell(pad.center.x + dx, pad.center.y + dy)
    
    def extract_nets(self):
        """Extract nets by grouping pads with the same net name and computing MST."""
        net_map: Dict[str, List[Pad]] = {}
        
        for pad in self.pads.values():
            if pad.net == "NC":
                continue
            if pad.net not in net_map:
                net_map[pad.net] = []
            net_map[pad.net].append(pad)
        
        self.nets = []
        for net_name, pads in net_map.items():
            if len(pads) < 2:
                continue
            
            net_type = "SIGNAL"
            if net_name == "VCC":
                net_type = "VCC"
            elif net_name == "GND":
                net_type = "GND"
            
            # Use MST to connect pads efficiently (matches TypeScript implementation)
            mst_edges = self._compute_mst(pads)
            for source, sink in mst_edges:
                self.nets.append(Net(
                    name=net_name,
                    source=source,
                    sink=sink,
                    net_type=net_type
                ))
    
    def _compute_mst(self, pads: List[Pad]) -> List[Tuple[GridCoordinate, GridCoordinate]]:
        """Compute Minimum Spanning Tree for connecting pads using Kruskal's algorithm."""
        if len(pads) < 2:
            return []
        
        # Build edges with weights (Manhattan distance)
        edges = []
        for i in range(len(pads)):
            for j in range(i + 1, len(pads)):
                weight = (abs(pads[i].center.x - pads[j].center.x) + 
                         abs(pads[i].center.y - pads[j].center.y))
                edges.append((weight, i, j))
        
        # Sort by weight
        edges.sort()
        
        # Union-Find for Kruskal's
        parent = list(range(len(pads)))
        
        def find(x: int) -> int:
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]
        
        def union(x: int, y: int) -> bool:
            px, py = find(x), find(y)
            if px == py:
                return False
            parent[px] = py
            return True
        
        # Build MST
        result = []
        for weight, i, j in edges:
            if union(i, j):
                result.append((pads[i].center, pads[j].center))
            if len(result) == len(pads) - 1:
                break
        
        return result
    
    def route(self) -> RoutingResult:
        """Route all nets."""
        # Unblock all pad locations so traces can reach them
        self._unblock_all_pads()
        
        self.extract_nets()
        
        # Sort nets: SIGNAL first, then GND, then VCC (matches TypeScript implementation)
        # Within same type, sort by Manhattan distance (shorter routes first)
        def net_priority(net: Net) -> Tuple[int, int]:
            type_priority = {"SIGNAL": 0, "GND": 1, "VCC": 2}
            distance = abs(net.source.x - net.sink.x) + abs(net.source.y - net.sink.y)
            return (type_priority.get(net.net_type, 0), distance)
        
        sorted_nets = sorted(self.nets, key=net_priority)
        
        for net in sorted_nets:
            path = self.pathfinder.find_path(net.source, net.sink)
            
            if path:
                self.traces.append(Trace(net=net.name, path=path))
                # Block the path for subsequent routes
                self.grid.block_trace_path(path, self.trace_padding)
            else:
                self.failed_nets.append(FailedNet(
                    net_name=net.name,
                    source_pin=f"({net.source.x}, {net.source.y})",
                    destination_pin=f"({net.sink.x}, {net.sink.y})",
                    reason="No path found"
                ))
        
        return RoutingResult(
            success=len(self.failed_nets) == 0,
            traces=self.traces,
            failed_nets=self.failed_nets
        )
    
    def get_pads(self) -> Dict[str, Pad]:
        return self.pads
    
    def get_traces(self) -> List[Trace]:
        return self.traces

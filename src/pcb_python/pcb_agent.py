"""
PCB Agent - Converts design_spec.json into pcb_layout.json

Responsibilities:
- Generate board outline from device constraints
- Place components (buttons, controller, battery, LEDs)
- Respect placement hints from design spec
- Add mounting holes
- Define keepout zones
- Apply fixes from feasibility reports
"""

from __future__ import annotations
from typing import Optional, Dict, List, Tuple
import json
import math

from src.core.hardware_config import board as hw_board, footprints as hw_footprints, manufacturing as hw_manufacturing

class PCBAgent:
    """
    PCB Agent generates component placement and board geometry.
    
    Input: design_spec.json
    Output: pcb_layout.json
    
    Placement strategy:
    1. Create board outline (device size minus enclosure wall clearance)
    2. Reserve areas for battery and controller
    3. Place high-priority buttons according to hints
    4. Place remaining buttons in optimal grid within available space
    5. Add mounting holes near corners (avoiding components)
    6. Define keepouts around all components
    """
    
    def __init__(self):
        brd = hw_board()
        self.enclosure_wall_clearance = brd["enclosure_wall_clearance_mm"]
        self.pcb_thickness = brd["pcb_thickness_mm"]
        self.mounting_hole_diameter = brd["mounting_hole_diameter_mm"]
        self.mounting_hole_inset = brd["mounting_hole_inset_mm"]
        self.component_margin = brd["component_margin_mm"]
    
    def generate_layout(
        self, 
        design_spec: dict,
        previous_feasibility_report: Optional[dict] = None
    ) -> dict:
        """
        Generate PCB layout from design spec.
        
        Args:
            design_spec: Validated design_spec.json
            previous_feasibility_report: If iterating, apply suggested fixes
        
        Returns:
            dict matching pcb_layout.schema.json
        """
        print("\n[PCB_AGENT] Generating PCB layout...")
        
        # Calculate board dimensions
        device = design_spec["device_constraints"]
        constraints = design_spec["constraints"]
        
        board_width = device["width_mm"] - (2 * self.enclosure_wall_clearance)
        board_length = device["length_mm"] - (2 * self.enclosure_wall_clearance)
        print(f"[PCB_AGENT] Board dimensions: {board_width}x{board_length}mm (from device {device['width_mm']}x{device['length_mm']}mm)")
        
        # Create board outline
        outline = self._create_board_outline(board_width, board_length)
        
        # Calculate usable regions
        edge_clearance = constraints["edge_clearance_mm"]
        min_spacing = constraints["min_button_spacing_mm"]
        print(f"[PCB_AGENT] Constraints: edge_clearance={edge_clearance}mm, min_spacing={min_spacing}mm")
        
        # Check if we have a previous report with fixes to apply
        fix_offsets = {}
        if previous_feasibility_report and not previous_feasibility_report.get("feasible", True):
            fix_offsets = self._extract_fix_offsets(previous_feasibility_report)
            print(f"[PCB_AGENT] PATH: ITERATION mode - applying {len(fix_offsets)} fixes from feasibility report")
            for comp_id, offset in fix_offsets.items():
                print(f"[PCB_AGENT]   Fix: {comp_id} offset by ({offset[0]}, {offset[1]})mm")
        else:
            print("[PCB_AGENT] PATH: INITIAL placement (no fixes to apply)")
        
        components = []
        reserved_regions = []
        
        # 1. Place battery first (reserves bottom region)
        print("[PCB_AGENT] Placing components...")
        if "battery" in design_spec:
            print(f"[PCB_AGENT]   1. Battery: {design_spec['battery'].get('type', 'unknown')}")
            battery_component, battery_region = self._place_battery_smart(
                design_spec["battery"],
                board_width,
                board_length,
                edge_clearance
            )
            # Apply any fixes for battery
            if battery_component["id"] in fix_offsets:
                dx, dy = fix_offsets[battery_component["id"]]
                battery_component["center"][0] += dx
                battery_component["center"][1] += dy
                # Clamp to valid position
                battery_component = self._clamp_to_board(battery_component, board_width, board_length, edge_clearance)
                print(f"[PCB_AGENT]      Applied fix offset ({dx}, {dy})mm")
            
            print(f"[PCB_AGENT]      Placed at: {battery_component['center']}")
            components.append(battery_component)
            reserved_regions.append(battery_region)
        else:
            print("[PCB_AGENT]   1. Battery: (none specified)")
        
        # 2. Place controller (reserves middle-bottom region)
        print("[PCB_AGENT]   2. Controller: ATmega328P")
        controller, controller_region = self._place_controller_smart(
            board_width,
            board_length,
            reserved_regions,
            edge_clearance
        )
        # Apply any fixes for controller
        if controller["id"] in fix_offsets:
            dx, dy = fix_offsets[controller["id"]]
            controller["center"][0] += dx
            controller["center"][1] += dy
            controller = self._clamp_to_board(controller, board_width, board_length, edge_clearance)
            print(f"[PCB_AGENT]      Applied fix offset ({dx}, {dy})mm")
        
        print(f"[PCB_AGENT]      Placed at: {controller['center']}")
        components.append(controller)
        reserved_regions.append(controller_region)
        
        # 3. Place LEDs at top
        led_count = len(design_spec.get("leds", []))
        if led_count > 0:
            print(f"[PCB_AGENT]   3. LEDs: {led_count} LED(s)")
        for led_spec in design_spec.get("leds", []):
            led_component = self._place_led(led_spec, board_width, board_length, edge_clearance)
            print(f"[PCB_AGENT]      LED {led_spec.get('id', '?')} at: {led_component['center']}")
            components.append(led_component)
        
        # 4. Place buttons intelligently - pass fix_offsets so buttons respect them
        button_count = len(design_spec["buttons"])
        print(f"[PCB_AGENT]   4. Buttons: {button_count} button(s)")
        button_components = self._place_buttons_smart(
            design_spec["buttons"],
            board_width,
            board_length,
            constraints,
            reserved_regions,
            fix_offsets
        )
        for btn in button_components:
            print(f"[PCB_AGENT]      {btn['id']} at: {btn['center']}")
        components.extend(button_components)
        
        # 5. Add mounting holes (avoiding all components)
        print("[PCB_AGENT]   5. Mounting holes...")
        mounting_holes = self._create_mounting_holes_smart(
            board_width,
            board_length,
            components,
            edge_clearance
        )
        print(f"[PCB_AGENT]      Created {len(mounting_holes)} mounting holes")
        
        total_components = len(components) + len(mounting_holes)
        print(f"[PCB_AGENT] ✓ Layout complete: {total_components} total elements")
        
        return {
            "board": {
                "outline_polygon": outline,
                "thickness_mm": self.pcb_thickness,
                "origin": "bottom_left"
            },
            "components": components,
            "mounting_holes": mounting_holes,
            "keepout_regions": [],
            "metadata": {
                "generated_timestamp": "",
                "preview_image_path": None
            }
        }
    
    def _extract_fix_offsets(self, feasibility_report: dict) -> Dict[str, Tuple[float, float]]:
        """Extract cumulative fix offsets from feasibility report."""
        offsets = {}
        
        for error in feasibility_report.get("errors", []):
            for fix in error.get("suggested_fixes", []):
                if fix.get("operation") == "translate":
                    comp_id = fix.get("id")
                    dx = fix.get("dx", 0)
                    dy = fix.get("dy", 0)
                    
                    if comp_id in offsets:
                        offsets[comp_id] = (offsets[comp_id][0] + dx, offsets[comp_id][1] + dy)
                    else:
                        offsets[comp_id] = (dx, dy)
        
        return offsets
    
    def _clamp_to_board(self, component: dict, board_width: float, board_length: float, edge_clearance: float) -> dict:
        """Clamp component to valid board position."""
        radius = self._get_keepout_radius(component)
        min_margin = edge_clearance + radius
        
        component["center"][0] = max(min_margin, min(board_width - min_margin, component["center"][0]))
        component["center"][1] = max(min_margin, min(board_length - min_margin, component["center"][1]))
        
        return component

    def _create_board_outline(self, width: float, length: float) -> list:
        """Create rectangular board outline."""
        return [
            [0, 0],
            [width, 0],
            [width, length],
            [0, length]
        ]
    
    def _place_battery_smart(
        self,
        battery_spec: dict,
        board_width: float,
        board_length: float,
        edge_clearance: float
    ) -> Tuple[dict, dict]:
        """Place battery and return reserved region."""
        battery_type = battery_spec.get("type", "2xAAA")
        
        # Battery dimensions from config (holder size)
        bat_fp = hw_footprints()["battery"]
        if battery_type == "2xAAA":
            battery_width = bat_fp["holder_width_mm"]
            battery_height = bat_fp["holder_height_mm"]
        elif battery_type == "CR2032":
            battery_width = 22.0
            battery_height = 22.0
        else:
            battery_width = 15.0
            battery_height = 30.0
        
        # Keepout padding from config
        padding = bat_fp["holder_padding_mm"]
        
        # Place at bottom center - ensure it fits within board
        x = board_width / 2
        y = edge_clearance + battery_height / 2 + padding
        
        # Clamp to board bounds
        min_x = edge_clearance + (battery_width + padding) / 2
        max_x = board_width - edge_clearance - (battery_width + padding) / 2
        min_y = edge_clearance + (battery_height + padding) / 2
        max_y = board_length - edge_clearance - (battery_height + padding) / 2
        
        x = max(min_x, min(max_x, x))
        y = max(min_y, min(max_y, y))
        
        component = {
            "id": "BAT1",
            "ref": "battery",
            "type": "battery",
            "footprint": battery_type,
            "center": [x, y],
            "rotation_deg": 0,
            "keepout": {
                "type": "rectangle",
                "width_mm": battery_width + padding * 2,
                "height_mm": battery_height + padding * 2
            }
        }
        
        # Reserved region for battery
        region = {
            "id": "battery_region",
            "y_min": 0,
            "y_max": y + battery_height / 2 + padding + 3,
            "x_min": 0,
            "x_max": board_width
        }
        
        return component, region
    
    def _place_controller_smart(
        self,
        board_width: float,
        board_length: float,
        reserved_regions: list,
        edge_clearance: float
    ) -> Tuple[dict, dict]:
        """Place ATMega328P-PU DIP-28 controller below button area."""
        ctrl_fp = hw_footprints()["controller"]
        controller_width = ctrl_fp["body_width_mm"]
        controller_height = ctrl_fp["body_height_mm"]
        
        # Find y position above battery region
        y_min_available = edge_clearance
        for region in reserved_regions:
            if region.get("y_max", 0) > y_min_available:
                y_min_available = region["y_max"]
        
        y = y_min_available + controller_height / 2 + 3
        x = board_width / 2
        
        component = {
            "id": "U1",
            "ref": "controller",
            "type": "controller",
            "footprint": ctrl_fp["type"],
            "center": [x, y],
            "rotation_deg": 0,
            "keepout": {
                "type": "rectangle",
                "width_mm": controller_width + ctrl_fp["keepout_padding_mm"],
                "height_mm": controller_height + ctrl_fp["keepout_padding_mm"]
            }
        }
        
        region = {
            "id": "controller_region",
            "y_min": y - controller_height / 2 - 2,
            "y_max": y + controller_height / 2 + 2,
            "x_min": x - controller_width / 2 - 2,
            "x_max": x + controller_width / 2 + 2
        }
        
        return component, region
    
    def _place_led(
        self,
        led_spec: dict,
        board_width: float,
        board_length: float,
        edge_clearance: float
    ) -> dict:
        """Place LED at top."""
        led_fp = hw_footprints()["led"]
        y = board_length - edge_clearance - 3
        x = board_width / 2
        
        return {
            "id": led_spec["id"],
            "ref": led_spec["id"],
            "type": "led",
            "footprint": led_fp["type"],
            "center": [x, y],
            "rotation_deg": 0,
            "keepout": {
                "type": "circle",
                "radius_mm": led_fp["keepout_radius_mm"]
            }
        }
    
    def _place_buttons_smart(
        self, 
        buttons: list, 
        board_width: float, 
        board_length: float,
        constraints: dict,
        reserved_regions: list,
        fix_offsets: Optional[Dict[str, Tuple[float, float]]] = None
    ) -> list:
        """
        Place buttons with intelligent layout.
        
        Strategy:
        1. Calculate actual space needed vs available
        2. Place buttons in a proper grid with guaranteed spacing
        3. Apply any fix offsets from previous iterations
        """
        if fix_offsets is None:
            fix_offsets = {}
            
        components = []
        
        # Margins and spacing
        edge_clearance = constraints["edge_clearance_mm"]
        min_spacing = constraints["min_button_spacing_mm"]
        
        # Find available Y range (above reserved regions)
        y_min_available = edge_clearance
        for region in reserved_regions:
            if region.get("y_max", 0) > y_min_available:
                y_min_available = region["y_max"]
        
        y_min_available += 3  # Extra margin above reserved regions
        y_max_available = board_length - edge_clearance
        
        # Available area for buttons
        available_height = y_max_available - y_min_available
        available_width = board_width - (2 * edge_clearance)
        
        # Button dimensions from config
        btn_fp = hw_footprints()["button"]
        button_hole_diam = btn_fp["min_hole_diameter_mm"]
        
        # Button pin footprint extends beyond the button hole
        button_pin_span_x = btn_fp["pin_spacing_x_mm"]  # Total X distance between left and right pins
        
        # Required center-to-center distance must account for pin footprint + routing clearance
        mfg = hw_manufacturing()
        routing_clearance = mfg["trace_width_mm"] + mfg["trace_clearance_mm"]  # gap needed between pin edges
        center_spacing = button_pin_span_x + routing_clearance
        
        keepout_radius = center_spacing / 2  # Half the required center-to-center distance
        
        # Calculate how many buttons fit
        num_buttons = len(buttons)
        
        # Calculate optimal grid
        max_cols = max(1, int((available_width + min_spacing) / center_spacing))
        max_rows = max(1, int((available_height + min_spacing) / center_spacing))
        
        # Find best grid layout
        if num_buttons <= max_cols:
            cols = num_buttons
            rows = 1
        elif num_buttons <= max_cols * 2:
            cols = min(max_cols, int(math.ceil(num_buttons / 2)))
            rows = int(math.ceil(num_buttons / cols))
        else:
            cols = min(max_cols, int(math.ceil(math.sqrt(num_buttons))))
            rows = int(math.ceil(num_buttons / cols))
        
        # Limit rows to available space
        rows = min(rows, max_rows)
        
        # Calculate grid dimensions
        grid_width = (cols - 1) * center_spacing if cols > 1 else 0
        grid_height = (rows - 1) * center_spacing if rows > 1 else 0
        
        # Center the grid in available space
        start_x = edge_clearance + (available_width - grid_width) / 2
        start_y = y_min_available + (available_height - grid_height) / 2
        
        # Place buttons in grid
        for i, btn_spec in enumerate(buttons):
            row = i // cols
            col = i % cols
            
            x = start_x + col * center_spacing
            y = start_y + row * center_spacing
            
            diam = btn_spec.get("cap_diameter_mm", button_hole_diam)
            
            # Create component ID
            comp_id = f"SW{i + 1}"
            
            # Apply any fix offsets from previous feasibility report
            if comp_id in fix_offsets:
                dx, dy = fix_offsets[comp_id]
                x += dx
                y += dy
            
            # Apply offsets from placement_hint in design spec
            hint = btn_spec.get("placement_hint", {})
            if "offset_x_mm" in hint:
                x += hint["offset_x_mm"]
            if "offset_y_mm" in hint:
                y += hint["offset_y_mm"]
            
            # Clamp to valid position
            min_x = edge_clearance + keepout_radius
            max_x = board_width - edge_clearance - keepout_radius
            min_y = y_min_available + keepout_radius
            max_y = y_max_available - keepout_radius
            
            x = max(min_x, min(max_x, x))
            y = max(min_y, min(max_y, y))
            
            component = self._create_button_component(
                comp_id,
                btn_spec,
                x, y, diam, min_spacing
            )
            components.append(component)
        
        return components
    
    def _get_hint_position(
        self,
        btn_spec: dict,
        board_width: float,
        board_length: float,
        edge_clearance: float,
        y_min: float,
        y_max: float
    ) -> Tuple[float, float]:
        """Convert placement hint to coordinates."""
        hint = btn_spec.get("placement_hint", {})
        diam = btn_spec.get("cap_diameter_mm", hw_footprints()["button"]["cap_diameter_mm"])
        
        # Default to center
        x = board_width / 2
        y = (y_min + y_max) / 2
        
        # Vertical region
        region = hint.get("region", "center")
        if region == "top":
            y = y_max - diam / 2 - 2
        elif region == "bottom":
            y = y_min + diam / 2 + 2
        elif region == "center":
            y = (y_min + y_max) / 2
        
        # Horizontal position
        horizontal = hint.get("horizontal", "center")
        if horizontal == "left":
            x = edge_clearance + diam / 2
        elif horizontal == "right":
            x = board_width - edge_clearance - diam / 2
        elif horizontal == "center":
            x = board_width / 2
        
        # Apply explicit offsets if provided
        offset_x = hint.get("offset_x_mm", 0)
        offset_y = hint.get("offset_y_mm", 0)
        x += offset_x
        y += offset_y
        
        # Clamp to valid range
        x = max(edge_clearance + diam / 2, min(x, board_width - edge_clearance - diam / 2))
        y = max(y_min + diam / 2, min(y, y_max - diam / 2))
        
        return x, y
    
    def _avoid_collision(
        self,
        x: float,
        y: float,
        diam: float,
        placed: List[Tuple[float, float, float]],
        edge_clearance: float,
        board_width: float,
        y_min: float,
        y_max: float,
        spacing: float
    ) -> Tuple[float, float]:
        """Adjust position to avoid collisions."""
        radius = diam / 2 + spacing
        max_attempts = 50
        
        for attempt in range(max_attempts):
            collision = False
            
            # Check edge constraints
            if x - radius < edge_clearance:
                x = edge_clearance + radius
            if x + radius > board_width - edge_clearance:
                x = board_width - edge_clearance - radius
            if y - radius < y_min:
                y = y_min + radius
            if y + radius > y_max:
                y = y_max - radius
            
            # Check collisions with placed components
            for px, py, pr in placed:
                dist = math.sqrt((x - px)**2 + (y - py)**2)
                min_dist = radius + pr + spacing
                
                if dist < min_dist:
                    collision = True
                    # Move away from collision
                    if dist > 0:
                        dx = (x - px) / dist
                        dy = (y - py) / dist
                    else:
                        dx, dy = 1, 0  # Default direction
                    
                    move = min_dist - dist + 1
                    x += dx * move
                    y += dy * move
                    break
            
            if not collision:
                break
        
        return x, y
    
    def _calculate_grid_positions(
        self,
        count: int,
        available_width: float,
        available_height: float,
        diam: float,
        spacing: float,
        edge_clearance: float,
        y_start: float
    ) -> List[Tuple[float, float]]:
        """Calculate grid positions for buttons."""
        positions = []
        
        # Calculate optimal grid dimensions
        cell_size = diam + spacing * 2
        
        max_cols = max(1, int(available_width / cell_size))
        max_rows = max(1, int(available_height / cell_size))
        
        # Find best grid that fits count
        cols = min(max_cols, max(1, int(math.ceil(math.sqrt(count)))))
        rows = int(math.ceil(count / cols))
        
        # Limit rows to available space
        rows = min(rows, max_rows)
        
        # Recalculate cols if needed
        if rows * cols < count:
            cols = min(max_cols, int(math.ceil(count / rows)))
        
        # Calculate actual grid size
        grid_width = cols * cell_size
        grid_height = rows * cell_size
        
        # Center the grid
        start_x = edge_clearance + (available_width - grid_width) / 2 + cell_size / 2
        start_y = y_start + (available_height - grid_height) / 2 + cell_size / 2
        
        # Generate positions
        for i in range(count):
            row = i // cols
            col = i % cols
            
            x = start_x + col * cell_size
            y = start_y + row * cell_size
            
            positions.append((x, y))
        
        return positions
    
    def _create_button_component(
        self,
        component_id: str,
        btn_spec: dict,
        x: float,
        y: float,
        diam: float,
        spacing: float
    ) -> dict:
        """Create a button component dict."""
        return {
            "id": component_id,
            "ref": btn_spec["id"],
            "type": "button",
            "footprint": btn_spec.get("switch_type", hw_footprints()["button"]["switch_type"]),
            "center": [x, y],
            "rotation_deg": 0,
            "keepout": {
                "type": "circle",
                "radius_mm": diam / 2 + spacing / 2
            }
        }
    
    def _create_mounting_holes_smart(
        self,
        board_width: float,
        board_length: float,
        components: list,
        edge_clearance: float
    ) -> list:
        """Create 4 mounting holes avoiding components."""
        inset = self.mounting_hole_inset
        hole_radius = self.mounting_hole_diameter / 2 + 3  # Plus clearance
        
        # Preferred corner positions
        corners = [
            (inset, inset),
            (board_width - inset, inset),
            (board_width - inset, board_length - inset),
            (inset, board_length - inset)
        ]
        
        holes = []
        
        for i, (target_x, target_y) in enumerate(corners):
            # Check for conflicts with components
            x, y = target_x, target_y
            
            for comp in components:
                cx, cy = comp["center"]
                comp_radius = self._get_keepout_radius(comp)
                
                dist = math.sqrt((x - cx)**2 + (y - cy)**2)
                min_dist = hole_radius + comp_radius + 2
                
                if dist < min_dist:
                    # Move hole along the edge away from component
                    if i in [0, 3]:  # Left edge
                        y = cy + (min_dist + 2) if y < cy else cy - (min_dist + 2)
                    else:  # Right edge
                        y = cy + (min_dist + 2) if y < cy else cy - (min_dist + 2)
            
            # Clamp to valid range
            x = max(inset, min(board_width - inset, x))
            y = max(inset, min(board_length - inset, y))
            
            holes.append({
                "id": f"MH{i + 1}",
                "center": [x, y],
                "drill_diameter_mm": self.mounting_hole_diameter,
                "pad_diameter_mm": self.mounting_hole_diameter + 2.0
            })
        
        return holes
    
    def _get_keepout_radius(self, component: dict) -> float:
        """Get effective keepout radius for a component."""
        keepout = component.get("keepout", {})
        
        if keepout.get("type") == "circle":
            return keepout.get("radius_mm", 5.0)
        elif keepout.get("type") == "rectangle":
            w = keepout.get("width_mm", 10.0)
            h = keepout.get("height_mm", 10.0)
            return math.sqrt(w**2 + h**2) / 2
        else:
            return 5.0
    
    def _apply_fixes(
        self, 
        components: list, 
        mounting_holes: list,
        feasibility_report: dict,
        board_width: float,
        board_length: float,
        edge_clearance: float
    ) -> tuple[list, list]:
        """
        Apply fix operations from feasibility report.
        """
        for error in feasibility_report.get("errors", []):
            for fix in error.get("suggested_fixes", []):
                op = fix["operation"]
                
                if op == "translate":
                    comp_id = fix.get("id")
                    dx = fix.get("dx", 0)
                    dy = fix.get("dy", 0)
                    
                    for comp in components:
                        if comp["id"] == comp_id:
                            new_x = comp["center"][0] + dx
                            new_y = comp["center"][1] + dy
                            
                            # Clamp to board bounds
                            radius = self._get_keepout_radius(comp)
                            new_x = max(edge_clearance + radius, 
                                       min(board_width - edge_clearance - radius, new_x))
                            new_y = max(edge_clearance + radius,
                                       min(board_length - edge_clearance - radius, new_y))
                            
                            comp["center"] = [new_x, new_y]
                            break
                
                elif op == "swap_footprint":
                    comp_id = fix.get("id")
                    new_footprint = fix.get("new_footprint")
                    
                    for comp in components:
                        if comp["id"] == comp_id:
                            comp["footprint"] = new_footprint
                            break
        
        return components, mounting_holes

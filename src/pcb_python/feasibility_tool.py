"""
Feasibility Tool - DRC-style validation with actionable fix operations

Responsibilities:
- Check component overlap
- Validate edge clearances
- Verify button spacing
- Check mounting hole placement
- Validate board dimensions
- Generate machine-actionable fix operations
"""

from __future__ import annotations
import math
from typing import List, Dict, Optional, Tuple

class FeasibilityTool:
    """
    Deterministic manufacturability checker for PCB layouts.
    
    Input: pcb_layout.json
    Output: feasibility_report.json
    
    Checks performed:
    - Component overlap detection
    - Edge clearance violations
    - Button spacing minimum
    - Mounting hole placement
    - Board dimension constraints
    """
    
    def __init__(self):
        # Manufacturing constraints (from printer_limits.json concepts)
        self.min_edge_clearance = 3.0  # mm
        self.min_button_spacing = 1.0  # mm
        self.min_mounting_hole_clearance = 4.0  # mm
        self.min_board_dimension = 20.0  # mm
        self.max_board_width = 70.0  # mm
        self.max_board_length = 240.0  # mm
    
    def check(self, pcb_layout: dict) -> dict:
        """
        Validate PCB layout and return feasibility report.
        
        Args:
            pcb_layout: dict matching pcb_layout.schema.json
        
        Returns:
            dict matching feasibility_report.schema.json
        """
        errors: List[dict] = []
        warnings: List[dict] = []
        checks = {
            "component_overlap": True,
            "edge_clearance": True,
            "button_spacing": True,
            "mounting_holes": True,
            "board_dimensions": True
        }
        
        board = pcb_layout["board"]
        components = pcb_layout["components"]
        mounting_holes = pcb_layout["mounting_holes"]
        
        # Extract board bounds
        outline = board["outline_polygon"]
        board_width, board_length = self._get_board_dimensions(outline)
        
        # Check 1: Board dimensions
        dimension_errors = self._check_board_dimensions(board_width, board_length)
        if dimension_errors:
            errors.extend(dimension_errors)
            checks["board_dimensions"] = False
        
        # Check 2: Component overlap
        overlap_errors = self._check_component_overlap(components)
        if overlap_errors:
            errors.extend(overlap_errors)
            checks["component_overlap"] = False
        
        # Check 3: Edge clearance
        edge_errors = self._check_edge_clearance(components, board_width, board_length)
        if edge_errors:
            errors.extend(edge_errors)
            checks["edge_clearance"] = False
        
        # Check 4: Button spacing
        spacing_errors = self._check_button_spacing(components)
        if spacing_errors:
            errors.extend(spacing_errors)
            checks["button_spacing"] = False
        
        # Check 5: Mounting hole placement
        mounting_errors = self._check_mounting_holes(mounting_holes, components, board_width, board_length)
        if mounting_errors:
            errors.extend(mounting_errors)
            checks["mounting_holes"] = False
        
        # Calculate statistics
        total_components = len(components)
        min_clearance = self._calculate_min_clearance(components)
        
        return {
            "feasible": len(errors) == 0,
            "checks": checks,
            "errors": errors,
            "warnings": warnings,
            "statistics": {
                "total_components": total_components,
                "board_utilization_percent": 0.0,  # TODO: calculate
                "min_clearance_found_mm": min_clearance
            }
        }
    
    def _get_board_dimensions(self, outline: list) -> Tuple[float, float]:
        """Extract width and length from outline polygon."""
        xs = [p[0] for p in outline]
        ys = [p[1] for p in outline]
        width = max(xs) - min(xs)
        length = max(ys) - min(ys)
        return width, length
    
    def _check_board_dimensions(self, width: float, length: float) -> List[dict]:
        """Validate board dimensions against limits."""
        errors = []
        
        if width < self.min_board_dimension:
            errors.append({
                "code": "BOARD_TOO_SMALL",
                "message": f"Board width {width:.1f}mm is below minimum {self.min_board_dimension}mm",
                "affected_entities": ["board"],
                "measured_value": width,
                "required_value": self.min_board_dimension,
                "suggested_fixes": [
                    {
                        "operation": "resize_board",
                        "id": "board",
                        "new_width": self.min_board_dimension
                    }
                ]
            })
        
        if length < self.min_board_dimension:
            errors.append({
                "code": "BOARD_TOO_SMALL",
                "message": f"Board length {length:.1f}mm is below minimum {self.min_board_dimension}mm",
                "affected_entities": ["board"],
                "measured_value": length,
                "required_value": self.min_board_dimension,
                "suggested_fixes": [
                    {
                        "operation": "resize_board",
                        "id": "board",
                        "new_height": self.min_board_dimension
                    }
                ]
            })
        
        if width > self.max_board_width:
            errors.append({
                "code": "BOARD_TOO_LARGE",
                "message": f"Board width {width:.1f}mm exceeds maximum {self.max_board_width}mm",
                "affected_entities": ["board"],
                "measured_value": width,
                "required_value": self.max_board_width,
                "suggested_fixes": []
            })
        
        if length > self.max_board_length:
            errors.append({
                "code": "BOARD_TOO_LARGE",
                "message": f"Board length {length:.1f}mm exceeds maximum {self.max_board_length}mm",
                "affected_entities": ["board"],
                "measured_value": length,
                "required_value": self.max_board_length,
                "suggested_fixes": []
            })
        
        return errors
    
    def _check_component_overlap(self, components: List[dict]) -> List[dict]:
        """Check for component keepout overlaps."""
        errors = []
        
        for i, comp1 in enumerate(components):
            for j, comp2 in enumerate(components[i+1:], start=i+1):
                distance = self._distance(comp1["center"], comp2["center"])
                
                # Get keepout radii
                r1 = self._get_keepout_radius(comp1)
                r2 = self._get_keepout_radius(comp2)
                
                min_distance = r1 + r2
                
                if distance < min_distance:
                    overlap = min_distance - distance
                    
                    # Suggest translation fix
                    # Move comp2 away from comp1
                    dx, dy = self._calculate_translation_vector(
                        comp1["center"], 
                        comp2["center"], 
                        overlap + 0.5  # Add 0.5mm safety margin
                    )
                    
                    errors.append({
                        "code": "COMPONENT_OVERLAP",
                        "message": f"Components {comp1['id']} and {comp2['id']} keepouts overlap by {overlap:.2f}mm",
                        "affected_entities": [comp1["id"], comp2["id"]],
                        "measured_value": distance,
                        "required_value": min_distance,
                        "suggested_fixes": [
                            {
                                "operation": "translate",
                                "id": comp2["id"],
                                "dx": dx,
                                "dy": dy
                            }
                        ]
                    })
        
        return errors
    
    def _check_edge_clearance(self, components: List[dict], board_width: float, board_length: float) -> List[dict]:
        """Check components are not too close to board edges."""
        errors = []
        
        for comp in components:
            x, y = comp["center"]
            
            # For edge clearance, use actual bounding box dimensions
            keepout = comp.get("keepout", {})
            if keepout.get("type") == "rectangle":
                half_w = keepout.get("width_mm", 10.0) / 2
                half_h = keepout.get("height_mm", 10.0) / 2
            elif keepout.get("type") == "circle":
                half_w = half_h = keepout.get("radius_mm", 5.0)
            else:
                half_w = half_h = 5.0
            
            # Check distances to edges using actual bounding box
            dist_left = x - half_w
            dist_right = board_width - (x + half_w)
            dist_bottom = y - half_h
            dist_top = board_length - (y + half_h)
            
            min_dist = min(dist_left, dist_right, dist_bottom, dist_top)
            
            if min_dist < self.min_edge_clearance:
                violation = self.min_edge_clearance - min_dist
                
                # Determine which edge and suggest fix
                dx, dy = 0.0, 0.0
                if dist_left < self.min_edge_clearance:
                    dx = violation + 0.5
                elif dist_right < self.min_edge_clearance:
                    dx = -(violation + 0.5)
                
                if dist_bottom < self.min_edge_clearance:
                    dy = violation + 0.5
                elif dist_top < self.min_edge_clearance:
                    dy = -(violation + 0.5)
                
                errors.append({
                    "code": "EDGE_CLEARANCE_VIOLATION",
                    "message": f"Component {comp['id']} is {min_dist:.2f}mm from edge (minimum {self.min_edge_clearance}mm)",
                    "affected_entities": [comp["id"]],
                    "measured_value": min_dist,
                    "required_value": self.min_edge_clearance,
                    "suggested_fixes": [
                        {
                            "operation": "translate",
                            "id": comp["id"],
                            "dx": dx,
                            "dy": dy
                        }
                    ]
                })
        
        return errors
    
    def _check_button_spacing(self, components: List[dict]) -> List[dict]:
        """Check button-to-button spacing."""
        errors = []
        
        buttons = [c for c in components if c["type"] == "button"]
        
        for i, btn1 in enumerate(buttons):
            for j, btn2 in enumerate(buttons[i+1:], start=i+1):
                distance = self._distance(btn1["center"], btn2["center"])
                
                # Get keepout radii - these already include spacing buffer
                r1 = self._get_keepout_radius(btn1)
                r2 = self._get_keepout_radius(btn2)
                
                # Minimum distance is sum of keepout radii (no extra spacing needed)
                min_distance = r1 + r2
                
                if distance < min_distance:
                    violation = min_distance - distance
                    
                    dx, dy = self._calculate_translation_vector(
                        btn1["center"], 
                        btn2["center"], 
                        violation + 0.5
                    )
                    
                    errors.append({
                        "code": "BUTTON_SPACING_VIOLATION",
                        "message": f"Buttons {btn1['id']} and {btn2['id']} are {distance:.2f}mm apart (minimum {min_distance:.2f}mm)",
                        "affected_entities": [btn1["id"], btn2["id"]],
                        "measured_value": distance,
                        "required_value": min_distance,
                        "suggested_fixes": [
                            {
                                "operation": "translate",
                                "id": btn2["id"],
                                "dx": dx,
                                "dy": dy
                            }
                        ]
                    })
        
        return errors
    
    def _check_mounting_holes(
        self, 
        mounting_holes: List[dict], 
        components: List[dict],
        board_width: float,
        board_length: float
    ) -> List[dict]:
        """Check mounting hole placement."""
        errors = []
        
        for hole in mounting_holes:
            x, y = hole["center"]
            
            # Check edge clearance
            dist_to_edge = min(x, y, board_width - x, board_length - y)
            
            if dist_to_edge < self.min_mounting_hole_clearance:
                errors.append({
                    "code": "MOUNTING_HOLE_TOO_CLOSE",
                    "message": f"Mounting hole {hole['id']} is {dist_to_edge:.2f}mm from edge (minimum {self.min_mounting_hole_clearance}mm)",
                    "affected_entities": [hole["id"]],
                    "measured_value": dist_to_edge,
                    "required_value": self.min_mounting_hole_clearance,
                    "suggested_fixes": []  # Manual adjustment needed
                })
            
            # Check clearance from components
            for comp in components:
                distance = self._distance(hole["center"], comp["center"])
                min_distance = self._get_keepout_radius(comp) + self.min_mounting_hole_clearance
                
                if distance < min_distance:
                    errors.append({
                        "code": "MOUNTING_HOLE_TOO_CLOSE",
                        "message": f"Mounting hole {hole['id']} too close to {comp['id']}",
                        "affected_entities": [hole["id"], comp["id"]],
                        "measured_value": distance,
                        "required_value": min_distance,
                        "suggested_fixes": []
                    })
        
        return errors
    
    def _distance(self, p1: list, p2: list) -> float:
        """Calculate Euclidean distance between two points."""
        return math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)
    
    def _get_keepout_radius(self, component: dict) -> float:
        """Get effective keepout radius for a component."""
        keepout = component.get("keepout", {})
        
        if keepout.get("type") == "circle":
            return keepout.get("radius_mm", 5.0)
        elif keepout.get("type") == "rectangle":
            # Use diagonal / 2 as approximate radius
            w = keepout.get("width_mm", 10.0)
            h = keepout.get("height_mm", 10.0)
            return math.sqrt(w**2 + h**2) / 2
        else:
            return 5.0  # Default
    
    def _calculate_translation_vector(self, p1: list, p2: list, required_distance: float) -> Tuple[float, float]:
        """Calculate translation vector to move p2 away from p1 by required_distance."""
        current_distance = self._distance(p1, p2)
        
        if current_distance == 0:
            return (required_distance, 0.0)
        
        # Unit vector from p1 to p2
        dx = (p2[0] - p1[0]) / current_distance
        dy = (p2[1] - p1[1]) / current_distance
        
        # Scale to required distance
        move_distance = required_distance - current_distance
        
        return (dx * move_distance, dy * move_distance)
    
    def _calculate_min_clearance(self, components: List[dict]) -> float:
        """Calculate minimum clearance found between any two components."""
        if len(components) < 2:
            return 999.9
        
        min_clearance = 999.9
        
        for i, comp1 in enumerate(components):
            for comp2 in components[i+1:]:
                distance = self._distance(comp1["center"], comp2["center"])
                r1 = self._get_keepout_radius(comp1)
                r2 = self._get_keepout_radius(comp2)
                clearance = distance - r1 - r2
                
                if clearance < min_clearance:
                    min_clearance = clearance
        
        return max(0.0, min_clearance)

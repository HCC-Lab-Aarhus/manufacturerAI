"""
Parametric 3D Enclosure Generator

Generates enclosure geometry directly from pcb_layout.json.
Uses OpenSCAD or CadQuery for parametric generation.

This replaces the legacy Blender adapter with a proper parametric approach.
"""

from __future__ import annotations
import json
import math
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any
from dataclasses import dataclass, field


@dataclass
class EnclosureParams:
    """Parameters for enclosure generation."""
    # From PCB
    board_width: float = 41.0
    board_length: float = 176.0
    board_thickness: float = 1.6
    
    # Enclosure walls
    wall_thickness: float = 1.6
    bottom_thickness: float = 3.0
    top_thickness: float = 1.5
    
    # Clearances
    pcb_clearance: float = 0.3  # Gap around PCB
    button_hole_clearance: float = 0.4  # Gap around button caps
    
    # Features
    corner_radius: float = 3.0
    standoff_height: float = 3.0
    standoff_outer_diameter: float = 6.0
    screw_hole_diameter: float = 2.5
    
    # Trace channels (for conductive filament)
    trace_channel_depth: float = 0.4  # mm depth of carved channels
    trace_channel_width: float = 1.5  # mm width of traces
    pinhole_depth: float = 0.8  # mm depth of pinholes (2x trace depth)
    pinhole_diameter: float = 1.0  # mm diameter of pinholes for component pins
    grid_resolution: float = 0.5  # mm per grid cell from router
    
    # Derived
    @property
    def outer_width(self) -> float:
        return self.board_width + 2 * (self.wall_thickness + self.pcb_clearance)
    
    @property
    def outer_length(self) -> float:
        return self.board_length + 2 * (self.wall_thickness + self.pcb_clearance)
    
    @property
    def total_height(self) -> float:
        return self.bottom_thickness + self.standoff_height + self.board_thickness + 5.0


@dataclass
class ButtonHole:
    """Button hole specification."""
    id: str
    center_x: float
    center_y: float
    diameter: float
    
    
@dataclass
class MountingPost:
    """Mounting post (standoff) specification."""
    id: str
    center_x: float
    center_y: float
    hole_diameter: float


class Enclosure3DAgent:
    """
    Parametric 3D enclosure generator.
    
    Reads pcb_layout.json and generates:
    - top_shell.stl (button holes, LED window)
    - bottom_shell.stl (battery cavity, standoffs)
    - OpenSCAD source files for customization
    """
    
    def __init__(self):
        self.params = EnclosureParams()
    
    def generate_from_pcb_layout(
        self,
        pcb_layout: dict,
        design_spec: dict,
        output_dir: Path,
        routing_result: Optional[dict] = None
    ) -> Dict[str, Path]:
        """
        Generate enclosure from PCB layout.
        
        Args:
            pcb_layout: pcb_layout.json dict
            design_spec: design_spec.json dict for additional parameters
            output_dir: Directory for output files
            routing_result: Optional routing result with traces for conductive channels
        
        Returns:
            Dict mapping output names to file paths
        """
        print("\n[ENCLOSURE] Generating 3D enclosure...")
        print(f"[ENCLOSURE] Output directory: {output_dir}")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Extract parameters from PCB layout
        print("[ENCLOSURE] Extracting parameters from PCB layout...")
        self._configure_from_layout(pcb_layout, design_spec)
        print(f"[ENCLOSURE] Board dimensions: {self.params.board_width}x{self.params.board_length}mm")
        
        # Extract features
        button_holes = self._extract_button_holes(pcb_layout)
        mounting_posts = self._extract_mounting_posts(pcb_layout)
        battery_cavity = self._extract_battery_cavity(pcb_layout)
        led_windows = self._extract_led_windows(pcb_layout)
        ir_diodes = self._extract_ir_diodes(pcb_layout)
        print(f"[ENCLOSURE] Features: {len(button_holes)} buttons, {len(mounting_posts)} mounting posts, {len(led_windows)} LEDs, {len(ir_diodes)} IR diodes")
        
        # Extract trace channels if routing result provided
        trace_channels = []
        all_pads = []
        if routing_result and routing_result.get("traces"):
            trace_channels = routing_result["traces"]
            print(f"[ENCLOSURE] Trace channels: {len(trace_channels)} nets for conductive filament")
        
        # Extract ALL pads from component footprints (including unconnected pads)
        all_pads = self._extract_all_pads_from_layout(pcb_layout, grid_resolution=0.5)
        print(f"[ENCLOSURE] Component pads: {len(all_pads)} pinholes for component pins")
        
        # Generate OpenSCAD files
        print("[ENCLOSURE] PATH: Generating OpenSCAD files...")
        top_scad = self._generate_top_shell_scad(button_holes, led_windows)
        bottom_scad = self._generate_bottom_shell_scad(mounting_posts, battery_cavity, trace_channels, all_pads, ir_diodes)
        
        # Write SCAD files
        top_scad_path = output_dir / "top_shell.scad"
        bottom_scad_path = output_dir / "bottom_shell.scad"
        
        top_scad_path.write_text(top_scad, encoding="utf-8")
        bottom_scad_path.write_text(bottom_scad, encoding="utf-8")
        print(f"[ENCLOSURE] ✓ Generated SCAD files: top_shell.scad ({len(top_scad)} chars), bottom_shell.scad ({len(bottom_scad)} chars)")
        
        # Try to render to STL using OpenSCAD
        outputs = {
            "top_shell_scad": top_scad_path,
            "bottom_shell_scad": bottom_scad_path
        }
        
        # Attempt OpenSCAD rendering
        print("[ENCLOSURE] PATH: Attempting OpenSCAD STL rendering...")
        top_stl_path = output_dir / "top_shell.stl"
        bottom_stl_path = output_dir / "bottom_shell.stl"
        
        if self._render_scad_to_stl(top_scad_path, top_stl_path):
            outputs["top_shell_stl"] = top_stl_path
            print("[ENCLOSURE] ✓ Rendered top_shell.stl")
        else:
            print("[ENCLOSURE] ⚠ Could not render top_shell.stl (OpenSCAD not available?)")
        
        if self._render_scad_to_stl(bottom_scad_path, bottom_stl_path):
            outputs["bottom_shell_stl"] = bottom_stl_path
            print("[ENCLOSURE] ✓ Rendered bottom_shell.stl")
        else:
            print("[ENCLOSURE] ⚠ Could not render bottom_shell.stl (OpenSCAD not available?)")
        
        # Also generate manifest
        manifest = self._generate_manifest(button_holes, mounting_posts)
        manifest_path = output_dir / "enclosure_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        outputs["manifest"] = manifest_path
        
        return outputs
    
    def _configure_from_layout(self, pcb_layout: dict, design_spec: dict) -> None:
        """Configure parameters from PCB layout and design spec."""
        # Board dimensions
        outline = pcb_layout["board"]["outline_polygon"]
        xs = [p[0] for p in outline]
        ys = [p[1] for p in outline]
        
        self.params.board_width = max(xs) - min(xs)
        self.params.board_length = max(ys) - min(ys)
        self.params.board_thickness = pcb_layout["board"].get("thickness_mm", 1.6)
        
        # From design spec
        if design_spec:
            constraints = design_spec.get("constraints", {})
            self.params.wall_thickness = constraints.get("min_wall_thickness_mm", 1.6)
    
    def _extract_button_holes(self, pcb_layout: dict) -> List[ButtonHole]:
        """Extract button hole specifications from layout."""
        holes = []
        
        for comp in pcb_layout.get("components", []):
            if comp.get("type") == "button":
                keepout = comp.get("keepout", {})
                
                # Button cap diameter
                if keepout.get("type") == "circle":
                    diameter = (keepout.get("radius_mm", 5) - 1.5) * 2  # Cap is smaller than keepout
                else:
                    diameter = 9.0  # Default
                
                holes.append(ButtonHole(
                    id=comp["id"],
                    center_x=comp["center"][0],
                    center_y=comp["center"][1],
                    diameter=diameter + self.params.button_hole_clearance
                ))
        
        return holes
    
    def _extract_mounting_posts(self, pcb_layout: dict) -> List[MountingPost]:
        """Extract mounting post locations from layout."""
        posts = []
        
        for hole in pcb_layout.get("mounting_holes", []):
            posts.append(MountingPost(
                id=hole["id"],
                center_x=hole["center"][0],
                center_y=hole["center"][1],
                hole_diameter=hole.get("drill_diameter_mm", 3.0)
            ))
        
        return posts
    
    def _extract_battery_cavity(self, pcb_layout: dict) -> Optional[Dict[str, float]]:
        """Extract battery cavity dimensions."""
        for comp in pcb_layout.get("components", []):
            if comp.get("type") == "battery":
                keepout = comp.get("keepout", {})
                
                if keepout.get("type") == "rectangle":
                    return {
                        "center_x": comp["center"][0],
                        "center_y": comp["center"][1],
                        "width": keepout.get("width_mm", 15),
                        "height": keepout.get("height_mm", 30),
                        "depth": 12.0  # Standard 2xAAA depth
                    }
        
        return None
    
    def _extract_led_windows(self, pcb_layout: dict) -> List[Dict[str, float]]:
        """Extract LED window locations."""
        windows = []
        
        for comp in pcb_layout.get("components", []):
            if comp.get("type") == "led":
                windows.append({
                    "id": comp["id"],
                    "center_x": comp["center"][0],
                    "center_y": comp["center"][1],
                    "diameter": 4.0  # Standard LED window
                })
        
        return windows
    
    def _extract_ir_diodes(self, pcb_layout: dict) -> List[Dict[str, float]]:
        """Extract IR diode locations for slit cutouts.
        
        IR diodes (LEDs used for IR transmission) should be at the top of the remote
        with a slit in the enclosure so the diode can point outward.
        """
        diodes = []
        
        for comp in pcb_layout.get("components", []):
            if comp.get("type") == "led":
                diodes.append({
                    "id": comp["id"],
                    "center_x": comp["center"][0],
                    "center_y": comp["center"][1],
                    "diameter": 5.0  # Standard 5mm IR LED
                })
        
        return diodes
    
    def _extract_all_pads(self, routing_result: Optional[dict]) -> List[Tuple[float, float]]:
        """
        Extract all pad positions from routing result.
        Returns list of (x, y) tuples in grid coordinates.
        
        Note: This currently only gets pads from traces. Need to extract from
        component footprints for complete coverage.
        """
        if not routing_result or not routing_result.get("traces"):
            return []
        
        pads = set()
        for trace in routing_result["traces"]:
            path = trace.get("path", [])
            if path:
                # Start and end of each trace are pads
                pads.add((path[0]["x"], path[0]["y"]))
                pads.add((path[-1]["x"], path[-1]["y"]))
        
        return list(pads)
    
    def _extract_all_pads_from_layout(self, pcb_layout: dict, grid_resolution: float = 0.5) -> List[Tuple[float, float]]:
        """
        Extract ALL pad positions from PCB layout by calculating footprint pad locations.
        This includes pads that don't have traces connected.
        
        Returns list of (grid_x, grid_y) tuples in grid coordinates.
        """
        pads = []
        
        # Footprint definitions (matches ts_router_bridge.py)
        footprints = {
            "button": {"pinSpacingX": 9.0, "pinSpacingY": 6.0},  # 4 pads: corners of rectangle
            "controller": {"pinSpacing": 2.5, "rowSpacing": 10.0},  # DIP-28: 2 rows of 14 pins
            "battery": {"padSpacing": 6.0},  # 2 pads
            "led": {"padSpacing": 5.0}  # 2 pads
        }
        
        for comp in pcb_layout.get("components", []):
            comp_type = comp.get("type")
            center_x, center_y = comp["center"]
            
            if comp_type == "button":
                # 4 pads at corners of rectangle
                fp = footprints["button"]
                dx = fp["pinSpacingX"] / 2
                dy = fp["pinSpacingY"] / 2
                for px, py in [(center_x - dx, center_y - dy),
                               (center_x + dx, center_y - dy),
                               (center_x - dx, center_y + dy),
                               (center_x + dx, center_y + dy)]:
                    pads.append((int(px / grid_resolution), int(py / grid_resolution)))
            
            elif comp_type == "controller":
                # DIP-28: 2 rows of 14 pins
                fp = footprints["controller"]
                pin_spacing = fp["pinSpacing"]
                row_spacing = fp["rowSpacing"]
                num_pins_per_side = 14
                
                # Calculate starting Y (top of chip)
                total_height = (num_pins_per_side - 1) * pin_spacing
                start_y = center_y - total_height / 2
                
                # Left row and right row
                for i in range(num_pins_per_side):
                    y = start_y + i * pin_spacing
                    left_x = center_x - row_spacing / 2
                    right_x = center_x + row_spacing / 2
                    pads.append((int(left_x / grid_resolution), int(y / grid_resolution)))
                    pads.append((int(right_x / grid_resolution), int(y / grid_resolution)))
            
            elif comp_type == "battery":
                # 2 pads along vertical axis
                fp = footprints["battery"]
                pad_spacing = fp["padSpacing"]
                pads.append((int(center_x / grid_resolution), int((center_y - pad_spacing / 2) / grid_resolution)))
                pads.append((int(center_x / grid_resolution), int((center_y + pad_spacing / 2) / grid_resolution)))
            
            elif comp_type == "led":
                # 2 pads along horizontal axis (matches TypeScript router: x - halfSpacing, x + halfSpacing)
                fp = footprints["led"]
                pad_spacing = fp["padSpacing"]
                pads.append((int((center_x - pad_spacing / 2) / grid_resolution), int(center_y / grid_resolution)))
                pads.append((int((center_x + pad_spacing / 2) / grid_resolution), int(center_y / grid_resolution)))
        
        return pads
    
    def _generate_top_shell_scad(
        self,
        button_holes: List[ButtonHole],
        led_windows: List[Dict[str, float]]
    ) -> str:
        """Generate OpenSCAD code for top shell."""
        p = self.params
        
        scad = f"""// Top Shell - Generated by ManufacturerAI
// This file is parametric - edit values below to customize

// Parameters
outer_width = {p.outer_width:.2f};
outer_length = {p.outer_length:.2f};
wall_thickness = {p.wall_thickness:.2f};
top_thickness = {p.top_thickness:.2f};
corner_radius = {p.corner_radius:.2f};
shell_height = 8;  // Height of top shell side walls

// Main shell
module top_shell() {{
    difference() {{
        // Outer shell
        hull() {{
            for (x = [corner_radius, outer_width - corner_radius])
                for (y = [corner_radius, outer_length - corner_radius])
                    translate([x, y, 0])
                        cylinder(r=corner_radius, h=shell_height, $fn=32);
        }}
        
        // Inner cavity
        translate([wall_thickness, wall_thickness, top_thickness])
            cube([outer_width - 2*wall_thickness, 
                  outer_length - 2*wall_thickness, 
                  shell_height]);
        
        // Button holes
{self._generate_button_holes_scad(button_holes)}
        
        // LED windows
{self._generate_led_windows_scad(led_windows)}
    }}
}}

top_shell();
"""
        return scad
    
    def _generate_button_holes_scad(self, button_holes: List[ButtonHole]) -> str:
        """Generate OpenSCAD code for button holes."""
        lines = []
        
        # Offset for enclosure wall
        offset_x = self.params.wall_thickness + self.params.pcb_clearance
        offset_y = self.params.wall_thickness + self.params.pcb_clearance
        
        for hole in button_holes:
            x = hole.center_x + offset_x
            y = hole.center_y + offset_y
            r = hole.diameter / 2
            
            lines.append(f"        // {hole.id}")
            lines.append(f"        translate([{x:.2f}, {y:.2f}, -1])")
            lines.append(f"            cylinder(r={r:.2f}, h=20, $fn=32);")
        
        return "\n".join(lines)
    
    def _generate_led_windows_scad(self, led_windows: List[Dict[str, float]]) -> str:
        """Generate OpenSCAD code for LED windows."""
        lines = []
        
        offset_x = self.params.wall_thickness + self.params.pcb_clearance
        offset_y = self.params.wall_thickness + self.params.pcb_clearance
        
        for window in led_windows:
            x = window["center_x"] + offset_x
            y = window["center_y"] + offset_y
            r = window["diameter"] / 2
            
            lines.append(f"        // LED window {window['id']}")
            lines.append(f"        translate([{x:.2f}, {y:.2f}, -1])")
            lines.append(f"            cylinder(r={r:.2f}, h=20, $fn=24);")
        
        return "\n".join(lines)
    
    def _generate_bottom_shell_scad(
        self,
        mounting_posts: List[MountingPost],
        battery_cavity: Optional[Dict[str, float]],
        trace_channels: Optional[List[dict]] = None,
        all_pads: Optional[List[Tuple[float, float]]] = None,
        ir_diodes: Optional[List[Dict[str, float]]] = None
    ) -> str:
        """Generate OpenSCAD code for bottom shell with trace channels, pinholes, and IR diode slits."""
        p = self.params
        
        # Generate trace channel code if traces provided
        trace_channel_code = self._generate_trace_channels_scad(trace_channels) if trace_channels else "        // No trace channels"
        
        # Generate pinhole code for all component pads
        pinhole_code = self._generate_pinholes_scad(all_pads) if all_pads else "        // No pinholes"
        
        # Generate IR diode slit code
        ir_slit_code = self._generate_ir_diode_slits_scad(ir_diodes) if ir_diodes else "        // No IR diode slits"
        
        scad = f"""// Bottom Shell - Generated by ManufacturerAI
// This file is parametric - edit values below to customize
// Trace channels carved into the inside base are meant to be filled
// with conductive filament after printing.

// Parameters
outer_width = {p.outer_width:.2f};
outer_length = {p.outer_length:.2f};
wall_thickness = {p.wall_thickness:.2f};
bottom_thickness = {p.bottom_thickness:.2f};
corner_radius = {p.corner_radius:.2f};
shell_height = 12;  // Height of bottom shell walls
standoff_height = {p.standoff_height:.2f};
standoff_od = {p.standoff_outer_diameter:.2f};
screw_hole_d = {p.screw_hole_diameter:.2f};

// Trace channel parameters
trace_channel_depth = {p.trace_channel_depth:.2f};  // Depth of conductive trace channels
trace_channel_width = {p.trace_channel_width:.2f};  // Width of trace channels
pinhole_depth = {p.pinhole_depth:.2f};  // Depth of pinholes for component pins (2x trace depth)
pinhole_diameter = {p.pinhole_diameter:.2f};  // Diameter of pinholes

// Main shell
module bottom_shell() {{
    difference() {{
        union() {{
            // Outer shell
            hull() {{
                for (x = [corner_radius, outer_width - corner_radius])
                    for (y = [corner_radius, outer_length - corner_radius])
                        translate([x, y, 0])
                            cylinder(r=corner_radius, h=shell_height, $fn=32);
            }}
            
            // Standoffs
{self._generate_standoffs_scad(mounting_posts)}
        }}
        
        // Inner cavity
        translate([wall_thickness, wall_thickness, bottom_thickness])
            cube([outer_width - 2*wall_thickness, 
                  outer_length - 2*wall_thickness, 
                  shell_height]);
        
        // Screw holes in standoffs
{self._generate_screw_holes_scad(mounting_posts)}
        
        // Battery cavity
{self._generate_battery_cavity_scad(battery_cavity)}
        
        // Trace channels carved into bottom (for conductive filament)
{trace_channel_code}
        
        // Pinholes for component pins (deeper than traces for good contact)
{pinhole_code}
        
        // IR diode slits (at top of enclosure, diode points outward)
{ir_slit_code}
    }}
}}

bottom_shell();
"""
        return scad
    
    def _generate_ir_diode_slits_scad(self, ir_diodes: List[Dict[str, float]]) -> str:
        """Generate OpenSCAD code for IR diode slits at the top edge of the enclosure.
        
        The slit allows the IR LED to protrude through the top wall,
        pointing outward for IR transmission.
        """
        if not ir_diodes:
            return "        // No IR diode slits"
        
        lines = ["        // IR diode slits for IR transmission"]
        p = self.params
        offset_x = p.wall_thickness + p.pcb_clearance
        offset_y = p.wall_thickness + p.pcb_clearance
        
        for diode in ir_diodes:
            x = diode["center_x"] + offset_x
            diameter = diode.get("diameter", 5.0)
            slit_width = diameter + 1.0  # 1mm clearance
            slit_height = diameter + 2.0  # Extends above and below LED center
            
            # Slit is cut through the top wall of the enclosure
            # Position at the very top (max Y)
            lines.append(f"        // IR diode slit {diode['id']}")
            lines.append(f"        translate([{x - slit_width/2:.2f}, outer_length - wall_thickness - 1, bottom_thickness])")
            lines.append(f"            cube([{slit_width:.2f}, wall_thickness + 2, {slit_height:.2f}]);")
            
            # Also cut a rounded hole for the LED body
            lines.append(f"        translate([{x:.2f}, outer_length - wall_thickness/2, bottom_thickness + {slit_height/2:.2f}])")
            lines.append(f"            rotate([-90, 0, 0])")
            lines.append(f"                cylinder(d={diameter:.2f}, h=wall_thickness + 2, center=true, $fn=24);")
        
        return "\n".join(lines)
    
    def _generate_standoffs_scad(self, mounting_posts: List[MountingPost]) -> str:
        """Generate OpenSCAD code for standoffs."""
        lines = []
        
        offset_x = self.params.wall_thickness + self.params.pcb_clearance
        offset_y = self.params.wall_thickness + self.params.pcb_clearance
        
        for post in mounting_posts:
            x = post.center_x + offset_x
            y = post.center_y + offset_y
            
            lines.append(f"            // Standoff {post.id}")
            lines.append(f"            translate([{x:.2f}, {y:.2f}, 0])")
            lines.append(f"                cylinder(d=standoff_od, h=bottom_thickness + standoff_height, $fn=24);")
        
        return "\n".join(lines)
    
    def _generate_screw_holes_scad(self, mounting_posts: List[MountingPost]) -> str:
        """Generate OpenSCAD code for screw holes."""
        lines = []
        
        offset_x = self.params.wall_thickness + self.params.pcb_clearance
        offset_y = self.params.wall_thickness + self.params.pcb_clearance
        
        for post in mounting_posts:
            x = post.center_x + offset_x
            y = post.center_y + offset_y
            
            lines.append(f"        // Screw hole {post.id}")
            lines.append(f"        translate([{x:.2f}, {y:.2f}, -1])")
            lines.append(f"            cylinder(d=screw_hole_d, h=bottom_thickness + standoff_height + 2, $fn=16);")
        
        return "\n".join(lines)
    
    def _generate_battery_cavity_scad(self, battery_cavity: Optional[Dict[str, float]]) -> str:
        """Generate OpenSCAD code for battery cavity."""
        if battery_cavity is None:
            return "        // No battery cavity"
        
        offset_x = self.params.wall_thickness + self.params.pcb_clearance
        offset_y = self.params.wall_thickness + self.params.pcb_clearance
        
        cx = battery_cavity["center_x"] + offset_x
        cy = battery_cavity["center_y"] + offset_y
        w = battery_cavity["width"]
        h = battery_cavity["height"]
        d = battery_cavity["depth"]
        
        x = cx - w / 2
        y = cy - h / 2
        
        return f"""        // Battery cavity
        translate([{x:.2f}, {y:.2f}, -1])
            cube([{w:.2f}, {h:.2f}, {d:.2f}]);"""
    
    def _generate_trace_channels_scad(self, traces: List[dict]) -> str:
        """
        Generate OpenSCAD code for trace channels carved into the bottom.
        
        These channels will be filled with conductive filament after printing.
        Uses a module-based approach with simple cubes instead of hull() to reduce CSG complexity.
        """
        if not traces:
            return "        // No trace channels"
        
        p = self.params
        offset_x = p.wall_thickness + p.pcb_clearance
        offset_y = p.wall_thickness + p.pcb_clearance
        grid_res = p.grid_resolution
        half_width = p.trace_channel_width / 2
        
        lines = ["        // Trace channels for conductive filament"]
        lines.append(f"        // {len(traces)} nets, carved depth={p.trace_channel_depth}mm, width={p.trace_channel_width}mm")
        lines.append(f"        // Using simplified cube-based traces for faster rendering")
        
        total_segments = 0
        pad_positions = set()
        
        for trace in traces:
            net_name = trace.get("net", "unknown")
            path = trace.get("path", [])
            
            if len(path) < 2:
                continue
            
            # Simplify path: keep only points where direction changes
            simplified = self._simplify_path(path)
            lines.append(f"        // Net: {net_name} ({len(path)} pts -> {len(simplified)} simplified)")
            
            # Collect pads
            pad_positions.add((path[0]["x"], path[0]["y"]))
            pad_positions.add((path[-1]["x"], path[-1]["y"]))
            
            # Generate simple cubes for each segment (much faster than hull)
            for i in range(len(simplified) - 1):
                p1 = simplified[i]
                p2 = simplified[i + 1]
                
                # Convert grid coords to world coords
                x1 = p1["x"] * grid_res + offset_x
                y1 = p1["y"] * grid_res + offset_y
                x2 = p2["x"] * grid_res + offset_x
                y2 = p2["y"] * grid_res + offset_y
                
                # Determine if this is a horizontal or vertical segment
                if abs(x2 - x1) > 0.01:  # Horizontal
                    min_x = min(x1, x2) - half_width
                    max_x = max(x1, x2) + half_width
                    lines.append(f"        translate([{min_x:.2f}, {y1 - half_width:.2f}, bottom_thickness - trace_channel_depth])")
                    lines.append(f"            cube([{max_x - min_x:.2f}, {p.trace_channel_width:.2f}, trace_channel_depth + 0.01]);")
                else:  # Vertical
                    min_y = min(y1, y2) - half_width
                    max_y = max(y1, y2) + half_width
                    lines.append(f"        translate([{x1 - half_width:.2f}, {min_y:.2f}, bottom_thickness - trace_channel_depth])")
                    lines.append(f"            cube([{p.trace_channel_width:.2f}, {max_y - min_y:.2f}, trace_channel_depth + 0.01]);")
                
                total_segments += 1
        
        lines.insert(3, f"        // Total segments after simplification: {total_segments}")
        
        # Add pad areas (slightly larger squares at endpoints - faster than cylinders)
        lines.append("        // Pad areas for component connections")
        pad_size = p.trace_channel_width * 1.5
        half_pad = pad_size / 2
        
        for px, py in pad_positions:
            x = px * grid_res + offset_x
            y = py * grid_res + offset_y
            lines.append(f"        translate([{x - half_pad:.2f}, {y - half_pad:.2f}, bottom_thickness - trace_channel_depth])")
            lines.append(f"            cube([{pad_size:.2f}, {pad_size:.2f}, trace_channel_depth + 0.01]);")
        
        return "\n".join(lines)
    
    def _generate_pinholes_scad(self, pads: List[Tuple[float, float]]) -> str:
        """
        Generate OpenSCAD code for pinholes at component pad locations.
        
        Pinholes are deeper than traces (2x depth) to ensure good electrical contact
        when filled with conductive filament.
        """
        if not pads:
            return "        // No pinholes"
        
        p = self.params
        offset_x = p.wall_thickness + p.pcb_clearance
        offset_y = p.wall_thickness + p.pcb_clearance
        grid_res = p.grid_resolution
        
        lines = ["        // Pinholes for component pins"]
        lines.append(f"        // {len(pads)} pinholes, depth={p.pinhole_depth}mm, diameter={p.pinhole_diameter}mm")
        
        for px, py in pads:
            x = px * grid_res + offset_x
            y = py * grid_res + offset_y
            lines.append(f"        translate([{x:.2f}, {y:.2f}, bottom_thickness - pinhole_depth])")
            lines.append(f"            cylinder(d=pinhole_diameter, h=pinhole_depth + 0.01, $fn=16);")
        
        return "\n".join(lines)
    
    def _simplify_path(self, path: List[dict]) -> List[dict]:
        """
        Simplify a path by keeping only corner points (where direction changes).
        
        Reduces a path like [(0,0), (1,0), (2,0), (2,1), (2,2)] 
        to [(0,0), (2,0), (2,2)] - only start, corners, and end.
        """
        if len(path) <= 2:
            return path
        
        simplified = [path[0]]  # Always keep start
        
        for i in range(1, len(path) - 1):
            prev = path[i - 1]
            curr = path[i]
            next_pt = path[i + 1]
            
            # Calculate direction vectors
            dx1 = curr["x"] - prev["x"]
            dy1 = curr["y"] - prev["y"]
            dx2 = next_pt["x"] - curr["x"]
            dy2 = next_pt["y"] - curr["y"]
            
            # If direction changes, this is a corner - keep it
            if dx1 != dx2 or dy1 != dy2:
                simplified.append(curr)
        
        simplified.append(path[-1])  # Always keep end
        return simplified
    
    def _render_scad_to_stl(self, scad_path: Path, stl_path: Path) -> bool:
        """Render OpenSCAD file to STL."""
        import os
        import shutil
        
        # Find OpenSCAD executable
        openscad_paths = [
            r"C:\Program Files\OpenSCAD\openscad.exe",
            r"C:\Program Files\OpenSCAD\openscad.com",
            shutil.which("openscad"),
            os.environ.get("OPENSCAD_BIN"),
        ]
        
        openscad_bin = None
        for path in openscad_paths:
            if path and Path(path).exists():
                openscad_bin = path
                print(f"[ENCLOSURE] Found OpenSCAD at: {path}")
                break
        
        if not openscad_bin:
            print("[ENCLOSURE] ✗ OpenSCAD not found in any of:")
            for p in openscad_paths:
                print(f"[ENCLOSURE]   - {p or '(None)'}")
            print("[ENCLOSURE] PATH: FALLBACK → SCAD files only (no STL rendering)")
            return False
        
        try:
            print(f"[ENCLOSURE] Rendering {scad_path.name} → {stl_path.name}...")
            result = subprocess.run(
                [openscad_bin, "-o", str(stl_path), str(scad_path)],
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes for complex bottom shells with trace channels
            )
            
            if result.returncode == 0:
                print(f"[ENCLOSURE] ✓ Generated {stl_path.name}")
                return True
            else:
                print(f"[ENCLOSURE] ✗ OpenSCAD error: {result.stderr[:200]}")
                return False
                
        except FileNotFoundError:
            print("[ENCLOSURE] ✗ OpenSCAD executable not found at runtime")
            print("[ENCLOSURE] PATH: FALLBACK → SCAD files only")
            return False
        except subprocess.TimeoutExpired:
            print("[ENCLOSURE] ✗ OpenSCAD rendering timed out (300s)")
            return False
    
    def _generate_manifest(
        self,
        button_holes: List[ButtonHole],
        mounting_posts: List[MountingPost]
    ) -> Dict[str, Any]:
        """Generate enclosure manifest with all parameters."""
        return {
            "generator": "ManufacturerAI Enclosure3DAgent",
            "parameters": {
                "board_width_mm": self.params.board_width,
                "board_length_mm": self.params.board_length,
                "board_thickness_mm": self.params.board_thickness,
                "wall_thickness_mm": self.params.wall_thickness,
                "corner_radius_mm": self.params.corner_radius,
                "standoff_height_mm": self.params.standoff_height,
                "screw_hole_diameter_mm": self.params.screw_hole_diameter
            },
            "clearances": {
                "pcb_clearance_mm": self.params.pcb_clearance,
                "button_hole_clearance_mm": self.params.button_hole_clearance
            },
            "features": {
                "button_holes": [
                    {
                        "id": h.id,
                        "center": [h.center_x, h.center_y],
                        "diameter_mm": h.diameter
                    }
                    for h in button_holes
                ],
                "mounting_posts": [
                    {
                        "id": p.id,
                        "center": [p.center_x, p.center_y],
                        "hole_diameter_mm": p.hole_diameter
                    }
                    for p in mounting_posts
                ]
            },
            "outer_dimensions": {
                "width_mm": self.params.outer_width,
                "length_mm": self.params.outer_length,
                "height_mm": self.params.total_height
            }
        }

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
    pinhole_depth: float = 2.5  # mm depth of pinholes for button pins
    pinhole_diameter: float = 1.2  # mm diameter of pinholes for component pins
    grid_resolution: float = 0.5  # mm per grid cell from router
    
    # Battery compartment (2x AAA side by side)
    # AAA dimensions: 10.5mm diameter, 44.5mm length
    battery_compartment_width: float = 25.0  # 2x AAA diameter + clearance (2*10.5 + 4)
    battery_compartment_height: float = 48.0  # AAA length + clearance (44.5 + 3.5)
    battery_guard_wall: float = 1.2  # Thickness of battery guard walls
    shell_height: float = 12.0  # Height of walls (fits AAA diameter 10.5mm + clearance)
    
    # Battery hatch
    battery_hatch_clearance: float = 0.3  # Gap around hatch for fit
    battery_hatch_thickness: float = 1.5  # Thickness of hatch panel
    spring_loop_width: float = 10.0  # Width of spring loop latch
    spring_loop_height: float = 8.0  # Height of spring loop arc
    spring_loop_thickness: float = 1.2  # Thickness of spring material
    
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
        # Note: IR diode slits are now in bottom shell walls (diodes point outward from back wall)
        print("[ENCLOSURE] PATH: Generating OpenSCAD files...")
        top_scad = self._generate_top_shell_scad(button_holes, [], None, battery_cavity)
        bottom_scad = self._generate_bottom_shell_scad(mounting_posts, battery_cavity, trace_channels, all_pads, ir_diodes)
        
        # Write SCAD files
        top_scad_path = output_dir / "top_shell.scad"
        bottom_scad_path = output_dir / "bottom_shell.scad"
        
        top_scad_path.write_text(top_scad, encoding="utf-8")
        bottom_scad_path.write_text(bottom_scad, encoding="utf-8")
        print(f"[ENCLOSURE] ✓ Generated SCAD files: top_shell.scad ({len(top_scad)} chars), bottom_shell.scad ({len(bottom_scad)} chars)")
        
        # Generate battery hatch if battery present
        if battery_cavity:
            battery_hatch_scad = self._generate_battery_hatch_scad(battery_cavity)
            battery_hatch_path = output_dir / "battery_hatch.scad"
            battery_hatch_path.write_text(battery_hatch_scad, encoding="utf-8")
            print(f"[ENCLOSURE] ✓ Generated battery_hatch.scad ({len(battery_hatch_scad)} chars)")
        
        # Collect SCAD outputs
        outputs = {
            "top_shell_scad": top_scad_path,
            "bottom_shell_scad": bottom_scad_path
        }
        
        if battery_cavity:
            outputs["battery_hatch_scad"] = battery_hatch_path
        
        # Generate combined assembly (bottom + flipped top + hatch beside)
        # Pass component and trace data so support pillars avoid them
        combined_scad = self._generate_combined_assembly_scad(
            battery_cavity=battery_cavity,
            pcb_layout=pcb_layout,
            trace_channels=trace_channels,
            all_pads=all_pads
        )
        combined_scad_path = output_dir / "combined_assembly.scad"
        combined_scad_path.write_text(combined_scad, encoding="utf-8")
        outputs["combined_assembly_scad"] = combined_scad_path
        print(f"[ENCLOSURE] ✓ Generated combined_assembly.scad ({len(combined_scad)} chars)")
        
        # Render all STLs for preview
        print("[ENCLOSURE] PATH: Rendering all models to STL...")
        
        # Top shell
        top_stl_path = output_dir / "top_shell.stl"
        if self._render_scad_to_stl(top_scad_path, top_stl_path):
            outputs["top_shell_stl"] = top_stl_path
            print("[ENCLOSURE] ✓ Rendered top_shell.stl")
        else:
            print("[ENCLOSURE] ⚠ Could not render top_shell.stl")
        
        # Bottom shell
        bottom_stl_path = output_dir / "bottom_shell.stl"
        if self._render_scad_to_stl(bottom_scad_path, bottom_stl_path):
            outputs["bottom_shell_stl"] = bottom_stl_path
            print("[ENCLOSURE] ✓ Rendered bottom_shell.stl")
        else:
            print("[ENCLOSURE] ⚠ Could not render bottom_shell.stl")
        
        # Battery hatch
        if battery_cavity:
            battery_hatch_stl_path = output_dir / "battery_hatch.stl"
            if self._render_scad_to_stl(battery_hatch_path, battery_hatch_stl_path):
                outputs["battery_hatch_stl"] = battery_hatch_stl_path
                print("[ENCLOSURE] ✓ Rendered battery_hatch.stl")
            else:
                print("[ENCLOSURE] ⚠ Could not render battery_hatch.stl")
        
        # Combined assembly
        combined_stl_path = output_dir / "combined_assembly.stl"
        if self._render_scad_to_stl(combined_scad_path, combined_stl_path):
            outputs["combined_assembly_stl"] = combined_stl_path
            print("[ENCLOSURE] ✓ Rendered combined_assembly.stl")
        else:
            print("[ENCLOSURE] ⚠ Could not render combined_assembly.stl")
        
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
        
        # Minimum button hole diameter for proper button cap fit
        min_button_hole_diameter = 12.8
        
        for comp in pcb_layout.get("components", []):
            if comp.get("type") == "button":
                keepout = comp.get("keepout", {})
                
                # Button cap diameter
                if keepout.get("type") == "circle":
                    diameter = (keepout.get("radius_mm", 5) - 1.5) * 2  # Cap is smaller than keepout
                else:
                    diameter = 9.0  # Default
                
                # Ensure minimum diameter for button caps
                diameter = max(diameter, min_button_hole_diameter)
                
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
        """Extract battery cavity dimensions for 2x AAA batteries side by side."""
        for comp in pcb_layout.get("components", []):
            if comp.get("type") == "battery":
                # Use standard 2xAAA dimensions regardless of keepout
                # AAA: 10.5mm diameter, 44.5mm length
                # 2 side by side: ~25mm wide, ~48mm long
                return {
                    "center_x": comp["center"][0],
                    "center_y": comp["center"][1],
                    "width": self.params.battery_compartment_width,  # 25mm for 2xAAA
                    "height": self.params.battery_compartment_height,  # 48mm for AAA length
                    "depth": self.params.shell_height  # Full wall height
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
        # Standard 12x12mm tactile button: 12.5mm between left/right columns, 5.0mm between pins on same side
        footprints = {
            "button": {"pinSpacingX": 12.5, "pinSpacingY": 5.0},  # 4 pads: ±6.25mm X, ±2.5mm Y from center
            "controller": {"pinSpacing": 2.5, "rowSpacing": 7.6},  # DIP-28: 2 rows of 14 pins
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
        led_windows: List[Dict[str, float]],
        ir_diodes: Optional[List[Dict[str, float]]] = None,
        battery_cavity: Optional[Dict[str, float]] = None
    ) -> str:
        """Generate OpenSCAD code for top shell (lid) with friction-fit rim.
        
        The top shell is a flat lid that sits on the bottom shell with:
        - A small inset rim for friction fit
        - Button holes and LED windows cut through
        """
        p = self.params
        
        # Generate friction-fit rim on bottom of lid
        friction_rim_code = self._generate_friction_rim_scad()
        
        scad = f"""// Top Shell (Lid) - Generated by ManufacturerAI
// This file is parametric - edit values below to customize
// Flat lid with friction-fit rim that sits inside the bottom shell walls

// Parameters
outer_width = {p.outer_width:.2f};
outer_length = {p.outer_length:.2f};
wall_thickness = {p.wall_thickness:.2f};
top_thickness = {p.top_thickness:.2f};
corner_radius = {p.corner_radius:.2f};

// Friction-fit rim parameters
rim_height = 3.0;  // Height of rim that fits inside bottom shell
rim_thickness = 1.2;  // Thickness of rim wall
rim_clearance = 0.3;  // Clearance for friction fit

// Main lid
module top_shell() {{
    difference() {{
        union() {{
            // Flat lid plate with rounded corners
            hull() {{
                for (x = [corner_radius, outer_width - corner_radius])
                    for (y = [corner_radius, outer_length - corner_radius])
                        translate([x, y, 0])
                            cylinder(r=corner_radius, h=top_thickness, $fn=32);
            }}
            
            // Friction-fit rim (hangs down from lid, fits inside bottom shell walls)
{friction_rim_code}
        }}
        
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
    
    def _generate_battery_guards_scad(self, battery_cavity: Optional[Dict[str, float]]) -> str:
        """Generate OpenSCAD code for battery guards on the bottom shell.
        
        Creates 4 walls forming a rectangle to hold the batteries.
        The guards sit on the floor of the bottom shell and extend upward
        to match the height of the surrounding walls.
        """
        if battery_cavity is None:
            return "    // No battery guards"
        
        lines = ["    // Battery guards - 4 walls to hold 2x AAA batteries"]
        p = self.params
        offset_x = p.wall_thickness + p.pcb_clearance
        offset_y = p.wall_thickness + p.pcb_clearance
        
        # Battery position and size
        cx = battery_cavity["center_x"] + offset_x
        cy = battery_cavity["center_y"] + offset_y
        width = battery_cavity["width"]
        height = battery_cavity["height"]
        
        # Guard dimensions - slightly larger than battery compartment
        guard_width = width + 2 * p.battery_guard_wall
        guard_height = height + 2 * p.battery_guard_wall
        wall = p.battery_guard_wall
        guard_z_height = p.shell_height - p.bottom_thickness  # Full height from floor to top of walls
        
        # Guards extend upward from the floor (Z = bottom_thickness)
        # Left wall
        lines.append(f"    translate([{cx - guard_width/2:.2f}, {cy - guard_height/2:.2f}, bottom_thickness])")
        lines.append(f"        cube([{wall:.2f}, {guard_height:.2f}, {guard_z_height:.2f}]);")
        
        # Right wall  
        lines.append(f"    translate([{cx + guard_width/2 - wall:.2f}, {cy - guard_height/2:.2f}, bottom_thickness])")
        lines.append(f"        cube([{wall:.2f}, {guard_height:.2f}, {guard_z_height:.2f}]);")
        
        # Front wall (bottom Y)
        lines.append(f"    translate([{cx - guard_width/2:.2f}, {cy - guard_height/2:.2f}, bottom_thickness])")
        lines.append(f"        cube([{guard_width:.2f}, {wall:.2f}, {guard_z_height:.2f}]);")
        
        # Back wall (top Y)
        lines.append(f"    translate([{cx - guard_width/2:.2f}, {cy + guard_height/2 - wall:.2f}, bottom_thickness])")
        lines.append(f"        cube([{guard_width:.2f}, {wall:.2f}, {guard_z_height:.2f}]);")
        
        return "\n".join(lines)
    
    def _generate_snap_clips_scad(self) -> str:
        """Generate OpenSCAD code for snap-fit clips on bottom shell walls.
        
        Creates small protruding hooks on the inside of the bottom shell walls
        that the friction rim of the top shell (lid) clicks onto.
        
        Note: No clips on back wall (where IR diode is) to avoid interference.
        """
        p = self.params
        lines = ["    // Snap-fit clips on inside of walls (left and right only)"]
        
        clip_width = 5.0  # Thinner clips
        clip_height = 1.5
        clip_depth = 1.0
        
        # Position clips near the top of the walls (where lid rim meets)
        clip_z = p.shell_height - 3.0  # 3mm from top edge of wall
        
        # Inset from corners - keep clips away from IR diode area at back
        front_inset = 15.0
        back_inset = 30.0  # Larger inset from back to avoid IR diode area
        
        # Left wall clips (X = wall_thickness)
        clip_x = p.wall_thickness
        lines.append(f"    // Left wall clips")
        lines.append(f"    translate([{clip_x:.2f}, {front_inset:.2f}, {clip_z:.2f}])")
        lines.append(f"        rotate([0, 0, 90]) snap_clip();")
        # Middle clip on left wall for long enclosures
        if p.outer_length > 100:
            mid_y = p.outer_length / 2 - clip_width / 2
            lines.append(f"    translate([{clip_x:.2f}, {mid_y:.2f}, {clip_z:.2f}])")
            lines.append(f"        rotate([0, 0, 90]) snap_clip();")
        lines.append(f"    translate([{clip_x:.2f}, {p.outer_length - back_inset - clip_width:.2f}, {clip_z:.2f}])")
        lines.append(f"        rotate([0, 0, 90]) snap_clip();")
        
        # Right wall clips (X = outer_width - wall_thickness)
        clip_x = p.outer_width - p.wall_thickness - clip_depth
        lines.append(f"    // Right wall clips")
        lines.append(f"    translate([{clip_x:.2f}, {front_inset + clip_width:.2f}, {clip_z:.2f}])")
        lines.append(f"        rotate([0, 0, -90]) snap_clip();")
        # Middle clip on right wall for long enclosures
        if p.outer_length > 100:
            mid_y = p.outer_length / 2 + clip_width / 2
            lines.append(f"    translate([{clip_x:.2f}, {mid_y:.2f}, {clip_z:.2f}])")
            lines.append(f"        rotate([0, 0, -90]) snap_clip();")
        lines.append(f"    translate([{clip_x:.2f}, {p.outer_length - back_inset:.2f}, {clip_z:.2f}])")
        lines.append(f"        rotate([0, 0, -90]) snap_clip();")
        
        # No front or back wall clips
        
        return "\n".join(lines)
    
    def _generate_friction_rim_scad(self) -> str:
        """Generate OpenSCAD code for friction-fit rim on top shell (lid).
        
        Creates a rim that hangs down from the lid and fits inside the 
        bottom shell's walls. The clips on the bottom shell walls catch
        this rim for a friction close.
        """
        p = self.params
        lines = ["            // Friction-fit rim hanging down from lid"]
        
        rim_height = 3.0
        rim_thickness = 1.2
        rim_clearance = 0.3  # Clearance to fit inside bottom shell walls
        
        # Rim hangs down from the underside of the lid
        # Positioned to fit just inside the bottom shell's walls
        rim_inset = p.wall_thickness + rim_clearance
        
        # Left rim
        lines.append(f"            translate([{rim_inset:.2f}, {rim_inset:.2f}, -{rim_height:.2f}])")
        lines.append(f"                cube([{rim_thickness:.2f}, {p.outer_length - 2*rim_inset:.2f}, {rim_height:.2f}]);")
        
        # Right rim
        lines.append(f"            translate([{p.outer_width - rim_inset - rim_thickness:.2f}, {rim_inset:.2f}, -{rim_height:.2f}])")
        lines.append(f"                cube([{rim_thickness:.2f}, {p.outer_length - 2*rim_inset:.2f}, {rim_height:.2f}]);")
        
        # Front rim
        lines.append(f"            translate([{rim_inset:.2f}, {rim_inset:.2f}, -{rim_height:.2f}])")
        lines.append(f"                cube([{p.outer_width - 2*rim_inset:.2f}, {rim_thickness:.2f}, {rim_height:.2f}]);")
        
        # Back rim
        lines.append(f"            translate([{rim_inset:.2f}, {p.outer_length - rim_inset - rim_thickness:.2f}, -{rim_height:.2f}])")
        lines.append(f"                cube([{p.outer_width - 2*rim_inset:.2f}, {rim_thickness:.2f}, {rim_height:.2f}]);")
        
        return "\n".join(lines)

    def _generate_bottom_shell_scad(
        self,
        mounting_posts: List[MountingPost],
        battery_cavity: Optional[Dict[str, float]],
        trace_channels: Optional[List[dict]] = None,
        all_pads: Optional[List[Tuple[float, float]]] = None,
        ir_diodes: Optional[List[Dict[str, float]]] = None
    ) -> str:
        """Generate OpenSCAD code for bottom shell with tall enclosing walls.
        
        The bottom shell has:
        - Tall enclosing walls around the perimeter
        - Snap-fit clips on the inside walls for the lid to click onto
        - Trace channels carved into the floor for conductive filament
        - Pinholes for component pins
        - Battery compartment cutout with ledges
        - IR diode holes through the back wall
        """
        p = self.params
        
        # Generate trace channel code if traces provided
        trace_channel_code = self._generate_trace_channels_scad(trace_channels) if trace_channels else "        // No trace channels"
        
        # Generate pinhole code for all component pads
        pinhole_code = self._generate_pinholes_scad(all_pads) if all_pads else "        // No pinholes"
        
        # Generate battery cavity cutout code
        battery_cutout_code = self._generate_battery_cutout_scad(battery_cavity) if battery_cavity else "        // No battery cavity"
        
        # Generate snap-fit clips on inside of walls
        snap_clips_code = self._generate_snap_clips_scad()
        
        # Generate IR diode slits (now in bottom shell walls)
        ir_slit_code = self._generate_ir_diode_slits_scad(ir_diodes) if ir_diodes else "        // No IR diode slits"
        
        # Generate battery guard code
        battery_guard_code = self._generate_battery_guards_scad(battery_cavity) if battery_cavity else "    // No battery guards"
        
        scad = f"""// Bottom Shell - Generated by ManufacturerAI
// This file is parametric - edit values below to customize
// Enclosing shell with tall walls and snap-fit clips for lid.
// Trace channels carved into the floor are meant to be filled
// with conductive filament after printing.

// Parameters
outer_width = {p.outer_width:.2f};
outer_length = {p.outer_length:.2f};
bottom_thickness = {p.bottom_thickness:.2f};
corner_radius = {p.corner_radius:.2f};
wall_thickness = {p.wall_thickness:.2f};
shell_height = {p.shell_height:.2f};  // Height of enclosing walls

// Trace channel parameters
trace_channel_depth = {p.trace_channel_depth:.2f};  // Depth of conductive trace channels
trace_channel_width = {p.trace_channel_width:.2f};  // Width of trace channels
pinhole_depth = {p.pinhole_depth:.2f};  // Depth of pinholes for component pins (2x trace depth)
pinhole_diameter = {p.pinhole_diameter:.2f};  // Diameter of pinholes

// Battery hatch parameters
battery_hatch_clearance = {p.battery_hatch_clearance:.2f};

// Snap-fit parameters
clip_width = 5.0;  // Thinner clips
clip_height = 1.5;
clip_depth = 1.0;  // How far clip protrudes

// Snap-fit clip module - small protruding hook
module snap_clip() {{
    // Ramped clip for easy insertion, hook for retention
    hull() {{
        cube([clip_width, 0.4, clip_height]);
        translate([0, clip_depth, clip_height * 0.6])
            cube([clip_width, 0.4, clip_height * 0.4]);
    }}
}}

// Main shell (base with tall enclosing walls)
module bottom_shell() {{
    difference() {{
        // Outer shell with walls
        hull() {{
            for (x = [corner_radius, outer_width - corner_radius])
                for (y = [corner_radius, outer_length - corner_radius])
                    translate([x, y, 0])
                        cylinder(r=corner_radius, h=shell_height, $fn=32);
        }}
        
        // Inner cavity (hollowed out, leaving walls and floor)
        translate([wall_thickness, wall_thickness, bottom_thickness])
            cube([outer_width - 2*wall_thickness, 
                  outer_length - 2*wall_thickness, 
                  shell_height]);
        
        // Battery compartment cutout (for spring-loaded hatch)
{battery_cutout_code}
        
        // Trace channels carved into floor (for conductive filament)
{trace_channel_code}
        
        // Pinholes for component pins (deeper than traces for good contact)
{pinhole_code}
        
        // IR diode holes through back wall
{ir_slit_code}
    }}
    
    // Snap-fit clips on inside of walls
{snap_clips_code}
    
    // Battery guards (4 walls to hold batteries)
{battery_guard_code}
}}

bottom_shell();
"""
        return scad
    
    def _generate_ir_diode_slits_scad(self, ir_diodes: List[Dict[str, float]]) -> str:
        """Generate OpenSCAD code for IR diode cutouts in the top shell.
        
        Creates a simple cylindrical hole through the end wall for each IR LED,
        centered on the diode's X position and at an appropriate height.
        """
        if not ir_diodes:
            return "        // No IR diode slits"
        
        lines = ["        // IR diode cutouts for IR transmission"]
        p = self.params
        offset_x = p.wall_thickness + p.pcb_clearance
        
        for diode in ir_diodes:
            x = diode["center_x"] + offset_x
            diameter = diode.get("diameter", 5.0)
            hole_diameter = diameter + 1.0  # 1mm clearance around LED
            
            # Simple cylindrical hole through the end wall
            # Positioned at the top edge (max Y), centered on diode X
            # Height is mid-wall level (shell_height / 2 in SCAD)
            lines.append(f"        // IR diode cutout {diode['id']}")
            lines.append(f"        translate([{x:.2f}, outer_length - wall_thickness - 0.5, shell_height / 2])")
            lines.append(f"            rotate([-90, 0, 0])")
            lines.append(f"                cylinder(d={hole_diameter:.2f}, h=wall_thickness + 1, $fn=32);")
        
        return "\n".join(lines)
    
    def _generate_battery_cutout_scad(self, battery_cavity: Optional[Dict[str, float]]) -> str:
        """Generate OpenSCAD code for battery compartment cutout in bottom shell.
        
        Creates a stepped opening for the spring-loaded hatch:
        1. Main cutout - through hole for battery access (full width/height)
        2. Side ledges (long edges only) - where the hatch panel rests flush
        
        The hatch inserts from below, the panel rests on the side ledges,
        and the spring hooks wrap around the shell edge to catch on the inside.
        Short edges have no lip - flat end for spring latch clearance.
        """
        if battery_cavity is None:
            return "        // No battery cavity"
        
        lines = ["        // Battery compartment opening for hatch"]
        p = self.params
        offset_x = p.wall_thickness + p.pcb_clearance
        offset_y = p.wall_thickness + p.pcb_clearance
        
        # Battery position and size from layout
        cx = battery_cavity["center_x"] + offset_x
        cy = battery_cavity["center_y"] + offset_y
        width = battery_cavity["width"]
        height = battery_cavity["height"]
        
        # Ledge dimensions
        hatch_thickness = p.battery_hatch_thickness
        ledge_width = 2.5  # Width of ledge on long edges
        ledge_depth = hatch_thickness + 0.3  # Recess depth for hatch panel
        
        # Main through-hole dimensions (narrower on long edges for ledge, full height)
        hole_width = width - 2 * ledge_width
        hole_height = height  # Full height - no ledge on short edges
        
        # 1. Main through-hole (center of battery compartment)
        lines.append(f"        // Main through-hole for battery access")
        lines.append(f"        translate([{cx - hole_width/2:.2f}, {cy - hole_height/2:.2f}, -1])")
        lines.append(f"            cube([{hole_width:.2f}, {hole_height:.2f}, bottom_thickness + 2]);")
        
        # 2. Side ledge recesses (long edges only - left and right sides)
        lines.append(f"        // Left ledge recess - hatch rests here")
        lines.append(f"        translate([{cx - width/2:.2f}, {cy - height/2:.2f}, -1])")
        lines.append(f"            cube([{ledge_width:.2f}, {height:.2f}, {ledge_depth + 1:.2f}]);")
        
        lines.append(f"        // Right ledge recess - hatch rests here")
        lines.append(f"        translate([{cx + width/2 - ledge_width:.2f}, {cy - height/2:.2f}, -1])")
        lines.append(f"            cube([{ledge_width:.2f}, {height:.2f}, {ledge_depth + 1:.2f}]);")
        
        # 3. Dent for hatch ledge notch (back end opposite spring latch)
        # The hatch has an 8mm wide, 2mm deep, 1.5mm tall ledge that hooks into this dent
        ledge_notch_width = 8.0
        ledge_notch_depth = 2.0 + 0.3  # Extra clearance for fit
        ledge_notch_height = 1.5 + 0.3  # Extra clearance
        lines.append(f"        // Dent for hatch ledge notch (back end)")
        lines.append(f"        translate([{cx - ledge_notch_width/2:.2f}, {cy + height/2 - ledge_notch_depth:.2f}, {ledge_depth - 0.5:.2f}])")
        lines.append(f"            cube([{ledge_notch_width:.2f}, {ledge_notch_depth + 1:.2f}, {ledge_notch_height + 1:.2f}]);")
        
        return "\n".join(lines)
    
    def _generate_battery_hatch_scad(self, battery_cavity: Dict[str, float]) -> str:
        """Generate OpenSCAD code for battery compartment hatch with spring latch.
        
        The hatch is a flat panel that covers the battery compartment with:
        - A lip around the edge to prevent falling through
        - A spring loop embedded within the plate (with a slit cutout for flex)
        - A hook on the spring tip that catches on the ledge in the bottom shell
        """
        p = self.params
        
        width = battery_cavity["width"]
        height = battery_cavity["height"]
        
        # Hatch dimensions
        hatch_thickness = p.battery_hatch_thickness
        lip_width = 1.5  # Lip extends beyond cutout
        
        # Spring loop dimensions
        loop_width = p.spring_loop_width
        loop_height = p.spring_loop_height
        loop_thickness = p.spring_loop_thickness
        
        # Hook dimensions (catches on ledge)
        hook_height = 1.5
        hook_depth = 1.5
        
        # Slit dimensions for spring flex
        slit_width = loop_width + 1.0  # Slightly wider than spring
        slit_length = loop_height + 2.0  # Length of slit in plate
        
        scad = f"""// Battery Hatch with Spring Latch - Generated by ManufacturerAI
// This hatch snaps into the battery compartment opening.
// The spring loop is embedded in the plate with a slit for flex.

// Parameters
hatch_width = {width:.2f};
hatch_height = {height:.2f};
hatch_thickness = {hatch_thickness:.2f};
lip_width = {lip_width:.2f};
loop_width = {loop_width:.2f};
loop_height = {loop_height:.2f};
loop_thickness = {loop_thickness:.2f};
hook_height = {hook_height:.2f};
hook_depth = {hook_depth:.2f};
slit_width = {slit_width:.2f};
slit_length = {slit_length:.2f};

$fn = 32;

module spring_latch() {{
    // Spring latch: arm extends up, curves over, comes back to plate
    // Hook and lip are on the outward arm (extends farthest from plate)
    
    arm_gap = loop_thickness * 2;  // Gap between outward and return arms
    bend_radius = arm_gap / 2 + loop_thickness / 2;  // Center radius of the bend
    
    // Outward arm (goes up/away from hatch) - this has the hook and lip
    translate([0, 0, 0])
        cube([loop_width, loop_thickness, loop_height]);
    
    // Hook base at the tip of outward arm (catches on ledge)
    translate([0, -hook_depth, 2])
        cube([loop_width, hook_depth + loop_thickness, hook_height]);
       
    // Curved top connecting the two arms
    translate([loop_width/2, loop_thickness + arm_gap/2, loop_height])
        rotate([90, 0, 90])
            rotate_extrude(angle=180, $fn=32)
                translate([bend_radius, 0, 0])
                    square([loop_thickness, loop_width], center=true);
    
    // Return arm (comes back toward plate) - connects to plate, no accessories
    translate([0, loop_thickness + arm_gap, 0])
        cube([loop_width, loop_thickness, loop_height]);
}}

module battery_hatch() {{
    arm_gap = loop_thickness * 2;  // Must match spring_latch module
    spring_total_depth = loop_thickness * 2 + arm_gap;  // Total Y depth of spring
    
    difference() {{
        // Main hatch body
        cube([hatch_width, hatch_height, hatch_thickness]);
        
        // Slit cutout for spring to flex through
        // Follows the spring position, allowing hook to extend beyond edge
        translate([(hatch_width - slit_width) / 2, -hook_depth - 1 + 2, -1])
            cube([slit_width, spring_total_depth + hook_depth, hatch_thickness + 2]);
    }}
    
    // Spring latch - positioned so outward arm with hook extends beyond plate edge
    // Return arm connects to plate, outward arm extends outward with hook
    // Moved 2mm back from edge for better fit
    translate([(hatch_width - loop_width) / 2, 2, 0])
        spring_latch();
    
    // Ledge notch on opposite end
    translate([(hatch_width - 8) / 2, hatch_height - 1, hatch_thickness])
        cube([8, 2, 1.5]);
}}

battery_hatch();
"""
        return scad
    
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
    
    def _generate_combined_assembly_scad(
        self,
        battery_cavity: Optional[Dict[str, float]],
        pcb_layout: Optional[dict] = None,
        trace_channels: Optional[List[dict]] = None,
        all_pads: Optional[List[Tuple[float, float]]] = None
    ) -> str:
        """Generate OpenSCAD code for combined print plate.
        
        Places all parts side by side for printing on one plate:
        - Bottom shell (flat, trace channels facing up)
        - Top shell (flat, button holes facing up)
        - Battery hatch
        
        Each part is in its optimal print orientation.
        """
        p = self.params
        
        # Include the other SCAD files
        hatch_include = 'use <battery_hatch.scad>' if battery_cavity else ''
        
        # Spacing between parts
        gap = 10.0
        
        # Calculate positions - all parts side by side along X axis
        bottom_x = 0
        top_x = p.outer_width + gap
        hatch_x = 2 * p.outer_width + 2 * gap if battery_cavity else 0
        
        hatch_placement = ''
        if battery_cavity:
            hatch_placement = f'''
// Battery hatch - beside top shell
translate([{hatch_x:.2f}, {p.outer_length/2 - battery_cavity["height"]/2:.2f}, 0])
    battery_hatch();
'''
        
        scad = f"""// Combined Print Plate - Generated by ManufacturerAI
// All parts laid out side by side for single-plate printing
// Each part is in optimal print orientation (flat)

use <bottom_shell.scad>
use <top_shell.scad>
{hatch_include}

// Parameters
outer_width = {p.outer_width:.2f};
outer_length = {p.outer_length:.2f};
gap = {gap:.2f};

// Bottom shell - at origin, trace channels facing up
translate([{bottom_x:.2f}, 0, 0])
    bottom_shell();

// Top shell - beside bottom shell, button holes facing up
// No rotation needed - prints with top plate at Z=0
translate([{top_x:.2f}, 0, 0])
    top_shell();
{hatch_placement}
"""
        return scad
    
    def _generate_support_pillars_scad(
        self,
        battery_cavity: Optional[Dict[str, float]],
        pcb_layout: Optional[dict] = None,
        trace_channels: Optional[List[dict]] = None,
        all_pads: Optional[List[Tuple[float, float]]] = None
    ) -> str:
        """Generate OpenSCAD code for internal support pillars.
        
        Places cylindrical pillars at strategic locations inside the enclosure
        to support the top shell during printing. Pillars are placed:
        - Near the corners (but inside the walls)
        - Along the long edges at regular intervals
        - Avoiding battery cavity, components, traces, and pinholes
        """
        p = self.params
        lines = ["// Support pillars for bridging - avoiding traces and components"]
        
        # Offset from PCB coordinates to enclosure coordinates
        offset_x = p.wall_thickness + p.pcb_clearance
        offset_y = p.wall_thickness + p.pcb_clearance
        
        # Pillar radius for collision detection
        pillar_radius = 2.0  # Slightly larger than half diameter for safety margin
        
        # Collect all exclusion zones (circles with center and radius)
        exclusion_zones = []
        
        # 1. Add component keepout zones
        if pcb_layout and "components" in pcb_layout:
            for comp in pcb_layout["components"]:
                cx, cy = comp["center"]
                # Convert to enclosure coordinates
                cx_enc = cx + offset_x
                cy_enc = cy + offset_y
                
                keepout = comp.get("keepout", {})
                if keepout.get("type") == "circle":
                    radius = keepout.get("radius_mm", 5.0) + 3.0  # Add margin
                    exclusion_zones.append((cx_enc, cy_enc, radius))
                elif keepout.get("type") == "rectangle":
                    # Use half-diagonal as exclusion radius
                    w = keepout.get("width_mm", 10.0) / 2 + 3.0
                    h = keepout.get("height_mm", 10.0) / 2 + 3.0
                    radius = (w**2 + h**2) ** 0.5
                    exclusion_zones.append((cx_enc, cy_enc, radius))
                else:
                    # Default 8mm exclusion for unknown components
                    exclusion_zones.append((cx_enc, cy_enc, 8.0))
        
        # 2. Add battery cavity exclusion
        if battery_cavity:
            bat_cx = battery_cavity["center_x"] + offset_x
            bat_cy = battery_cavity["center_y"] + offset_y
            bat_w = battery_cavity["width"] / 2 + 5.0
            bat_h = battery_cavity["height"] / 2 + 5.0
            bat_radius = (bat_w**2 + bat_h**2) ** 0.5
            exclusion_zones.append((bat_cx, bat_cy, bat_radius))
        
        # 3. Add pinhole exclusion zones (smaller radius around each pad)
        if all_pads:
            for (px, py) in all_pads:
                px_enc = px + offset_x
                py_enc = py + offset_y
                exclusion_zones.append((px_enc, py_enc, 3.0))  # 3mm around each pinhole
        
        # 4. Add trace exclusion zones (sample points along traces)
        if trace_channels:
            for trace in trace_channels:
                segments = trace.get("segments", [])
                for seg in segments:
                    x1, y1 = seg.get("start", [0, 0])
                    x2, y2 = seg.get("end", [0, 0])
                    # Sample points along segment
                    length = ((x2-x1)**2 + (y2-y1)**2) ** 0.5
                    num_samples = max(2, int(length / 5.0))  # Sample every 5mm
                    for i in range(num_samples + 1):
                        t = i / num_samples if num_samples > 0 else 0
                        sx = x1 + t * (x2 - x1) + offset_x
                        sy = y1 + t * (y2 - y1) + offset_y
                        exclusion_zones.append((sx, sy, 2.5))  # 2.5mm around traces
        
        # Helper function to check if position conflicts with exclusion zones
        def is_valid_position(px, py):
            for (ex, ey, er) in exclusion_zones:
                dist = ((px - ex)**2 + (py - ey)**2) ** 0.5
                if dist < er + pillar_radius:
                    return False
            return True
        
        # Generate candidate pillar positions
        # Use a grid approach for better coverage
        pillar_positions = []
        
        # Inset from walls (pillars should be inside the cavity)
        inset = p.wall_thickness + 2.0
        
        # Grid spacing - place pillars every ~20mm for good support
        grid_spacing = 18.0
        
        # Generate grid of candidate positions
        x = inset
        while x < p.outer_width - inset:
            y = inset
            while y < p.outer_length - inset:
                if is_valid_position(x, y):
                    pillar_positions.append((x, y))
                y += grid_spacing
            x += grid_spacing
        
        # Also add edge positions for better perimeter support
        edge_positions = [
            (inset, inset),  # Bottom-left
            (p.outer_width - inset, inset),  # Bottom-right
            (inset, p.outer_length - inset),  # Top-left
            (p.outer_width - inset, p.outer_length - inset),  # Top-right
        ]
        for pos in edge_positions:
            if is_valid_position(pos[0], pos[1]) and pos not in pillar_positions:
                pillar_positions.append(pos)
        
        # If no valid positions found, warn and return empty
        if not pillar_positions:
            lines.append("// WARNING: No valid pillar positions found - all positions conflict with components/traces")
            lines.append("// Consider using slicer-generated supports instead")
            return "\n".join(lines)
        
        lines.append(f"// Generated {len(pillar_positions)} support pillars")
        
        # Generate SCAD for each pillar
        for i, (px, py) in enumerate(pillar_positions):
            lines.append(f"translate([{px:.2f}, {py:.2f}, bottom_thickness])")
            lines.append(f"    cylinder(d=pillar_diameter, h=pillar_height, $fn=16);")
        
        return "\n".join(lines)

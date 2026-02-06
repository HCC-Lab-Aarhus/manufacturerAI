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

from src.core.hardware_config import (
    board as hw_board,
    footprints as hw_footprints,
    manufacturing as hw_manufacturing,
    enclosure as hw_enclosure,
)


@dataclass
class EnclosureParams:
    """Parameters for enclosure generation."""
    # From PCB (set by _configure_from_layout)
    board_width: float = 41.0
    board_length: float = 176.0
    board_thickness: float = field(default_factory=lambda: hw_board()["pcb_thickness_mm"])
    
    # Enclosure walls
    wall_thickness: float = field(default_factory=lambda: hw_enclosure()["wall_thickness_mm"])
    bottom_thickness: float = field(default_factory=lambda: hw_enclosure()["bottom_thickness_mm"])
    top_thickness: float = field(default_factory=lambda: hw_enclosure()["top_thickness_mm"])
    
    # Clearances
    pcb_clearance: float = field(default_factory=lambda: hw_enclosure()["pcb_clearance_mm"])
    button_hole_clearance: float = field(default_factory=lambda: hw_footprints()["button"]["hole_clearance_mm"])
    
    # Features
    corner_radius: float = field(default_factory=lambda: hw_enclosure()["corner_radius_mm"])
    
    # Trace channels (for conductive filament)
    trace_channel_depth: float = field(default_factory=lambda: hw_manufacturing()["trace_channel_depth_mm"])
    trace_channel_width: float = field(default_factory=lambda: hw_manufacturing()["trace_width_mm"])
    pinhole_depth: float = field(default_factory=lambda: hw_manufacturing()["pinhole_depth_mm"])
    pinhole_diameter: float = field(default_factory=lambda: hw_manufacturing()["pinhole_diameter_mm"])
    grid_resolution: float = field(default_factory=lambda: hw_board()["grid_resolution_mm"])
    
    # Battery compartment (2x AAA side by side)
    battery_compartment_width: float = field(default_factory=lambda: hw_footprints()["battery"]["compartment_width_mm"])
    battery_compartment_height: float = field(default_factory=lambda: hw_footprints()["battery"]["compartment_height_mm"])
    battery_guard_wall: float = field(default_factory=lambda: hw_enclosure()["battery_guard_wall_mm"])
    shell_height: float = field(default_factory=lambda: hw_enclosure()["shell_height_mm"])
    
    # Battery hatch
    battery_hatch_clearance: float = field(default_factory=lambda: hw_enclosure()["battery_hatch_clearance_mm"])
    battery_hatch_thickness: float = field(default_factory=lambda: hw_enclosure()["battery_hatch_thickness_mm"])
    spring_loop_width: float = field(default_factory=lambda: hw_enclosure()["spring_loop_width_mm"])
    spring_loop_height: float = field(default_factory=lambda: hw_enclosure()["spring_loop_height_mm"])
    spring_loop_thickness: float = field(default_factory=lambda: hw_enclosure()["spring_loop_thickness_mm"])
    
    # Derived
    @property
    def outer_width(self) -> float:
        return self.board_width + 2 * (self.wall_thickness + self.pcb_clearance)
    
    @property
    def outer_length(self) -> float:
        return self.board_length + 2 * (self.wall_thickness + self.pcb_clearance)
    
    @property
    def total_height(self) -> float:
        return self.bottom_thickness + self.shell_height + self.board_thickness + 5.0


@dataclass
class ButtonHole:
    """Button hole specification."""
    id: str
    center_x: float
    center_y: float
    diameter: float


class Enclosure3DAgent:
    """
    Parametric 3D enclosure generator.
    
    Reads pcb_layout.json and generates:
    - remote.stl (unified enclosure with button holes, trace channels, and component cutouts)
    - battery_hatch.stl (removable battery compartment cover)
    - print_plate.stl (all parts laid out for printing)
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
        battery_cavity = self._extract_battery_cavity(pcb_layout)
        led_windows = self._extract_led_windows(pcb_layout)
        ir_diodes = self._extract_diodes(pcb_layout)
        print(f"[ENCLOSURE] Features: {len(button_holes)} buttons, {len(led_windows)} LEDs, {len(ir_diodes)} diodes")
        
        # Extract trace channels if routing result provided
        trace_channels = []
        all_pads = []
        if routing_result and routing_result.get("traces"):
            trace_channels = routing_result["traces"]
            print(f"[ENCLOSURE] Trace channels: {len(trace_channels)} nets for conductive filament")
        
        # Extract ALL pads from component footprints (including unconnected pads)
        all_pads = self._extract_all_pads_from_layout(pcb_layout, grid_resolution=hw_board()["grid_resolution_mm"])
        print(f"[ENCLOSURE] Component pads: {len(all_pads)} pinholes for component pins")
        
        # Generate unified remote SCAD (single closed shell)
        print("[ENCLOSURE] PATH: Generating unified remote SCAD...")
        remote_scad = self._generate_unified_remote_scad(
            button_holes=button_holes,
            battery_cavity=battery_cavity,
            ir_diodes=ir_diodes,
            trace_channels=trace_channels,
            all_pads=all_pads,
            pcb_layout=pcb_layout
        )
        
        # Write remote SCAD file (needed for print_plate assembly)
        remote_scad_path = output_dir / "remote.scad"
        remote_scad_path.write_text(remote_scad, encoding="utf-8")
        
        # Collect outputs
        outputs = {}
        
        # Generate battery hatch if battery present (needed for print_plate assembly)
        battery_hatch_path = None
        if battery_cavity:
            battery_hatch_scad = self._generate_battery_hatch_scad(battery_cavity)
            battery_hatch_path = output_dir / "battery_hatch.scad"
            battery_hatch_path.write_text(battery_hatch_scad, encoding="utf-8")
        
        # Generate print plate assembly (the only STL we render)
        print_plate_scad = self._generate_print_plate_scad(battery_cavity)
        print_plate_scad_path = output_dir / "print_plate.scad"
        print_plate_scad_path.write_text(print_plate_scad, encoding="utf-8")
        outputs["print_plate_scad"] = print_plate_scad_path
        
        # Render only print_plate.stl (contains both remote and hatch)
        print("[ENCLOSURE] Rendering print_plate.stl...")
        print_plate_stl_path = output_dir / "print_plate.stl"
        if self._render_scad_to_stl(print_plate_scad_path, print_plate_stl_path):
            outputs["print_plate_stl"] = print_plate_stl_path
            print(f"[ENCLOSURE] ✓ Rendered print_plate.stl")
        else:
            print("[ENCLOSURE] ⚠ Could not render print_plate.stl")
        
        # Also generate manifest
        manifest = self._generate_manifest(button_holes)
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
        self.params.board_thickness = pcb_layout["board"].get("thickness_mm", hw_board()["pcb_thickness_mm"])
        
        # From design spec
        if design_spec:
            constraints = design_spec.get("constraints", {})
            self.params.wall_thickness = constraints.get("min_wall_thickness_mm", hw_enclosure()["wall_thickness_mm"])
    
    def _extract_button_holes(self, pcb_layout: dict) -> List[ButtonHole]:
        """Extract button hole specifications from layout."""
        holes = []
        
        # Minimum button hole diameter for proper button cap fit
        btn_fp = hw_footprints()["button"]
        min_button_hole_diameter = btn_fp["min_hole_diameter_mm"]
        
        for comp in pcb_layout.get("components", []):
            if comp.get("type") == "button":
                keepout = comp.get("keepout", {})
                
                # Button cap diameter
                if keepout.get("type") == "circle":
                    diameter = (keepout.get("radius_mm", 5) - 1.5) * 2  # Cap is smaller than keepout
                else:
                    diameter = btn_fp["cap_diameter_mm"]
                
                # Ensure minimum diameter for button caps
                diameter = max(diameter, min_button_hole_diameter)
                
                holes.append(ButtonHole(
                    id=comp["id"],
                    center_x=comp["center"][0],
                    center_y=comp["center"][1],
                    diameter=diameter + self.params.button_hole_clearance
                ))
        
        return holes
    
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
        """Extract LED window locations.
        
        Note: Currently returns empty list since we only have diodes (IR LED),
        not indicator LEDs. This is kept for future use if indicator LEDs are added.
        """
        return []  # No indicator LEDs in current design
    
    def _extract_diodes(self, pcb_layout: dict) -> List[Dict[str, float]]:
        """Extract diode locations for slit cutouts.
        
        Diodes (IR LEDs) should be at the top of the remote
        with a slit in the enclosure so the diode can point outward.
        """
        diodes = []
        
        for comp in pcb_layout.get("components", []):
            if comp.get("type") == "diode":
                diodes.append({
                    "id": comp["id"],
                    "center_x": comp["center"][0],
                    "center_y": comp["center"][1],
                    "diameter": hw_footprints()["diode"]["diameter_mm"]  # Standard 5mm IR LED
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
        
        # Footprint definitions from shared hardware config
        fp = hw_footprints()
        footprints = {
            "button": {"pinSpacingX": fp["button"]["pin_spacing_x_mm"], "pinSpacingY": fp["button"]["pin_spacing_y_mm"]},
            "controller": {"pinSpacing": fp["controller"]["pin_spacing_mm"], "rowSpacing": fp["controller"]["row_spacing_mm"]},
            "battery": {"padSpacing": fp["battery"]["pad_spacing_mm"]},
            "diode": {"padSpacing": fp["diode"]["pad_spacing_mm"]}
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
                num_pins_per_side = hw_footprints()["controller"]["pins_per_side"]
                
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
            
            elif comp_type == "diode":
                # 2 pads along horizontal axis (matches TypeScript router: x - halfSpacing, x + halfSpacing)
                fp = footprints["diode"]
                pad_spacing = fp["padSpacing"]
                pads.append((int((center_x - pad_spacing / 2) / grid_resolution), int(center_y / grid_resolution)))
                pads.append((int((center_x + pad_spacing / 2) / grid_resolution), int(center_y / grid_resolution)))
        
        return pads
    
    def _generate_unified_remote_scad(
        self,
        button_holes: List[ButtonHole],
        battery_cavity: Optional[Dict[str, float]],
        ir_diodes: Optional[List[Dict[str, float]]] = None,
        trace_channels: Optional[List[dict]] = None,
        all_pads: Optional[List[Tuple[float, float]]] = None,
        pcb_layout: Optional[dict] = None
    ) -> str:
        """Generate OpenSCAD code for unified remote enclosure (single piece).
        
        The remote is a single closed shell with:
        - Floor with trace channels and pinholes
        - Walls around the perimeter
        - Solid top (ceiling) with button holes cut through
        - IR diode hole in the back wall
        - Battery compartment with cutout for hatch
        - Component cutouts for placing parts after printing
        """
        p = self.params
        
        # Generate all the subcomponent code
        trace_channel_code = self._generate_trace_channels_scad(trace_channels) if trace_channels else "        // No trace channels"
        pinhole_code = self._generate_pinholes_scad(all_pads) if all_pads else "        // No pinholes"
        battery_cutout_code = self._generate_battery_cutout_scad(battery_cavity) if battery_cavity else "        // No battery cavity"
        ir_slit_code = self._generate_ir_diode_slits_scad(ir_diodes) if ir_diodes else "        // No IR diode slits"
        battery_guard_code = self._generate_battery_guards_scad(battery_cavity) if battery_cavity else "    // No battery guards"
        component_cutout_code = self._generate_component_cutouts_scad(pcb_layout) if pcb_layout else "        // No component cutouts"
        button_holes_code = self._generate_button_holes_scad(button_holes)
        
        # Total height = floor + inner cavity + ceiling
        total_height = p.bottom_thickness + p.shell_height + p.top_thickness
        inner_height = p.shell_height  # Height of inner cavity
        
        scad = f"""// Unified Remote Enclosure - Generated by ManufacturerAI
// Single-piece closed shell with button holes, trace channels, and component cutouts
// This file is parametric - edit values below to customize

// ═══════════════════════════════════════════════════════════════════════════
// PARAMETERS
// ═══════════════════════════════════════════════════════════════════════════

// Outer dimensions
outer_width = {p.outer_width:.2f};
outer_length = {p.outer_length:.2f};
total_height = {total_height:.2f};
corner_radius = {p.corner_radius:.2f};

// Wall and floor/ceiling thickness
wall_thickness = {p.wall_thickness:.2f};
bottom_thickness = {p.bottom_thickness:.2f};  // Floor thickness
top_thickness = {p.top_thickness:.2f};  // Ceiling thickness
shell_height = {p.shell_height:.2f};  // Inner cavity height

// Trace channel parameters (for conductive filament)
trace_channel_depth = {p.trace_channel_depth:.2f};
trace_channel_width = {p.trace_channel_width:.2f};
pinhole_depth = {p.pinhole_depth:.2f};
pinhole_diameter = {p.pinhole_diameter:.2f};

// Battery hatch parameters
battery_hatch_clearance = {p.battery_hatch_clearance:.2f};

// ═══════════════════════════════════════════════════════════════════════════
// MAIN MODULE
// ═══════════════════════════════════════════════════════════════════════════

module remote() {{
    difference() {{
        union() {{
            difference() {{
                // Outer shell - closed box with rounded corners
                hull() {{
                    for (x = [corner_radius, outer_width - corner_radius])
                        for (y = [corner_radius, outer_length - corner_radius])
                            translate([x, y, 0])
                                cylinder(r=corner_radius, h=total_height, $fn=32);
                }}
                
                // Inner cavity (hollowed out, leaving floor, walls, and ceiling)
                translate([wall_thickness, wall_thickness, bottom_thickness])
                    cube([outer_width - 2*wall_thickness, 
                          outer_length - 2*wall_thickness, 
                          shell_height]);
                
                // Battery compartment cutout (for spring-loaded hatch)
{battery_cutout_code}
                
                // IR diode hole through back wall
{ir_slit_code}
            }}
            
            // Solid fill between battery guard boundary and outer walls
{battery_guard_code}
        }}
        
        // Button holes cut through the top (ceiling)
{button_holes_code}
        
        // Trace channels carved into floor (for conductive filament)
{trace_channel_code}
        
        // Pinholes for component pins
{pinhole_code}
        
        // Component cutouts through solid fill
{component_cutout_code}
    }}
}}

remote();
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
        """Generate OpenSCAD code for solid battery fill on the bottom shell.
        
        Fills the entire space between the battery guard boundary and the
        outer walls with solid plastic.  The battery pocket itself stays
        open (handled by the battery cutout subtraction).
        The fill is as tall as the surrounding walls.
        """
        if battery_cavity is None:
            return "    // No battery guards"
        
        lines = ["    // Solid fill between battery guard boundary and outer walls"]
        p = self.params
        offset_x = p.wall_thickness + p.pcb_clearance
        offset_y = p.wall_thickness + p.pcb_clearance
        
        # Battery position and size
        cx = battery_cavity["center_x"] + offset_x
        cy = battery_cavity["center_y"] + offset_y
        width = battery_cavity["width"]
        height = battery_cavity["height"]
        
        # Guard boundary (slightly larger than battery compartment)
        guard_width = width + 2 * p.battery_guard_wall
        guard_height = height + 2 * p.battery_guard_wall
        guard_z_height = p.shell_height  # Fill entire inner cavity height to meet ceiling
        
        # Inner cavity bounds
        inner_x_min = p.wall_thickness
        inner_x_max = p.outer_width - p.wall_thickness
        inner_y_min = p.wall_thickness
        inner_y_max = p.outer_length - p.wall_thickness
        
        # Guard boundary coords
        guard_x_min = cx - guard_width / 2
        guard_x_max = cx + guard_width / 2
        guard_y_min = cy - guard_height / 2
        guard_y_max = cy + guard_height / 2
        
        # Left fill: inner wall to guard left edge, full Y
        left_w = guard_x_min - inner_x_min
        if left_w > 0.1:
            lines.append(f"    translate([{inner_x_min:.2f}, {inner_y_min:.2f}, bottom_thickness])")
            lines.append(f"        cube([{left_w:.2f}, {inner_y_max - inner_y_min:.2f}, {guard_z_height:.2f}]);")
        
        # Right fill: guard right edge to inner wall, full Y
        right_w = inner_x_max - guard_x_max
        if right_w > 0.1:
            lines.append(f"    translate([{guard_x_max:.2f}, {inner_y_min:.2f}, bottom_thickness])")
            lines.append(f"        cube([{right_w:.2f}, {inner_y_max - inner_y_min:.2f}, {guard_z_height:.2f}]);")
        
        # Front fill: guard left to guard right, inner wall to guard front
        front_h = guard_y_min - inner_y_min
        if front_h > 0.1:
            lines.append(f"    translate([{guard_x_min:.2f}, {inner_y_min:.2f}, bottom_thickness])")
            lines.append(f"        cube([{guard_width:.2f}, {front_h:.2f}, {guard_z_height:.2f}]);")
        
        # Back fill: guard left to guard right, guard back to inner wall
        back_h = inner_y_max - guard_y_max
        if back_h > 0.1:
            lines.append(f"    translate([{guard_x_min:.2f}, {guard_y_max:.2f}, bottom_thickness])")
            lines.append(f"        cube([{guard_width:.2f}, {back_h:.2f}, {guard_z_height:.2f}]);")
        
        return "\n".join(lines)

    def _generate_component_cutouts_scad(self, pcb_layout: Optional[dict]) -> str:
        """Generate OpenSCAD code for rectangular component cutouts.
        
        Creates rectangular pockets that cut through the solid fill
        down to the floor (bottom_thickness), so components can be placed
        into the shell after printing. Battery components are skipped
        since they have their own dedicated cutout.
        """
        if not pcb_layout or "components" not in pcb_layout:
            return "        // No component cutouts"
        
        lines = ["        // Component cutouts - rectangular pockets for placing components"]
        p = self.params
        offset_x = p.wall_thickness + p.pcb_clearance
        offset_y = p.wall_thickness + p.pcb_clearance
        clearance = hw_board()["component_margin_mm"]  # clearance around each component
        
        for comp in pcb_layout["components"]:
            if comp.get("type") == "battery":
                continue  # Battery has its own dedicated cutout
            
            comp_id = comp.get("id", "unknown")
            cx = comp["center"][0] + offset_x
            cy = comp["center"][1] + offset_y
            keepout = comp.get("keepout", {})
            
            if keepout.get("type") == "circle":
                radius = keepout.get("radius_mm", 5.0)
                # Convert circle to bounding rectangle
                w = (radius + clearance) * 2
                h = w
            elif keepout.get("type") == "rectangle":
                w = keepout.get("width_mm", 10.0) + 2 * clearance
                h = keepout.get("height_mm", 10.0) + 2 * clearance
            else:
                # Default 10x10mm for unknown components
                w = 10.0 + 2 * clearance
                h = 10.0 + 2 * clearance
            
            # Cut from floor up through the full inner cavity height
            lines.append(f"        // {comp_id} ({comp.get('type', 'unknown')})")
            lines.append(f"        translate([{cx - w/2:.2f}, {cy - h/2:.2f}, bottom_thickness])")
            lines.append(f"            cube([{w:.2f}, {h:.2f}, shell_height + 0.01]);")
        
        return "\n".join(lines)
    
    def _generate_ir_diode_slits_scad(self, ir_diodes: List[Dict[str, float]]) -> str:
        """Generate OpenSCAD code for IR diode cutouts in the top shell.
        
        Creates a simple cylindrical hole through the end wall for each IR LED,
        centered on the diode's X position and at an appropriate height.
        """
        if not ir_diodes:
            return "        // No IR diode slits"
        
        lines = ["        // Diode cutouts for IR transmission"]
        p = self.params
        offset_x = p.wall_thickness + p.pcb_clearance
        
        for diode in ir_diodes:
            x = diode["center_x"] + offset_x
            diameter = diode.get("diameter", hw_footprints()["diode"]["diameter_mm"])
            hole_diameter = diameter + hw_footprints()["diode"]["hole_clearance_mm"]
            
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
                # Cut from below the floor up through the full shell height
                # so traces pass through any solid fill above the floor
                if abs(x2 - x1) > 0.01:  # Horizontal
                    min_x = min(x1, x2) - half_width
                    max_x = max(x1, x2) + half_width
                    lines.append(f"        translate([{min_x:.2f}, {y1 - half_width:.2f}, bottom_thickness - trace_channel_depth])")
                    lines.append(f"            cube([{max_x - min_x:.2f}, {p.trace_channel_width:.2f}, shell_height - bottom_thickness + trace_channel_depth + 0.01]);")
                else:  # Vertical
                    min_y = min(y1, y2) - half_width
                    max_y = max(y1, y2) + half_width
                    lines.append(f"        translate([{x1 - half_width:.2f}, {min_y:.2f}, bottom_thickness - trace_channel_depth])")
                    lines.append(f"            cube([{p.trace_channel_width:.2f}, {max_y - min_y:.2f}, shell_height - bottom_thickness + trace_channel_depth + 0.01]);")
                
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
            lines.append(f"            cube([{pad_size:.2f}, {pad_size:.2f}, shell_height - bottom_thickness + trace_channel_depth + 0.01]);")
        
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
            lines.append(f"            cylinder(d=pinhole_diameter, h=shell_height - bottom_thickness + pinhole_depth + 0.01, $fn=16);")
        
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
        button_holes: List[ButtonHole]
    ) -> Dict[str, Any]:
        """Generate enclosure manifest with all parameters."""
        return {
            "generator": "ManufacturerAI Enclosure3DAgent",
            "parameters": {
                "board_width_mm": self.params.board_width,
                "board_length_mm": self.params.board_length,
                "board_thickness_mm": self.params.board_thickness,
                "wall_thickness_mm": self.params.wall_thickness,
                "corner_radius_mm": self.params.corner_radius
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
                ]
            },
            "outer_dimensions": {
                "width_mm": self.params.outer_width,
                "length_mm": self.params.outer_length,
                "height_mm": self.params.total_height
            }
        }
    
    def _generate_print_plate_scad(
        self,
        battery_cavity: Optional[Dict[str, float]]
    ) -> str:
        """Generate OpenSCAD code for print plate with remote and battery hatch.
        
        Places parts side by side for printing on one plate:
        - Remote enclosure (unified shell)
        - Battery hatch (if present)
        
        Each part is in its optimal print orientation.
        """
        p = self.params
        
        # Include the other SCAD files
        hatch_include = 'use <battery_hatch.scad>' if battery_cavity else ''
        
        # Spacing between parts
        gap = 10.0
        
        # Calculate positions
        remote_x = 0
        hatch_x = p.outer_width + gap if battery_cavity else 0
        
        hatch_placement = ''
        if battery_cavity:
            hatch_placement = f'''
// Battery hatch - beside remote
translate([{hatch_x:.2f}, {p.outer_length/2 - battery_cavity["height"]/2:.2f}, 0])
    battery_hatch();
'''
        
        scad = f"""// Print Plate - Generated by ManufacturerAI
// Remote and battery hatch laid out side by side for single-plate printing
// Each part is in optimal print orientation

use <remote.scad>
{hatch_include}

// Parameters
outer_width = {p.outer_width:.2f};
outer_length = {p.outer_length:.2f};
gap = {gap:.2f};

// Remote - at origin
translate([{remote_x:.2f}, 0, 0])
    remote();
{hatch_placement}
"""
        return scad
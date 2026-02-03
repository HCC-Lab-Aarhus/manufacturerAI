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
    bottom_thickness: float = 2.0
    top_thickness: float = 1.5
    
    # Clearances
    pcb_clearance: float = 0.3  # Gap around PCB
    button_hole_clearance: float = 0.4  # Gap around button caps
    
    # Features
    corner_radius: float = 3.0
    standoff_height: float = 3.0
    standoff_outer_diameter: float = 6.0
    screw_hole_diameter: float = 2.5
    
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
        output_dir: Path
    ) -> Dict[str, Path]:
        """
        Generate enclosure from PCB layout.
        
        Args:
            pcb_layout: pcb_layout.json dict
            design_spec: design_spec.json dict for additional parameters
            output_dir: Directory for output files
        
        Returns:
            Dict mapping output names to file paths
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Extract parameters from PCB layout
        self._configure_from_layout(pcb_layout, design_spec)
        
        # Extract features
        button_holes = self._extract_button_holes(pcb_layout)
        mounting_posts = self._extract_mounting_posts(pcb_layout)
        battery_cavity = self._extract_battery_cavity(pcb_layout)
        led_windows = self._extract_led_windows(pcb_layout)
        
        # Generate OpenSCAD files
        top_scad = self._generate_top_shell_scad(button_holes, led_windows)
        bottom_scad = self._generate_bottom_shell_scad(mounting_posts, battery_cavity)
        
        # Write SCAD files
        top_scad_path = output_dir / "top_shell.scad"
        bottom_scad_path = output_dir / "bottom_shell.scad"
        
        top_scad_path.write_text(top_scad, encoding="utf-8")
        bottom_scad_path.write_text(bottom_scad, encoding="utf-8")
        
        # Try to render to STL using OpenSCAD
        outputs = {
            "top_shell_scad": top_scad_path,
            "bottom_shell_scad": bottom_scad_path
        }
        
        # Attempt OpenSCAD rendering
        top_stl_path = output_dir / "top_shell.stl"
        bottom_stl_path = output_dir / "bottom_shell.stl"
        
        if self._render_scad_to_stl(top_scad_path, top_stl_path):
            outputs["top_shell_stl"] = top_stl_path
        
        if self._render_scad_to_stl(bottom_scad_path, bottom_stl_path):
            outputs["bottom_shell_stl"] = bottom_stl_path
        
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
        battery_cavity: Optional[Dict[str, float]]
    ) -> str:
        """Generate OpenSCAD code for bottom shell."""
        p = self.params
        
        scad = f"""// Bottom Shell - Generated by ManufacturerAI
// This file is parametric - edit values below to customize

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
    }}
}}

bottom_shell();
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
                break
        
        if not openscad_bin:
            print("OpenSCAD not found. SCAD files generated but not rendered to STL.")
            print("Install OpenSCAD to automatically render STL files.")
            return False
        
        try:
            result = subprocess.run(
                [openscad_bin, "-o", str(stl_path), str(scad_path)],
                capture_output=True,
                text=True,
                timeout=120
            )
            
            if result.returncode == 0:
                print(f"Generated {stl_path}")
                return True
            else:
                print(f"OpenSCAD error: {result.stderr}")
                return False
                
        except FileNotFoundError:
            print("OpenSCAD not found. SCAD files generated but not rendered to STL.")
            print("Install OpenSCAD to automatically render STL files.")
            return False
        except subprocess.TimeoutExpired:
            print("OpenSCAD rendering timed out")
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

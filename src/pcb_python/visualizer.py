"""
PCB Layout Visualizer - Generates debug images for PCB layouts.

Outputs:
- pcb_debug.png: Full visualization with grid, components, keepouts
- pcb_positive.png: Conductive areas (white on black)
- pcb_negative.png: Non-conductive/void areas (white on black)
"""

from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import math

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class PCBVisualizer:
    """Generate debug and manufacturing images for PCB layouts."""
    
    # Color palette
    COLORS = {
        'background': (11, 17, 32),        # Dark blue (matches UI)
        'board': (30, 41, 59),              # Slate dark
        'grid': (51, 65, 85),               # Slate grid lines
        'button': (147, 197, 253),          # Light blue
        'button_outline': (59, 130, 246),   # Blue
        'controller': (253, 186, 116),      # Orange
        'controller_outline': (249, 115, 22),
        'battery': (134, 239, 172),         # Green
        'battery_outline': (34, 197, 94),
        'led': (252, 211, 77),              # Yellow
        'led_outline': (234, 179, 8),
        'mounting_hole': (148, 163, 184),   # Gray
        'keepout': (239, 68, 68, 80),       # Red with alpha
        'edge_clearance': (251, 191, 36, 60),  # Amber with alpha
        'text': (248, 250, 252),            # White text
        'dimension': (156, 163, 175),       # Gray text
        'pin': (220, 220, 220),             # Silver pins
        'pin_outline': (180, 180, 180),
        'trace': (200, 150, 50),            # Copper trace color
        'trace_gnd': (100, 100, 100),       # Ground trace
        'trace_vcc': (200, 50, 50),         # Power trace
        'pad': (255, 215, 0),               # Gold pad color
    }
    
    def __init__(self, dpi: int = 150, margin_mm: float = 5.0):
        self.dpi = dpi
        self.margin_mm = margin_mm
        self.mm_to_px = dpi / 25.4  # Conversion factor
        
    def generate_all(
        self, 
        pcb_layout: dict, 
        output_dir: Path,
        prefix: str = "pcb"
    ) -> Dict[str, Path]:
        """
        Generate all debug and manufacturing images.
        
        Returns dict of generated file paths.
        """
        if not HAS_PIL:
            print("Warning: PIL not installed, skipping PCB visualization")
            return {}
        
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        outputs = {}
        
        # Generate debug visualization
        debug_path = output_dir / f"{prefix}_debug.png"
        self._generate_debug_image(pcb_layout, debug_path)
        outputs['debug'] = debug_path
        
        # Generate positive mask (conductive areas)
        positive_path = output_dir / f"{prefix}_positive.png"
        self._generate_positive_mask(pcb_layout, positive_path)
        outputs['positive'] = positive_path
        
        # Generate negative mask (void/insulating areas)
        negative_path = output_dir / f"{prefix}_negative.png"
        self._generate_negative_mask(pcb_layout, negative_path)
        outputs['negative'] = negative_path
        
        return outputs
    
    def _get_board_dimensions(self, pcb_layout: dict) -> Tuple[float, float]:
        """Extract board dimensions from outline polygon."""
        outline = pcb_layout["board"]["outline_polygon"]
        xs = [p[0] for p in outline]
        ys = [p[1] for p in outline]
        return max(xs) - min(xs), max(ys) - min(ys)
    
    def _create_canvas(self, board_width: float, board_height: float) -> Tuple[Image.Image, float, float]:
        """Create canvas with margins and return image plus offsets."""
        total_width_mm = board_width + 2 * self.margin_mm
        total_height_mm = board_height + 2 * self.margin_mm
        
        width_px = int(total_width_mm * self.mm_to_px)
        height_px = int(total_height_mm * self.mm_to_px)
        
        img = Image.new('RGBA', (width_px, height_px), self.COLORS['background'])
        
        offset_x = self.margin_mm * self.mm_to_px
        offset_y = self.margin_mm * self.mm_to_px
        
        return img, offset_x, offset_y
    
    def _mm_to_canvas(self, x: float, y: float, offset_x: float, offset_y: float, height_px: int) -> Tuple[int, int]:
        """Convert mm coordinates to canvas pixels (Y-inverted)."""
        px = int(offset_x + x * self.mm_to_px)
        py = int(height_px - (offset_y + y * self.mm_to_px))
        return px, py
    
    def _generate_debug_image(self, pcb_layout: dict, output_path: Path):
        """Generate full debug visualization."""
        board_width, board_height = self._get_board_dimensions(pcb_layout)
        img, offset_x, offset_y = self._create_canvas(board_width, board_height)
        draw = ImageDraw.Draw(img, 'RGBA')
        
        # Draw board background
        board_corners = [
            self._mm_to_canvas(0, 0, offset_x, offset_y, img.height),
            self._mm_to_canvas(board_width, 0, offset_x, offset_y, img.height),
            self._mm_to_canvas(board_width, board_height, offset_x, offset_y, img.height),
            self._mm_to_canvas(0, board_height, offset_x, offset_y, img.height),
        ]
        draw.polygon(board_corners, fill=self.COLORS['board'])
        
        # Draw grid (5mm spacing)
        self._draw_grid(draw, board_width, board_height, offset_x, offset_y, img.height, spacing_mm=5.0)
        
        # Draw edge clearance zone
        constraints = pcb_layout.get("metadata", {}).get("constraints", {})
        edge_clearance = constraints.get("edge_clearance_mm", 3.0) if constraints else 3.0
        self._draw_edge_clearance(draw, board_width, board_height, edge_clearance, offset_x, offset_y, img.height)
        
        # Draw traces connecting components
        self._draw_traces(draw, pcb_layout, offset_x, offset_y, img.height)
        
        # Draw components
        for component in pcb_layout.get("components", []):
            self._draw_component(draw, component, offset_x, offset_y, img.height)
        
        # Draw mounting holes
        for hole in pcb_layout.get("mounting_holes", []):
            self._draw_mounting_hole(draw, hole, offset_x, offset_y, img.height)
        
        # Draw dimensions
        self._draw_dimensions(draw, board_width, board_height, offset_x, offset_y, img.height)
        
        # Save
        img = img.convert('RGB')
        img.save(output_path, 'PNG')
        print(f"  ✓ Generated {output_path}")
    
    def _draw_grid(self, draw: ImageDraw, board_w: float, board_h: float, 
                   ox: float, oy: float, height_px: int, spacing_mm: float = 5.0):
        """Draw grid lines."""
        for x in range(0, int(board_w) + 1, int(spacing_mm)):
            x1, y1 = self._mm_to_canvas(x, 0, ox, oy, height_px)
            x2, y2 = self._mm_to_canvas(x, board_h, ox, oy, height_px)
            draw.line([(x1, y1), (x2, y2)], fill=self.COLORS['grid'], width=1)
        
        for y in range(0, int(board_h) + 1, int(spacing_mm)):
            x1, y1 = self._mm_to_canvas(0, y, ox, oy, height_px)
            x2, y2 = self._mm_to_canvas(board_w, y, ox, oy, height_px)
            draw.line([(x1, y1), (x2, y2)], fill=self.COLORS['grid'], width=1)
    
    def _draw_edge_clearance(self, draw: ImageDraw, board_w: float, board_h: float,
                             clearance: float, ox: float, oy: float, height_px: int):
        """Draw edge clearance zone."""
        # Draw the clearance zone as a semi-transparent rectangle
        outer = [
            self._mm_to_canvas(0, 0, ox, oy, height_px),
            self._mm_to_canvas(board_w, 0, ox, oy, height_px),
            self._mm_to_canvas(board_w, board_h, ox, oy, height_px),
            self._mm_to_canvas(0, board_h, ox, oy, height_px),
        ]
        inner = [
            self._mm_to_canvas(clearance, clearance, ox, oy, height_px),
            self._mm_to_canvas(board_w - clearance, clearance, ox, oy, height_px),
            self._mm_to_canvas(board_w - clearance, board_h - clearance, ox, oy, height_px),
            self._mm_to_canvas(clearance, board_h - clearance, ox, oy, height_px),
        ]
        
        # Draw inner rectangle outline (safe zone border)
        draw.line(inner + [inner[0]], fill=self.COLORS['edge_clearance'], width=2)
    
    def _draw_component(self, draw: ImageDraw, component: dict, ox: float, oy: float, height_px: int):
        """Draw a component with its keepout zone and pins."""
        comp_type = component.get("type", "unknown")
        center = component.get("center", [0, 0])
        cx, cy = center[0], center[1]
        footprint = component.get("footprint", "")
        
        # Get colors based on type
        colors = {
            "button": (self.COLORS['button'], self.COLORS['button_outline']),
            "controller": (self.COLORS['controller'], self.COLORS['controller_outline']),
            "battery": (self.COLORS['battery'], self.COLORS['battery_outline']),
            "led": (self.COLORS['led'], self.COLORS['led_outline']),
        }
        fill_color, outline_color = colors.get(comp_type, (self.COLORS['mounting_hole'], (100, 100, 100)))
        
        # Get keepout dimensions
        keepout = component.get("keepout", {})
        keepout_type = keepout.get("type", "circle")
        
        if comp_type == "button":
            self._draw_tactile_switch(draw, cx, cy, footprint, fill_color, outline_color, ox, oy, height_px)
        elif comp_type == "controller":
            self._draw_microcontroller(draw, cx, cy, footprint, fill_color, outline_color, keepout, ox, oy, height_px)
        elif comp_type == "battery":
            self._draw_battery(draw, cx, cy, footprint, fill_color, outline_color, keepout, ox, oy, height_px)
        elif keepout_type == "circle":
            radius = keepout.get("radius_mm", 5.0)
            # Draw keepout zone (semi-transparent)
            x1, y1 = self._mm_to_canvas(cx - radius, cy - radius, ox, oy, height_px)
            x2, y2 = self._mm_to_canvas(cx + radius, cy + radius, ox, oy, height_px)
            draw.ellipse([(x1, y2), (x2, y1)], outline=self.COLORS['keepout'], width=1)
            
            # Draw component (smaller)
            inner_radius = radius * 0.7
            x1, y1 = self._mm_to_canvas(cx - inner_radius, cy - inner_radius, ox, oy, height_px)
            x2, y2 = self._mm_to_canvas(cx + inner_radius, cy + inner_radius, ox, oy, height_px)
            draw.ellipse([(x1, y2), (x2, y1)], fill=fill_color, outline=outline_color, width=2)
        else:
            width = keepout.get("width_mm", 10.0)
            height = keepout.get("height_mm", 10.0)
            
            # Draw keepout zone
            x1, y1 = self._mm_to_canvas(cx - width/2, cy - height/2, ox, oy, height_px)
            x2, y2 = self._mm_to_canvas(cx + width/2, cy + height/2, ox, oy, height_px)
            draw.rectangle([(x1, y2), (x2, y1)], outline=self.COLORS['keepout'], width=1)
            
            # Draw component (smaller)
            inner_w, inner_h = width * 0.8, height * 0.8
            x1, y1 = self._mm_to_canvas(cx - inner_w/2, cy - inner_h/2, ox, oy, height_px)
            x2, y2 = self._mm_to_canvas(cx + inner_w/2, cy + inner_h/2, ox, oy, height_px)
            draw.rectangle([(x1, y2), (x2, y1)], fill=fill_color, outline=outline_color, width=2)
        
        # Draw component ID label
        label_x, label_y = self._mm_to_canvas(cx, cy, ox, oy, height_px)
        comp_id = component.get("id", "?")
        try:
            font = ImageFont.truetype("arial.ttf", 10)
        except:
            font = ImageFont.load_default()
        
        # Get text bounding box for centering
        bbox = draw.textbbox((0, 0), comp_id, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        draw.text((label_x - text_width//2, label_y - text_height//2), comp_id, 
                  fill=self.COLORS['text'], font=font)
    
    def _draw_tactile_switch(self, draw: ImageDraw, cx: float, cy: float, footprint: str,
                              fill_color: tuple, outline_color: tuple, 
                              ox: float, oy: float, height_px: int):
        """Draw a tactile switch with 4 pins."""
        # 6x6mm tactile switch standard dimensions
        body_size = 6.0 if "6x6" in footprint else 12.0
        half = body_size / 2
        
        # Draw the switch body (square)
        x1, y1 = self._mm_to_canvas(cx - half, cy - half, ox, oy, height_px)
        x2, y2 = self._mm_to_canvas(cx + half, cy + half, ox, oy, height_px)
        draw.rectangle([(x1, y2), (x2, y1)], fill=fill_color, outline=outline_color, width=2)
        
        # Draw the button circle in center
        btn_radius = body_size * 0.3
        bx1, by1 = self._mm_to_canvas(cx - btn_radius, cy - btn_radius, ox, oy, height_px)
        bx2, by2 = self._mm_to_canvas(cx + btn_radius, cy + btn_radius, ox, oy, height_px)
        draw.ellipse([(bx1, by2), (bx2, by1)], fill=outline_color, outline=(255, 255, 255), width=1)
        
        # Pin positions (4 pins at corners, standard 6.5mm apart for 6x6 switch)
        pin_spacing = 6.5 if "6x6" in footprint else 10.0
        pin_size = 0.8  # Pin diameter in mm
        pins = [
            (cx - pin_spacing/2, cy - pin_spacing/2),  # Bottom-left
            (cx + pin_spacing/2, cy - pin_spacing/2),  # Bottom-right
            (cx - pin_spacing/2, cy + pin_spacing/2),  # Top-left
            (cx + pin_spacing/2, cy + pin_spacing/2),  # Top-right
        ]
        
        # Draw pins as small pads
        for px, py in pins:
            self._draw_pad(draw, px, py, pin_size, ox, oy, height_px)
    
    def _draw_microcontroller(self, draw: ImageDraw, cx: float, cy: float, footprint: str,
                               fill_color: tuple, outline_color: tuple, keepout: dict,
                               ox: float, oy: float, height_px: int):
        """
        Draw ATMega328P-PU 28-pin DIP package.
        
        DIP-28 Layout:
        - Pins 1-14 on left side (top to bottom)
        - Pins 15-28 on right side (bottom to top)
        - Pin spacing: 2.54mm (0.1")
        - Row spacing: 7.62mm (0.3" = 300 mil)
        - Body width: ~6.35mm, length: ~34.8mm
        """
        # DIP-28 dimensions
        pins_per_side = 14
        pin_spacing = 2.54  # 0.1 inch
        row_spacing = 7.62  # 0.3 inch
        
        # Body dimensions (slightly smaller than pin span)
        body_width = 6.35  # Standard DIP width
        body_height = (pins_per_side - 1) * pin_spacing  # 33.02mm
        
        # Pin dimensions for through-hole
        pin_length = 2.0  # How far pins extend from body
        pin_width = 0.6   # Pin thickness
        pad_diameter = 1.6  # Through-hole pad size
        
        # Draw IC body (rectangle)
        x1, y1 = self._mm_to_canvas(cx - body_width/2, cy - body_height/2, ox, oy, height_px)
        x2, y2 = self._mm_to_canvas(cx + body_width/2, cy + body_height/2, ox, oy, height_px)
        draw.rectangle([(x1, y2), (x2, y1)], fill=fill_color, outline=outline_color, width=2)
        
        # Draw notch at top (pin 1 indicator)
        notch_radius = 2.0
        notch_x, notch_y = self._mm_to_canvas(cx, cy + body_height/2, ox, oy, height_px)
        notch_r_px = int(notch_radius * self.mm_to_px)
        draw.arc([(notch_x - notch_r_px, notch_y - notch_r_px), 
                  (notch_x + notch_r_px, notch_y + notch_r_px)], 
                 start=180, end=360, fill=(255, 255, 255), width=2)
        
        # Draw pin 1 dot (inside body, top-left)
        dot_x, dot_y = self._mm_to_canvas(cx - body_width/4, cy + body_height/2 - 3, ox, oy, height_px)
        draw.ellipse([(dot_x - 3, dot_y - 3), (dot_x + 3, dot_y + 3)], fill=(255, 255, 255))
        
        # Import pin names for labeling
        from .router import ATMEGA328P_PINOUT
        
        # Draw left side pins (1-14, top to bottom)
        for i in range(pins_per_side):
            pin_number = i + 1
            pin_y = cy + body_height/2 - i * pin_spacing
            pin_x = cx - row_spacing/2
            
            # Draw the pin lead
            lead_x = cx - body_width/2 - pin_length/2
            self._draw_ic_pin(draw, lead_x, pin_y, pin_length, pin_width, 'horizontal', ox, oy, height_px)
            
            # Draw the through-hole pad
            self._draw_pad(draw, pin_x, pin_y, pad_diameter, ox, oy, height_px)
            
            # Draw pin number
            pin_info = ATMEGA328P_PINOUT.get(pin_number, ("", "", []))
            label_x, label_y = self._mm_to_canvas(pin_x - 4, pin_y, ox, oy, height_px)
            try:
                font = ImageFont.truetype("arial.ttf", 7)
            except:
                font = ImageFont.load_default()
            draw.text((label_x, label_y - 4), f"{pin_number}", fill=(150, 150, 150), font=font)
        
        # Draw right side pins (15-28, bottom to top)
        for i in range(pins_per_side):
            pin_number = 15 + i
            pin_y = cy - body_height/2 + i * pin_spacing
            pin_x = cx + row_spacing/2
            
            # Draw the pin lead
            lead_x = cx + body_width/2 + pin_length/2
            self._draw_ic_pin(draw, lead_x, pin_y, pin_length, pin_width, 'horizontal', ox, oy, height_px)
            
            # Draw the through-hole pad
            self._draw_pad(draw, pin_x, pin_y, pad_diameter, ox, oy, height_px)
            
            # Draw pin number
            label_x, label_y = self._mm_to_canvas(pin_x + 2.5, pin_y, ox, oy, height_px)
            try:
                font = ImageFont.truetype("arial.ttf", 7)
            except:
                font = ImageFont.load_default()
            draw.text((label_x, label_y - 4), f"{pin_number}", fill=(150, 150, 150), font=font)
        
        # Draw chip label in center
        try:
            font = ImageFont.truetype("arial.ttf", 9)
        except:
            font = ImageFont.load_default()
        label_x, label_y = self._mm_to_canvas(cx, cy, ox, oy, height_px)
        draw.text((label_x - 20, label_y - 5), "ATMega", fill=self.COLORS['text'], font=font)
        draw.text((label_x - 20, label_y + 5), "328P-PU", fill=self.COLORS['text'], font=font)
    
    def _draw_battery(self, draw: ImageDraw, cx: float, cy: float, footprint: str,
                       fill_color: tuple, outline_color: tuple, keepout: dict,
                       ox: float, oy: float, height_px: int):
        """Draw battery holder with terminals."""
        width = keepout.get("width_mm", 16.0)
        height = keepout.get("height_mm", 49.0)
        
        # Draw battery holder outline
        x1, y1 = self._mm_to_canvas(cx - width/2, cy - height/2, ox, oy, height_px)
        x2, y2 = self._mm_to_canvas(cx + width/2, cy + height/2, ox, oy, height_px)
        draw.rectangle([(x1, y2), (x2, y1)], fill=fill_color, outline=outline_color, width=2)
        
        # Draw battery cells (for 2xAAA)
        if "AAA" in footprint:
            cell_width = 5.0
            cell_height = height * 0.8
            # Two cells side by side
            for offset in [-3.5, 3.5]:
                cx1, cy1 = self._mm_to_canvas(cx + offset - cell_width/2, cy - cell_height/2, ox, oy, height_px)
                cx2, cy2 = self._mm_to_canvas(cx + offset + cell_width/2, cy + cell_height/2, ox, oy, height_px)
                draw.rectangle([(cx1, cy2), (cx2, cy1)], outline=(80, 80, 80), width=1)
                
                # Draw + and - symbols
                plus_y = cy + cell_height/2 - 3
                minus_y = cy - cell_height/2 + 3
                px, py = self._mm_to_canvas(cx + offset, plus_y, ox, oy, height_px)
                draw.text((px - 3, py - 5), "+", fill=(200, 50, 50))
                mx, my = self._mm_to_canvas(cx + offset, minus_y, ox, oy, height_px)
                draw.text((mx - 3, my - 5), "-", fill=(50, 50, 200))
        
        # Draw power terminals
        terminal_size = 2.0
        # Positive terminal at top
        self._draw_pad(draw, cx - 5, cy + height/2 - 5, terminal_size, ox, oy, height_px, color=(200, 50, 50))
        # Negative terminal at bottom
        self._draw_pad(draw, cx + 5, cy - height/2 + 5, terminal_size, ox, oy, height_px, color=(50, 50, 200))
    
    def _draw_ic_pin(self, draw: ImageDraw, cx: float, cy: float, 
                      length: float, width: float, orientation: str,
                      ox: float, oy: float, height_px: int):
        """Draw an IC pin."""
        if orientation == 'horizontal':
            x1, y1 = self._mm_to_canvas(cx - length/2, cy - width/2, ox, oy, height_px)
            x2, y2 = self._mm_to_canvas(cx + length/2, cy + width/2, ox, oy, height_px)
        else:
            x1, y1 = self._mm_to_canvas(cx - width/2, cy - length/2, ox, oy, height_px)
            x2, y2 = self._mm_to_canvas(cx + width/2, cy + length/2, ox, oy, height_px)
        draw.rectangle([(x1, y2), (x2, y1)], fill=self.COLORS['pin'], outline=self.COLORS['pin_outline'])
    
    def _draw_pad(self, draw: ImageDraw, cx: float, cy: float, diameter: float,
                   ox: float, oy: float, height_px: int, color: tuple = None):
        """Draw a circular pad."""
        if color is None:
            color = self.COLORS['pad']
        radius = diameter / 2
        x1, y1 = self._mm_to_canvas(cx - radius, cy - radius, ox, oy, height_px)
        x2, y2 = self._mm_to_canvas(cx + radius, cy + radius, ox, oy, height_px)
        draw.ellipse([(x1, y2), (x2, y1)], fill=color, outline=(180, 150, 50))
        # Draw hole in center
        hole_r = radius * 0.4
        hx1, hy1 = self._mm_to_canvas(cx - hole_r, cy - hole_r, ox, oy, height_px)
        hx2, hy2 = self._mm_to_canvas(cx + hole_r, cy + hole_r, ox, oy, height_px)
        draw.ellipse([(hx1, hy2), (hx2, hy1)], fill=self.COLORS['board'])
    
    def _draw_traces(self, draw: ImageDraw, pcb_layout: dict, ox: float, oy: float, height_px: int):
        """Draw traces connecting buttons to the microcontroller using routing."""
        # Try TypeScript router first (better routing), fall back to Python router
        try:
            self._draw_traces_ts(draw, pcb_layout, ox, oy, height_px)
        except Exception as e:
            print(f"TypeScript router failed ({e}), using Python router")
            self._draw_traces_python(draw, pcb_layout, ox, oy, height_px)
    
    def _draw_traces_ts(self, draw: ImageDraw, pcb_layout: dict, ox: float, oy: float, height_px: int):
        """Draw traces using the TypeScript router via subprocess."""
        from .ts_router_bridge import TSPCBRouter
        import tempfile
        from pathlib import Path
        
        board_width, board_height = self._get_board_dimensions(pcb_layout)
        
        # Use the TypeScript router
        ts_router = TSPCBRouter()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            result = ts_router.route(pcb_layout, Path(tmpdir))
        
        if not result.get("success") or not result.get("traces"):
            raise RuntimeError("TypeScript router returned no traces")
        
        # Draw traces from the routing result
        trace_colors = [
            (0, 255, 100),      # Green
            (100, 200, 255),    # Light blue
            (255, 100, 100),    # Red
            (255, 200, 50),     # Yellow
            (200, 100, 255),    # Purple
            (100, 255, 200),    # Cyan
        ]
        
        net_color_map = {}
        color_idx = 0
        
        for trace in result["traces"]:
            net_name = trace.get("net", "")
            path = trace.get("path", [])
            
            if not path:
                continue
            
            # Assign color based on net type
            if "VCC" in net_name:
                color = self.COLORS['trace_vcc']
            elif "GND" in net_name:
                color = self.COLORS['trace_gnd']
            else:
                if net_name not in net_color_map:
                    net_color_map[net_name] = trace_colors[color_idx % len(trace_colors)]
                    color_idx += 1
                color = net_color_map[net_name]
            
            # Draw the trace path (TS router uses grid coordinates)
            # Grid resolution is typically 0.5mm in TS router
            grid_resolution = 0.5
            
            points = []
            for coord in path:
                world_x = coord["x"] * grid_resolution
                world_y = coord["y"] * grid_resolution
                px, py = self._mm_to_canvas(world_x, world_y, ox, oy, height_px)
                points.append((px, py))
            
            # Draw line segments
            for i in range(len(points) - 1):
                draw.line([points[i], points[i + 1]], fill=color, width=3)
            
            # Draw nodes at each corner
            for px, py in points:
                r = 2
                draw.ellipse([(px - r, py - r), (px + r, py + r)], fill=color)
    
    def _draw_traces_python(self, draw: ImageDraw, pcb_layout: dict, ox: float, oy: float, height_px: int):
        """Draw traces using the Python A* router (fallback)."""
        from .router import PCBRouter, BoardParameters, ManufacturingConstraints, Footprints
        
        components = pcb_layout.get("components", [])
        board_width, board_height = self._get_board_dimensions(pcb_layout)
        
        # Find controller and buttons
        controller = None
        buttons = []
        battery = None
        
        for comp in components:
            if comp.get("type") == "controller":
                controller = comp
            elif comp.get("type") == "button":
                buttons.append(comp)
            elif comp.get("type") == "battery":
                battery = comp
        
        if not controller:
            return
        
        # Set up the router
        board_params = BoardParameters(
            board_width=board_width,
            board_height=board_height,
            grid_resolution=1.0  # 1mm grid
        )
        manufacturing = ManufacturingConstraints(
            trace_width=0.8,
            trace_clearance=0.4
        )
        footprints = Footprints()
        
        router = PCBRouter(board_params, manufacturing, footprints)
        
        # Place components
        ctrl_x, ctrl_y = controller["center"]
        
        # Build controller pins dictionary using ATMega328P pin names
        # Use pins from alternating sides for better routing:
        # - Left side pins (1-14): PD2(4), PD3(5), PD4(6), PD5(11), PD6(12), PD7(13)
        # - Right side pins (15-28): PB0(14), PB1(15), PB2(16), PB3(17), PB4(18), PB5(19)
        # Interleave to spread traces across both sides of the chip
        button_pins = ["PD2", "PB0", "PD3", "PB1", "PD4", "PB2", "PD5", "PB3", "PD6", "PB4", "PD7", "PB5"]
        
        ctrl_pins = {}
        for i, btn in enumerate(buttons):
            if i < len(button_pins):
                ctrl_pins[button_pins[i]] = f"BTN{i+1}_SIG"
        ctrl_pins["VCC"] = "VCC"
        ctrl_pins["GND"] = "GND"
        ctrl_pins["AVCC"] = "VCC"  # Connect AVCC to VCC
        
        router.place_controller("MCU", ctrl_x, ctrl_y, ctrl_pins)
        
        # Place buttons
        for i, btn in enumerate(buttons):
            bx, by = btn["center"]
            router.place_button(f"BTN{i+1}", bx, by, f"BTN{i+1}_SIG")
        
        # Place battery if present
        if battery:
            bat_x, bat_y = battery["center"]
            # Get battery dimensions from keepout
            bat_keepout = battery.get("keepout", {})
            bat_width = bat_keepout.get("width_mm", 12.0)
            bat_height = bat_keepout.get("height_mm", 45.0)
            router.place_battery("BAT1", bat_x, bat_y, bat_width, bat_height)
        
        # Route all nets
        result = router.route()
        
        # Define trace colors by net type
        trace_colors = [
            (0, 255, 100),      # Green
            (100, 200, 255),    # Light blue
            (255, 100, 100),    # Red
            (255, 200, 50),     # Yellow
            (200, 100, 255),    # Purple
            (100, 255, 200),    # Cyan
        ]
        
        net_color_map = {}
        color_idx = 0
        
        for trace in result.traces:
            net_name = trace.net
            
            # Assign color based on net type
            if net_name == "VCC":
                color = self.COLORS['trace_vcc']
            elif net_name == "GND":
                color = self.COLORS['trace_gnd']
            else:
                if net_name not in net_color_map:
                    net_color_map[net_name] = trace_colors[color_idx % len(trace_colors)]
                    color_idx += 1
                color = net_color_map[net_name]
            
            # Draw the trace path
            if len(trace.path) >= 2:
                points = []
                for coord in trace.path:
                    # Convert grid coordinates back to mm, then to canvas
                    world_x, world_y = router.grid.grid_to_world(coord.x, coord.y)
                    px, py = self._mm_to_canvas(world_x, world_y, ox, oy, height_px)
                    points.append((px, py))
                
                # Draw line segments
                for i in range(len(points) - 1):
                    draw.line([points[i], points[i + 1]], fill=color, width=3)
                
                # Draw nodes at each point
                for px, py in points:
                    r = 2
                    draw.ellipse([(px - r, py - r), (px + r, py + r)], fill=color)
        
        # Draw pads from the router
        pads = router.get_pads()
        for pad_key, pad in pads.items():
            if pad.net == "NC":
                continue
            world_x, world_y = router.grid.grid_to_world(pad.center.x, pad.center.y)
            self._draw_pad(draw, world_x, world_y, 1.5, ox, oy, height_px)
    
    def _draw_mounting_hole(self, draw: ImageDraw, hole: dict, ox: float, oy: float, height_px: int):
        """Draw a mounting hole."""
        center = hole.get("center", [0, 0])
        cx, cy = center[0], center[1]
        diameter = hole.get("diameter_mm", 3.0)
        radius = diameter / 2
        
        x1, y1 = self._mm_to_canvas(cx - radius, cy - radius, ox, oy, height_px)
        x2, y2 = self._mm_to_canvas(cx + radius, cy + radius, ox, oy, height_px)
        
        # Draw hole with cross
        draw.ellipse([(x1, y2), (x2, y1)], outline=self.COLORS['mounting_hole'], width=2)
        
        # Cross inside
        px, py = self._mm_to_canvas(cx, cy, ox, oy, height_px)
        r_px = int(radius * self.mm_to_px * 0.5)
        draw.line([(px - r_px, py), (px + r_px, py)], fill=self.COLORS['mounting_hole'], width=1)
        draw.line([(px, py - r_px), (px, py + r_px)], fill=self.COLORS['mounting_hole'], width=1)
    
    def _draw_dimensions(self, draw: ImageDraw, board_w: float, board_h: float,
                         ox: float, oy: float, height_px: int):
        """Draw dimension annotations."""
        try:
            font = ImageFont.truetype("arial.ttf", 11)
        except:
            font = ImageFont.load_default()
        
        # Width dimension (bottom)
        x_mid = board_w / 2
        x1, y1 = self._mm_to_canvas(0, -2, ox, oy, height_px)
        x2, y2 = self._mm_to_canvas(board_w, -2, ox, oy, height_px)
        draw.line([(x1, y1), (x2, y2)], fill=self.COLORS['dimension'], width=1)
        
        mid_x, mid_y = self._mm_to_canvas(x_mid, -3.5, ox, oy, height_px)
        dim_text = f"{board_w:.0f}mm"
        bbox = draw.textbbox((0, 0), dim_text, font=font)
        text_width = bbox[2] - bbox[0]
        draw.text((mid_x - text_width//2, mid_y), dim_text, fill=self.COLORS['dimension'], font=font)
        
        # Height dimension (left)
        y_mid = board_h / 2
        x1, y1 = self._mm_to_canvas(-2, 0, ox, oy, height_px)
        x2, y2 = self._mm_to_canvas(-2, board_h, ox, oy, height_px)
        draw.line([(x1, y1), (x2, y2)], fill=self.COLORS['dimension'], width=1)
        
        mid_x, mid_y = self._mm_to_canvas(-3.5, y_mid, ox, oy, height_px)
        dim_text = f"{board_h:.0f}mm"
        draw.text((mid_x - 10, mid_y), dim_text, fill=self.COLORS['dimension'], font=font)
    
    def _generate_positive_mask(self, pcb_layout: dict, output_path: Path):
        """Generate positive mask (conductive areas = white)."""
        board_width, board_height = self._get_board_dimensions(pcb_layout)
        
        width_px = int(board_width * self.mm_to_px)
        height_px = int(board_height * self.mm_to_px)
        
        img = Image.new('L', (width_px, height_px), 0)  # Black background
        draw = ImageDraw.Draw(img)
        
        # Draw pads for each component as white circles/rectangles
        for component in pcb_layout.get("components", []):
            center = component.get("center", [0, 0])
            cx, cy = center[0], center[1]
            
            # Simple pad representation
            keepout = component.get("keepout", {})
            if keepout.get("type") == "circle":
                radius = keepout.get("radius_mm", 5.0) * 0.3  # Pad is smaller than keepout
            else:
                radius = min(keepout.get("width_mm", 10), keepout.get("height_mm", 10)) * 0.2
            
            px = int(cx * self.mm_to_px)
            py = int(height_px - cy * self.mm_to_px)
            r = int(radius * self.mm_to_px)
            
            draw.ellipse([(px - r, py - r), (px + r, py + r)], fill=255)
        
        img.save(output_path, 'PNG')
        print(f"  ✓ Generated {output_path}")
    
    def _generate_negative_mask(self, pcb_layout: dict, output_path: Path):
        """Generate negative mask (non-conductive areas = white)."""
        board_width, board_height = self._get_board_dimensions(pcb_layout)
        
        width_px = int(board_width * self.mm_to_px)
        height_px = int(board_height * self.mm_to_px)
        
        img = Image.new('L', (width_px, height_px), 255)  # White background (all non-conductive)
        draw = ImageDraw.Draw(img)
        
        # Draw mounting holes as black (they're voids in the board)
        for hole in pcb_layout.get("mounting_holes", []):
            center = hole.get("center", [0, 0])
            cx, cy = center[0], center[1]
            radius = hole.get("diameter_mm", 3.0) / 2
            
            px = int(cx * self.mm_to_px)
            py = int(height_px - cy * self.mm_to_px)
            r = int(radius * self.mm_to_px)
            
            draw.ellipse([(px - r, py - r), (px + r, py + r)], fill=0)
        
        img.save(output_path, 'PNG')
        print(f"  ✓ Generated {output_path}")


def generate_pcb_debug_images(pcb_layout: dict, output_dir: Path, prefix: str = "pcb") -> Dict[str, Path]:
    """
    Generate PCB debug images using the TypeScript router.
    
    This calls the TypeScript CLI which handles routing and visualization in one step.
    Falls back to Python visualizer if TypeScript fails.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Try TypeScript router first (better routing and visualization)
    try:
        result = _generate_with_typescript(pcb_layout, output_dir)
        if result:
            return result
    except Exception as e:
        print(f"  ⚠ TypeScript router failed: {e}")
    
    # Fallback to Python visualizer
    print("  Using Python visualizer as fallback...")
    visualizer = PCBVisualizer()
    return visualizer.generate_all(pcb_layout, output_dir, prefix)


def _generate_with_typescript(pcb_layout: dict, output_dir: Path) -> Optional[Dict[str, Path]]:
    """Call TypeScript PCB CLI to generate routed images."""
    import subprocess
    import json
    
    # Find the TypeScript CLI
    ts_router_dir = Path(__file__).parent.parent / "pcb"
    cli_path = ts_router_dir / "dist" / "pcb-cli.js"
    
    if not cli_path.exists():
        # Try to build it
        print("  Building TypeScript router...")
        subprocess.run(
            ["npm", "run", "build"],
            cwd=ts_router_dir,
            capture_output=True,
            check=True,
            shell=True
        )
    
    if not cli_path.exists():
        print(f"  ⚠ TypeScript CLI not found at {cli_path}")
        return None
    
    # Call the CLI
    result = subprocess.run(
        ["node", str(cli_path), "--output", str(output_dir)],
        cwd=ts_router_dir,
        input=json.dumps(pcb_layout),
        capture_output=True,
        text=True,
        shell=True
    )
    
    if result.returncode != 0 and not (output_dir / "pcb_debug.png").exists():
        print(f"  ⚠ TypeScript CLI failed: {result.stderr}")
        return None
    
    # Return the generated files
    files = {}
    for name in ["pcb_debug.png", "pcb_positive.png", "pcb_negative.png"]:
        path = output_dir / name
        if path.exists():
            files[name.replace(".png", "")] = path
    
    # Parse routing result for any failures
    routing_result_path = output_dir / "routing_result.json"
    if routing_result_path.exists():
        try:
            routing_result = json.loads(routing_result_path.read_text())
            if not routing_result.get("success"):
                failed = routing_result.get("failedNets", [])
                print(f"  ⚠ Routing had {len(failed)} failed nets")
                for f in failed[:3]:  # Show first 3
                    print(f"    - {f.get('netName')}: {f.get('reason')}")
        except Exception:
            pass
    
    if files:
        print(f"  ✓ TypeScript generated: {list(files.keys())}")
    
    return files if files else None


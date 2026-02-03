"""
Tests for the TypeScript PCB Router Bridge.

Run: python -m pytest tests/test_routing.py -v
"""

import pytest
from pathlib import Path
import tempfile
import json

from src.pcb_python.ts_router_bridge import (
    TSPCBRouter, 
    RouterError, 
    RouterNotFoundError,
    route_pcb,
    ATMEGA328P_PINOUT
)


# Sample PCB layout for testing
SAMPLE_PCB_LAYOUT = {
    "board": {
        "outline_polygon": [[0, 0], [56, 0], [56, 196], [0, 196]],
        "thickness_mm": 1.6
    },
    "components": [
        {"id": "BAT1", "type": "battery", "center": [28.0, 29.5]},
        {"id": "U1", "type": "controller", "center": [28.0, 78.0]},
        {"id": "SW1", "type": "button", "center": [16.0, 146.0]},
        {"id": "SW2", "type": "button", "center": [28.0, 146.0]},
        {"id": "SW3", "type": "button", "center": [40.0, 146.0]},
    ]
}


class TestTSPCBRouter:
    """Tests for TSPCBRouter class."""
    
    def test_router_initialization(self):
        """Test that router initializes correctly."""
        router = TSPCBRouter()
        assert router.ts_router_dir.exists()
    
    def test_cli_path_exists(self):
        """Test that CLI path is found or built."""
        router = TSPCBRouter()
        cli_path = router.cli_path
        assert cli_path.exists()
        assert cli_path.name == "cli.js"
    
    def test_convert_layout(self):
        """Test layout conversion to TS router format."""
        router = TSPCBRouter()
        converted = router._convert_layout(SAMPLE_PCB_LAYOUT)
        
        # Check board dimensions
        assert converted["board"]["boardWidth"] == 56
        assert converted["board"]["boardHeight"] == 196
        assert converted["board"]["gridResolution"] == 0.5
        
        # Check components were converted
        assert len(converted["placement"]["buttons"]) == 3
        assert len(converted["placement"]["controllers"]) == 1
        assert len(converted["placement"]["batteries"]) == 1
        
        # Check button signal nets
        buttons = converted["placement"]["buttons"]
        assert buttons[0]["signalNet"] == "SW1_SIG"
        assert buttons[1]["signalNet"] == "SW2_SIG"
        assert buttons[2]["signalNet"] == "SW3_SIG"
    
    def test_controller_pins_generation(self):
        """Test controller pin assignment for buttons."""
        router = TSPCBRouter()
        
        # Test with 3 buttons
        pins = router._generate_controller_pins(3)
        assert pins["VCC"] == "VCC"
        assert pins["GND1"] == "GND"
        assert pins["PD0"] == "SW1_SIG"
        assert pins["PD1"] == "SW2_SIG"
        assert pins["PD2"] == "SW3_SIG"
        assert pins["PD3"] == "NC"  # Not assigned
    
    def test_route_success(self):
        """Test successful routing."""
        router = TSPCBRouter()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            result = router.route(SAMPLE_PCB_LAYOUT, Path(tmpdir))
            
            assert result["success"] is True
            assert len(result["traces"]) > 0
            
            # Check output files were created
            assert (Path(tmpdir) / "ts_router_input.json").exists()
            assert (Path(tmpdir) / "ts_router_result.json").exists()
            
            # Verify trace structure
            for trace in result["traces"]:
                assert "net" in trace
                assert "path" in trace
                assert len(trace["path"]) > 0
    
    def test_route_creates_signal_traces(self):
        """Test that routing creates traces for button signals."""
        router = TSPCBRouter()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            result = router.route(SAMPLE_PCB_LAYOUT, Path(tmpdir))
            
            # Get unique net names from traces
            net_names = set(trace["net"] for trace in result["traces"])
            
            # Should have signal traces for buttons
            signal_nets = [n for n in net_names if "_SIG" in n]
            assert len(signal_nets) >= 3, f"Expected 3+ signal nets, got {signal_nets}"
            
            # Should have power traces
            assert "GND" in net_names or any("GND" in n for n in net_names)
            assert "VCC" in net_names or any("VCC" in n for n in net_names)


class TestRouteConvenienceFunction:
    """Tests for the route_pcb convenience function."""
    
    def test_route_pcb_function(self):
        """Test the route_pcb convenience function."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = route_pcb(SAMPLE_PCB_LAYOUT, Path(tmpdir))
            
            assert result["success"] is True
            assert len(result["traces"]) > 0


class TestTSVisualization:
    """Tests that TypeScript CLI generates visualization files."""
    
    def test_ts_router_generates_debug_image(self):
        """Test that TypeScript router generates debug images."""
        router = TSPCBRouter()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            result = router.route(SAMPLE_PCB_LAYOUT, Path(tmpdir))
            
            # TS CLI should generate pcb_debug.png
            debug_image = Path(tmpdir) / "pcb_debug.png"
            assert debug_image.exists(), "TypeScript CLI should generate debug image"
            assert debug_image.stat().st_size > 1000, "Debug image should have content"


class TestConstants:
    """Tests for module constants."""
    
    def test_atmega_pinout_complete(self):
        """Test that ATMega pinout has all 28 pins."""
        assert len(ATMEGA328P_PINOUT) == 28
        
        # Check specific pins
        assert ATMEGA328P_PINOUT[1][0] == "PC6"  # RESET
        assert ATMEGA328P_PINOUT[7][0] == "VCC"
        assert ATMEGA328P_PINOUT[8][0] == "GND"
        assert ATMEGA328P_PINOUT[28][0] == "PC5"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

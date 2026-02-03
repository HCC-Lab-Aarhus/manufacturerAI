"""
Test the refactored pipeline with real prompts.

Run: python -m pytest tests/test_pipeline.py -v
Or:  python tests/test_pipeline.py
"""

import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm.consultant_agent import ConsultantAgent
from src.pcb_python.pcb_agent import PCBAgent
from src.pcb_python.feasibility_tool import FeasibilityTool


def test_consultant_agent_basic():
    """Test Consultant Agent with a basic prompt."""
    agent = ConsultantAgent(use_llm=False)  # Use mock for testing
    
    prompt = "I want a remote with 6 buttons, 120x50mm"
    
    design_spec = agent.generate_design_spec(prompt, use_llm=False)
    
    # Verify structure
    assert "units" in design_spec
    assert design_spec["units"] == "mm"
    assert "device_constraints" in design_spec
    assert "buttons" in design_spec
    assert "constraints" in design_spec
    assert "assumptions" in design_spec
    
    print("✓ Consultant Agent basic test passed")
    print(f"  Buttons: {len(design_spec['buttons'])}")
    print(f"  Assumptions: {design_spec['assumptions']}")
    
    return design_spec


def test_pcb_agent_from_design_spec():
    """Test PCB Agent generates valid layout."""
    # Create a sample design spec
    design_spec = {
        "units": "mm",
        "device_constraints": {
            "length_mm": 180.0,
            "width_mm": 45.0,
            "thickness_mm": 18.0
        },
        "buttons": [
            {"id": "BTN_POWER", "switch_type": "tactile_6x6", "cap_diameter_mm": 9.0, "priority": "high",
             "placement_hint": {"region": "top", "horizontal": "center"}},
            {"id": "BTN1", "switch_type": "tactile_6x6", "cap_diameter_mm": 9.0, "priority": "normal"},
            {"id": "BTN2", "switch_type": "tactile_6x6", "cap_diameter_mm": 9.0, "priority": "normal"},
            {"id": "BTN3", "switch_type": "tactile_6x6", "cap_diameter_mm": 9.0, "priority": "normal"},
            {"id": "BTN4", "switch_type": "tactile_6x6", "cap_diameter_mm": 9.0, "priority": "normal"},
            {"id": "BTN5", "switch_type": "tactile_6x6", "cap_diameter_mm": 9.0, "priority": "normal"},
        ],
        "battery": {"type": "2xAAA", "placement_hint": "bottom"},
        "leds": [{"id": "LED1", "placement_hint": "top"}],
        "constraints": {
            "min_button_spacing_mm": 3.0,
            "edge_clearance_mm": 5.0,
            "min_wall_thickness_mm": 1.6,
            "mounting_preference": "screws"
        },
        "assumptions": ["Test design spec"]
    }
    
    agent = PCBAgent()
    pcb_layout = agent.generate_layout(design_spec)
    
    # Verify structure
    assert "board" in pcb_layout
    assert "outline_polygon" in pcb_layout["board"]
    assert "components" in pcb_layout
    assert "mounting_holes" in pcb_layout
    
    # Count components
    buttons = [c for c in pcb_layout["components"] if c["type"] == "button"]
    assert len(buttons) == 6
    
    print("✓ PCB Agent test passed")
    print(f"  Board outline: {len(pcb_layout['board']['outline_polygon'])} vertices")
    print(f"  Components: {len(pcb_layout['components'])}")
    print(f"  Mounting holes: {len(pcb_layout['mounting_holes'])}")
    
    return pcb_layout


def test_feasibility_tool():
    """Test Feasibility Tool checks layout."""
    # Create a layout with some violations
    pcb_layout = {
        "board": {
            "outline_polygon": [[0, 0], [40, 0], [40, 176], [0, 176]],
            "thickness_mm": 1.6
        },
        "components": [
            {"id": "SW1", "ref": "BTN1", "type": "button", "footprint": "tactile_6x6",
             "center": [5, 10], "rotation_deg": 0, "keepout": {"type": "circle", "radius_mm": 6}},
            {"id": "SW2", "ref": "BTN2", "type": "button", "footprint": "tactile_6x6",
             "center": [15, 10], "rotation_deg": 0, "keepout": {"type": "circle", "radius_mm": 6}},  # Too close!
            {"id": "U1", "ref": "controller", "type": "controller", "footprint": "ATMEGA328P",
             "center": [20, 60], "rotation_deg": 0, "keepout": {"type": "rectangle", "width_mm": 12, "height_mm": 12}},
        ],
        "mounting_holes": [
            {"id": "MH1", "center": [5, 5], "drill_diameter_mm": 3.0},
            {"id": "MH2", "center": [35, 5], "drill_diameter_mm": 3.0},
        ]
    }
    
    tool = FeasibilityTool()
    report = tool.check(pcb_layout)
    
    # Verify structure
    assert "feasible" in report
    assert "checks" in report
    assert "errors" in report
    assert "warnings" in report
    
    print("✓ Feasibility Tool test passed")
    print(f"  Feasible: {report['feasible']}")
    print(f"  Errors: {len(report['errors'])}")
    
    if report['errors']:
        for error in report['errors']:
            print(f"    - {error['code']}: {error['message']}")
            if error.get('suggested_fixes'):
                for fix in error['suggested_fixes']:
                    print(f"      Fix: {fix}")
    
    return report


def test_full_pipeline_no_llm():
    """Test full pipeline without LLM."""
    print("\n" + "="*60)
    print("FULL PIPELINE TEST (No LLM)")
    print("="*60)
    
    # Step 1: Consultant
    print("\n[Step 1] Consultant Agent")
    consultant = ConsultantAgent(use_llm=False)
    design_spec = consultant.generate_design_spec(
        "I want a TV remote with 8 buttons, power at top, volume buttons on the right"
    )
    print(f"  Created design spec with {len(design_spec['buttons'])} buttons")
    
    # Step 2: PCB Agent
    print("\n[Step 2] PCB Agent")
    pcb_agent = PCBAgent()
    pcb_layout = pcb_agent.generate_layout(design_spec)
    print(f"  Created layout with {len(pcb_layout['components'])} components")
    
    # Step 3: Feasibility Check
    print("\n[Step 3] Feasibility Tool")
    feasibility_tool = FeasibilityTool()
    report = feasibility_tool.check(pcb_layout)
    print(f"  Feasible: {report['feasible']}")
    
    # Step 4: Iterate if needed
    iteration = 1
    max_iterations = 5
    
    while not report['feasible'] and iteration < max_iterations:
        print(f"\n[Step 4] Iteration {iteration + 1}")
        print(f"  Applying {len(report['errors'])} fixes...")
        
        pcb_layout = pcb_agent.generate_layout(design_spec, previous_feasibility_report=report)
        report = feasibility_tool.check(pcb_layout)
        
        print(f"  Feasible: {report['feasible']}")
        iteration += 1
    
    if report['feasible']:
        print("\n✓ Pipeline completed successfully!")
    else:
        print(f"\n✗ Pipeline failed after {iteration} iterations")
        for error in report['errors']:
            print(f"  - {error['code']}: {error['message']}")
    
    return design_spec, pcb_layout, report


def test_complex_prompt():
    """Test with a complex real-world prompt."""
    print("\n" + "="*60)
    print("COMPLEX PROMPT TEST")
    print("="*60)
    
    prompt = """
    I need a custom remote control for my home automation system.
    - Size: approximately 180mm x 45mm
    - 18 buttons total:
      * Power button at the very top center (most important)
      * 4 navigation buttons in a cross pattern in the middle (up/down/left/right)
      * 1 OK/Select button in the center of the navigation
      * Volume up/down on the right side
      * Channel up/down on the left side
      * 8 number buttons (1-8) at the bottom in 2 rows of 4
    - 1 LED at the top for status indication
    - 2xAAA batteries at the bottom
    - Black color (for 3D printing)
    """
    
    # Use mock LLM client for deterministic testing
    consultant = ConsultantAgent(use_llm=False)
    design_spec = consultant.generate_design_spec(prompt, use_llm=False)
    
    print(f"\nDesign Spec created:")
    print(f"  Device: {design_spec['device_constraints']}")
    print(f"  Buttons: {len(design_spec['buttons'])}")
    print(f"  Battery: {design_spec.get('battery', {})}")
    print(f"  Assumptions: {len(design_spec['assumptions'])}")
    
    for assumption in design_spec['assumptions']:
        print(f"    - {assumption}")
    
    return design_spec


if __name__ == "__main__":
    print("ManufacturerAI Pipeline Tests\n")
    
    # Run tests
    test_consultant_agent_basic()
    print()
    
    test_pcb_agent_from_design_spec()
    print()
    
    test_feasibility_tool()
    print()
    
    test_full_pipeline_no_llm()
    print()
    
    test_complex_prompt()
    print()
    
    print("\n" + "="*60)
    print("ALL TESTS COMPLETED")
    print("="*60)

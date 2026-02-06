"""
Consultant Agent - Normalizes user requirements into design_spec.json

Responsibilities:
- Translate vague user input into concrete parameters
- Fill in defaults when user is unclear
- Log all assumptions
- Validate obvious impossibilities early
- Output structured design_spec.json
- Handle incremental modifications to existing designs
"""

from __future__ import annotations
from pathlib import Path
import json

from src.core.hardware_config import footprints as hw_footprints, enclosure as hw_enclosure
from typing import Optional

from src.llm.client import MockLLMClient, GeminiClient, LLMClient

def project_root() -> Path:
    return Path(__file__).resolve().parents[2]

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def is_modification_request(text: str) -> bool:
    """Detect if user is asking to modify an existing design rather than create new."""
    text_lower = text.lower()
    
    # Modification keywords
    mod_keywords = [
        "move", "raise", "lower", "shift", "offset", "change", "modify",
        "make the", "set the", "adjust", "increase", "decrease",
        "bigger", "smaller", "wider", "narrower", "taller", "shorter",
        "add a", "add an", "remove", "delete", "swap", "replace", "create a", "put a", "insert a",
        "the button", "the middle", "the left", "the right", "the top", "the bottom",
        "more", "less", "higher", "2cm", "1cm", "5mm", "10mm",
        "diode", "led", "ir led", "ir diode", "infrared",
    ]
    
    # If text starts with action verbs, it's likely a modification
    action_starts = ["move", "raise", "lower", "shift", "add", "remove", "make", "change", "set", "create", "put", "insert"]
    first_word = text_lower.split()[0] if text_lower.split() else ""
    
    if first_word in action_starts:
        return True
    
    # Check for modification keywords
    return any(kw in text_lower for kw in mod_keywords)

class ConsultantAgent:
    """
    Consultant Agent converts user prompts into normalized design specifications.
    
    Output: design_spec.json with:
    - Device constraints (length, width, thickness)
    - Button list with placement hints
    - Battery/LED specs
    - Manufacturing constraints
    - Logged assumptions
    """
    
    def __init__(self, use_llm: bool = True):
        self.use_llm = use_llm
    
    def _client(self, stage: str = "consultant") -> LLMClient:
        print("[CONSULTANT] Initializing LLM client...")
        if self.use_llm:
            try:
                client = GeminiClient()
                client.current_stage = stage  # Set stage for usage tracking
                print("[CONSULTANT] PATH: Using GeminiClient (LLM enabled)")
                return client
            except Exception as e:
                print(f"[CONSULTANT] ✗ GeminiClient failed: {e}")
                print("[CONSULTANT] PATH: FALLBACK → Using MockLLMClient")
        else:
            print("[CONSULTANT] PATH: Using MockLLMClient (LLM disabled)")
        return MockLLMClient()
    
    def generate_design_spec(
        self, 
        user_prompt: str, 
        use_llm: Optional[bool] = None,
        previous_design: Optional[dict] = None
    ) -> dict:
        """
        Generate design_spec.json from user prompt.
        
        Args:
            user_prompt: Natural language description of desired remote
            use_llm: Override default LLM setting
            previous_design: If provided, treat prompt as modification request
        
        Returns:
            dict matching design_spec.schema.json
        """
        print("\n[CONSULTANT] Generating design spec...")
        print(f"[CONSULTANT] Prompt: {user_prompt[:80]}{'...' if len(user_prompt) > 80 else ''}")
        print(f"[CONSULTANT] use_llm override: {use_llm}")
        print(f"[CONSULTANT] previous_design provided: {previous_design is not None}")
        
        root = project_root()
        
        # Load library and system prompt
        library = read_text(root / "library" / "remote_design_rules.md")
        consultant_prompt = read_text(root / "prompts" / "consultant_v2.md")
        
        # Load schema for reference
        schema = json.loads(read_text(root / "schemas" / "design_spec.schema.json"))
        
        # Check if this is a modification request
        is_modification = previous_design is not None and is_modification_request(user_prompt)
        print(f"[CONSULTANT] is_modification_request: {is_modification}")
        
        if is_modification:
            print("[CONSULTANT] PATH: MODIFICATION mode - keeping previous design, applying changes")
            system_prompt = f"""{consultant_prompt}

# Design Spec Schema
{json.dumps(schema, indent=2)}

# Library Reference
{library}

# IMPORTANT: MODIFICATION MODE
The user is asking to MODIFY an existing design, not create a new one.
Below is the CURRENT design that should be modified according to the user's request.
Keep all existing settings EXCEPT what the user specifically asks to change.

## Current Design (to be modified):
```json
{json.dumps(previous_design, indent=2)}
```

# MODIFICATION INSTRUCTIONS
Apply ONLY the changes requested. Do not add extra buttons, do not change dimensions 
unless specifically asked.

## For adding components (LED, diode, IR diode):
- If the user asks to "add a diode", "add an IR LED", "add IR", or "create a diode":
  Add an entry to the `leds` array with `"id": "LED1"` and `"placement_hint": "top"`
  IR diodes should be placed at the top of the remote so they can point outward.
- Example: Add `"leds": [{{"id": "LED1", "color": "IR", "placement_hint": "top"}}]`

## For position changes like "move button X higher/lower/left/right":
- Use the `offset_y_mm` field for vertical movement (positive = towards top)
- Use the `offset_x_mm` field for horizontal movement (positive = towards right)
- Example: "move middle button 20mm higher" → add `"offset_y_mm": 20` to that button's placement_hint

## For region changes like "put button at the top":
- Change the `region` field to "top", "center", or "bottom"
- Change the `horizontal` field to "left", "center", or "right"

# Output Requirement
Output **valid JSON only** matching the design_spec.schema.json structure.
The output should be the MODIFIED design with the user's changes applied.
Add an assumption noting what was modified.
"""
        else:
            print("[CONSULTANT] PATH: NEW DESIGN mode - generating from scratch")
            system_prompt = f"""{consultant_prompt}

# Design Spec Schema
{json.dumps(schema, indent=2)}

# Library Reference
{library}

# Output Requirement
Output **valid JSON only** matching the design_spec.schema.json structure.
"""
        
        effective_use_llm = use_llm if use_llm is not None else self.use_llm
        print(f"[CONSULTANT] Effective use_llm: {effective_use_llm}")
        
        # Determine stage name for tracking
        stage_name = "consultant_modify" if is_modification else "consultant_new"
        
        if effective_use_llm:
            client = self._client(stage=stage_name)
        else:
            print("[CONSULTANT] PATH: LLM disabled, using MockLLMClient")
            client = MockLLMClient()
        
        print(f"[CONSULTANT] Client type: {type(client).__name__}")
        print("[CONSULTANT] Calling LLM for design spec...")
        design_spec = client.complete_json(system=system_prompt, user=user_prompt)
        print(f"[CONSULTANT] ✓ LLM returned design spec with {len(design_spec.get('buttons', []))} buttons")
        
        # Validate and fill defaults if needed
        print("[CONSULTANT] Validating and filling defaults...")
        design_spec = self._validate_and_fill_defaults(design_spec)
        print(f"[CONSULTANT] ✓ Validation complete, {len(design_spec.get('assumptions', []))} assumptions logged")
        
        return design_spec
    
    def _validate_and_fill_defaults(self, spec: dict) -> dict:
        """Ensure design_spec has all required fields with reasonable defaults."""
        assumptions = spec.get("assumptions", [])
        
        # Ensure units
        spec.setdefault("units", "mm")
        
        # Device constraints
        if "device_constraints" not in spec:
            spec["device_constraints"] = {}
            assumptions.append("Device constraints not specified, using defaults")
        
        device = spec["device_constraints"]
        if "length_mm" not in device:
            device["length_mm"] = 180.0
            assumptions.append("Device length defaulted to 180mm")
        if "width_mm" not in device:
            device["width_mm"] = 45.0
            assumptions.append("Device width defaulted to 45mm")
        if "thickness_mm" not in device:
            device["thickness_mm"] = 18.0
            assumptions.append("Device thickness defaulted to 18mm")
        
        # Buttons
        btn_fp = hw_footprints()["button"]
        if "buttons" not in spec or not spec["buttons"]:
            spec["buttons"] = [
                {
                    "id": "BTN1",
                    "label": "Button 1",
                    "switch_type": btn_fp["switch_type"],
                    "cap_diameter_mm": btn_fp["cap_diameter_mm"],
                    "priority": "normal"
                }
            ]
            assumptions.append("No buttons specified, created 1 default button")
        
        # Ensure each button has required fields
        for i, btn in enumerate(spec["buttons"]):
            if "id" not in btn:
                btn["id"] = f"BTN{i+1}"
            if "label" not in btn:
                btn["label"] = btn.get("id", f"Button {i+1}")
            if "switch_type" not in btn:
                btn["switch_type"] = btn_fp["switch_type"]
            if "cap_diameter_mm" not in btn:
                btn["cap_diameter_mm"] = btn_fp["cap_diameter_mm"]
            if "priority" not in btn:
                btn["priority"] = "normal"
        
        # Constraints
        if "constraints" not in spec:
            spec["constraints"] = {}
            assumptions.append("Constraints not specified, using defaults")
        
        enc = hw_enclosure()
        constraints = spec["constraints"]
        constraints.setdefault("min_button_spacing_mm", 3.0)
        constraints.setdefault("edge_clearance_mm", 5.0)
        constraints.setdefault("min_wall_thickness_mm", enc["wall_thickness_mm"])
        constraints.setdefault("mounting_preference", "screws")
        
        # Battery (optional)
        if "battery" not in spec:
            spec["battery"] = {
                "type": "2xAAA",
                "placement_hint": "bottom"
            }
            assumptions.append("Battery defaulted to 2xAAA at bottom")
        
        # LEDs (optional)
        spec.setdefault("leds", [])
        
        # Store assumptions
        spec["assumptions"] = assumptions
        
        return spec

# Refactor: Split Single Agent into Design + Circuit Agents

This document describes how to refactor the current single `DesignAgent` into two
separate agents — a **DesignAgent** (Step 1: physical design) and a **CircuitAgent**
(Step 2: electrical design). The 2D outline-based design is preserved; no 3D/CSG
changes are involved.

---

## Why Split?

The current agent handles everything in one `submit_design` call: component selection,
net design, outline shape, enclosure, and UI placements. This overloads a single
system prompt with both physical-design concerns (shape, ergonomics, placement) and
electrical-engineering concerns (pin addressing, current limiting, bypass caps, net
topology). Splitting gives:

- **Shorter, focused prompts** — each agent is an expert at one thing
- **Autonomous circuit step** — the circuit agent runs without user interaction, reading
  the design output and making its own decisions
- **Separate conversations** — design iteration doesn't pollute circuit context and
  vice versa
- **Targeted invalidation** — changing the design invalidates circuit → placement →
  routing; changing only the circuit invalidates placement → routing but not design
- **Extensibility** — the `_BaseAgent` pattern makes adding future agents (firmware,
  testing) trivial

---

## Architecture Overview

```
User prompt
    │
    ▼
┌──────────────────┐   SSE    ┌───────────────┐
│   DesignAgent    │ ◀──────▶ │  design.js    │   Chat UI (user-facing)
│  (Step 1)        │          │               │
│  submit_design   │          └───────────────┘
└────────┬─────────┘
         │ design.json
         ▼
┌──────────────────┐   SSE    ┌───────────────┐
│  CircuitAgent    │ ◀──────▶ │  circuit.js   │   Read-only log (autonomous)
│  (Step 2)        │          │               │
│  submit_circuit  │          └───────────────┘
└────────┬─────────┘
         │ circuit.json
         ▼
    placement → routing → scad → stl → gcode
```

---

## File-by-File Changes

### 1. `src/agent/core.py` — Extract `_BaseAgent`, add `CircuitAgent`

**Current state:** Single `DesignAgent` class (~270 lines) with the full streaming
loop, tool dispatch, conversation persistence, and submit logic all inline.

**Target state:** Three classes:

#### `_BaseAgent` (abstract)

Extracts the shared agent loop from the current `DesignAgent`:

```python
class _BaseAgent:
    """Shared streaming agent loop — subclasses define tools and submit logic."""

    def __init__(self, catalog: CatalogResult, session: Session,
                 conversation_file: str):
        self.client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.catalog = catalog
        self.session = session
        self._conversation_file = conversation_file

        saved = session.read_artifact(conversation_file)
        self.messages: list[dict] = sanitize_messages(saved) if isinstance(saved, list) else []

    # ── Abstract interface (subclasses must implement) ─────────
    def _get_tools(self) -> list[dict]: ...
    def _get_system_prompt(self) -> str: ...
    def _handle_tool(self, name: str, inp: dict) -> tuple[str, bool]: ...
    #   Returns (result_text, is_terminal_submit)

    def _save_conversation(self) -> None:
        self.session.write_artifact(self._conversation_file, self.messages)

    async def run(self, user_prompt: str) -> AsyncGenerator[AgentEvent, None]:
        """Generic agent loop — streaming, tool calls, conversation persistence."""
        self.messages.append({"role": "user", "content": user_prompt})

        for turn in range(MAX_TURNS):
            api_messages = prune_messages(self.messages)

            async with self.client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                thinking={"type": "enabled", "budget_tokens": THINKING_BUDGET},
                system=self._get_system_prompt(),
                tools=self._get_tools(),
                messages=api_messages,
            ) as stream:
                async for event in stream:
                    agent_event = self._handle_stream_event(event)
                    if agent_event:
                        yield agent_event
                response = await stream.get_final_message()

            content_blocks = serialize_content(response.content)
            self.messages.append({"role": "assistant", "content": content_blocks})

            # Token counting (best-effort)
            try:
                tc = await self.client.messages.count_tokens(
                    model=MODEL, messages=api_messages,
                    system=self._get_system_prompt(),
                    tools=self._get_tools(),
                    thinking={"type": "enabled", "budget_tokens": THINKING_BUDGET},
                )
                yield AgentEvent("token_usage", {
                    "input_tokens": tc.input_tokens, "budget": TOKEN_BUDGET
                })
            except Exception:
                pass

            if response.stop_reason == "max_tokens":
                self._save_conversation()
                yield AgentEvent("error", {"message": "Response truncated"})
                return

            tool_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]
            if not tool_blocks:
                self._save_conversation()
                yield AgentEvent("done", {})
                return

            tool_results = []
            submit_done = False
            for block in tool_blocks:
                yield AgentEvent("tool_call", {"name": block["name"], "input": block["input"]})
                result_text, is_submit = self._handle_tool(block["name"], block["input"])
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": result_text,
                })
                yield AgentEvent("tool_result", {
                    "name": block["name"], "content": result_text,
                    "is_error": not is_submit and block["name"].startswith("submit_"),
                })
                if is_submit:
                    submit_done = True

            self.messages.append({"role": "user", "content": tool_results})

            if submit_done:
                self._save_conversation()
                # Subclass emits its own success event before returning
                return

        self._save_conversation()
        yield AgentEvent("error", {"message": f"Exceeded {MAX_TURNS} turns"})

    def _handle_stream_event(self, event) -> AgentEvent | None:
        """Convert Anthropic stream events to AgentEvents."""
        # (identical to current implementation — thinking/text/block_stop deltas)
        ...
```

The `_handle_stream_event` method is identical to the current one — it maps
`content_block_start`, `content_block_delta`, and `content_block_stop` into
`thinking_start/delta`, `message_start/delta`, and `block_stop` AgentEvents.

#### `DesignAgent(_BaseAgent)` — Step 1

Handles **physical design only**: outline, enclosure, UI placements, and a
`device_description`. No longer responsible for components or nets.

```python
class DesignAgent(_BaseAgent):
    def __init__(self, catalog, session):
        super().__init__(catalog, session, conversation_file="design_conversation.json")

    def _get_tools(self) -> list[dict]:
        return DESIGN_TOOLS  # list_components, get_component, submit_design

    def _get_system_prompt(self) -> str:
        printer = get_printer(self.session.printer_id)
        return build_design_prompt(self.catalog, printer=printer)

    def _handle_tool(self, name, inp):
        if name == "list_components":
            return catalog_summary(self.catalog), False
        if name == "get_component":
            return self._tool_get_component(inp), False
        if name == "submit_design":
            return self._tool_submit_design(inp)
        if name == "check_placement_feasibility":
            return self._tool_check_feasibility(inp), False
        return f"Unknown tool: {name}", False

    def _tool_submit_design(self, inp):
        """Validate outline + ui placements + device_description. Save design.json."""
        # Parse and validate (reuse existing parse_design / validate_design)
        # On success: save design.json, invalidate circuit/placement/routing
        # Return (result_text, True) on success, (error_text, False) on failure
        ...
```

**Key change to `submit_design` tool schema:** The `components` and `nets` fields
are **removed**. The design agent now submits:

| Field | Purpose |
|-------|---------|
| `device_description` | 2–4 sentence description used by circuit agent |
| `outline` | Polygon vertices (unchanged from current) |
| `enclosure` | Height, top_surface, edge profiles (unchanged) |
| `ui_placements` | Button/LED/switch positions (unchanged) |

The agent still calls `get_component` to understand body dimensions and mounting
requirements for placement decisions, but it does **not** select internal components
or design nets.

**Invalidation on submit:** `circuit.json`, `placement.json`, `routing.json` (and
downstream) are deleted.

#### `CircuitAgent(_BaseAgent)` — Step 2

Handles **electrical design**: selects all components (including internal ones like
MCU, battery, resistors, capacitors) and designs the net topology.

```python
class CircuitAgent(_BaseAgent):
    def __init__(self, catalog, session):
        super().__init__(catalog, session, conversation_file="circuit_conversation.json")

    def _get_tools(self):
        return CIRCUIT_TOOLS  # list_components, get_component, submit_circuit

    def _get_system_prompt(self):
        return build_circuit_prompt(self.catalog)

    def _handle_tool(self, name, inp):
        if name == "list_components":
            return catalog_summary(self.catalog), False
        if name == "get_component":
            return self._tool_get_component(inp), False
        if name == "submit_circuit":
            return self._tool_submit_circuit(inp)
        return f"Unknown tool: {name}", False

    def _tool_submit_circuit(self, inp):
        """Validate components + nets. Save circuit.json."""
        # Validate: catalog IDs exist, mounting styles allowed, pins valid, ...
        # Enrich components with ui_placement flag from design.json
        # On success: save circuit.json, invalidate placement/routing
        # Return (result_text, True) on success, (error_text, False) on failure
        ...
```

**Autonomous invocation:** The circuit agent is NOT called with a user prompt from
the chat. Instead, the server reads `design.json` and generates the user message
automatically via `build_circuit_user_prompt(design_data)`.

**Invalidation on submit:** `placement.json`, `routing.json` (and downstream) are
deleted. Does NOT invalidate `design.json`.

---

### 2. `src/agent/tools.py` — Split into `DESIGN_TOOLS` and `CIRCUIT_TOOLS`

**Current state:** A single `TOOLS` list containing `list_components`, `get_component`,
`submit_design` (with components, nets, outline, enclosure, ui_placements), and
`check_placement_feasibility`.

**Target state:** Two separate tool lists sharing the lookup tools.

```python
# Shared (identical for both agents)
_LIST_COMPONENTS = { ... }   # unchanged
_GET_COMPONENT = { ... }     # unchanged

# Design agent tools
DESIGN_TOOLS = [
    _LIST_COMPONENTS,
    _GET_COMPONENT,
    {
        "name": "submit_design",
        "description": "Submit a physical device design for validation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_description": {
                    "type": "string",
                    "description": (
                        "2-4 sentence description of what the device does, "
                        "how it is used, and what the user asked for. "
                        "This is passed to the circuit agent."
                    ),
                },
                "outline": { ... },        # unchanged from current
                "enclosure": { ... },      # unchanged from current
                "ui_placements": { ... },  # unchanged from current
            },
            "required": ["device_description", "outline", "enclosure", "ui_placements"],
        },
    },
    _CHECK_PLACEMENT_FEASIBILITY,  # unchanged
]

# Circuit agent tools
CIRCUIT_TOOLS = [
    _LIST_COMPONENTS,
    _GET_COMPONENT,
    {
        "name": "submit_circuit",
        "description": "Submit a complete circuit design for validation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "components": { ... },  # moved from submit_design — unchanged schema
                "nets": { ... },        # moved from submit_design — unchanged schema
            },
            "required": ["components", "nets"],
        },
    },
]
```

The `check_placement_feasibility` tool stays with the design agent only, since
it checks whether auto-placed components fit given the outline and UI placements.

---

### 3. `src/agent/prompt.py` — Split into `build_design_prompt` and `build_circuit_prompt`

**Current state:** A single `_build_system_prompt()` function returning a ~350-line
prompt covering everything: manufacturing process, component rules, net rules,
outline rules, enclosure rules, UI placement rules, feasibility checks, and two
full examples.

**Target state:** Three functions.

#### `catalog_summary(catalog)` — shared (renamed from `_catalog_summary`)

Unchanged. Returns a markdown table of all components.

#### `build_design_prompt(catalog, printer)` — Design agent

Focuses on the **physical product**:

- Manufacturing process overview (for context)
- Device design philosophy (ergonomics, proportions, real-world sizing)
- Build plate constraints (from printer)
- Outline rules (vertices, easing, winding)
- Enclosure rules (height_mm, z_top, top_surface, edge profiles)
- UI placement rules (position within outline, edge_index for side-mount, edge clearance)
- Space reservation for auto-placed components
- Feasibility check instructions
- `device_description` requirement (clear summary for circuit agent)
- Component catalog summary (so the agent knows what UI components exist)
- Examples: physical designs ONLY (outline + enclosure + ui_placements)

**Removed from design prompt:** Net design rules, pin addressing, dynamic pin
allocation, internal_nets, current limiting, bypass cap rules, power distribution.

#### `build_circuit_prompt(catalog)` — Circuit agent

Focuses on **electronics engineering**:

- Role: "You are an electronics engineer selecting components and designing
  electrical connections for a device whose physical shape has already been decided."
- Component selection rules (catalog_id, instance_id, config, mounting_style)
- Pin addressing: `"instance_id:pin_id"` and `"instance_id:group_id"`
- Dynamic pin allocation (pin_groups, allocatable groups)
- Internal nets (e.g. button sides A/B)
- Net topology rules (2+ pins per net, unique direct pin refs)
- Power distribution patterns (VCC rail, GND rail)
- Current limiting (LED resistor calculation)
- Bypass capacitors and pull-up resistors
- Component catalog summary
- Examples: circuit designs ONLY (components + nets)

#### `build_circuit_user_prompt(design_data)` — Auto-generates the circuit agent's input

This is called by the server, not the LLM. It reads `design.json` and produces:

```python
def build_circuit_user_prompt(design_data: dict) -> str:
    """Generate the user message for the circuit agent from design.json."""
    desc = design_data.get("device_description", "")
    placements = design_data.get("ui_placements", [])

    parts = [f"Device description: {desc}", "", "UI components already placed:"]
    for p in placements:
        parts.append(f"  - {p['instance_id']} (catalog: {p.get('catalog_id', '?')})")

    parts.append("")
    parts.append(
        "Select all components needed (including the UI components above) "
        "and design the electrical nets. Call submit_circuit when ready."
    )
    return "\n".join(parts)
```

#### Specific system prompts

This markdown must be self-contained. The exact target prompt text is included below.
These are the prompt bodies the refactor should implement.

##### Full target `build_design_prompt()` body

Use this prompt body, with `{summary}` and `{build_plate_section}` injected the same
way the current code injects catalog and printer context.

```text
You are a product designer who creates beautiful, ergonomic electronic devices. You design enclosures for 3D-printed (PLA) devices with silver ink conductive traces.

## Your Task
Given a user's device description:
1. Envision the product — how it looks, how it's held, how it feels, and how it is used
2. Select UI components from the catalog (buttons, LEDs, switches, speakers, etc.)
3. Design the device outline and enclosure shape
4. Place UI components where fingers naturally reach them
5. Write a device description for the electronics engineer

You select and place only **UI components** — the ones users interact with directly (buttons, LEDs, switches, speakers, etc.). Components marked `UI: yes` in the catalog need UI placement. Internal components (MCU, resistors, batteries, capacitors) are selected by the electronics engineer in the next step.

You do NOT design the full circuit. Do NOT choose internal components unless they are directly user-facing. Do NOT create nets.

## Available Components
{summary}

Use `get_component` to read full details before placing a component.

{build_plate_section}

## Manufacturing Process
1. 3D printer prints the PLA enclosure shell with two pauses
2. Silver ink printer deposits conductive traces on the ironed floor surface
3. Components are inserted — pins poke through holes into the ink traces
4. 3D printer resumes and seals the ceiling

The enclosure has: solid floor (2mm PLA), ink layer at Z=2mm (ironed surface), cavity for components, solid ceiling (2mm PLA). Components sit in pockets; their pins reach down through pinholes to contact the ink traces.

## Physical Design Philosophy

**Think like an industrial designer.** You are defining a physical object that a person will hold, touch, look at, and understand immediately. The outline, proportions, enclosure height, and UI positions must all support the intended use.

### Design Thinking
Before writing any JSON, you must be able to describe the finished object in plain language:
- What is its overall **silhouette**? Describe it as if sketching on paper — “an elongated handheld remote with a gently tapered nose”, “a rounded wedge that leans toward the user”, “a pebble-like oval with a clear thumb zone”.
- How does it **feel in the hand** or on the table? Where does the palm rest? Where does the thumb press? Which face is the interaction face?
- What are the **surfaces** the user interacts with? A top button deck, a front indicator area, a side switch zone, a rear cable edge.
- What gives it **character**? The form should look intentional, not like a random polygon around some components.

### Ergonomic Dimensions
- A handheld remote or wand is commonly ~100–140mm long and ~35–55mm wide.
- A compact tabletop controller is commonly ~50–90mm wide and ~40–100mm deep.
- Buttons should sit where the intended finger can reach naturally without awkward repositioning.
- Heavier internal components (especially batteries) should be given central, uninterrupted floor space.

### Device Orientation
Coordinate system for 2D layout: **x** increases rightward, **y** increases downward. `y = 0` is the top of the device.
- For a handheld device, the top face is usually the user-facing surface with buttons and indicators.
- For a tabletop device, the front edge is usually the edge closest to the user.
- Think in screen-space when defining the outline, but always justify dimensions in real physical terms.

## Design Rules

### Outline (device shape)
- The outline is a flat list of vertex objects, clockwise winding
- Each vertex has `x` and `y` in mm
- A vertex may include `ease_in` and/or `ease_out` in mm for rounded transitions
- If only one of `ease_in` or `ease_out` is set, the other mirrors it
- The polygon must be valid, non-self-intersecting, and have positive area
- The outline must fit on the printer build plate

### Enclosure
The floor is always flat. Only the ceiling and outer walls vary in shape.

The `enclosure` object controls the third dimension:
- `height_mm` is the default ceiling height and the minimum height everywhere
- vertices may override local ceiling height using `z_top`
- `top_surface` may add a dome or ridge over the base vertex interpolation
- `edge_top` and `edge_bottom` may add fillets or chamfers

Rules:
- `height_mm` must be at least floor (2mm) + tallest internal component + ceiling (2mm)
- If you use `z_top`, it should support the intended form, not create arbitrary unevenness
- Use `top_surface` only when it improves ergonomics or visual character
- Keep edge treatments modest; large bottom edge treatments reduce usable internal floor area

### Space Reservation for Auto-Placed Components
Internal components are auto-placed later. Your UI placements must leave enough uninterrupted area for them.

Before placing UI components:
- Use `get_component` to check the body size of any likely large internals such as batteries or MCUs
- Reserve a contiguous rectangle large enough for the biggest likely internal component plus keepout margins
- If `edge_bottom` is a fillet or chamfer, remember it reduces usable floor area near the walls

UI placement patterns that work well:
- Cluster buttons and LEDs in one interaction zone and leave another zone clear for internals
- Avoid splitting the interior into thin strips with scattered UI parts
- If the outline is narrow or tapered, keep the narrowest end visually simple and structurally light

### UI Placements
- Only create `ui_placements` for components with `ui_placement = true`
- Top-mount components get `x_mm` and `y_mm` inside the outline
- Side-mount components must include `edge_index`
- `edge_index` is 0-based: edge `i` runs from `outline[i]` to `outline[(i + 1) % n]`
- Non-side-mount components must not specify `edge_index`
- Respect body size and keepout margins from `get_component`
- The placement should make ergonomic sense for the intended use

### Feasibility Check Before Submitting
After finalizing the UI components, outline, enclosure, and ui_placements — but before calling `submit_design` — call `check_placement_feasibility`.

Use the same:
- `components` for likely internal and UI footprint checks
- `outline`
- `ui_placements`
- `enclosure`

If any component reports `[FAIL]`, adjust the layout and run the check again before submitting.

### Device Description
You must write a `device_description` of 2–4 sentences that explains:
- what the device does
- how the user interacts with it
- what role each UI component serves

This is read by the electronics engineer who designs the circuit. It must be specific enough to guide circuit decisions.

## Process
1. Describe the object in plain language before writing any JSON
2. Browse UI components with `list_components` and `get_component`
3. Write a layout blueprint before writing the final JSON
4. Define the outline and enclosure
5. Place the UI components
6. Run `check_placement_feasibility`
7. Write the `device_description`
8. Submit with `submit_design`
9. If validation fails, read errors, fix, and resubmit

### Layout Blueprint (required before writing JSON)
Before writing the final design JSON, produce a short blueprint.

For the outline, state:
- overall width and height
- silhouette description
- where the widest and narrowest regions are
- why the corner easing values make sense

For the enclosure, state:
- default height
- any local height changes using `z_top`
- whether `top_surface` is used and why
- whether edge treatments are used and why

For UI placements, state:
- where each component goes
- why that location fits hand use or viewing angle
- what clear region is being reserved for internal components

Example blueprint format:
Outline:
- 48mm wide × 128mm tall handheld remote
- rounded rectangle with a slightly narrower nose
- larger bottom half reserved for battery cavity

Enclosure:
- default height 18mm
- front corners lower, rear corners higher to create a gentle wedge feel
- top_surface omitted for a cleaner, flatter button deck

UI placements:
- power button centered in upper thumb zone
- status LED near the nose for line-of-sight visibility
- lower half left clear for battery and MCU

## Example: Handheld Tapered Remote
*A slim handheld remote that feels stable in one hand. The top third holds the thumb controls; the lower half stays clear for battery and logic. The nose is slightly narrower so the front LED reads as the “head” of the device.*

*UI components chosen: led_5mm (front status emitter), tactile_button_6x6 (main action button), tactile_button_6x6 (secondary button).*

**Blueprint:**
```text
Outline:
- 46mm wide × 128mm tall remote
- rounded rectangle body with a gentle taper in the top quarter
- 8mm corner easing at the bottom, 6mm in the upper shoulder to keep the nose crisper

Enclosure:
- default height 18mm
- rear half slightly taller than the front using z_top to give a subtle wedge feel in hand
- small edge_top fillet for comfort, modest edge_bottom chamfer to keep the base clean

UI placements:
- main button at x=23, y=34 in the primary thumb zone
- secondary button below it at x=23, y=58
- status LED near the nose at x=23, y=16 for immediate visibility
- lower half from roughly y=72 to y=118 left open for battery cavity and MCU
```

```json
{
    "device_description": "A handheld remote with two thumb buttons and a front status LED. The upper button is the primary action control, the lower button is a secondary mode control, and the LED indicates status near the front of the device. The lower half is reserved for the battery and control electronics.",
    "outline": [
        {"x": 6,  "y": 0,   "ease_in": 6, "ease_out": 6, "z_top": 16},
        {"x": 40, "y": 0,   "ease_in": 6, "ease_out": 6, "z_top": 16},
        {"x": 46, "y": 22,  "ease_in": 4, "ease_out": 6, "z_top": 17},
        {"x": 46, "y": 112, "ease_in": 10, "ease_out": 10, "z_top": 19},
        {"x": 38, "y": 128, "ease_in": 8, "ease_out": 8, "z_top": 19},
        {"x": 8,  "y": 128, "ease_in": 8, "ease_out": 8, "z_top": 19},
        {"x": 0,  "y": 112, "ease_in": 10, "ease_out": 10, "z_top": 19},
        {"x": 0,  "y": 22,  "ease_in": 6, "ease_out": 4, "z_top": 17}
    ],
    "enclosure": {
        "height_mm": 18,
        "edge_top": {"type": "fillet", "size_mm": 2},
        "edge_bottom": {"type": "chamfer", "size_mm": 1.5}
    },
    "ui_placements": [
        {"instance_id": "led_1", "catalog_id": "led_5mm", "x_mm": 23, "y_mm": 16},
        {"instance_id": "btn_1", "catalog_id": "tactile_button_6x6", "x_mm": 23, "y_mm": 34},
        {"instance_id": "btn_2", "catalog_id": "tactile_button_6x6", "x_mm": 23, "y_mm": 58}
    ]
}
```

Why this works:
- The nose is visually lighter, which helps the LED read as the focal point
- The buttons sit in a natural thumb column
- The lower half remains open for a battery holder and logic placement
- The small height increase toward the bottom makes the grip feel more substantial without making the front bulky

## Example: Rounded Wedge Console
*A small tabletop controller that tilts toward the user. The front edge is lower and visually lighter, while the back edge is taller to hold internal space and make the controls easier to see. The LED sits at the front center; the two buttons sit on the upper deck with balanced spacing.*

*UI components chosen: led_5mm (front indicator), tactile_button_6x6 (left control), tactile_button_6x6 (right control).*

**Blueprint:**
```text
Outline:
- 72mm wide × 86mm deep tabletop controller
- soft rounded rectangle with broader rear shoulders and gentler front corners
- width supports two buttons with clear finger spacing

Enclosure:
- default height 16mm
- front vertices lower, rear vertices higher to create the wedge character
- shallow dome top_surface centered between the buttons to soften the top plane without hurting usability
- top fillet only; bottom kept sharper to preserve interior floor area

UI placements:
- front LED centered at x=36, y=18 for easy visibility
- left and right buttons at x=24 and x=48, y=42 in the primary interaction zone
- rear third left clear for internal components and cable routing
```

```json
{
    "device_description": "A tabletop two-button controller with a front status LED. The buttons sit on an angled upper deck facing the user, and the LED provides front-facing feedback. The rear portion of the enclosure is kept taller and clearer internally for the control electronics.",
    "outline": [
        {"x": 8,  "y": 0,  "ease_in": 8,  "ease_out": 8,  "z_top": 14},
        {"x": 64, "y": 0,  "ease_in": 8,  "ease_out": 8,  "z_top": 14},
        {"x": 72, "y": 18, "ease_in": 6,  "ease_out": 8,  "z_top": 15},
        {"x": 72, "y": 74, "ease_in": 12, "ease_out": 12, "z_top": 20},
        {"x": 62, "y": 86, "ease_in": 10, "ease_out": 10, "z_top": 20},
        {"x": 10, "y": 86, "ease_in": 10, "ease_out": 10, "z_top": 20},
        {"x": 0,  "y": 74, "ease_in": 12, "ease_out": 12, "z_top": 20},
        {"x": 0,  "y": 18, "ease_in": 8,  "ease_out": 6,  "z_top": 15}
    ],
    "enclosure": {
        "height_mm": 16,
        "top_surface": {
            "type": "dome",
            "peak_x_mm": 36,
            "peak_y_mm": 38,
            "peak_height_mm": 19,
            "base_height_mm": 16
        },
        "edge_top": {"type": "fillet", "size_mm": 2},
        "edge_bottom": {"type": "none"}
    },
    "ui_placements": [
        {"instance_id": "led_1", "catalog_id": "led_5mm", "x_mm": 36, "y_mm": 18},
        {"instance_id": "btn_1", "catalog_id": "tactile_button_6x6", "x_mm": 24, "y_mm": 42},
        {"instance_id": "btn_2", "catalog_id": "tactile_button_6x6", "x_mm": 48, "y_mm": 42}
    ]
}
```

Why this works:
- The lower front and taller rear create a clear wedge profile without overcomplicating the outline
- The button spacing is readable and finger-friendly
- The LED is centered on the front visual axis
- The rear volume is preserved for internal placement and routing

When you are ready, call `submit_design` with `device_description`, `outline`, `enclosure`, and `ui_placements`.
```

##### Full target `build_circuit_prompt()` body

Use this prompt body as the circuit prompt. This remains close to the already split
version because it is structurally correct.

```text
You are an electronics engineer who designs circuits for 3D-printed electronic devices. Your circuits will be manufactured with silver ink conductive traces on a PLA enclosure.

## Your Task
A product designer has already shaped the device and placed UI components (buttons, LEDs, etc.) on its surface. You receive a device description and the list of placed UI components. Your job is to:
1. Include the already-placed UI components in the circuit (with their exact instance_ids)
2. Add any internal components needed (MCU, resistors, batteries, capacitors, etc.)
3. Design the net list connecting all component pins

Work autonomously — read component details, design the circuit, and submit. Do not ask questions.

## Available Components
{summary}

Use `get_component` to read full pin/mounting details before using a component in your design.

## Design Rules

### Components
- `catalog_id`: must match an ID from the catalog
- `instance_id`: your unique name for this instance (e.g. "r_1", "mcu_1"). **Important:** for UI components already placed by the designer, use their exact instance_ids as given.
- `config`: only for configurable components (e.g. resistor value)
- `mounting_style`: optional override from the component's `allowed_styles`

### Nets (electrical connections)
- Pin addressing: `"instance_id:pin_id"` (e.g. `"bat_1:V+"`, `"led_1:anode"`)
- **Dynamic pin allocation**: components with allocatable `pin_groups` support `"instance_id:group_id"` references (e.g. `"mcu_1:gpio"`, `"btn_1:A"`). You can use the same group reference in multiple nets — each use allocates a different physical pin from the pool. The router picks the optimal pin for each.
- Each direct pin reference may appear in at most ONE net (group references are exempt — they're dynamic)
- Components with `internal_nets` have pins that are internally connected (e.g. button pins 1↔2 are side A, 3↔4 are side B) — use the group reference instead of picking individual pins
- Each net must have at least 2 pins

### Circuit Design Principles
- Every component needs power: connect power pins to VCC/GND nets
- LEDs need current-limiting resistors — calculate the value from supply voltage, LED forward voltage, and desired current (~10–20mA)
- MCUs need bypass capacitors on their power pins
- Buttons/switches: use the group references (A/B) rather than individual pins
- Keep net names descriptive: "VCC", "GND", "BTN1_IN", "LED_DRIVE", etc.

## Process
1. Read the device description and placed UI component list
2. Read component details with `get_component` for each component you plan to use
3. Select all needed internal components
4. Include the placed UI components with their exact instance_ids
5. Design the nets — power, ground, control, and signal paths
6. Submit with `submit_circuit`
7. If validation fails, read errors, fix, and resubmit

## Example: Simple LED Device
Given: device_description = "A handheld spotlight. Button toggles the LED."
Placed UI components: led_1 (led_5mm), btn_1 (tactile_button_6x6)
```json
{
    "components": [
        {"catalog_id": "battery_holder_2xAAA", "instance_id": "bat_1"},
        {"catalog_id": "resistor_axial", "instance_id": "r_1", "config": {"resistance_ohms": 150}},
        {"catalog_id": "led_5mm", "instance_id": "led_1", "config": {"wavelength_nm": 620, "forward_voltage_v": 2.0}},
        {"catalog_id": "tactile_button_6x6", "instance_id": "btn_1"}
    ],
    "nets": [
        {"id": "POWER", "pins": ["bat_1:V+", "r_1:1"]},
        {"id": "LED_DRIVE", "pins": ["r_1:2", "led_1:anode"]},
        {"id": "BTN_IN", "pins": ["btn_1:A", "bat_1:GND"]},
        {"id": "BTN_OUT", "pins": ["btn_1:B", "led_1:cathode"]}
    ]
}
```

## Example: MCU-Based Controller
Given: device_description = "A two-button controller with status LED. MCU reads buttons and drives LED."
Placed UI components: btn_1 (tactile_button_6x6), btn_2 (tactile_button_6x6), led_status (led_5mm)
```json
{
    "components": [
        {"catalog_id": "battery_holder_2xAAA", "instance_id": "bat_1"},
        {"catalog_id": "atmega328p_dip28", "instance_id": "mcu_1"},
        {"catalog_id": "capacitor_100nf", "instance_id": "c_bypass"},
        {"catalog_id": "tactile_button_6x6", "instance_id": "btn_1"},
        {"catalog_id": "tactile_button_6x6", "instance_id": "btn_2"},
        {"catalog_id": "led_5mm", "instance_id": "led_status", "config": {"wavelength_nm": 525, "forward_voltage_v": 2.2}},
        {"catalog_id": "resistor_axial", "instance_id": "r_led", "config": {"resistance_ohms": 68}}
    ],
    "nets": [
        {"id": "VCC", "pins": ["bat_1:V+", "mcu_1:power", "c_bypass:1"]},
        {"id": "GND", "pins": ["bat_1:GND", "mcu_1:ground", "c_bypass:2", "btn_1:B", "btn_2:B", "led_status:cathode"]},
        {"id": "BTN1", "pins": ["btn_1:A", "mcu_1:gpio"]},
        {"id": "BTN2", "pins": ["btn_2:A", "mcu_1:gpio"]},
        {"id": "LED_CTRL", "pins": ["mcu_1:gpio", "r_led:1"]},
        {"id": "LED_DRIVE", "pins": ["r_led:2", "led_status:anode"]}
    ]
}
```
```

##### Full target `build_circuit_user_prompt()` body

This function reads `design_data` (the submitted design JSON) and generates the
circuit agent's initial user message. Use this exact format and logic:

```text
Design the circuit for this device.

**Device Description:**
<device_description from design_data>

**Placed UI Components (use these exact instance_ids):**
- <instance_id> (<catalog_id>) — <face> face
- <instance_id> (<catalog_id>) — <face> face

Include these UI components in your circuit. Add all needed internal components (batteries, resistors, MCU, capacitors, etc.) and design the electrical connections.
```

The implementation reads from `design_data["ui_placements"]` (main branch key) rather
than `surface_placements` (3D branch key). Each line includes the face/edge context so
the circuit agent knows where the component sits. The placed UI component instance IDs
must be repeated exactly, because the circuit agent must preserve them.

---

### 4. `src/agent/messages.py` — Rename private functions to public

**Current state:** `_serialize_content`, `_sanitize_messages`, `_prune_messages`
(underscore-prefixed private functions).

**Target state:** `serialize_content`, `sanitize_messages`, `prune_messages` (public).
No logic changes. Both agents import from the same module.

The `prune_messages` function already handles both `submit_design` and `submit_circuit`
tool names — just ensure the keep-verbatim list includes both:

```python
_KEEP_VERBATIM = {"submit_design", "submit_circuit"}
```

---

### 5. `src/agent/config.py` — No changes

```python
MODEL = "claude-opus-4-6"
MAX_TOKENS = 16384
THINKING_BUDGET = 6000
MAX_TURNS = 25
TOKEN_BUDGET = 50000
```

Both agents share these constants.

---

### 6. `src/web/server.py` — Add circuit endpoints

**Current design endpoint** (`POST /api/session/design`) stays the same but now
drives the `DesignAgent` which yields `"design"` events (no `"circuit"` events).

**New endpoints to add:**

#### `POST /api/session/circuit` — Run the circuit agent (SSE)

```python
@app.post("/api/session/circuit")
async def run_circuit(request: Request):
    """Run the autonomous circuit agent. No user prompt — reads design.json."""
    session = _get_session(request)
    design = session.read_artifact("design.json")
    if not design:
        raise HTTPException(400, "No design.json — run design agent first")

    catalog = load_catalog()
    agent = CircuitAgent(catalog, session)
    user_prompt = build_circuit_user_prompt(design)

    async def stream():
        async for event in agent.run(user_prompt):
            yield f"event: {event.type}\ndata: {json.dumps(event.data)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
```

#### `GET /api/session/circuit/conversation`

```python
@app.get("/api/session/circuit/conversation")
async def get_circuit_conversation(request: Request):
    session = _get_session(request)
    conv = session.read_artifact("circuit_conversation.json")
    return conv or []
```

#### `GET /api/session/circuit/result`

```python
@app.get("/api/session/circuit/result")
async def get_circuit_result(request: Request):
    session = _get_session(request)
    circuit = session.read_artifact("circuit.json")
    if not circuit:
        raise HTTPException(404, "No circuit result yet")
    return circuit
```

**Existing endpoint changes:**

- `POST /api/session/design` — Change to instantiate the new `DesignAgent` (which
  no longer produces `circuit.json`). On success, also call `enrich_design_components`
  to add catalog metadata to `design.json` for the viewport, then generate the
  session name if this is the first design.
- `GET /api/session` — Include `"circuit"` in `pipeline_state` (complete if
  `circuit.json` exists).
- Session hydration in `main.js` — Enable circuit tab when design is complete.

**SSE event types** are identical for both agents:

| Event | Data | Notes |
|-------|------|-------|
| `thinking_start` | `{}` | New thinking block |
| `thinking_delta` | `{text}` | Incremental thinking |
| `message_start` | `{}` | New text block |
| `message_delta` | `{text}` | Incremental text |
| `block_stop` | `{}` | Block finished |
| `tool_call` | `{name, input}` | Tool invoked |
| `tool_result` | `{name, content, is_error}` | Tool result |
| `design` | `{design: {...}}` | Design agent success |
| `circuit` | `{circuit: {...}}` | Circuit agent success |
| `token_usage` | `{input_tokens, budget}` | Token meter |
| `error` | `{message}` | Error |
| `done` | `{}` | Agent finished |

---

### 7. `src/web/static/index.html` — Add circuit panel

Add a new step button and panel section:

```html
<nav id="pipeline-nav">
  <button class="step active" data-step="design">1. Design</button>
  <button class="step" data-step="circuit" disabled>2. Circuit</button>  <!-- NEW -->
  <button class="step" data-step="placement" disabled>3. Placement</button>
  <button class="step" data-step="routing" disabled>4. Routing</button>
  <button class="step" data-step="bitmap" disabled>5. Bitmap</button>
  <button class="step" data-step="scad" disabled>6. SCAD</button>
  <button class="step" data-step="manufacturing" disabled>7. Manufacturing</button>
</nav>
```

Circuit panel (between design and placement):

```html
<section id="step-circuit" class="step-panel" hidden>
  <div id="circuit-hero" class="placement-hero">
    <p>The circuit agent automatically selects internal components and
       designs electrical connections based on your device design.</p>
    <button id="btn-run-circuit" class="cta-button">Run Circuit Agent</button>
    <span id="circuit-status"></span>
  </div>
  <div id="circuit-scroll" class="placement-scroll" hidden>
    <div id="circuit-log" class="chat-messages"></div>
    <div id="circuit-info" class="placement-info"></div>
  </div>
</section>
```

Key differences from the design panel:
- **No chat input** — the circuit agent is autonomous
- **Hero section** with a single "Run" button
- **Read-only log** showing the agent's thinking, messages, and tool calls
- **Result summary** after completion (component count, net count)

---

### 8. `src/web/static/js/circuit.js` — New file

Create the circuit agent frontend module. Structure mirrors `design.js` but without
user input.

```javascript
// circuit.js — Circuit agent UI (autonomous, read-only log)

import { state, API } from './state.js';
import { enablePlacementTab, resetPlacementPanel } from './placement.js';
import { resetRoutingPanel } from './routing.js';

// ── Public API ──────────────────────────────────────────────────

export function enableCircuitTab(flash = true) {
    const btn = document.querySelector('[data-step="circuit"]');
    btn.disabled = false;
    if (flash) btn.classList.add('flash');
}

export function resetCircuitPanel() {
    document.getElementById('circuit-scroll').hidden = true;
    document.getElementById('circuit-log').innerHTML = '';
    document.getElementById('circuit-info').innerHTML = '';
    document.getElementById('circuit-hero').hidden = false;
    document.getElementById('circuit-status').textContent = '';
}

export async function loadCircuitConversation() {
    const res = await fetch(`${API}/session/circuit/conversation?session=${state.session}`);
    if (!res.ok) return;
    const messages = await res.json();
    if (messages.length) renderConversation(messages);
}

export async function loadCircuitResult() {
    const res = await fetch(`${API}/session/circuit/result?session=${state.session}`);
    if (!res.ok) return;
    const circuit = await res.json();
    showCircuitSummary(circuit);
}

// ── Run circuit agent ───────────────────────────────────────────

export async function runCircuit() {
    const log = document.getElementById('circuit-log');
    const scroll = document.getElementById('circuit-scroll');
    const hero = document.getElementById('circuit-hero');
    const status = document.getElementById('circuit-status');

    log.innerHTML = '';
    scroll.hidden = false;
    hero.hidden = true;
    status.textContent = 'Running...';

    const res = await fetch(`${API}/session/circuit?session=${state.session}`, {
        method: 'POST',
    });

    if (!res.ok) {
        status.textContent = 'Error';
        return;
    }

    await consumeSSE(res, log);
    status.textContent = '';
}

// ── SSE consumer ────────────────────────────────────────────────

async function consumeSSE(response, container) {
    // Identical SSE parsing pattern as design.js:
    // Read lines from response.body, parse "event:" and "data:" lines,
    // dispatch to handlers for thinking, message, tool_call, tool_result,
    // circuit, error, done events.
    //
    // On "circuit" event → call showCircuitSummary(data.circuit),
    //   enable placement tab, reset downstream panels.
    // On "done" → finalize.
}

// ── Rendering helpers ───────────────────────────────────────────

function renderConversation(messages) { /* render saved history into log */ }
function showCircuitSummary(circuit) {
    // Show: "✅ Circuit Validated — N components (M UI) · K nets"
    // with a collapsible <details> showing the full circuit JSON
}
function createThinkingBubble() { /* same pattern as design.js */ }
function createMessageBubble() { /* same pattern as design.js */ }
function createToolGroup() { /* same pattern as design.js */ }
function renderMarkdown(text) { /* same pattern as design.js */ }
```

**Note:** The SSE consumer, thinking bubbles, message bubbles, tool groups, and
markdown renderer are largely duplicated from `design.js`. A future cleanup could
extract these into a shared `sse-ui.js` module, but this is not required for the
initial refactor.

---

### 9. `src/web/static/js/design.js` — Adjust for design-only events

**Current state:** Handles `"design"` SSE event which contains the full design
(components + nets + outline + enclosure + ui_placements).

**Changes:**

- On `"design"` event: enable **circuit tab** (not placement), since placement now
  depends on circuit completion.
- Remove any circuit-related result display from the design panel.
- The design data passed to the viewport no longer contains `components` or `nets` —
  only outline, enclosure, ui_placements, and enriched component metadata for preview.

```javascript
// In SSE handler:
case 'design':
    setViewportData('design', data.design);
    enableCircuitTab(true);       // was: enablePlacementTab
    break;
```

---

### 10. `src/web/static/js/main.js` — Wire circuit tab

Add imports and event listeners for the circuit module:

```javascript
import { runCircuit, loadCircuitConversation, loadCircuitResult, enableCircuitTab } from './circuit.js';

// In DOMContentLoaded:
document.getElementById('btn-run-circuit').addEventListener('click', runCircuit);

// In session hydration (when loading existing session):
if (pipelineState.design === 'complete') {
    enableCircuitTab(false);
    loadCircuitConversation();
}
if (pipelineState.circuit === 'complete') {
    enablePlacementTab(false);
    loadCircuitResult();
}
```

---

### 11. `src/session.py` — New artifact files

No structural changes needed. The session already supports arbitrary artifact files.
The new files:

| Artifact | Written by | Read by |
|----------|-----------|---------|
| `design_conversation.json` | DesignAgent | Server (GET conversation) |
| `circuit_conversation.json` | CircuitAgent | Server (GET circuit/conversation) |
| `design.json` | DesignAgent | CircuitAgent, Server, Frontend |
| `circuit.json` | CircuitAgent | Placer, Router, Frontend |

**Rename:** The current `conversation.json` becomes `design_conversation.json`.
Update all references (server endpoint, session hydration, `_save_conversation`).

---

### 12. Pipeline integration: `design.json` → `circuit.json`

**Current `design.json`** output (single agent):
```json
{
  "components": [...],
  "nets": [...],
  "outline": [...],
  "enclosure": {...},
  "ui_placements": [...]
}
```

**New `design.json`** output (design agent only):
```json
{
  "device_description": "A flashlight with one button and one LED...",
  "outline": [...],
  "enclosure": {...},
  "ui_placements": [
    {"instance_id": "btn_1", "catalog_id": "tactile_button_6x6", "x_mm": 15, "y_mm": 25},
    {"instance_id": "led_1", "catalog_id": "led_5mm", "x_mm": 15, "y_mm": 65}
  ]
}
```

The design agent still references catalog components for UI placements (to know
body dimensions, mounting requirements), but does not produce the full component
list or nets.

**New `circuit.json`** output (circuit agent):
```json
{
  "components": [
    {"catalog_id": "battery_holder_2xAAA", "instance_id": "bat_1", "ui_placement": false},
    {"catalog_id": "resistor_axial", "instance_id": "r_1", "config": {"resistance_ohms": 150}, "ui_placement": false},
    {"catalog_id": "led_5mm", "instance_id": "led_1", "mounting_style": "top", "ui_placement": true},
    {"catalog_id": "tactile_button_6x6", "instance_id": "btn_1", "ui_placement": true}
  ],
  "nets": [
    {"id": "POWER", "pins": ["bat_1:V+", "r_1:1"]},
    {"id": "LED_DRIVE", "pins": ["r_1:2", "led_1:anode"]},
    {"id": "BTN_IN", "pins": ["btn_1:A", "bat_1:GND"]},
    {"id": "BTN_OUT", "pins": ["btn_1:B", "led_1:cathode"]}
  ]
}
```

The circuit agent's `submit_circuit` handler enriches each component with
`"ui_placement": true/false` by cross-referencing `design.json`'s `ui_placements`.

**Downstream consumers** (placer, router) currently read components + nets from
`design.json`. They must be updated to read from `circuit.json` instead. The outline
and enclosure still come from `design.json`.

---

### 13. Invalidation Chain

```
Design changes  →  invalidate circuit.json, placement.json, routing.json, ...
Circuit changes →  invalidate placement.json, routing.json, ...
```

Implementation in submit handlers:

```python
# In DesignAgent._tool_submit_design (on success):
for artifact in ("circuit.json", "circuit_conversation.json",
                 "placement.json", "routing.json", "trace_bitmap.txt",
                 "enclosure.scad", "enclosure.stl"):
    self.session.delete_artifact(artifact)

# In CircuitAgent._tool_submit_circuit (on success):
for artifact in ("placement.json", "routing.json", "trace_bitmap.txt",
                 "enclosure.scad", "enclosure.stl"):
    self.session.delete_artifact(artifact)
```

---

### 14. `build_circuit_user_prompt` — The data bridge

The circuit agent never receives a user-typed message. Instead, the server reads
`design.json` and generates a structured prompt:

```python
def build_circuit_user_prompt(design_data: dict) -> str:
    desc = design_data.get("device_description", "")
    placements = design_data.get("ui_placements", [])

    parts = [
        f"Device description: {desc}",
        "",
        "UI components already placed on the device:",
    ]
    for p in placements:
        cid = p.get("catalog_id", p.get("instance_id", "?"))
        iid = p.get("instance_id", "?")
        parts.append(f"  - {iid} (catalog: {cid})")

    parts.append("")
    parts.append(
        "Select ALL components needed for this device — including the UI "
        "components listed above and any internal components (battery, MCU, "
        "resistors, capacitors, etc.). Then design the electrical nets "
        "connecting them. Call submit_circuit when ready."
    )
    return "\n".join(parts)
```

This ensures the circuit agent knows:
1. What the device does (device_description)
2. Which UI components are already placed (must include them in its component list)
3. That it needs to add all internal components and design all nets

---

## Refactoring Order

Execute the refactor in this order to keep the app working at each step:

1. **Rename message helpers** — Remove underscore prefix from `_serialize_content`,
   `_sanitize_messages`, `_prune_messages` and update imports.

2. **Rename prompt/catalog helpers** — Remove underscore prefix from
   `_build_system_prompt` → `build_design_prompt` and `_catalog_summary` →
   `catalog_summary`. Update imports.

3. **Split tools.py** — Factor `TOOLS` into `DESIGN_TOOLS` + `CIRCUIT_TOOLS`.
   `DESIGN_TOOLS` gets submit_design (minus components/nets) + check_feasibility.
   `CIRCUIT_TOOLS` gets submit_circuit (components + nets).

4. **Split prompt.py** — Extract circuit-specific content from `build_design_prompt`
   into `build_circuit_prompt`. Add `build_circuit_user_prompt`.

5. **Split core.py** — Extract `_BaseAgent`, refactor `DesignAgent` to inherit,
   create `CircuitAgent`. Update conversation file names.

6. **Add server endpoints** — `POST /circuit`, `GET /circuit/conversation`,
   `GET /circuit/result`. Update pipeline_state to include circuit step.

7. **Add circuit.js** — New frontend module for the autonomous circuit tab.

8. **Update index.html** — Add circuit step button and panel section.

9. **Update main.js** — Wire circuit tab imports, event listeners, session hydration.

10. **Update design.js** — Change `design` SSE event to enable circuit tab instead
    of placement tab.

11. **Update downstream pipeline** — Placer and router read components/nets from
    `circuit.json` instead of `design.json`. Outline/enclosure still from `design.json`.

12. **Test end-to-end** — Design → Circuit → Placement → Routing → SCAD flow.

---

## What NOT to Change

- **2D outline system** — No CSG/3D changes. The outline, enclosure, easing, z_top,
  top_surface, and edge profiles all stay as-is.
- **Viewport** — `viewportDesign.js` continues rendering the 2D outline + placed
  components. No 3D viewport needed.
- **Pipeline steps 3–7** — Placement, routing, bitmap, SCAD, G-code are unchanged
  except for reading `circuit.json` instead of `design.json` for components/nets.
- **Catalog system** — No changes to catalog loading, models, or serialization.
- **Config constants** — Both agents share the same MODEL, MAX_TOKENS, etc.
- **Session structure** — File-based, timestamp IDs, same `Session` class.

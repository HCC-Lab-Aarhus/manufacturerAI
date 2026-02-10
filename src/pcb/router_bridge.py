"""
TypeScript PCB Router Bridge — calls the TS CLI and returns routing results.
"""

from __future__ import annotations
import json
import subprocess
from pathlib import Path

from src.config.hardware import hw
from src.geometry.polygon import polygon_bounds


_PCB_DIR = Path(__file__).resolve().parents[2] / "pcb"


class RouterError(Exception):
    pass


def _find_or_build_cli() -> Path:
    cli = _PCB_DIR / "dist" / "cli.js"
    if not cli.exists():
        subprocess.run(["npm", "install"], cwd=_PCB_DIR, capture_output=True, check=True, shell=True)
        subprocess.run(["npm", "run", "build"], cwd=_PCB_DIR, capture_output=True, check=True, shell=True)
    if not cli.exists():
        raise RouterError("TS router CLI not found — run npm install && npm run build in pcb/")
    return cli


def route_traces(
    pcb_layout: dict,
    output_dir: Path,
    *,
    max_attempts: int | None = None,
) -> dict:
    """
    Route traces via the TypeScript A* router CLI.

    Parameters
    ----------
    max_attempts : int, optional
        When set, limits the total rip-up/reroute attempts in the TS
        router.  Use a low value (e.g. 8) for fast screening of multiple
        placement candidates, and *None* (default → 25) for thorough routing.

    Returns dict with 'success', 'traces', 'failed_nets'.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    router_input = _convert_layout(pcb_layout)
    if max_attempts is not None:
        router_input["maxAttempts"] = max_attempts

    # save debug copy
    (output_dir / "ts_router_input.json").write_text(
        json.dumps(router_input, indent=2), encoding="utf-8"
    )

    cli = _find_or_build_cli()
    try:
        result = subprocess.run(
            ["node", str(cli), "--output", str(output_dir / "pcb")],
            cwd=_PCB_DIR,
            input=json.dumps(router_input),
            capture_output=True,
            text=True,
            check=False,
            shell=True,
        )
    except FileNotFoundError:
        raise RouterError("Node.js not found.")

    (output_dir / "ts_router_stdout.txt").write_text(result.stdout or "", encoding="utf-8")
    (output_dir / "ts_router_stderr.txt").write_text(result.stderr or "", encoding="utf-8")

    if not result.stdout.strip():
        raise RouterError(f"Router produced no output. stderr: {result.stderr}")

    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RouterError(f"Failed to parse router output: {e}\n{result.stdout[:500]}")

    (output_dir / "ts_router_result.json").write_text(
        json.dumps(parsed, indent=2), encoding="utf-8"
    )

    return {
        "success": parsed.get("success", False),
        "traces": parsed.get("traces", []),
        "failed_nets": parsed.get("failedNets", []),
    }


def _convert_layout(pcb_layout: dict) -> dict:
    """Convert pcb_layout to the TS router input format.

    Normalizes all coordinates so the outline starts at the origin
    (min_x=0, min_y=0) to match the grid's coordinate space.
    """
    outline = pcb_layout["board"]["outline_polygon"]
    min_x, min_y, max_x, max_y = polygon_bounds(outline)
    board_width = max_x - min_x
    board_height = max_y - min_y

    # Shift outline to origin so grid coords match
    board_outline = [[p[0] - min_x, p[1] - min_y] for p in outline]

    components = pcb_layout.get("components", [])
    button_comps = [c for c in components if c.get("type") == "button"]
    diode_comps = [c for c in components if c.get("type") == "diode"]

    buttons, controllers, batteries, diodes = [], [], [], []

    for comp in components:
        ctype = comp.get("type")
        x, y = comp["center"][0] - min_x, comp["center"][1] - min_y
        cid = comp["id"]

        if ctype == "button":
            buttons.append({"id": cid, "x": x, "y": y, "signalNet": f"{cid}_SIG"})
        elif ctype == "controller":
            pins = _controller_pins(button_comps, diode_comps)
            ctrl_entry: dict = {"id": cid, "x": x, "y": y, "pins": pins}
            rot = comp.get("rotation_deg", 0)
            if rot:
                ctrl_entry["rotation"] = rot
            controllers.append(ctrl_entry)
        elif ctype == "battery":
            batteries.append({
                "id": cid, "x": x, "y": y,
                "bodyWidth": comp.get("body_width_mm", 0),
                "bodyHeight": comp.get("body_height_mm", 0),
            })
        elif ctype == "diode":
            diodes.append({"id": cid, "x": x, "y": y, "signalNet": f"{cid}_SIG"})

    board_params: dict = {
        "boardWidth": board_width,
        "boardHeight": board_height,
        "gridResolution": hw.grid_resolution,
        "boardOutline": board_outline,
        "edgeClearance": hw.edge_clearance,
    }

    return {
        "board": board_params,
        "manufacturing": hw.router_manufacturing(),
        "footprints": hw.router_footprints(),
        "placement": {
            "buttons": buttons,
            "controllers": controllers,
            "batteries": batteries,
            "diodes": diodes,
        },
    }


def build_pin_mapping(
    pcb_layout: dict,
    button_positions: list[dict],
) -> list[dict]:
    """
    Build a human-readable mapping: button label → controller pin.

    This tells the user which physical MCU pin each button is wired to,
    so they can program the firmware accordingly.
    """
    # Gather button IDs in placement order
    button_comps = [
        c for c in pcb_layout.get("components", [])
        if c.get("type") == "button"
    ]
    diode_comps = [
        c for c in pcb_layout.get("components", [])
        if c.get("type") == "diode"
    ]

    pins = _controller_pins(button_comps, diode_comps)

    # Invert: signal net → controller pin name
    net_to_pin: dict[str, str] = {}
    for pin_name, net in pins.items():
        if net.endswith("_SIG") and net != "NC":
            net_to_pin[net] = pin_name

    # Build label → pin list
    label_lookup = {b["id"]: b.get("label", b["id"]) for b in button_positions}
    mapping = []
    for comp in button_comps:
        cid = comp["id"]
        sig_net = f"{cid}_SIG"
        mapping.append({
            "button_id": cid,
            "label": label_lookup.get(cid, cid),
            "signal_net": sig_net,
            "controller_pin": net_to_pin.get(sig_net, "unrouted"),
        })

    for comp in diode_comps:
        cid = comp["id"]
        sig_net = f"{cid}_SIG"
        mapping.append({
            "component_id": cid,
            "type": "IR diode",
            "signal_net": sig_net,
            "controller_pin": net_to_pin.get(sig_net, "unrouted"),
        })

    return mapping


def _controller_pins(
    button_comps: list[dict],
    diode_comps: list[dict],
) -> dict[str, str]:
    button_count = len(button_comps)
    diode_count = len(diode_comps)
    pins = hw.pin_assignments(button_count, diode_count)
    cp = hw.controller_pins
    # rename generic SWn / LEDn nets to actual component IDs
    btn_idx = 0
    diode_idx = 0
    for pin in cp["digital_order"]:
        net = pins.get(pin, "")
        if net.startswith("SW") and net.endswith("_SIG"):
            if btn_idx < len(button_comps):
                pins[pin] = f"{button_comps[btn_idx]['id']}_SIG"
                btn_idx += 1
        elif net.startswith("LED") and net.endswith("_SIG"):
            if diode_idx < len(diode_comps):
                pins[pin] = f"{diode_comps[diode_idx]['id']}_SIG"
                diode_idx += 1
    return pins

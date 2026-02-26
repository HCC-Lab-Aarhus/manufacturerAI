"""
TypeScript PCB Router Bridge — calls the TS CLI and returns routing results.
"""

from __future__ import annotations
import json
import logging
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from src.config.hardware import hw
from src.geometry.polygon import polygon_bounds

log = logging.getLogger("manufacturerAI.router_bridge")

_PCB_DIR = Path(__file__).resolve().parents[2] / "pcb"


# ── ATmega328P DIP-28 physical pin layout ──────────────────────────
#
# Left side:  pin 1 (bottom) → pin 14 (top)
# Right side: pin 15 (top, opposite pin 14) → pin 28 (bottom)
#
# The dict key iteration order sent to the TS router determines which
# index → physical-position mapping the router uses.  This list
# matches the real ATmega328P-PU DIP-28 pinout so that simulated
# pin positions correspond to reality.

_DIP28_PIN_ORDER: list[str] = [
    # Left side  (pins 1-14, bottom → top)
    "PC6", "PD0", "PD1", "PD2", "PD3", "PD4", "VCC", "GND1",
    "PB6", "PB7", "PD5", "PD6", "PD7", "PB0",
    # Right side (pins 15-28, top → bottom)
    "PB1", "PB2", "PB3", "PB4", "PB5", "AVCC", "AREF", "GND2",
    "PC0", "PC1", "PC2", "PC3", "PC4", "PC5",
]


def _pin_world_positions(
    cx: float, cy: float, rotation: int = 0,
) -> dict[str, tuple[float, float]]:
    """Compute world (x, y) for every DIP-28 pin.

    Mirrors the TS router's ``placeController`` geometry exactly so the
    Python-side proximity calculation matches the actual pad positions.
    """
    fp = hw.router_footprints()["controller"]
    pin_sp: float = fp["pinSpacing"]    # 2.54 mm
    row_sp: float = fp["rowSpacing"]    # 7.62 mm
    n = len(_DIP28_PIN_ORDER)           # 28
    half = n // 2                       # 14
    span = (half - 1) * pin_sp          # 33.02 mm

    pos: dict[str, tuple[float, float]] = {}
    for i, name in enumerate(_DIP28_PIN_ORDER):
        pn = i + 1  # 1-based physical pin number
        if rotation == 90:
            if pn <= half:
                px = cx - span / 2 + (pn - 1) * pin_sp
                py = cy - row_sp / 2
            else:
                ri = n - pn
                px = cx - span / 2 + ri * pin_sp
                py = cy + row_sp / 2
        else:
            if pn <= half:
                px = cx - row_sp / 2
                py = cy - span / 2 + (pn - 1) * pin_sp
            else:
                ri = n - pn
                px = cx + row_sp / 2
                py = cy - span / 2 + ri * pin_sp
        pos[name] = (px, py)
    return pos


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


def _kill_proc_tree(pid: int) -> None:
    """Kill a process and all its children.

    Needed on Windows because ``shell=True`` starts ``cmd.exe`` and
    ``proc.kill()`` only kills cmd — the child node.exe keeps running.
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True,
            shell=True,
        )
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def route_traces(
    pcb_layout: dict,
    output_dir: Path,
    *,
    max_attempts: int | None = None,
    cancel: threading.Event | None = None,
) -> dict:
    """
    Route traces via the TypeScript A* router CLI.

    Parameters
    ----------
    max_attempts : int, optional
        When set, limits the total rip-up/reroute attempts in the TS
        router.  Use a low value (e.g. 8) for fast screening of multiple
        placement candidates, and *None* (default → 25) for thorough routing.
    cancel : threading.Event, optional
        If set, the subprocess is killed and a ``RouterError`` is raised.

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
        proc = subprocess.Popen(
            ["node", str(cli), "--output", str(output_dir / "pcb")],
            cwd=_PCB_DIR,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=True,
        )
        # Feed input and close stdin
        proc.stdin.write(json.dumps(router_input))
        proc.stdin.close()

        # Read pipes in background threads to prevent deadlock.
        # (Node outputs 50-70 KB JSON; Windows pipe buffer is ~4 KB.
        # Without draining, node blocks and the poll loop waits forever.)
        stdout_buf: list[str] = []
        stderr_buf: list[str] = []

        def _drain_stdout():
            stdout_buf.append(proc.stdout.read())

        def _drain_stderr():
            stderr_buf.append(proc.stderr.read())

        t_out = threading.Thread(target=_drain_stdout, daemon=True)
        t_err = threading.Thread(target=_drain_stderr, daemon=True)
        t_out.start()
        t_err.start()

        # Poll with cancel support (check every 0.25 s)
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                _kill_proc_tree(proc.pid)
                proc.wait(timeout=5)
                t_out.join(timeout=2)
                t_err.join(timeout=2)
                raise RouterError("Routing cancelled.")
            time.sleep(0.25)

        t_out.join(timeout=10)
        t_err.join(timeout=10)

        stdout = stdout_buf[0] if stdout_buf else ""
        stderr = stderr_buf[0] if stderr_buf else ""
        proc.stdout.close()
        proc.stderr.close()
    except FileNotFoundError:
        raise RouterError("Node.js not found.")

    (output_dir / "ts_router_stdout.txt").write_text(stdout or "", encoding="utf-8")
    (output_dir / "ts_router_stderr.txt").write_text(stderr or "", encoding="utf-8")

    if not (stdout or "").strip():
        raise RouterError(f"Router produced no output. stderr: {stderr}")

    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RouterError(f"Failed to parse router output: {e}\n{(stdout or '')[:500]}")

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
            pins = _controller_pins(
                button_comps, diode_comps,
                ctrl_x=x, ctrl_y=y,
                ctrl_rotation=comp.get("rotation_deg", 0),
                comp_offset=(min_x, min_y),
            )
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
    Build a human-readable mapping: button label/function → controller pin.

    This tells the user which physical MCU pin each button is wired to,
    and what IR function each button performs, so they can verify the
    firmware configuration.
    """
    components = pcb_layout.get("components", [])
    button_comps = [c for c in components if c.get("type") == "button"]
    diode_comps  = [c for c in components if c.get("type") == "diode"]
    ctrl_comps   = [c for c in components if c.get("type") == "controller"]

    outline = pcb_layout["board"]["outline_polygon"]
    min_x, min_y, _, _ = polygon_bounds(outline)

    if ctrl_comps:
        ctrl = ctrl_comps[0]
        cx = ctrl["center"][0] - min_x
        cy = ctrl["center"][1] - min_y
        rot = ctrl.get("rotation_deg", 0)
    else:
        cx, cy, rot = 0.0, 0.0, 0

    pins = _controller_pins(
        button_comps, diode_comps,
        ctrl_x=cx, ctrl_y=cy,
        ctrl_rotation=rot,
        comp_offset=(min_x, min_y),
    )

    # Invert: signal net → controller pin name
    net_to_pin: dict[str, str] = {}
    for pin_name, net in pins.items():
        if net.endswith("_SIG") and net != "NC":
            net_to_pin[net] = pin_name

    # Build lookups from button_positions
    label_lookup = {b["id"]: b.get("label", b["id"]) for b in button_positions}
    function_lookup = {b["id"]: b.get("function", "") for b in button_positions}
    
    mapping = []
    for comp in button_comps:
        cid = comp["id"]
        sig_net = f"{cid}_SIG"
        mapping.append({
            "button_id": cid,
            "label": label_lookup.get(cid, cid),
            "function": function_lookup.get(cid, ""),
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
    *,
    ctrl_x: float = 0.0,
    ctrl_y: float = 0.0,
    ctrl_rotation: int = 0,
    comp_offset: tuple[float, float] = (0.0, 0.0),
) -> dict[str, str]:
    """Build controller pin → net mapping using proximity-based assignment.

    Instead of blindly assigning PD0, PD1, PD2 … in sequence, this
    computes the physical (x, y) of every available GPIO pin and assigns
    each button / diode signal to the *nearest* free pin **that has the
    required capability**.

    Pin capability matching
    -----------------------
    Each component type declares what kind of pin it needs:

    * ``button``  → ``"digital"`` — any GPIO pin works.
    * ``diode``   → ``"pwm"``     — IR LEDs need hardware PWM for the
      38 kHz carrier signal (Timer OC pins).
    * (future) analog sensors → ``"analog"`` (ADC-capable PC0-PC5).
    * (future) I2C peripherals → ``"i2c"`` (PC4/SDA, PC5/SCL).

    Constrained components (pwm, analog, …) are assigned **first** so
    that unconstrained buttons don't accidentally consume scarce pins.
    If no matching pin is available the component falls back to any
    remaining GPIO.

    Parameters
    ----------
    ctrl_x, ctrl_y : float
        Controller centre, already in the origin-shifted coordinate
        system (board bottom-left = 0, 0).
    ctrl_rotation : int
        0 or 90.
    comp_offset : tuple[float, float]
        ``(min_x, min_y)`` to subtract from raw component ``center``
        values to bring them into the same coordinate space.
    """
    cp = hw.controller_pins
    power  = dict(cp["power"])       # VCC→VCC, GND1→GND, …
    unused = set(cp["unused"])       # {PC6, PB6, PB7}
    gpio   = [p for p in cp["digital_order"]
              if p not in power and p not in unused]

    # ── Pin capability sets (from config) ───────────────────────────
    capabilities: dict[str, set[str]] = {}
    for cap_name, pin_list in cp.get("pin_capabilities", {}).items():
        if cap_name.startswith("$"):
            continue  # skip JSON comments
        capabilities[cap_name] = set(pin_list)

    # Component type → required pin capability.
    # "digital" is implicit and means "any GPIO".
    _COMPONENT_PIN_REQ: dict[str, str] = {
        "button": "digital",
        "diode":  "pwm",      # IR LED needs hardware PWM for 38 kHz carrier
        # Future: "sensor": "analog", "i2c_device": "i2c", ...
    }

    # Start every pin at NC; overwrite power/unused/signal below.
    pin_net: dict[str, str] = {p: "NC" for p in _DIP28_PIN_ORDER}
    for p in _DIP28_PIN_ORDER:
        if p in power:
            pin_net[p] = power[p]

    # Physical pin positions on the board
    pin_pos = _pin_world_positions(ctrl_x, ctrl_y, ctrl_rotation)

    # Build targets: (net_name, x, y, required_capability)
    ox, oy = comp_offset
    targets: list[tuple[str, float, float, str]] = []
    for c in button_comps:
        req = _COMPONENT_PIN_REQ.get("button", "digital")
        targets.append((f"{c['id']}_SIG", c["center"][0] - ox, c["center"][1] - oy, req))
    for c in diode_comps:
        req = _COMPONENT_PIN_REQ.get("diode", "digital")
        targets.append((f"{c['id']}_SIG", c["center"][0] - ox, c["center"][1] - oy, req))

    # ── Sort: constrained targets first ─────────────────────────────
    # Assign components that require scarce pins (pwm, analog, i2c…)
    # before unconstrained digital-only components, so buttons don't
    # accidentally consume the limited PWM / ADC pins.
    def _constraint_priority(t: tuple[str, float, float, str]) -> int:
        req = t[3]
        if req == "digital":
            return 1   # assign last — any GPIO will do
        return 0       # assign first — limited pool

    targets.sort(key=_constraint_priority)

    # ── Greedy nearest-pin with capability filtering ────────────────
    free = set(gpio)
    for net_name, tx, ty, req in targets:
        if not free:
            break

        # Build candidate pool: only pins matching the required capability.
        if req != "digital" and req in capabilities:
            candidates = free & capabilities[req]
        else:
            candidates = free  # "digital" → any GPIO

        # Fallback: if no matching pin is left, allow any free GPIO
        # (better to route sub-optimally than to leave unrouted).
        if not candidates:
            log.warning(
                "No free %s-capable pin for %s — falling back to any GPIO",
                req, net_name,
            )
            candidates = free

        best = min(
            candidates,
            key=lambda p: (pin_pos[p][0] - tx) ** 2 + (pin_pos[p][1] - ty) ** 2,
        )
        pin_net[best] = net_name
        free.discard(best)
        log.debug("Pin %s → %s  (cap=%s, dist %.1f mm)",
                  best, net_name, req,
                  ((pin_pos[best][0] - tx) ** 2 + (pin_pos[best][1] - ty) ** 2) ** 0.5)

    # Return in DIP-28 physical order so the TS router places pins correctly.
    return {p: pin_net[p] for p in _DIP28_PIN_ORDER}

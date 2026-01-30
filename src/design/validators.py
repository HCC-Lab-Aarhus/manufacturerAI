from __future__ import annotations
import json
from pathlib import Path
from src.design.layout import compute_grid_metrics, choose_rows_cols

def load_printer_limits() -> dict:
    p = Path(__file__).resolve().parents[3] / "configs" / "printer_limits.json"
    return json.loads(p.read_text(encoding="utf-8"))

def validate_and_fix_params(params: dict) -> tuple[dict, list[str]]:
    issues: list[str] = []
    limits = load_printer_limits()

    params = dict(params)
    params.setdefault("remote", {})
    params.setdefault("buttons", {})

    remote = params["remote"]
    buttons = params["buttons"]

    # Defaults
    remote.setdefault("length_mm", 180)
    remote.setdefault("width_mm", 45)
    remote.setdefault("thickness_mm", 18)
    remote.setdefault("wall_mm", 1.6)
    remote.setdefault("corner_radius_mm", 6)

    # Allow button_count as a higher-level input
    if "button_count" in buttons and ("rows" not in buttons or "cols" not in buttons):
        r, c = choose_rows_cols(int(buttons["button_count"]))
        buttons["rows"], buttons["cols"] = r, c
        issues.append(f"Derived button grid rows×cols = {r}×{c} from button_count={buttons['button_count']}.")

    buttons.setdefault("rows", 4)
    buttons.setdefault("cols", 3)
    buttons.setdefault("diam_mm", 9)
    buttons.setdefault("spacing_mm", 3)
    buttons.setdefault("margin_top_mm", 20)
    buttons.setdefault("margin_bottom_mm", 18)
    buttons.setdefault("margin_side_mm", 6)
    buttons.setdefault("hole_clearance_mm", 0.25)

    # Clamp to printer limits
    if remote["wall_mm"] < limits["min_wall_mm"]:
        issues.append(f"wall_mm increased from {remote['wall_mm']} to {limits['min_wall_mm']} (min wall).")
        remote["wall_mm"] = limits["min_wall_mm"]

    if buttons["diam_mm"] < limits["min_button_diam_mm"]:
        issues.append(f"diam_mm increased from {buttons['diam_mm']} to {limits['min_button_diam_mm']} (min button diameter).")
        buttons["diam_mm"] = limits["min_button_diam_mm"]

    if buttons["spacing_mm"] < limits["min_button_spacing_mm"]:
        issues.append(f"spacing_mm increased from {buttons['spacing_mm']} to {limits['min_button_spacing_mm']} (min spacing).")
        buttons["spacing_mm"] = limits["min_button_spacing_mm"]

    def fits() -> bool:
        m = compute_grid_metrics(
            length_mm=float(remote["length_mm"]),
            width_mm=float(remote["width_mm"]),
            rows=int(buttons["rows"]),
            cols=int(buttons["cols"]),
            diam_mm=float(buttons["diam_mm"]),
            spacing_mm=float(buttons["spacing_mm"]),
            margin_top_mm=float(buttons["margin_top_mm"]),
            margin_bottom_mm=float(buttons["margin_bottom_mm"]),
            margin_side_mm=float(buttons["margin_side_mm"]),
        )
        return (m.grid_width_mm <= m.usable_width_mm) and (m.grid_height_mm <= m.usable_length_mm)

    # Simple repair loop
    steps = 0
    while not fits() and steps < 50:
        steps += 1
        m = compute_grid_metrics(
            length_mm=float(remote["length_mm"]),
            width_mm=float(remote["width_mm"]),
            rows=int(buttons["rows"]),
            cols=int(buttons["cols"]),
            diam_mm=float(buttons["diam_mm"]),
            spacing_mm=float(buttons["spacing_mm"]),
            margin_top_mm=float(buttons["margin_top_mm"]),
            margin_bottom_mm=float(buttons["margin_bottom_mm"]),
            margin_side_mm=float(buttons["margin_side_mm"]),
        )

        if m.grid_width_mm > m.usable_width_mm and remote["width_mm"] < limits["max_width_mm"]:
            remote["width_mm"] = min(limits["max_width_mm"], float(remote["width_mm"]) + 2.0)
            issues.append("Increased remote width by 2mm to fit button grid.")
            continue

        if m.grid_height_mm > m.usable_length_mm and remote["length_mm"] < limits["max_length_mm"]:
            remote["length_mm"] = min(limits["max_length_mm"], float(remote["length_mm"]) + 4.0)
            issues.append("Increased remote length by 4mm to fit button grid.")
            continue

        if float(buttons["diam_mm"]) > limits["min_button_diam_mm"]:
            buttons["diam_mm"] = max(limits["min_button_diam_mm"], float(buttons["diam_mm"]) - 0.5)
            issues.append("Decreased button diameter by 0.5mm to fit.")
            continue

        if float(buttons["spacing_mm"]) > limits["min_button_spacing_mm"]:
            buttons["spacing_mm"] = max(limits["min_button_spacing_mm"], float(buttons["spacing_mm"]) - 0.5)
            issues.append("Decreased button spacing by 0.5mm to fit.")
            continue

        break

    if not fits():
        raise ValueError(
            "Button grid does not fit within remote dimensions after auto-fix. "
            "Try fewer buttons, smaller diameter, or a bigger remote."
        )

    return params, issues
